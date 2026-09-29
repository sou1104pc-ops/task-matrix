"""Discord Bot 本体。`python -m src.bot` で起動する。

- 毎日 DRAFT_TIME に、全アカウントの翌日分の下書きを作って #下書き に出す
- 下書きは [承認] するまで投稿されない。承認済みのものは予約時刻に1分以内で投稿される
- [修正依頼]（AIが書き直す）/ [直接編集] / [時刻変更] / [ボツ] で内容を整える
"""
import asyncio
import logging
import random
import re
from datetime import date, datetime, time, timedelta

import discord
from discord import app_commands
from discord.ext import tasks

from . import accounts, checker, generator, secretary, storage, threads_api
from .config import (
    DISCORD_TOKEN, DRAFT_CHANNEL_ID, DRAFT_TIME, GUILD_ID, JST, MAX_CHAIN, POST_JITTER_MIN,
    REPORT_CHANNEL_ID, SECRETARY_CHANNEL_ID, load_accounts,
)
from .threads_api import ThreadsError

log = logging.getLogger("bot")
_h, _m = map(int, DRAFT_TIME.split(":"))
DRAFT_AT = time(hour=_h, minute=_m, tzinfo=JST)
REFRESH_AT = time(hour=4, minute=0, tzinfo=JST)
MAX_ATTEMPTS = 3
RETRY_AFTER = timedelta(minutes=5)

KIND_LABEL = {"value": "価値提供", "cta": "誘導", "manual": "手動"}
STATUS_LABEL = {
    "pending": "承認待ち", "approved": "予約済み", "posting": "投稿中", "posted": "投稿済み",
    "failed": "投稿失敗", "rejected": "ボツ", "cancelled": "取消",
}
STATUS_COLOR = {
    "pending": 0xF2A33A, "approved": 0x3AA0F2, "posting": 0x3AA0F2, "posted": 0x41C9B4,
    "failed": 0xE0474C, "rejected": 0x777777, "cancelled": 0x777777,
}
WEEKDAY = "月火水木金土日"


# ---------------------------------------------------------------- 表示
def fmt_time(iso):
    d = datetime.fromisoformat(iso)
    return f"{d.month}/{d.day}({WEEKDAY[d.weekday()]}) {d:%H:%M}"


def parse_when(text, base):
    """「21:00」「9/30 21:00」「明日 21:00」「2026-09-30 21:00」を JST の datetime にする。
    時刻だけなら base の日付を使う。"""
    s = text.strip().replace("：", ":").replace("　", " ")
    day = base
    if s.startswith("今日"):
        day, s = datetime.now(JST).date(), s[2:].strip()
    elif s.startswith("明日"):
        day, s = datetime.now(JST).date() + timedelta(days=1), s[2:].strip()
    m = re.fullmatch(r"(?:(?:(\d{4})[-/])?(\d{1,2})[-/](\d{1,2})\s+)?(\d{1,2}):(\d{2})", s)
    if not m:
        raise ValueError("「21:00」「9/30 21:00」「明日 21:00」のように入力してください")
    y, mo, d, hh, mm = m.groups()
    if mo:
        today = datetime.now(JST).date()
        day = date(int(y) if y else today.year, int(mo), int(d))
        if not y and day < today:  # 12月に「1/5」と書いたら翌年
            day = day.replace(year=today.year + 1)
    return datetime(day.year, day.month, day.day, int(hh), int(mm), tzinfo=JST)


def format_issues(issues):
    if not issues:
        return "✅ 問題なし"
    icon = {"error": "❌", "warn": "⚠️"}
    return "\n".join(f"{icon[lv]} {msg}" for lv, msg in issues)[:1000]


def account_name(account_id):
    a = load_accounts().get(account_id)
    return a["name"] if a else account_id


def draft_embed(d):
    posts = d["posts"]
    if len(posts) == 1:
        body = posts[0]
    else:
        body = "\n\n".join(f"**── {i}/{len(posts)} ──**\n{p}" for i, p in enumerate(posts, 1))
    e = discord.Embed(
        title=f"{account_name(d['account_id'])}｜{fmt_time(d['scheduled_at'])}｜{KIND_LABEL.get(d['kind'], d['kind'])}",
        description=body[:4000], color=STATUS_COLOR.get(d["status"], 0x777777),
    )
    if d.get("memo"):
        e.add_field(name="狙い", value=d["memo"][:200], inline=False)
    e.add_field(name="自動チェック", value=format_issues(d["issues"]), inline=False)
    if d.get("permalink"):
        e.add_field(name="投稿", value=d["permalink"], inline=False)
    if d["status"] == "failed" and d.get("error"):
        e.add_field(name="エラー", value=d["error"][:500], inline=False)
    chars = " / ".join(f"{len(p)}字" for p in posts)
    e.set_footer(text=f"下書き #{d['id']} ・ {chars} ・ {STATUS_LABEL.get(d['status'], d['status'])}")
    return e


def view_for(bot, status):
    return {"pending": PendingView, "approved": ScheduledView, "failed": FailedView}.get(status, lambda b: None)(bot)


# ---------------------------------------------------------------- モーダル
class ReviseModal(discord.ui.Modal, title="修正依頼（AIが書き直します）"):
    instruction = discord.ui.TextInput(
        label="どう直してほしいか", style=discord.TextStyle.paragraph, max_length=1000,
        placeholder="例：1行目をもっと強く。最後は質問で終える。全体を短く。",
    )

    def __init__(self, bot, draft_id):
        super().__init__()
        self.bot, self.draft_id = bot, draft_id

    async def on_submit(self, interaction):
        await interaction.response.send_message(f"✏️ 下書き #{self.draft_id} を書き直しています…", ephemeral=True)
        msg = await self.bot.revise_draft(self.draft_id, str(self.instruction))
        await interaction.followup.send(msg, ephemeral=True)


class EditModal(discord.ui.Modal):
    """本文を直接書き換える。空欄にした投稿はツリーから外れ、空いている欄に書けば投稿を足せる。"""

    def __init__(self, bot, draft):
        super().__init__(title=f"下書き #{draft['id']} を編集")
        self.bot, self.draft_id = bot, draft["id"]
        posts = draft["posts"] + ([""] if len(draft["posts"]) < MAX_CHAIN else [])
        self.fields = []
        for i, text in enumerate(posts[:MAX_CHAIN], 1):
            f = discord.ui.TextInput(
                label=f"{i}投稿目" + ("（1つ前へのリプライとしてつながります）" if i > 1 else ""),
                style=discord.TextStyle.paragraph, default=text or None, required=(i == 1), max_length=4000,
            )
            self.add_item(f)
            self.fields.append(f)

    async def on_submit(self, interaction):
        posts = [str(f).strip() for f in self.fields if str(f).strip()]
        msg = await self.bot.edit_draft(self.draft_id, posts)
        await interaction.response.send_message(msg, ephemeral=True)


class RescheduleModal(discord.ui.Modal, title="投稿時刻を変更"):
    when = discord.ui.TextInput(label="新しい投稿時刻", placeholder="21:00 / 9/30 21:00 / 明日 7:30", max_length=30)

    def __init__(self, bot, draft_id):
        super().__init__()
        self.bot, self.draft_id = bot, draft_id

    async def on_submit(self, interaction):
        msg = await self.bot.reschedule(self.draft_id, str(self.when))
        await interaction.response.send_message(msg, ephemeral=True)


class TokenModal(discord.ui.Modal):
    """/接続 でトークンを登録する。モーダルの入力はチャンネルに残らない。"""
    token = discord.ui.TextInput(label="アクセストークン", style=discord.TextStyle.paragraph, max_length=1000,
                                 placeholder="Metaの「ユーザートークン生成ツール」でコピーしたもの")

    def __init__(self, account_id):
        super().__init__(title=f"{account_name(account_id)} を接続"[:45])
        self.account_id = account_id

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            t = await asyncio.to_thread(accounts.register_token, self.account_id, str(self.token).strip())
        except ThreadsError as e:
            return await interaction.followup.send(f"⚠️ 登録できませんでした: {e}", ephemeral=True)
        await interaction.followup.send(
            f"✅ {account_name(self.account_id)} を @{t['username']} として接続しました"
            f"（トークン期限 {(t['expires_at'] or '不明')[:10]}、以後は自動で延長します）", ephemeral=True,
        )


class ManualPostModal(discord.ui.Modal):
    """/予約 で手書きの投稿を登録する。"""

    def __init__(self, bot, account_id, when):
        super().__init__(title=f"{account_name(account_id)}｜{fmt_time(when.isoformat())} に予約"[:45])
        self.bot, self.account_id, self.when = bot, account_id, when
        self.fields = []
        for i in range(1, 4):
            f = discord.ui.TextInput(
                label=f"{i}投稿目" + ("（任意・ツリーの続き）" if i > 1 else ""),
                style=discord.TextStyle.paragraph, required=(i == 1), max_length=4000,
            )
            self.add_item(f)
            self.fields.append(f)

    async def on_submit(self, interaction):
        posts = [str(f).strip() for f in self.fields if str(f).strip()]
        await interaction.response.send_message("登録しています…", ephemeral=True)
        msg = await self.bot.add_manual(self.account_id, posts, self.when)
        await interaction.followup.send(msg, ephemeral=True)


# ---------------------------------------------------------------- ボタン
class _DraftButtons(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    async def _draft(self, interaction, *statuses):
        d = storage.draft_by_message(interaction.message.id)
        if not d or d["status"] not in statuses:
            await interaction.response.send_message("この下書きは既に処理されています", ephemeral=True)
            return None
        return d


class PendingView(_DraftButtons):
    @discord.ui.button(label="承認", style=discord.ButtonStyle.success, custom_id="d:approve")
    async def approve(self, interaction, button):
        if d := await self._draft(interaction, "pending"):
            msg = await self.bot.approve(d)
            await interaction.response.send_message(msg, ephemeral=True)

    @discord.ui.button(label="修正依頼", style=discord.ButtonStyle.primary, custom_id="d:revise")
    async def revise(self, interaction, button):
        if d := await self._draft(interaction, "pending"):
            await interaction.response.send_modal(ReviseModal(self.bot, d["id"]))

    @discord.ui.button(label="直接編集", style=discord.ButtonStyle.secondary, custom_id="d:edit")
    async def edit(self, interaction, button):
        if d := await self._draft(interaction, "pending"):
            await interaction.response.send_modal(EditModal(self.bot, d))

    @discord.ui.button(label="時刻変更", style=discord.ButtonStyle.secondary, custom_id="d:time")
    async def reschedule(self, interaction, button):
        if d := await self._draft(interaction, "pending"):
            await interaction.response.send_modal(RescheduleModal(self.bot, d["id"]))

    @discord.ui.button(label="ボツ", style=discord.ButtonStyle.danger, custom_id="d:reject")
    async def reject(self, interaction, button):
        if d := await self._draft(interaction, "pending"):
            storage.claim_draft(d["id"], "pending", "rejected")
            await self.bot.refresh_message(d["id"])
            await interaction.response.send_message(f"🗑️ 下書き #{d['id']} をボツにしました", ephemeral=True)


class ScheduledView(_DraftButtons):
    @discord.ui.button(label="承認を取り消す（編集に戻す）", style=discord.ButtonStyle.secondary, custom_id="s:unapprove")
    async def unapprove(self, interaction, button):
        if d := await self._draft(interaction, "approved"):
            if not storage.claim_draft(d["id"], "approved", "pending"):
                return await interaction.response.send_message("投稿処理が始まっているため取り消せません", ephemeral=True)
            await self.bot.refresh_message(d["id"])
            await interaction.response.send_message(f"↩️ #{d['id']} を承認待ちに戻しました", ephemeral=True)

    @discord.ui.button(label="時刻変更", style=discord.ButtonStyle.secondary, custom_id="s:time")
    async def reschedule(self, interaction, button):
        if d := await self._draft(interaction, "approved"):
            await interaction.response.send_modal(RescheduleModal(self.bot, d["id"]))

    @discord.ui.button(label="予約取消", style=discord.ButtonStyle.danger, custom_id="s:cancel")
    async def cancel(self, interaction, button):
        if d := await self._draft(interaction, "approved"):
            if not storage.claim_draft(d["id"], "approved", "cancelled"):
                return await interaction.response.send_message("投稿処理が始まっているため取り消せません", ephemeral=True)
            await self.bot.refresh_message(d["id"])
            await interaction.response.send_message(f"🛑 #{d['id']} の予約を取り消しました", ephemeral=True)


class FailedView(_DraftButtons):
    @discord.ui.button(label="再試行", style=discord.ButtonStyle.primary, custom_id="f:retry")
    async def retry(self, interaction, button):
        if d := await self._draft(interaction, "failed"):
            storage.update_draft(d["id"], status="approved", attempts=0, error=None,
                                 scheduled_at=storage.now())
            await self.bot.refresh_message(d["id"])
            await interaction.response.send_message(f"🔁 #{d['id']} を1分以内に再投稿します", ephemeral=True)

    @discord.ui.button(label="ボツ", style=discord.ButtonStyle.danger, custom_id="f:reject")
    async def reject(self, interaction, button):
        if d := await self._draft(interaction, "failed"):
            storage.claim_draft(d["id"], "failed", "rejected")
            await self.bot.refresh_message(d["id"])
            await interaction.response.send_message(f"🗑️ #{d['id']} をボツにしました", ephemeral=True)


# ---------------------------------------------------------------- bot
class ThreadsBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = bool(SECRETARY_CHANNEL_ID)  # #秘書 を使うときだけ必要
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.generating = asyncio.Lock()

    async def setup_hook(self):
        storage.connect().close()
        # 投稿中に止まったもの。途中まで出ている可能性があるので自動では再開せず、[再試行] に任せる
        for d in storage.drafts_by_status(["posting"]):
            storage.update_draft(d["id"], status="failed",
                                 error="投稿中にBotが止まりました。Threadsを確認してから [再試行] してください")
        for v in (PendingView, ScheduledView, FailedView):
            self.add_view(v(self))
        register_commands(self)
        guild = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        self.poster.start()
        self.nightly.start()
        self.token_refresh.start()

    async def on_ready(self):
        log.info("ログインしました: %s", self.user)

    # ---- 秘書との会話 ----
    async def on_message(self, message):
        if message.author.bot or not SECRETARY_CHANNEL_ID or message.channel.id != SECRETARY_CHANNEL_ID:
            return
        if not message.content.strip():
            return
        thinking = await message.channel.send("🤔 考えています…")

        async def progress(tool_name):
            await thinking.edit(content=f"🔧 {tool_name} を実行中…")

        try:
            async with message.channel.typing():
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

    def channel_for(self, account_id):
        a = load_accounts().get(account_id) or {}
        cid = a.get("channel_id") or DRAFT_CHANNEL_ID
        ch = self.get_channel(int(cid)) if cid else None
        if not ch:
            raise RuntimeError(f"下書きチャンネル（{cid}）が見つかりません。DRAFT_CHANNEL_ID / channel_id を確認してください")
        return ch

    def report_channel(self):
        return self.get_channel(REPORT_CHANNEL_ID) if REPORT_CHANNEL_ID else None

    async def report(self, text):
        ch = self.report_channel()
        if ch:
            await ch.send(text[:1900])

    async def refresh_message(self, draft_id):
        """下書きのDiscordメッセージを、今の内容と状態に合わせて描き直す。"""
        d = storage.get_draft(draft_id)
        if not d or not d["message_id"]:
            return
        try:
            ch = self.get_channel(d["channel_id"]) or self.channel_for(d["account_id"])
            msg = await ch.fetch_message(d["message_id"])
            await msg.edit(embed=draft_embed(d), view=view_for(self, d["status"]))
        except (discord.HTTPException, RuntimeError):
            log.warning("draft #%s のメッセージを更新できませんでした", draft_id)

    async def post_draft_message(self, draft_id, content=None):
        d = storage.get_draft(draft_id)
        ch = self.channel_for(d["account_id"])
        msg = await ch.send(content=content, embed=draft_embed(d), view=view_for(self, d["status"]))
        storage.update_draft(draft_id, message_id=msg.id, channel_id=ch.id)

    # ---- 下書きの生成 ----
    async def generate_for(self, account_id, day, topic=None, only_future=False):
        """1アカウント1日分の下書きを作って出す。作った件数を返す。失敗は例外。"""
        a = load_accounts()[account_id]
        slots = a["slots"]
        if only_future:
            limit = datetime.now(JST) + timedelta(minutes=15)
            slots = [s for s in slots if parse_when(s["time"], day) > limit]
        if not slots:
            return 0
        self.channel_for(account_id)  # 出し先が無いまま下書きだけDBに残らないよう、生成前に確かめる
        recent_all = storage.recent_texts(limit=60)
        own = [r for r in recent_all if r[0] == account_id][:20]
        others = [r for r in recent_all if r[0] != account_id][:20]
        items = await generator.generate_day(a, slots, own, others, topic)
        made = 0
        for item in items:
            when = parse_when(item["time"], day) + timedelta(minutes=random.randint(0, POST_JITTER_MIN))
            issues = checker.check(a, item["posts"], storage.recent_texts(limit=60))
            did = storage.create_draft(account_id, item["type"], item["posts"], when.isoformat(), issues, item["memo"])
            await self.post_draft_message(did)
            made += 1
        return made

    async def generate_all(self, day, only_future=False, account_ids=None, topic=None, skip_existing=True):
        async with self.generating:
            accs = load_accounts()
            targets = account_ids or [aid for aid, a in accs.items() if a["enabled"]]
            head = await self.get_channel(DRAFT_CHANNEL_ID).send(
                f"🧠 {day.month}/{day.day} 分の下書きを作っています（{len(targets)}アカウント）…"
            )
            done, failed = [], []
            for aid in targets:
                if skip_existing and storage.has_drafts_for(aid, day.isoformat()):
                    continue
                try:
                    n = await self.generate_for(aid, day, topic, only_future)
                    done.append(f"{accs[aid]['name']} {n}件")
                except Exception as e:  # noqa: BLE001 - 1アカウントの失敗で残りを止めない
                    log.exception("generate failed: %s", aid)
                    failed.append(f"{accs[aid]['name']}: {e}")
            text = f"📝 {day.month}/{day.day} 分の下書きができました: " + (" / ".join(done) or "なし")
            if failed:
                text += "\n⚠️ 失敗:\n" + "\n".join(failed)
            await head.edit(content=text[:1900])

    # ---- 下書きの操作 ----
    async def approve(self, d):
        a = load_accounts().get(d["account_id"])
        if not a:
            return f"アカウント {d['account_id']} が accounts.json にありません"
        if not storage.get_token(d["account_id"]):
            return f"❌ {a['name']} はまだThreadsに接続されていません（python -m src.accounts で接続してください）"
        if checker.has_error(d["issues"]):
            return "❌ 自動チェックでエラーがあるため承認できません。[修正依頼] か [直接編集] で直してください"
        if not storage.claim_draft(d["id"], "pending", "approved"):
            return "この下書きは既に処理されています"
        late = datetime.fromisoformat(d["scheduled_at"]) < datetime.now(JST)
        if late:
            storage.update_draft(d["id"], scheduled_at=storage.now())
        await self.refresh_message(d["id"])
        if late:
            return f"✅ 承認しました。予定時刻を過ぎているので、1分以内に投稿します（#{d['id']}）"
        return f"✅ 承認しました。{fmt_time(d['scheduled_at'])} に投稿します（#{d['id']}）"

    async def revise_draft(self, draft_id, instruction):
        d = storage.get_draft(draft_id)
        a = load_accounts().get(d["account_id"])
        try:
            posts = await generator.revise(a, d["posts"], d["kind"], instruction)
        except generator.GenerationError as e:
            return f"⚠️ 修正に失敗しました: {e}"
        if storage.get_draft(draft_id)["status"] != "pending":
            return "修正中に下書きの状態が変わったため、反映しませんでした"
        issues = checker.check(a, posts, storage.recent_texts(limit=60, exclude_id=draft_id))
        storage.update_draft(draft_id, posts=posts, issues=issues)
        await self.refresh_message(draft_id)
        return f"✏️ #{draft_id} を書き直しました。内容を確認して [承認] してください"

    async def edit_draft(self, draft_id, posts):
        d = storage.get_draft(draft_id)
        if d["status"] != "pending":
            return "この下書きは既に処理されています"
        a = load_accounts().get(d["account_id"])
        issues = checker.check(a, posts, storage.recent_texts(limit=60, exclude_id=draft_id))
        storage.update_draft(draft_id, posts=posts, issues=issues)
        await self.refresh_message(draft_id)
        return f"✏️ #{draft_id} を更新しました" + ("（チェックでエラーがあります）" if checker.has_error(issues) else "")

    async def reschedule(self, draft_id, text):
        d = storage.get_draft(draft_id)
        if d["status"] not in ("pending", "approved"):
            return "この下書きは既に処理されています"
        try:
            when = parse_when(text, datetime.fromisoformat(d["scheduled_at"]).date())
        except ValueError as e:
            return f"⚠️ {e}"
        if when < datetime.now(JST):
            return f"⚠️ {fmt_time(when.isoformat())} は過ぎています"
        storage.update_draft(draft_id, scheduled_at=when.isoformat())
        await self.refresh_message(draft_id)
        return f"🕒 #{draft_id} を {fmt_time(when.isoformat())} に変更しました"

    async def add_manual(self, account_id, posts, when):
        a = load_accounts()[account_id]
        issues = checker.check(a, posts, storage.recent_texts(limit=60))
        ok = storage.get_token(account_id) and not checker.has_error(issues)
        did = storage.create_draft(account_id, "manual", posts, when.isoformat(), issues,
                                   status="approved" if ok else "pending")
        await self.post_draft_message(did)
        if ok:
            return f"✅ {fmt_time(when.isoformat())} に予約しました（#{did}）"
        return f"⚠️ チェックでエラーがあるか未接続のため、承認待ちとして登録しました（#{did}）"

    # ---- 投稿 ----
    async def publish(self, d):
        if not storage.claim_draft(d["id"], "approved", "posting"):
            return
        token = storage.get_token(d["account_id"])
        ids = list(d["published_ids"])
        try:
            if not token:
                raise ThreadsError("Threadsに接続されていません")
            for text in d["posts"][len(ids):]:
                mid = await asyncio.to_thread(
                    threads_api.publish_text, token["user_id"], token["token"], text, ids[-1] if ids else None
                )
                ids.append(mid)
                storage.update_draft(d["id"], published_ids=ids)
            link = await asyncio.to_thread(threads_api.permalink, ids[0], token["token"])
        except ThreadsError as e:
            attempts = d["attempts"] + 1
            if attempts < MAX_ATTEMPTS:
                storage.update_draft(d["id"], status="approved", attempts=attempts, error=str(e),
                                     scheduled_at=(datetime.now(JST) + RETRY_AFTER).isoformat(timespec="seconds"))
                log.warning("draft #%s 投稿失敗（%d回目、再試行します）: %s", d["id"], attempts, e)
            else:
                storage.update_draft(d["id"], status="failed", attempts=attempts, error=str(e))
                await self.report(f"⚠️ {account_name(d['account_id'])} の投稿 #{d['id']} に失敗しました: {e}")
            await self.refresh_message(d["id"])
            return
        storage.update_draft(d["id"], status="posted", permalink=link, posted_at=storage.now(), error=None)
        await self.refresh_message(d["id"])
        log.info("posted #%s %s", d["id"], link)

    @tasks.loop(minutes=1)
    async def poster(self):
        for d in storage.due_drafts(storage.now()):
            try:
                await self.publish(d)
            except Exception:  # noqa: BLE001 - 1件の想定外エラーで予約全体を止めない
                log.exception("publish #%s failed", d["id"])
                storage.update_draft(d["id"], status="failed", error="想定外のエラー（ログを確認してください）")
                await self.refresh_message(d["id"])

    @tasks.loop(time=DRAFT_AT)
    async def nightly(self):
        if storage.get_setting("paused") == "1":
            return
        try:
            await self.generate_all(datetime.now(JST).date() + timedelta(days=1))
        except Exception as e:  # noqa: BLE001 - 1日の失敗で毎日の実行を止めない
            log.exception("nightly failed")
            await self.report(f"⚠️ 下書きの自動生成でエラーが起きました: {e}")

    @tasks.loop(time=REFRESH_AT)
    async def token_refresh(self):
        results = await asyncio.to_thread(accounts.refresh_due)
        bad = [f"{aid}: {msg}" for aid, msg in results if "失敗" in msg]
        if bad:
            await self.report("⚠️ Threadsトークンの延長に失敗しました。再接続が必要かもしれません\n" + "\n".join(bad))

    @poster.before_loop
    @nightly.before_loop
    @token_refresh.before_loop
    async def _wait_ready(self):
        await self.wait_until_ready()


# ---------------------------------------------------------------- コマンド
def register_commands(bot):
    accs = load_accounts()
    choices = [app_commands.Choice(name=a["name"][:100], value=aid) for aid, a in accs.items()][:25]
    day_choices = [app_commands.Choice(name="明日", value="tomorrow"), app_commands.Choice(name="今日", value="today")]

    @bot.tree.command(name="アカウント", description="アカウントの接続状況と予約数を見る")
    async def accounts_cmd(interaction):
        lines = []
        for aid, name, state in accounts.status_rows():
            pend = len(storage.drafts_by_status(["pending"], aid))
            appr = len(storage.drafts_by_status(["approved"], aid))
            lines.append(f"**{name}**（{aid}）{state}\n　承認待ち {pend}件 / 予約済み {appr}件")
        await interaction.response.send_message("\n".join(lines)[:1900] or "config/accounts.json にアカウントがありません")

    @bot.tree.command(name="接続", description="Threadsアカウントのトークンを登録する（再接続にも使う）")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.choices(アカウント=choices)
    async def connect_cmd(interaction, アカウント: app_commands.Choice[str]):
        await interaction.response.send_modal(TokenModal(アカウント.value))

    @bot.tree.command(name="生成", description="下書きを今すぐ作る")
    @app_commands.describe(アカウント="省略すると全アカウント", 日付="省略すると明日", ネタ="書いてほしいテーマ（省略可）")
    @app_commands.choices(アカウント=choices, 日付=day_choices)
    async def generate_cmd(interaction, アカウント: app_commands.Choice[str] = None,
                           日付: app_commands.Choice[str] = None, ネタ: str = None):
        today = datetime.now(JST).date()
        is_today = 日付 and 日付.value == "today"
        day = today if is_today else today + timedelta(days=1)
        if bot.generating.locked():
            return await interaction.response.send_message("いま別の下書きを生成中です。終わってから試してください", ephemeral=True)
        await interaction.response.send_message("了解です。下書きを作ります（1アカウント1〜2分）", ephemeral=True)
        await bot.generate_all(day, only_future=is_today, account_ids=[アカウント.value] if アカウント else None,
                               topic=ネタ, skip_existing=False)

    @bot.tree.command(name="予約", description="自分で書いた投稿を予約する")
    @app_commands.describe(日時="21:00 / 9/30 21:00 / 明日 7:30")
    @app_commands.choices(アカウント=choices)
    async def manual_cmd(interaction, アカウント: app_commands.Choice[str], 日時: str):
        try:
            when = parse_when(日時, datetime.now(JST).date())
        except ValueError as e:
            return await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
        if when < datetime.now(JST):
            return await interaction.response.send_message("⚠️ その時刻は過ぎています", ephemeral=True)
        await interaction.response.send_modal(ManualPostModal(bot, アカウント.value, when))

    @bot.tree.command(name="予約一覧", description="承認待ち・予約済みの投稿を見る")
    @app_commands.choices(アカウント=choices)
    async def queue_cmd(interaction, アカウント: app_commands.Choice[str] = None):
        rows = storage.drafts_by_status(["pending", "approved", "failed"], アカウント.value if アカウント else None)
        if not rows:
            return await interaction.response.send_message("承認待ち・予約済みの投稿はありません")
        lines = [
            f"`#{d['id']}` {fmt_time(d['scheduled_at'])} {account_name(d['account_id'])}"
            f"【{STATUS_LABEL[d['status']]}】{d['posts'][0][:30].replace(chr(10), ' ')}"
            for d in rows
        ]
        await interaction.response.send_message("\n".join(lines)[:1900])

    @bot.tree.command(name="秘書リセット", description="秘書との会話の記憶を消す")
    async def reset_chat_cmd(interaction):
        n = storage.clear_chat(interaction.channel_id)
        await interaction.response.send_message(f"🧹 会話の記憶を消しました（{n}件）", ephemeral=True)

    @bot.tree.command(name="停止", description="毎日の下書き自動生成を止める（予約済みの投稿は止まりません）")
    async def pause_cmd(interaction):
        storage.set_setting("paused", "1")
        await interaction.response.send_message("⏸️ 下書きの自動生成を停止しました（/再開 で再開）")

    @bot.tree.command(name="再開", description="毎日の下書き自動生成を再開する")
    async def resume_cmd(interaction):
        storage.set_setting("paused", "0")
        await interaction.response.send_message(f"▶️ 再開しました。毎日 {DRAFT_TIME} に翌日分を作ります")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ThreadsBot().run(DISCORD_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
