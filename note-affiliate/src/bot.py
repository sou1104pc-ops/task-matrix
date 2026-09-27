"""Discord Bot 本体。`python -m src.bot` で起動する。

- 毎朝 DAILY_TIME に下書きを生成して #下書き チャンネルへ送る
- [承認して投稿] で note に投稿し、#レポート チャンネルに報告する
- /生成 /修正 /テーマ追加 などのスラッシュコマンドで指示を出せる
"""
import asyncio
import io
import json
import logging
import urllib.request
from datetime import datetime, time, timedelta

import discord
from discord import app_commands
from discord.ext import tasks

from . import checker, fix_existing, generator, storage
from .config import (
    AUTO_PUBLISH, DAILY_TIME, DISCORD_TOKEN, DRAFT_CHANNEL_ID, DRAFTS_DIR, GUILD_ID, JST, NOTE_USER,
    REPORT_CHANNEL_ID, load_programs,
)
from .note_client import NoteClient, NoteError, NotLoggedIn

log = logging.getLogger("bot")
browser_lock = asyncio.Lock()
_h, _m = map(int, DAILY_TIME.split(":"))
DAILY_AT = time(hour=_h, minute=_m, tzinfo=JST)
WEEKLY_AT = time(hour=9, minute=0, tzinfo=JST)


def preview_html(data):
    tags = " ".join(data.get("hashtags", []))
    return (
        "<!doctype html><meta charset='utf-8'><title>下書きプレビュー</title>"
        "<body style='max-width:720px;margin:auto;font-family:sans-serif;line-height:1.8;padding:16px'>"
        f"<h1>{data['title']}</h1><p style='color:#888'>{tags}</p>{data['body_html']}</body>"
    )


def format_issues(issues):
    if not issues:
        return "✅ 問題なし"
    icon = {"error": "❌", "warn": "⚠️"}
    lines = [f"{icon[lv]} {msg}" for lv, msg in issues]
    return "\n".join(lines)[:1000]


def draft_embed(draft_id, data, issues, status="承認待ち"):
    e = discord.Embed(title=data["title"][:250], description=(data.get("summary") or "")[:1000], color=0x41C9B4)
    e.add_field(name="ハッシュタグ", value=" ".join(data.get("hashtags", [])) or "-", inline=False)
    e.add_field(name="自動チェック", value=format_issues(issues), inline=False)
    chars = len(checker.text_of(data["body_html"]))
    e.set_footer(text=f"下書き #{draft_id} ・ {chars:,}字 ・ {status}")
    return e


def draft_files(draft_id, data):
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    html = preview_html(data)
    (DRAFTS_DIR / f"draft-{draft_id}.html").write_text(html, encoding="utf-8")
    return [discord.File(io.BytesIO(html.encode()), filename=f"draft-{draft_id}.html")]


# ---------------------------------------------------------------- views
class ReviseModal(discord.ui.Modal, title="修正依頼"):
    instruction = discord.ui.TextInput(
        label="どう直してほしいか", style=discord.TextStyle.paragraph,
        placeholder="例：導入をもっと短く。SAP保守担当の悩みに寄せて。", max_length=1000,
    )

    def __init__(self, bot, draft_id):
        super().__init__()
        self.bot, self.draft_id = bot, draft_id

    async def on_submit(self, interaction):
        await interaction.response.send_message(f"✏️ 下書き #{self.draft_id} を修正中です（数分かかります）")
        await self.bot.revise_draft(self.draft_id, str(self.instruction), interaction.channel)


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

    @discord.ui.button(label="承認して投稿", style=discord.ButtonStyle.success, custom_id="draft:approve")
    async def approve(self, interaction, button):
        draft = self._draft(interaction)
        if not draft:
            return await interaction.response.send_message("この下書きは処理済みです", ephemeral=True)
        if checker.has_error(draft["issues"]):
            return await interaction.response.send_message(
                "❌ チェックでエラーがあるため投稿できません。[修正依頼] で直してください。", ephemeral=True
            )
        await interaction.response.send_message(f"🚀 下書き #{draft['id']} を note に投稿しています…")
        await self.bot.publish_draft(draft, interaction.message, interaction.channel)

    @discord.ui.button(label="修正依頼", style=discord.ButtonStyle.primary, custom_id="draft:revise")
    async def revise(self, interaction, button):
        draft = self._draft(interaction)
        if not draft:
            return await interaction.response.send_message("この下書きは処理済みです", ephemeral=True)
        await interaction.response.send_modal(ReviseModal(self.bot, draft["id"]))

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


class FixView(discord.ui.View):
    """既存記事の修正を1記事ずつ承認する。"""

    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="この内容でnoteを更新", style=discord.ButtonStyle.success, custom_id="fix:apply")
    async def apply(self, interaction, button):
        key = storage.get_setting(f"fixmsg:{interaction.message.id}")
        if not key:
            return await interaction.response.send_message("対象が見つかりません", ephemeral=True)
        await interaction.response.send_message(f"🔧 {key} を更新しています…")
        await self.bot.apply_fix(key, interaction.message, interaction.channel)

    @discord.ui.button(label="スキップ", style=discord.ButtonStyle.secondary, custom_id="fix:skip")
    async def skip(self, interaction, button):
        await interaction.message.edit(view=None)
        await interaction.response.send_message("スキップしました", ephemeral=True)


# ---------------------------------------------------------------- bot
class AffiliateBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        storage.init()
        self.add_view(DraftView(self))
        self.add_view(FixView(self))
        register_commands(self)
        guild = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        self.daily.start()
        self.weekly.start()

    async def on_ready(self):
        log.info("ログインしました: %s", self.user)

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

    @tasks.loop(time=WEEKLY_AT)
    async def weekly(self):
        if datetime.now(JST).weekday() == 0:  # 月曜
            await self.report_channel().send(embed=await weekly_report())

    @weekly.before_loop
    async def _wait_weekly(self):
        await self.wait_until_ready()

    # ---- draft lifecycle ----
    async def make_draft(self, theme=None):
        ch = self.draft_channel()
        theme = theme or storage.next_theme()
        if not theme:
            return await ch.send("テーマが登録されていません。/テーマ追加 で追加してください。")
        note = await ch.send(f"🧠 記事を生成中…　テーマ: {theme['theme']}")
        try:
            data = await generator.generate(theme, storage.published_titles())
        except generator.GenerationError as e:
            return await note.edit(content=f"⚠️ 生成に失敗しました: {e}")
        issues = checker.check(data)
        draft_id = storage.create_draft(theme.get("id"), data, issues)
        if theme.get("id"):
            storage.mark_theme_used(theme["id"])
        msg = await ch.send(
            content="📝 今日の下書きです。プレビューは添付のHTMLを開いてください。",
            embed=draft_embed(draft_id, data, issues), files=draft_files(draft_id, data), view=DraftView(self),
        )
        storage.update_draft(draft_id, message_id=msg.id)
        await note.delete()

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
            embed=draft_embed(draft_id, data, issues), files=draft_files(draft_id, data), view=DraftView(self),
        )
        storage.update_draft(draft_id, message_id=msg.id)

    async def publish_draft(self, draft, message, channel):
        data = draft["data"]
        async with browser_lock:
            try:
                async with NoteClient() as nc:
                    url = await nc.create(data["title"], data["body_html"], data["hashtags"], publish=AUTO_PUBLISH)
            except NotLoggedIn as e:
                return await channel.send(f"🔒 {e}")
            except NoteError as e:
                return await channel.send(f"⚠️ 投稿に失敗しました（もう一度 [承認して投稿] を押せます）: {e}")
        status = "published" if AUTO_PUBLISH else "saved"
        storage.update_draft(draft["id"], status=status, note_url=url)
        label = "投稿済み" if AUTO_PUBLISH else "note下書き保存済み"
        await message.edit(embed=draft_embed(draft["id"], data, draft["issues"], label), view=None)
        await channel.send(f"✅ {label}: {url}")
        if AUTO_PUBLISH and self.report_channel():
            await self.report_channel().send(f"📣 今日は「{data['title']}」を投稿しました\n{url}")

    # ---- existing articles ----
    async def send_fixes(self, channel):
        for fix in fix_existing.load_all():
            msg = await channel.send(
                content=f"🔧 **{fix.title_before}**\n{fix.url}",
                embed=fix.embed(), files=[fix.diff_file()], view=FixView(self),
            )
            storage.set_setting(f"fixmsg:{msg.id}", fix.key)

    async def apply_fix(self, key, message, channel):
        fix = fix_existing.load(key)
        async with browser_lock:
            try:
                async with NoteClient() as nc:
                    result = await fix.apply(nc)
            except NotLoggedIn as e:
                return await channel.send(f"🔒 {e}")
            except NoteError as e:
                return await channel.send(f"⚠️ 更新に失敗しました: {e}")
        await message.edit(view=None)
        text = f"✅ 更新しました: {fix.url}"
        if result["not_found"]:
            text += "\n⚠️ 画面上で見つからず手動対応が必要な箇所:\n" + "\n".join(
                f"・{s[:60]}" for s in result["not_found"]
            )
        await channel.send(text[:1900])


# ---------------------------------------------------------------- report
def fetch_note_stats():
    url = f"https://note.com/api/v2/creators/{NOTE_USER}/contents?kind=note&page=1"
    with urllib.request.urlopen(url, timeout=20) as r:
        data = json.load(r)["data"]
    return data.get("totalCount"), data["contents"]


async def weekly_report():
    since = (datetime.now(JST) - timedelta(days=7)).isoformat()
    posts = storage.posts_since(since)
    convs = storage.conversions_since(since)
    e = discord.Embed(title="📊 週次レポート（直近7日）", color=0xF2A33A)
    e.add_field(name="投稿数", value=f"{len(posts)}本", inline=True)
    e.add_field(name="成約（手入力分）", value=f"{len(convs)}件 / {sum(c['amount'] or 0 for c in convs):,}円", inline=True)
    try:
        total, contents = await asyncio.to_thread(fetch_note_stats)
        top = sorted(contents, key=lambda n: n.get("likeCount", 0), reverse=True)[:5]
        e.add_field(name="note全記事数", value=f"{total}本", inline=True)
        e.add_field(
            name="スキが多い記事（最新ページ内）",
            value="\n".join(f"{n.get('likeCount', 0)}♡ {n['name'][:40]}" for n in top) or "-", inline=False,
        )
    except Exception as ex:  # noqa: BLE001 - レポートは統計が取れなくても出す
        e.add_field(name="note統計", value=f"取得できませんでした: {ex}", inline=False)
    return e


# ---------------------------------------------------------------- commands
def register_commands(bot):
    programs = load_programs()
    program_choices = [app_commands.Choice(name=p["name"], value=pid) for pid, p in programs.items()]

    @bot.tree.command(name="生成", description="今すぐ記事の下書きを1本作る")
    @app_commands.describe(テーマ="省略するとテーマリストの次のものを使う", 案件="テーマ指定時に紹介する案件")
    @app_commands.choices(案件=program_choices)
    async def generate_cmd(interaction, テーマ: str = None, 案件: app_commands.Choice[str] = None):
        await interaction.response.send_message("了解です。下書きを作ります。", ephemeral=True)
        theme = None
        if テーマ:
            theme = {"theme": テーマ, "persona": "", "programs": 案件.value if 案件 else "sap_tenshoku"}
        try:
            await bot.make_draft(theme)
        except Exception as e:  # noqa: BLE001
            log.exception("generate failed")
            await interaction.followup.send(f"⚠️ 生成でエラーが起きました: {e}")

    @bot.tree.command(name="テーマ追加", description="記事テーマをリストに追加する")
    @app_commands.describe(テーマ="例: SAP MMコンサルの年収", 読者="想定読者", 案件="紹介する案件")
    @app_commands.choices(案件=program_choices)
    async def add_theme_cmd(interaction, テーマ: str, 案件: app_commands.Choice[str], 読者: str = ""):
        ok = storage.add_theme(テーマ, 読者, 案件.value)
        await interaction.response.send_message("✅ 追加しました" if ok else "同じテーマが既にあります")

    @bot.tree.command(name="テーマ一覧", description="未使用のテーマを表示する")
    async def list_themes_cmd(interaction):
        themes = storage.list_themes()
        text = "\n".join(f"{t['id']}. {t['theme']}（{t['programs']}）" for t in themes) or "未使用のテーマはありません"
        await interaction.response.send_message(text[:1900])

    @bot.tree.command(name="成約", description="A8の成約を記録する")
    @app_commands.choices(案件=program_choices)
    async def conversion_cmd(interaction, 案件: app_commands.Choice[str], 金額: int, 記事url: str = None, メモ: str = None):
        storage.add_conversion(案件.value, 金額, 記事url, メモ)
        await interaction.response.send_message(f"💰 記録しました: {案件.name} {金額:,}円")

    @bot.tree.command(name="レポート", description="直近7日のレポートを表示する")
    async def report_cmd(interaction):
        await interaction.response.defer()
        await interaction.followup.send(embed=await weekly_report())

    @bot.tree.command(name="停止", description="毎朝の自動生成を止める")
    async def pause_cmd(interaction):
        storage.set_setting("paused", "1")
        await interaction.response.send_message("⏸️ 毎朝の自動生成を停止しました（/再開 で再開）")

    @bot.tree.command(name="再開", description="毎朝の自動生成を再開する")
    async def resume_cmd(interaction):
        storage.set_setting("paused", "0")
        await interaction.response.send_message(f"▶️ 再開しました。毎朝 {DAILY_TIME} に下書きを作ります")

    @bot.tree.command(name="既存記事修正", description="既存記事の修正案を1記事ずつ表示する")
    async def fixes_cmd(interaction):
        await interaction.response.send_message("既存記事の修正案を送ります。内容を確認して1記事ずつ更新してください。")
        await bot.send_fixes(interaction.channel)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    AffiliateBot().run(DISCORD_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
