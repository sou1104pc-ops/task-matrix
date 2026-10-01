"""Discord Bot 本体。`python -m src.bot` で起動する。

- 毎朝 DAILY_TIME に有料記事の下書きを生成して #下書き チャンネルへ送る
- [承認して公開申請] で Brain に投稿（公開申請）し、#レポート チャンネルに報告する
- 毎日 DAILY_REPORT_TIME に、その日の売上・投稿を #レポート に送る
"""
import asyncio
import base64
import io
import logging
from datetime import datetime, time, timedelta

import discord
from discord import app_commands
from discord.ext import tasks

from . import checker, generator, images, sales, secretary, storage
from .brain_client import BrainClient, BrainError, NotLoggedIn
from .config import (
    AFFILIATE_RATE, AUTO_PUBLISH, DAILY_REPORT_TIME, DAILY_TIME, DEFAULT_CATEGORY, DISCORD_TOKEN, DRAFT_CHANNEL_ID,
    DRAFTS_DIR, GUILD_ID, JST, REPORT_CHANNEL_ID, SECRETARY_CHANNEL_ID,
)

log = logging.getLogger("bot")
browser_lock = asyncio.Lock()
_h, _m = map(int, DAILY_TIME.split(":"))
DAILY_AT = time(hour=_h, minute=_m, tzinfo=JST)
_rh, _rm = map(int, DAILY_REPORT_TIME.split(":"))
DAILY_REPORT_AT = time(hour=_rh, minute=_rm, tzinfo=JST)
WEEKLY_AT = time(hour=9, minute=0, tzinfo=JST)
DONE_LABEL = {"published": "公開申請済み", "saved": "Brain下書き保存済み"}


def preview_html(data, imgs):
    thumb = base64.b64encode(imgs["thumbnail"].read_bytes()).decode()
    free, paid = checker.split_paywall(data["body_html"])
    line = ("<div style='margin:32px 0;padding:12px;border:2px dashed #f96204;color:#f96204;text-align:center;"
            f"font-weight:bold'>ここから有料（{data['price']:,}円）</div>")
    return (
        "<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>下書きプレビュー</title>"
        "<body style='max-width:720px;margin:auto;font-family:sans-serif;line-height:1.8;padding:16px'>"
        f"<img src='data:image/png;base64,{thumb}' style='width:100%;border-radius:8px'>"
        f"<h1>{data['title']}</h1>"
        f"<p style='color:#888'>{data['price']:,}円 ・ {data['category']} {data.get('subcategory') or ''}</p>"
        f"{images.embed_images(free, imgs)}{line}{images.embed_images(paid, imgs)}</body>"
    )


def format_issues(issues):
    if not issues:
        return "✅ 問題なし"
    icon = {"error": "❌", "warn": "⚠️"}
    lines = [f"{icon[lv]} {msg}" for lv, msg in issues]
    return "\n".join(lines)[:1000]


def draft_embed(draft_id, data, issues, status="承認待ち"):
    e = discord.Embed(title=data["title"][:250], description=(data.get("summary") or "")[:1000], color=0xF96204)
    sub = f" ＞ {data['subcategory']}" if data.get("subcategory") else ""
    e.add_field(name="販売設定", value=f"{data['price']:,}円 ・ {data['category']}{sub}", inline=False)
    e.add_field(name="自動チェック", value=format_issues(issues), inline=False)
    free, paid = checker.split_paywall(data["body_html"])
    e.set_footer(text=f"下書き #{draft_id} ・ 無料 {len(checker.text_of(free)):,}字 / "
                      f"有料 {len(checker.text_of(paid)):,}字 ・ {status}")
    return e


async def draft_files(draft_id, data):
    """メイン画像・図・プレビューHTMLを Discord の添付にする（画像はスマホでもそのまま見られる）。"""
    imgs = await images.render(draft_id, data)
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    html = preview_html(data, imgs)
    (DRAFTS_DIR / f"draft-{draft_id}.html").write_text(html, encoding="utf-8")
    files = [discord.File(imgs["thumbnail"], filename=f"draft-{draft_id}-thumbnail.png")]
    files += [discord.File(p, filename=f"draft-{draft_id}-{fid}.png") for fid, p in imgs["figures"].items()]
    return files[:9] + [discord.File(io.BytesIO(html.encode()), filename=f"draft-{draft_id}.html")]


# ---------------------------------------------------------------- views
class ReviseModal(discord.ui.Modal, title="修正依頼"):
    instruction = discord.ui.TextInput(
        label="どう直してほしいか", style=discord.TextStyle.paragraph,
        placeholder="例：無料部分をもっと短く。有料部分にテンプレートを追加。", max_length=1000,
    )

    def __init__(self, bot, draft_id):
        super().__init__()
        self.bot, self.draft_id = bot, draft_id

    async def on_submit(self, interaction):
        await interaction.response.send_message(f"✏️ 下書き #{self.draft_id} を修正中です（数分かかります）")
        await self.bot.revise_draft(self.draft_id, str(self.instruction), interaction.channel)


class SalesModal(discord.ui.Modal, title="販売設定"):
    def __init__(self, bot, draft):
        super().__init__()
        self.bot, self.draft_id = bot, draft["id"]
        d = draft["data"]
        self.price = discord.ui.TextInput(label="価格（円）", default=str(d["price"]), max_length=7)
        self.category = discord.ui.TextInput(label="カテゴリー", default=d["category"], max_length=40)
        self.subcategory = discord.ui.TextInput(label="サブカテゴリー（空でも可）", default=d.get("subcategory") or "",
                                                required=False, max_length=40)
        for item in (self.price, self.category, self.subcategory):
            self.add_item(item)

    async def on_submit(self, interaction):
        try:
            price = int(str(self.price).replace(",", "").replace("円", ""))
        except ValueError:
            return await interaction.response.send_message("価格は数字で入れてください", ephemeral=True)
        await self.bot.update_sales(self.draft_id, interaction, price=price,
                                    category=str(self.category).strip(), subcategory=str(self.subcategory).strip())


class DraftView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    def _draft(self, interaction):
        for d in storage.pending_drafts():
            draft = storage.get_draft(d)
            if draft["message_id"] == interaction.message.id:
                return draft
        return None

    @discord.ui.button(label="承認して公開申請", style=discord.ButtonStyle.success, custom_id="draft:approve")
    async def approve(self, interaction, button):
        draft = self._draft(interaction)
        if not draft:
            return await interaction.response.send_message("この下書きは処理済みです", ephemeral=True)
        if checker.has_error(draft["issues"]):
            return await interaction.response.send_message(
                "❌ チェックでエラーがあるため投稿できません。[修正依頼] か [販売設定] で直してください。", ephemeral=True
            )
        await interaction.response.send_message(f"🚀 下書き #{draft['id']} を Brain に投稿しています…")
        await self.bot.publish_draft(draft, interaction.message, interaction.channel)

    @discord.ui.button(label="修正依頼", style=discord.ButtonStyle.primary, custom_id="draft:revise")
    async def revise(self, interaction, button):
        draft = self._draft(interaction)
        if not draft:
            return await interaction.response.send_message("この下書きは処理済みです", ephemeral=True)
        await interaction.response.send_modal(ReviseModal(self.bot, draft["id"]))

    @discord.ui.button(label="販売設定", style=discord.ButtonStyle.secondary, custom_id="draft:sales")
    async def sales_setting(self, interaction, button):
        draft = self._draft(interaction)
        if not draft:
            return await interaction.response.send_message("この下書きは処理済みです", ephemeral=True)
        await interaction.response.send_modal(SalesModal(self.bot, draft))

    @discord.ui.button(label="ボツ", style=discord.ButtonStyle.danger, custom_id="draft:reject")
    async def reject(self, interaction, button):
        draft = self._draft(interaction)
        if not draft:
            return await interaction.response.send_message("この下書きは処理済みです", ephemeral=True)
        storage.update_draft(draft["id"], status="rejected")
        await interaction.message.edit(
            embed=draft_embed(draft["id"], draft["data"], draft["issues"], "ボツ"), view=None
        )
        await interaction.response.send_message(f"🗑️ 下書き #{draft['id']} をボツにしました")


# ---------------------------------------------------------------- bot
class BrainBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = bool(SECRETARY_CHANNEL_ID)  # #秘書 を使うときだけ必要
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        storage.init()
        self.add_view(DraftView(self))
        register_commands(self)
        guild = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        self.daily.start()
        self.daily_report_task.start()
        self.weekly.start()

    async def on_ready(self):
        log.info("ログインしました: %s", self.user)

    # ---- 秘書との会話 ----
    async def on_message(self, message):
        if message.author.bot or not SECRETARY_CHANNEL_ID:
            return
        if message.channel.id != SECRETARY_CHANNEL_ID or not message.content.strip():
            return
        async with message.channel.typing():
            thinking = await message.channel.send("🤔 考えています…")

            async def progress(tool_name):
                await thinking.edit(content=f"🔧 {tool_name} を実行中…")

            try:
                reply, used = await secretary.chat(message.channel.id, message.content, self, progress)
            except secretary.SecretaryError as e:
                return await thinking.edit(content=f"⚠️ {e}")
            except Exception as e:  # noqa: BLE001 - 会話の失敗でBotを落とさない
                log.exception("secretary failed")
                return await thinking.edit(content=f"⚠️ エラーが起きました: {e}")
        await thinking.delete()
        for i in range(0, len(reply), 1900):
            await message.channel.send(reply[i:i + 1900])
        if used:
            log.info("secretary tools: %s", ", ".join(used))

    @property
    def browser_lock(self):
        return browser_lock

    def draft_channel(self):
        return self.get_channel(DRAFT_CHANNEL_ID)

    def report_channel(self):
        return self.get_channel(REPORT_CHANNEL_ID)

    # ---- daily / weekly ----
    @tasks.loop(time=DAILY_AT)
    async def daily(self):
        if storage.get_setting("paused") == "1":
            return
        try:
            await self.make_draft()
        except Exception as e:  # noqa: BLE001 - 1日の失敗で毎日の実行を止めない
            log.exception("daily failed")
            await self.draft_channel().send(f"⚠️ 今朝の下書き生成でエラーが起きました: {e}")

    @daily.before_loop
    async def _wait_daily(self):
        await self.wait_until_ready()

    @tasks.loop(time=DAILY_REPORT_AT)
    async def daily_report_task(self):
        try:
            await self.report_channel().send(embed=await daily_report())
        except Exception as e:  # noqa: BLE001 - 1日の失敗で毎日の実行を止めない
            log.exception("daily report failed")
            await self.report_channel().send(f"⚠️ デイリーレポートでエラーが起きました: {e}")

    @daily_report_task.before_loop
    async def _wait_daily_report(self):
        await self.wait_until_ready()

    @tasks.loop(time=WEEKLY_AT)
    async def weekly(self):
        if datetime.now(JST).weekday() == 0:  # 月曜
            await self.report_channel().send(embed=weekly_report())

    @weekly.before_loop
    async def _wait_weekly(self):
        await self.wait_until_ready()

    # ---- draft lifecycle ----
    async def make_draft(self, theme=None):
        """(draft_id, None) か、失敗したら (None, 理由) を返す。"""
        ch = self.draft_channel()
        theme = theme or storage.next_theme()
        if not theme:
            reason = "テーマが登録されていません。/テーマ追加 で追加してください。"
            await ch.send(reason)
            return None, reason
        note = await ch.send(f"🧠 有料記事を生成中…　テーマ: {theme['theme']}")
        try:
            data = await generator.generate(theme, storage.published_titles())
        except generator.GenerationError as e:
            await note.edit(content=f"⚠️ 生成に失敗しました: {e}")
            return None, str(e)
        issues = checker.check(data)
        draft_id = storage.create_draft(theme.get("id"), data, issues)
        if theme.get("id"):
            storage.mark_theme_used(theme["id"])
        msg = await ch.send(
            content="📝 今日の下書きです。メイン画像と図は添付の画像、本文（有料ラインの位置つき）は添付のHTMLで確認できます。",
            embed=draft_embed(draft_id, data, issues), files=await draft_files(draft_id, data), view=DraftView(self),
        )
        storage.update_draft(draft_id, message_id=msg.id)
        await note.delete()
        return draft_id, None

    async def revise_draft(self, draft_id, instruction, channel):
        draft = storage.get_draft(draft_id)
        try:
            data = await generator.revise(draft["data"], instruction)
        except generator.GenerationError as e:
            return await channel.send(f"⚠️ 修正に失敗しました: {e}")
        issues = checker.check(data)
        storage.update_draft(draft_id, data=data, issues=issues)
        old = await channel.fetch_message(draft["message_id"])
        await old.edit(view=None)
        msg = await channel.send(
            content=f"✏️ 修正版です（指示: {instruction[:100]}）",
            embed=draft_embed(draft_id, data, issues), files=await draft_files(draft_id, data), view=DraftView(self),
        )
        storage.update_draft(draft_id, message_id=msg.id)

    async def update_sales(self, draft_id, interaction, **fields):
        draft = storage.get_draft(draft_id)
        data = {**draft["data"], **fields}
        issues = checker.check(data)
        storage.update_draft(draft_id, data=data, issues=issues)
        await interaction.message.edit(embed=draft_embed(draft_id, data, issues))
        sub = f" ＞ {data['subcategory']}" if data.get("subcategory") else ""
        await interaction.response.send_message(
            f"💴 下書き #{draft_id} の販売設定を {data['price']:,}円 ・ {data['category']}{sub} にしました")

    async def publish(self, draft):
        """Brain に投稿して (status, url) を返す。失敗は BrainError / NotLoggedIn。"""
        data = draft["data"]
        async with browser_lock:
            imgs = await images.render(draft["id"], data)
            async with BrainClient() as bc:
                article_id, url = await bc.create(data, imgs["thumbnail"], imgs["figures"],
                                                  publish=AUTO_PUBLISH, affiliate_rate=AFFILIATE_RATE)
        status = "published" if AUTO_PUBLISH else "saved"
        storage.update_draft(draft["id"], status=status, brain_id=article_id, brain_url=url)
        return status, url

    async def publish_draft(self, draft, message, channel):
        try:
            status, url = await self.publish(draft)
        except NotLoggedIn as e:
            return await channel.send(f"🔒 {e}")
        except BrainError as e:
            return await channel.send(f"⚠️ 投稿に失敗しました（もう一度 [承認して公開申請] を押せます）: {e}")
        label = DONE_LABEL[status]
        await message.edit(embed=draft_embed(draft["id"], draft["data"], draft["issues"], label), view=None)
        await channel.send(f"✅ {label}: {url}" + ("\n（Brainの審査が終わると公開されます）" if status == "published" else ""))
        if status == "published" and self.report_channel():
            await self.report_channel().send(
                f"📣 今日は「{draft['data']['title']}」（{draft['data']['price']:,}円）を公開申請しました\n{url}")

    async def mark_draft_done(self, draft, status):
        """秘書が投稿したとき、#下書き の承認ボタンを消して報告する。"""
        ch = self.draft_channel()
        if ch and draft.get("message_id"):
            try:
                msg = await ch.fetch_message(draft["message_id"])
                await msg.edit(embed=draft_embed(draft["id"], draft["data"], draft["issues"], DONE_LABEL[status]),
                               view=None)
            except discord.HTTPException:
                pass
        url = storage.get_draft(draft["id"])["brain_url"]
        if status == "published" and self.report_channel():
            await self.report_channel().send(f"📣 秘書が「{draft['data']['title']}」を公開申請しました\n{url}")


# ---------------------------------------------------------------- report
def _sign(n):
    return f"+{n:,}" if n >= 0 else f"{n:,}"


async def fetch_sales(day):
    """(累計売上, 累計部数, その日の販売リスト) を Brain から取る。"""
    async with browser_lock:
        async with BrainClient(headless=True) as bc:
            total_raw = await bc.total_sales()
            hist_raw = await bc.sales_histories(day.strftime("%Y/%m"))
    total, count = sales.totals(total_raw)
    return total, count, sales.sales_on(hist_raw, day)


async def daily_report():
    """その日の売上・投稿をまとめる。

    「今日の売上」は販売履歴のうち今日の日付のもの、「前回からの増分」は累計売上のスナップショット差分。
    """
    today = datetime.now(JST).date()
    since = datetime.combine(today, time(0, 0, tzinfo=JST)).isoformat()
    posts = storage.posts_since(since)

    e = discord.Embed(title=f"📊 Brain デイリーレポート（{today:%-m/%-d}）", color=0xF96204)
    try:
        total, count, todays = await fetch_sales(today)
    except Exception as ex:  # noqa: BLE001 - 売上が取れなくてもレポートは出す
        log.exception("daily sales failed")
        e.add_field(name="売上", value=f"取得できませんでした: {ex}"[:1000], inline=False)
    else:
        e.add_field(name="今日の売上", value=f"{len(todays)}部 / {sum(s['amount'] for s in todays):,}円", inline=True)
        if total is not None:
            prev = storage.previous_sales_stats(today.isoformat())
            storage.save_sales_stats(today.isoformat(), total, count)
            if prev and prev["total_sales"] is not None:
                e.add_field(name=f"前回（{prev['date']}）からの増分",
                            value=f"{_sign(total - prev['total_sales'])}円", inline=True)
            e.add_field(name="累計売上", value=f"{total:,}円" + (f" / {count:,}部" if count is not None else ""),
                        inline=True)
        else:
            e.add_field(name="累計売上", value="読み取れませんでした（src/sales.py の項目名を確認）", inline=True)
        if todays:
            e.add_field(name="今日売れた記事",
                        value="\n".join(f"{s['amount']:,}円 {s['title'][:40]}" for s in todays[:10]), inline=False)
    e.add_field(name="今日の公開申請", value=f"{len(posts)}本", inline=True)
    return e


def weekly_report():
    since = (datetime.now(JST) - timedelta(days=7)).isoformat()
    posts = storage.posts_since(since)
    e = discord.Embed(title="📊 週次レポート（直近7日）", color=0xF2A33A)
    e.add_field(name="公開申請", value=f"{len(posts)}本", inline=True)
    if posts:
        e.add_field(name="記事", value="\n".join(
            f"{p['data']['price']:,}円 {p['data']['title'][:40]}" for p in posts[:10]), inline=False)
    return e


# ---------------------------------------------------------------- commands
def register_commands(bot):
    @bot.tree.command(name="生成", description="今すぐ有料記事の下書きを1本作る")
    @app_commands.describe(テーマ="省略するとテーマリストの次のものを使う", 価格="円（テーマ指定時。省略すると既定の価格）",
                           カテゴリー="Brainのカテゴリー名（テーマ指定時）")
    async def generate_cmd(interaction, テーマ: str = None, 価格: int = None, カテゴリー: str = None):
        await interaction.response.send_message("了解です。下書きを作ります。", ephemeral=True)
        theme = {"theme": テーマ, "persona": "", "price": 価格, "category": カテゴリー} if テーマ else None
        try:
            await bot.make_draft(theme)
        except Exception as e:  # noqa: BLE001
            log.exception("generate failed")
            await interaction.followup.send(f"⚠️ 生成でエラーが起きました: {e}")

    @bot.tree.command(name="テーマ追加", description="記事テーマをリストに追加する")
    @app_commands.describe(テーマ="例: レンタルスタジオ開業の物件選び", 読者="想定読者", 価格="円（省略すると既定の価格）",
                           カテゴリー=f"Brainのカテゴリー名（省略すると「{DEFAULT_CATEGORY}」）", サブカテゴリー="省略可")
    async def add_theme_cmd(interaction, テーマ: str, 読者: str = "", 価格: int = None,
                            カテゴリー: str = None, サブカテゴリー: str = None):
        ok = storage.add_theme(テーマ, 読者, 価格, カテゴリー, サブカテゴリー)
        await interaction.response.send_message("✅ 追加しました" if ok else "同じテーマが既にあります")

    @bot.tree.command(name="テーマ一覧", description="未使用のテーマを表示する")
    async def list_themes_cmd(interaction):
        themes = storage.list_themes()
        text = "\n".join(
            f"{t['id']}. {t['theme']}（{t['price'] or '既定'}円 / {t['category'] or '既定'}）" for t in themes
        ) or "未使用のテーマはありません"
        await interaction.response.send_message(text[:1900])

    @bot.tree.command(name="秘書リセット", description="秘書との会話の記憶を消す")
    async def reset_chat_cmd(interaction):
        n = storage.clear_chat(str(interaction.channel_id))
        await interaction.response.send_message(f"🧹 会話の記憶を消しました（{n}件）", ephemeral=True)

    @bot.tree.command(name="日次レポート", description="今日の売上・公開申請を表示する")
    async def daily_report_cmd(interaction):
        await interaction.response.defer()
        await interaction.followup.send(embed=await daily_report())

    @bot.tree.command(name="レポート", description="直近7日のレポートを表示する")
    async def report_cmd(interaction):
        await interaction.response.send_message(embed=weekly_report())

    @bot.tree.command(name="停止", description="毎朝の自動生成を止める")
    async def pause_cmd(interaction):
        storage.set_setting("paused", "1")
        await interaction.response.send_message("⏸️ 毎朝の自動生成を停止しました（/再開 で再開）")

    @bot.tree.command(name="再開", description="毎朝の自動生成を再開する")
    async def resume_cmd(interaction):
        storage.set_setting("paused", "0")
        await interaction.response.send_message(f"▶️ 再開しました。毎朝 {DAILY_TIME} に下書きを作ります")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    BrainBot().run(DISCORD_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
