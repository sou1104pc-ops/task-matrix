"""Discord Bot 本体。`python -m src.bot` で起動する。

- 毎朝 DAILY_TIME に Kindle 本の下書き（原稿・表紙・紹介文・キーワード）を作って #下書き チャンネルへ送る
- [承認して出版] で KDP に登録し、#レポート チャンネルに報告する
- /生成 /テーマ追加 などのスラッシュコマンドで指示を出せる
"""
import asyncio
import base64
import io
import logging
from datetime import datetime, time, timedelta
from html import escape

import discord
from discord import app_commands
from discord.ext import tasks

from . import checker, cover, epub, generator, storage
from .config import (
    AUTO_PUBLISH, DAILY_REPORT_TIME, DAILY_TIME, DISCORD_TOKEN, DRAFT_CHANNEL_ID, GUILD_ID, JST, KDP_SELECT,
    PRICE_JPY, REPORT_CHANNEL_ID,
)
from .kdp_client import KdpClient, KdpError, NotLoggedIn

log = logging.getLogger("bot")
browser_lock = asyncio.Lock()
_h, _m = map(int, DAILY_TIME.split(":"))
DAILY_AT = time(hour=_h, minute=_m, tzinfo=JST)
_rh, _rm = map(int, DAILY_REPORT_TIME.split(":"))
DAILY_REPORT_AT = time(hour=_rh, minute=_rm, tzinfo=JST)
WEEKLY_AT = time(hour=9, minute=0, tzinfo=JST)


def preview_html(data, cover_jpg):
    """本の中身をスマホで確認するためのHTML（表紙・紹介文・キーワード・本文）。"""
    img = base64.b64encode(cover_jpg.read_bytes()).decode()
    kws = "".join(f"<li>{escape(k)}</li>" for k in data.get("keywords") or [])
    cats = "".join(f"<li>{escape(c)}</li>" for c in data.get("categories") or [])
    return (
        "<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>本のプレビュー</title>"
        "<body style='max-width:720px;margin:auto;font-family:sans-serif;line-height:1.8;padding:16px'>"
        f"<img src='data:image/jpeg;base64,{img}' style='width:60%;display:block;margin:auto;border:1px solid #ddd'>"
        f"<h1>{escape(data['title'])}</h1><p><b>{escape(data.get('subtitle') or '')}</b></p>"
        f"<p style='color:#888'>読み: {escape(data.get('title_kana') or '')} / {escape(data.get('subtitle_kana') or '')}</p>"
        "<h2 style='background:#f1f3f5;padding:8px'>Amazon の紹介文</h2>"
        f"<div style='border:1px solid #ddd;padding:12px'>{data.get('description_html') or ''}</div>"
        f"<h2 style='background:#f1f3f5;padding:8px'>7つのキーワード</h2><ol>{kws}</ol>"
        f"<h2 style='background:#f1f3f5;padding:8px'>カテゴリー</h2><ul>{cats}</ul>"
        "<h2 style='background:#f1f3f5;padding:8px'>本文</h2>"
        f"{checker.body_html(data)}</body>"
    )


async def build_files(book_id, data):
    """表紙・EPUB・プレビューを作って (epub, cover_jpg, cover_png) を返す。"""
    jpg, png = await cover.render(book_id, data)
    path = epub.build(book_id, data, jpg)
    (cover.book_dir(book_id) / "preview.html").write_text(preview_html(data, jpg), encoding="utf-8")
    return path, jpg, png


def format_issues(issues):
    if not issues:
        return "✅ 問題なし"
    icon = {"error": "❌", "warn": "⚠️"}
    return "\n".join(f"{icon[lv]} {msg}" for lv, msg in issues)[:1000]


def book_embed(book_id, data, issues, status="承認待ち"):
    e = discord.Embed(title=data["title"][:250], description=(data.get("subtitle") or "")[:1000], color=0xFF9900)
    e.add_field(name="検索キーワード", value=data.get("main_keyword") or "-", inline=True)
    e.add_field(name="価格", value=f"{PRICE_JPY:,}円{' ・ KDPセレクト' if KDP_SELECT else ''}", inline=True)
    e.add_field(
        name="7つのキーワード",
        value="\n".join(f"{i}. {k}" for i, k in enumerate(data.get("keywords") or [], 1))[:1000] or "-",
        inline=False,
    )
    e.add_field(name="カテゴリー", value="\n".join(data.get("categories") or [])[:1000] or "-", inline=False)
    e.add_field(name="目次", value=generator.toc_text(data)[:1000], inline=False)
    e.add_field(name="自動チェック", value=format_issues(issues), inline=False)
    e.set_image(url=f"attachment://book-{book_id}-cover.png")
    e.set_footer(text=f"本 #{book_id} ・ 本文 {checker.body_chars(data):,}字 ・ {status}")
    return e


async def book_files(book_id, data):
    path, jpg, png = await build_files(book_id, data)
    html = (cover.book_dir(book_id) / "preview.html").read_bytes()
    return [
        discord.File(png, filename=f"book-{book_id}-cover.png"),
        discord.File(jpg, filename=f"book-{book_id}-cover-full.jpg"),
        discord.File(io.BytesIO(html), filename=f"book-{book_id}-preview.html"),
        discord.File(path, filename=f"book-{book_id}.epub"),
    ]


# ---------------------------------------------------------------- views
class ReviseModal(discord.ui.Modal, title="修正依頼"):
    instruction = discord.ui.TextInput(
        label="どう直してほしいか", style=discord.TextStyle.paragraph,
        placeholder="例：タイトルをもっと短く。第3章に具体例を増やして。", max_length=1000,
    )

    def __init__(self, bot, book_id):
        super().__init__()
        self.bot, self.book_id = bot, book_id

    async def on_submit(self, interaction):
        await interaction.response.send_message(f"✏️ 本 #{self.book_id} を修正中です（章を書き直すと10分以上かかります）")
        await self.bot.revise_book(self.book_id, str(self.instruction), interaction.channel)


class CoverModal(discord.ui.Modal, title="表紙作り直し"):
    instruction = discord.ui.TextInput(
        label="どう直してほしいか（空でもOK）", style=discord.TextStyle.paragraph, required=False,
        placeholder="例：もっと目立つ色に。「初心者」を大きく。", max_length=500,
    )

    def __init__(self, bot, book_id):
        super().__init__()
        self.bot, self.book_id = bot, book_id

    async def on_submit(self, interaction):
        await interaction.response.send_message(f"🎨 本 #{self.book_id} の表紙を作り直しています")
        await self.bot.redo_cover(self.book_id, str(self.instruction or ""), interaction.channel)


class BookView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    async def _book(self, interaction):
        book = storage.book_by_message(interaction.message.id)
        if not book:
            await interaction.response.send_message("この下書きは処理済みです", ephemeral=True)
        return book

    @discord.ui.button(label="承認して出版", style=discord.ButtonStyle.success, custom_id="book:approve")
    async def approve(self, interaction, button):
        book = await self._book(interaction)
        if not book:
            return
        if checker.has_error(book["issues"]):
            return await interaction.response.send_message(
                "❌ チェックでエラーがあるため出版できません。[修正依頼] で直してください。", ephemeral=True
            )
        what = "出版申請" if AUTO_PUBLISH else "KDPに下書き保存"
        await interaction.response.send_message(f"🚀 本 #{book['id']} を KDP に登録しています（{what}・数分かかります）…")
        await self.bot.publish_book(book, interaction.message, interaction.channel)

    @discord.ui.button(label="修正依頼", style=discord.ButtonStyle.primary, custom_id="book:revise")
    async def revise(self, interaction, button):
        book = await self._book(interaction)
        if book:
            await interaction.response.send_modal(ReviseModal(self.bot, book["id"]))

    @discord.ui.button(label="表紙作り直し", style=discord.ButtonStyle.secondary, custom_id="book:cover")
    async def redo_cover(self, interaction, button):
        book = await self._book(interaction)
        if book:
            await interaction.response.send_modal(CoverModal(self.bot, book["id"]))

    @discord.ui.button(label="ボツ", style=discord.ButtonStyle.danger, custom_id="book:reject")
    async def reject(self, interaction, button):
        book = await self._book(interaction)
        if not book:
            return
        storage.update_book(book["id"], status="rejected")
        await interaction.message.edit(embed=book_embed(book["id"], book["data"], book["issues"], "ボツ"), view=None)
        await interaction.response.send_message(f"🗑️ 本 #{book['id']} をボツにしました")


# ---------------------------------------------------------------- bot
class KindleBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        storage.init()
        self.add_view(BookView(self))
        register_commands(self)
        guild = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        self.daily.start()
        self.daily_report_task.start()
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
            await self.make_book()
        except Exception as e:  # noqa: BLE001 - 1日の失敗で毎日の実行を止めない
            log.exception("daily failed")
            await self.draft_channel().send(f"⚠️ 今朝の本の生成でエラーが起きました: {e}")

    @daily.before_loop
    async def _wait_daily(self):
        await self.wait_until_ready()

    @tasks.loop(time=DAILY_REPORT_AT)
    async def daily_report_task(self):
        try:
            await self.report_channel().send(embed=daily_report())
        except Exception as e:  # noqa: BLE001
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

    # ---- book lifecycle ----
    async def _progress_message(self, channel, first):
        msg = await channel.send(f"🧠 {first}")

        async def progress(text):
            try:
                await msg.edit(content=f"🧠 {text}")
            except discord.HTTPException:
                pass
        return msg, progress

    async def send_book(self, channel, book_id, data, issues, content):
        msg = await channel.send(
            content=content, embed=book_embed(book_id, data, issues),
            files=await book_files(book_id, data), view=BookView(self),
        )
        storage.update_book(book_id, message_id=msg.id)

    async def make_book(self, theme=None):
        """(book_id, None) か、失敗したら (None, 理由) を返す。"""
        ch = self.draft_channel()
        theme = theme or storage.next_theme()
        if not theme:
            reason = "未使用のテーマがありません。/テーマ追加 で追加してください。"
            await ch.send(reason)
            return None, reason
        status, progress = await self._progress_message(ch, f"本を作っています…　テーマ: {theme['theme']}")
        try:
            data = await generator.generate(theme, storage.published_titles(), progress)
        except generator.GenerationError as e:
            await status.edit(content=f"⚠️ 生成に失敗しました（テーマ: {theme['theme']}）: {e}")
            return None, str(e)
        issues = checker.check(data)
        book_id = storage.create_book(theme.get("id"), data, issues)
        if theme.get("id"):
            storage.mark_theme_used(theme["id"])
        await self.send_book(
            ch, book_id, data, issues,
            "📚 今日の本の下書きです。表紙は画像、紹介文・キーワード・本文は添付の preview.html で確認できます"
            "（.epub は Kindle アプリやブックで開けます）。",
        )
        await status.delete()
        return book_id, None

    async def _replace(self, book_id, data, channel, content):
        book = storage.get_book(book_id)
        issues = checker.check(data)
        storage.update_book(book_id, data=data, issues=issues)
        try:
            old = await channel.fetch_message(book["message_id"])
            await old.edit(view=None)
        except discord.HTTPException:
            pass
        await self.send_book(channel, book_id, data, issues, content)

    async def revise_book(self, book_id, instruction, channel):
        book = storage.get_book(book_id)
        status, progress = await self._progress_message(channel, "修正しています…")
        try:
            data = await generator.revise(book["data"], instruction, progress)
        except generator.GenerationError as e:
            return await status.edit(content=f"⚠️ 修正に失敗しました: {e}")
        await self._replace(book_id, data, channel, f"✏️ 修正版です（指示: {instruction[:100]}）")
        await status.delete()

    async def redo_cover(self, book_id, instruction, channel):
        book = storage.get_book(book_id)
        data = book["data"]
        try:
            data["cover"] = await generator.redo_cover(data, instruction)
        except generator.GenerationError as e:
            return await channel.send(f"⚠️ 表紙の作り直しに失敗しました: {e}")
        await self._replace(book_id, data, channel, f"🎨 表紙を作り直しました（指示: {instruction[:100] or 'おまかせ'}）")

    async def publish_book(self, book, message, channel):
        data = book["data"]
        async with browser_lock:
            try:
                path, jpg, _ = await build_files(book["id"], data)
                async with KdpClient(tag=f"book{book['id']}") as kc:
                    result = await kc.create(data, path, jpg, publish=AUTO_PUBLISH)
                    notes = kc.log
            except NotLoggedIn as e:
                return await channel.send(f"🔒 {e}")
            except KdpError as e:
                return await channel.send(f"⚠️ KDPへの登録に失敗しました（もう一度 [承認して出版] を押せます）: {e}")
            except Exception as e:  # noqa: BLE001 - 想定外の画面でもBotは落とさない
                log.exception("publish failed")
                return await channel.send(f"⚠️ KDPへの登録中にエラーが起きました: {e}")
        status = "published" if AUTO_PUBLISH else "saved"
        storage.update_book(book["id"], status=status, kdp_note=result + "\n" + "\n".join(notes))
        label = "出版申請済み" if AUTO_PUBLISH else "KDP下書き保存済み"
        await message.edit(embed=book_embed(book["id"], data, book["issues"], label), view=None)
        text = f"✅ {label}: {result}"
        if notes:
            text += "\n" + "\n".join(notes)
        await channel.send(text[:1900])
        if self.report_channel():
            if AUTO_PUBLISH:
                await self.report_channel().send(
                    f"📣 今日は「{data['title']}」を出版申請しました（KDPの審査は通常72時間以内）。\n"
                    "公開されたら、KDPセレクトの無料キャンペーン（5日間）を設定して SNS で告知すると、初月から読まれやすくなります。"
                )
            else:
                await self.report_channel().send(
                    f"📝 「{data['title']}」を KDP に下書き保存しました。KDP の本棚で内容を確認して「出版」を押してください。"
                )


# ---------------------------------------------------------------- report
def daily_report():
    today = datetime.now(JST).date()
    since = datetime.combine(today, time(0, 0, tzinfo=JST)).isoformat()
    done = storage.published_since(since)
    e = discord.Embed(title=f"📊 デイリーレポート（{today:%-m/%-d}）", color=0x3AA0F2)
    e.add_field(name="今日KDPに登録した本", value="\n".join(f"・{b['data']['title'][:60]}" for b in done) or "なし",
                inline=False)
    e.add_field(name="承認待ちの下書き", value=f"{len(storage.books_with_status('pending'))}冊", inline=True)
    e.add_field(name="これまでに登録した本", value=f"{len(storage.books_with_status('published', 'saved'))}冊", inline=True)
    e.add_field(name="残りのテーマ", value=f"{len(storage.list_themes(limit=1000))}件", inline=True)
    e.set_footer(text="売上（ロイヤリティ・既読ページ数）は KDP のレポート画面で確認してください")
    return e


def weekly_report():
    since = (datetime.now(JST) - timedelta(days=7)).isoformat()
    done = storage.published_since(since)
    e = discord.Embed(title="📊 週次レポート（直近7日）", color=0xF2A33A)
    e.add_field(name="KDPに登録した本", value=f"{len(done)}冊", inline=True)
    e.add_field(name="これまでの合計", value=f"{len(storage.books_with_status('published', 'saved'))}冊", inline=True)
    e.add_field(name="タイトル", value="\n".join(f"・{b['data']['title'][:60]}" for b in done) or "-", inline=False)
    e.set_footer(text="冊数が増えるほど「この本を読んだ人はこれも読んでいます」に自分の本が並びやすくなります")
    return e


# ---------------------------------------------------------------- commands
def register_commands(bot):
    @bot.tree.command(name="生成", description="今すぐ本の下書きを1冊作る")
    @app_commands.describe(テーマ="省略するとテーマリストの次のものを使う", 読者="想定読者",
                           メモ="あなたの体験・入れてほしい内容（本に書いてよい事実）",
                           売れ筋="同じテーマで売れている本のタイトル（| 区切りで複数）")
    async def generate_cmd(interaction, テーマ: str = None, 読者: str = "", メモ: str = "", 売れ筋: str = ""):
        await interaction.response.send_message("了解です。本の下書きを作ります（15〜30分かかります）。", ephemeral=True)
        theme = {"theme": テーマ, "persona": 読者, "memo": メモ, "rivals": 売れ筋} if テーマ else None
        try:
            await bot.make_book(theme)
        except Exception as e:  # noqa: BLE001
            log.exception("generate failed")
            await interaction.followup.send(f"⚠️ 生成でエラーが起きました: {e}")

    @bot.tree.command(name="テーマ追加", description="本のテーマをリストに追加する")
    @app_commands.describe(テーマ="本のテーマ（例: スマホで始める家計簿）", 読者="想定読者",
                           メモ="あなたの体験・入れてほしい内容（本に書いてよい事実）",
                           売れ筋="同じテーマで売れている本のタイトル（| 区切りで複数）")
    async def add_theme_cmd(interaction, テーマ: str, 読者: str = "", メモ: str = "", 売れ筋: str = ""):
        ok = storage.add_theme(テーマ, 読者, メモ, 売れ筋)
        await interaction.response.send_message("✅ 追加しました" if ok else "同じテーマが既にあります")

    @bot.tree.command(name="テーマ一覧", description="未使用のテーマを表示する")
    async def list_themes_cmd(interaction):
        themes = storage.list_themes()
        text = "\n".join(f"{t['id']}. {t['theme']}" + (f"（{t['persona']}）" if t["persona"] else "") for t in themes)
        await interaction.response.send_message((text or "未使用のテーマはありません")[:1900])

    @bot.tree.command(name="日次レポート", description="今日の登録状況を表示する")
    async def daily_report_cmd(interaction):
        await interaction.response.send_message(embed=daily_report())

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
        await interaction.response.send_message(f"▶️ 再開しました。毎朝 {DAILY_TIME} に本の下書きを作ります")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    KindleBot().run(DISCORD_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
