"""Discord Bot 本体。`python -m src.bot` で起動する。

- 毎朝 DAILY_TIME に有料記事の下書きを生成して #下書き チャンネルへ送る（#材料 に届いた材料を優先して使う）
- [承認して公開申請] で Brain に投稿（公開申請）し、#レポート チャンネルに報告する
- 1時間ごとに公開状態を見て、公開から PRICE_SCHEDULE の日数が経った記事を値上げする
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

from . import accounts, checker, generator, images, materials, pricing, sales, secretary, storage, thumbnail
from .brain_client import BrainClient, BrainError, NotLoggedIn
from .config import (
    AUTO_PUBLISH, DAILY_REPORT_TIME, DAILY_TIME, DEFAULT_CATEGORY, DISCORD_TOKEN, DRAFT_CHANNEL_ID,
    DRAFTS_DIR, GUILD_ID, JST, MATERIAL_CHANNEL_ID, PRICE_SCHEDULE, REPORT_CHANNEL_ID, SECRETARY_CHANNEL_ID,
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
    account = accounts.get(data.get("account"))
    box = "<div style='background:#fff6ef;border-left:4px solid #f96204;padding:8px 16px;margin:16px 0'>{}</div>"
    head, tail = accounts.reward_blocks(account, "top"), accounts.reward_blocks(account, "bottom")
    paid = (box.format("".join(head)) if head else "") + paid + (box.format("".join(tail)) if tail else "")
    line = ("<div style='margin:32px 0;padding:12px;border:2px dashed #f96204;color:#f96204;text-align:center;"
            f"font-weight:bold'>ここから有料（{data['price']:,}円）</div>")
    return (
        "<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>下書きプレビュー</title>"
        "<body style='max-width:720px;margin:auto;font-family:sans-serif;line-height:1.8;padding:16px'>"
        f"<img src='data:image/png;base64,{thumb}' style='width:100%;border-radius:8px'>"
        f"<h1>{data['title']}</h1>"
        f"<p style='color:#888'>{account['name']} ・ {data['price']:,}円 ・ {data['category']} {data.get('subcategory') or ''}</p>"
        f"{images.embed_images(free, imgs)}{line}{images.embed_images(paid, imgs)}</body>"
    )


def format_issues(issues):
    if not issues:
        return "✅ 問題なし"
    icon = {"error": "❌", "warn": "⚠️"}
    lines = [f"{icon[lv]} {msg}" for lv, msg in issues]
    return "\n".join(lines)[:1000]


def price_plan(first_price):
    later = " → ".join(f"{d}日後 {p:,}円" for d, p in PRICE_SCHEDULE[1:])
    return f"{first_price:,}円" + (f" → {later}" if later else "")


def draft_embed(draft_id, data, issues, status="承認待ち"):
    e = discord.Embed(title=data["title"][:250], description=(data.get("summary") or "")[:1000], color=0xF96204)
    sub = f" ＞ {data['subcategory']}" if data.get("subcategory") else ""
    account = accounts.get(data.get("account"))
    e.add_field(name="アカウント", value=account["name"], inline=False)
    e.add_field(name="販売設定", value=f"{price_plan(data['price'])}\n{data['category']}{sub}", inline=False)
    by = data.get("thumbnail_by")
    if by == "chatgpt":
        e.add_field(name="サムネ", value="ChatGPTで作成（文字の誤りがあれば [サムネ作り直し]）", inline=False)
    elif by == "html":
        e.add_field(name="サムネ", value=f"⚠️ ChatGPTで作れず、仮のサムネです: {data.get('thumbnail_error') or ''}"[:1000],
                    inline=False)
    if accounts.has_reward(account):
        e.add_field(name="特典（有料部分のはじめと最後に掲載）",
                    value=f"{account.get('reward_title') or '購入者限定の特典'}\n{account['line_url']}", inline=False)
    else:
        e.add_field(name="特典", value="なし（config/accounts.json に line_url が無い）", inline=False)
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
        self.price = discord.ui.TextInput(label="公開直後の価格（円）。その後の値上げは自動", default=str(d["price"]),
                                          max_length=7)
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


class ThumbnailModal(discord.ui.Modal, title="サムネ作り直し"):
    def __init__(self, bot, draft):
        super().__init__()
        self.bot, self.draft_id = bot, draft["id"]
        t = draft["data"].get("thumbnail") or {}
        self.catch = discord.ui.TextInput(label="サムネの文字（改行も反映されます）", style=discord.TextStyle.paragraph,
                                          default=t.get("catch") or "", max_length=80)
        self.sub = discord.ui.TextInput(label="補足の一行（空でも可）", default=t.get("sub") or "", required=False,
                                        max_length=40)
        self.visual = discord.ui.TextInput(label="絵の雰囲気（空でも可）", style=discord.TextStyle.paragraph,
                                           default=t.get("visual") or "", required=False, max_length=300)
        for item in (self.catch, self.sub, self.visual):
            self.add_item(item)

    async def on_submit(self, interaction):
        await interaction.response.send_message(f"🎨 下書き #{self.draft_id} のサムネを作り直しています（1〜2分）")
        await self.bot.remake_thumbnail(self.draft_id, interaction.channel, catch=str(self.catch).strip(),
                                        sub=str(self.sub).strip(), visual=str(self.visual).strip())


class DraftView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot
        if len(accounts.ACCOUNTS) < 2:
            self.remove_item(self.choose_account)

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

    @discord.ui.button(label="サムネ作り直し", style=discord.ButtonStyle.secondary, custom_id="draft:thumbnail")
    async def remake_thumb(self, interaction, button):
        draft = self._draft(interaction)
        if not draft:
            return await interaction.response.send_message("この下書きは処理済みです", ephemeral=True)
        await interaction.response.send_modal(ThumbnailModal(self.bot, draft))

    @discord.ui.select(placeholder="出すアカウントを変える", custom_id="draft:account", row=1,
                       options=[discord.SelectOption(label=a["name"], value=a["id"]) for a in accounts.ACCOUNTS])
    async def choose_account(self, interaction, select):
        draft = self._draft(interaction)
        if not draft:
            return await interaction.response.send_message("この下書きは処理済みです", ephemeral=True)
        await self.bot.change_account(draft, select.values[0], interaction)

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


class MaterialView(discord.ui.View):
    """#材料 の投稿に付けるボタン。"""

    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="この材料で今すぐ記事にする", style=discord.ButtonStyle.success, custom_id="material:generate")
    async def generate(self, interaction, button):
        material = storage.get_material(thread_id=interaction.channel.id)
        if not material or not storage.material_items(material["id"]):
            return await interaction.response.send_message("まだ材料がありません。この投稿に返信で材料を送ってください",
                                                           ephemeral=True)
        await interaction.response.send_message("🧠 この材料で記事を作ります。#下書き に届きます（数分かかります）")
        try:
            await self.bot.make_draft(material=material)
        except Exception as e:  # noqa: BLE001
            log.exception("material generate failed")
            await interaction.followup.send(f"⚠️ 生成でエラーが起きました: {e}")


# ---------------------------------------------------------------- bot
class BrainBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = bool(SECRETARY_CHANNEL_ID or MATERIAL_CHANNEL_ID)  # #秘書 #材料 を使うときだけ必要
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        storage.init()
        self.add_view(DraftView(self))
        self.add_view(MaterialView(self))
        register_commands(self)
        guild = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        self.daily.start()
        self.daily_report_task.start()
        self.weekly.start()
        self.price_task.start()

    async def on_ready(self):
        log.info("ログインしました: %s", self.user)

    # ---- #材料 ----
    def _is_material_thread(self, channel):
        return bool(MATERIAL_CHANNEL_ID) and isinstance(channel, discord.Thread) and channel.parent_id == MATERIAL_CHANNEL_ID

    async def on_thread_create(self, thread):
        if not self._is_material_thread(thread):
            return
        storage.add_material(thread.id, thread.name)
        await asyncio.sleep(1)  # 最初の投稿が届くのを待つ
        await thread.send(
            f"📥 「{thread.name}」の材料として受け付けます。メモ・URL・画像・PDF・テキストファイルを、"
            "この投稿にどんどん返信してください。\n毎朝の記事作りで、まだ記事にしていない材料を古い順に使います。",
            view=MaterialView(self),
        )

    async def on_material(self, message):
        material = storage.get_material(thread_id=message.channel.id) or storage.add_material(
            message.channel.id, message.channel.name)
        skipped = await materials.save_message(material, message)
        await message.add_reaction("📥")
        if material["status"] == "used":
            storage.set_material_status(material["id"], "open")
            await message.channel.send("この材料は一度記事にしましたが、追加があったので次の記事作りでまた使います")
        if skipped:
            await message.reply("⚠️ 読めなかったもの:\n" + "\n".join(f"・{s}" for s in skipped)[:1800])

    # ---- 秘書との会話 ----
    async def on_message(self, message):
        if message.author.bot:
            return
        if self._is_material_thread(message.channel):
            try:
                return await self.on_material(message)
            except Exception as e:  # noqa: BLE001 - 材料の保存失敗でBotを落とさない
                log.exception("material save failed")
                return await message.reply(f"⚠️ 材料を保存できませんでした: {e}")
        if not SECRETARY_CHANNEL_ID:
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

    @tasks.loop(hours=1)
    async def price_task(self):
        for account in accounts.ACCOUNTS:
            if not pricing.watching(account):
                continue
            key = f"login_warned:{account['id']}"
            try:
                async with browser_lock:
                    async with BrainClient(account, headless=True) as bc:
                        events = await pricing.check_all(bc)
            except NotLoggedIn as e:
                events = [f"🔒 値上げの確認ができません: {e}"] if storage.get_setting(key) != "1" else []
                storage.set_setting(key, "1")
            except Exception:  # noqa: BLE001 - 次の回にもう一度試す
                log.exception("price task failed: %s", account["id"])
                continue
            else:
                storage.set_setting(key, "0")
            for text in events:
                await self.report_channel().send(text[:1900])

    @price_task.before_loop
    async def _wait_price(self):
        await self.wait_until_ready()

    # ---- draft lifecycle ----
    async def make_draft(self, theme=None, material=None):
        """(draft_id, None) か、失敗したら (None, 理由) を返す。

        何も指定が無ければ、#材料 のまだ使っていない材料 → テーマリストの順に使う。
        """
        ch = self.draft_channel()
        if not theme and not material:
            material = storage.next_material()
        account = accounts.DEFAULT
        if material:
            theme = {"theme": material["theme"]}
            account = await self.material_account(material)
        theme = theme or storage.next_theme()
        if not theme:
            reason = "材料もテーマもありません。#材料 に材料を送るか、/テーマ追加 で追加してください。"
            await ch.send(reason)
            return None, reason
        src = "（#材料 から）" if material else ""
        note = await ch.send(f"🧠 有料記事を生成中…　テーマ: {theme['theme']}{src}")
        try:
            data = await generator.generate(theme, storage.published_titles(), material, account)
        except generator.GenerationError as e:
            await note.edit(content=f"⚠️ 生成に失敗しました: {e}")
            return None, str(e)
        data["account"] = account["id"]
        if material:
            data["material_id"] = material["id"]
        issues = checker.check(data)
        draft_id = storage.create_draft(theme.get("id"), data, issues, material["id"] if material else None)
        data["thumbnail_by"], data["thumbnail_error"] = await thumbnail.make(draft_id, data)
        storage.update_draft(draft_id, data=data)
        if theme.get("id"):
            storage.mark_theme_used(theme["id"])
        if material:
            storage.set_material_status(material["id"], "used")
        msg = await ch.send(
            content="📝 今日の下書きです。メイン画像と図は添付の画像、本文（有料ラインの位置つき）は添付のHTMLで確認できます。",
            embed=draft_embed(draft_id, data, issues), files=await draft_files(draft_id, data), view=DraftView(self),
        )
        storage.update_draft(draft_id, message_id=msg.id)
        await note.delete()
        if material:
            thread = self.get_channel(material["thread_id"])
            if thread:
                await thread.send(f"📝 この材料で下書き #{draft_id} を作りました（#下書き を見てください）: {msg.jump_url}")
        return draft_id, None

    async def revise_draft(self, draft_id, instruction, channel):
        draft = storage.get_draft(draft_id)
        try:
            data = await generator.revise(draft["data"], instruction)
        except generator.GenerationError as e:
            return await channel.send(f"⚠️ 修正に失敗しました: {e}")
        for k in ("material_id", "account"):
            if draft["data"].get(k):
                data[k] = draft["data"][k]
        if data.get("thumbnail") != draft["data"].get("thumbnail"):  # サムネの文字が変わったときだけ描き直す
            data["thumbnail_by"], data["thumbnail_error"] = await thumbnail.make(draft_id, data)
        else:
            data["thumbnail_by"], data["thumbnail_error"] = draft["data"].get("thumbnail_by"), draft["data"].get(
                "thumbnail_error")
        issues = checker.check(data)
        storage.update_draft(draft_id, data=data, issues=issues)
        old = await channel.fetch_message(draft["message_id"])
        await old.edit(view=None)
        msg = await channel.send(
            content=f"✏️ 修正版です（指示: {instruction[:100]}）",
            embed=draft_embed(draft_id, data, issues), files=await draft_files(draft_id, data), view=DraftView(self),
        )
        storage.update_draft(draft_id, message_id=msg.id)

    async def remake_thumbnail(self, draft_id, channel, **fields):
        draft = storage.get_draft(draft_id)
        data = dict(draft["data"])
        data["thumbnail"] = {**(data.get("thumbnail") or {}), **fields}
        data["thumbnail_by"], data["thumbnail_error"] = await thumbnail.make(draft_id, data)
        issues = checker.check(data)
        storage.update_draft(draft_id, data=data, issues=issues)
        old = await channel.fetch_message(draft["message_id"])
        await old.edit(view=None)
        msg = await channel.send(
            content="🎨 サムネを作り直しました",
            embed=draft_embed(draft_id, data, issues), files=await draft_files(draft_id, data), view=DraftView(self),
        )
        storage.update_draft(draft_id, message_id=msg.id)

    async def material_account(self, material):
        """#材料 の投稿のタグから、出すアカウントを決める。"""
        thread = self.get_channel(material["thread_id"])
        if thread is None:
            try:
                thread = await self.fetch_channel(material["thread_id"])
            except discord.HTTPException:
                return accounts.DEFAULT
        return accounts.from_tags([t.name for t in getattr(thread, "applied_tags", [])])

    async def change_account(self, draft, account_id, interaction):
        data = {**draft["data"], "account": account_id}
        old = accounts.get(draft["data"].get("account"))
        new = accounts.get(account_id)
        if old["id"] == new["id"]:
            return await interaction.response.send_message(f"すでに {new['name']} です", ephemeral=True)
        storage.update_draft(draft["id"], data=data)
        await interaction.response.send_message(
            f"🔁 下書き #{draft['id']} を {new['name']} で出すように変えました（特典も {new['name']} のものになります）")
        await interaction.message.edit(view=None)
        msg = await interaction.channel.send(
            content=f"🔁 アカウントを {new['name']} に変えた版です",
            embed=draft_embed(draft["id"], data, draft["issues"]), files=await draft_files(draft["id"], data),
            view=DraftView(self),
        )
        storage.update_draft(draft["id"], message_id=msg.id)

    async def update_sales(self, draft_id, interaction, **fields):
        draft = storage.get_draft(draft_id)
        data = {**draft["data"], **fields}
        issues = checker.check(data)
        storage.update_draft(draft_id, data=data, issues=issues)
        await interaction.message.edit(embed=draft_embed(draft_id, data, issues))
        sub = f" ＞ {data['subcategory']}" if data.get("subcategory") else ""
        await interaction.response.send_message(
            f"💴 下書き #{draft_id} の販売設定を {price_plan(data['price'])} ・ {data['category']}{sub} にしました")

    async def publish(self, draft):
        """Brain に投稿して (status, url) を返す。失敗は BrainError / NotLoggedIn。"""
        data = draft["data"]
        async with browser_lock:
            imgs = await images.render(draft["id"], data)
            async with BrainClient(accounts.get(data.get("account"))) as bc:
                article_id, url = await bc.create(data, imgs["thumbnail"], imgs["figures"], publish=AUTO_PUBLISH)
        status = "published" if AUTO_PUBLISH else "saved"
        storage.update_draft(draft["id"], status=status, brain_id=article_id, brain_url=url,
                             submitted_at=storage.now(), price_stage=0)
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
        await channel.send(f"✅ {label}: {url}" + (
            "\n（Brainの審査が終わると公開されます。公開されたら自動の値上げが始まります）" if status == "published" else ""))
        if status == "published" and self.report_channel():
            name = accounts.get(draft["data"].get("account"))["name"]
            await self.report_channel().send(
                f"📣 今日は［{name}］「{draft['data']['title']}」（{draft['data']['price']:,}円）を公開申請しました\n{url}")

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


async def fetch_sales(day, account=None):
    """(累計売上, 累計部数, その日の販売リスト) を Brain から取る（アカウント1つぶん）。"""
    async with browser_lock:
        async with BrainClient(account, headless=True) as bc:
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
    for account in accounts.ACCOUNTS:
        e.add_field(name=f"■ {account['name']}", value=await _account_sales_text(account, today), inline=False)
    e.add_field(name="今日の公開申請", value=f"{len(posts)}本", inline=True)
    return e


async def _account_sales_text(account, today):
    try:
        total, count, todays = await fetch_sales(today, account)
    except Exception as ex:  # noqa: BLE001 - 売上が取れなくてもレポートは出す
        log.exception("daily sales failed: %s", account["id"])
        return f"売上を取得できませんでした: {ex}"[:1000]
    lines = [f"今日: {len(todays)}部 / {sum(s['amount'] for s in todays):,}円"]
    if total is not None:
        prev = storage.previous_sales_stats(today.isoformat(), account["id"])
        storage.save_sales_stats(today.isoformat(), account["id"], total, count)
        diff = f"（前回 {prev['date']} から {_sign(total - prev['total_sales'])}円）" if prev and prev["total_sales"] is not None else ""
        lines.append(f"累計: {total:,}円" + (f" / {count:,}部" if count is not None else "") + diff)
    else:
        lines.append("累計: 読み取れませんでした（src/sales.py の項目名を確認）")
    lines += [f"・{s['amount']:,}円 {s['title'][:36]}" for s in todays[:8]]
    return "\n".join(lines)[:1000]


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

    @bot.tree.command(name="材料一覧", description="#材料 に届いた、まだ記事にしていない材料を表示する")
    async def list_materials_cmd(interaction):
        items = storage.list_materials()
        text = "\n".join(f"{m['id']}. {m['theme']}（材料{m['items']}件） <#{m['thread_id']}>" for m in items)
        await interaction.response.send_message((text or "まだ記事にしていない材料はありません")[:1900])

    @bot.tree.command(name="値上げ確認", description="公開状態を確認して、予定の日を過ぎた記事を今すぐ値上げする")
    async def price_check_cmd(interaction):
        await interaction.response.defer()
        events = []
        for account in accounts.ACCOUNTS:
            if not pricing.watching(account):
                continue
            try:
                async with browser_lock:
                    async with BrainClient(account, headless=True) as bc:
                        events += await pricing.check_all(bc)
            except BrainError as e:
                events.append(f"⚠️ {e}")
        await interaction.followup.send("\n".join(events)[:1900] or "変更はありませんでした")

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
