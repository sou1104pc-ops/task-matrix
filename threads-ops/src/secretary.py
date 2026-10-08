"""Discord の #秘書 チャンネルで会話する秘書エージェント。

下書き生成と同じ Claude Code（claude -p）を頭脳に使い、下で定義したツールだけを実行する。
何でもできるシェル権限は渡さず、ここに書いた操作しかできない。

流れ: ユーザー発言 → claude -p が {reply, tools} を返す → ツールを実行 → 結果を渡して再度呼ぶ
（tools が空になるまで最大 MAX_STEPS 回）。
"""
import asyncio
import json
import re
from datetime import datetime, timedelta

from . import checker, generator, storage, threads_api
from .config import DRAFT_TIME, JST, MAX_CHAIN, POST_JITTER_MIN, load_accounts, save_accounts

MAX_STEPS = 5          # ツール実行のループ上限
HISTORY_TURNS = 16     # 会話履歴として渡す件数
CLAUDE_TIMEOUT = 240

# 秘書が書き換えてよいアカウント設定の項目
EDITABLE = {
    "name": "Discordでの表示名", "enabled": "true/false。自動生成の対象にするか",
    "theme": "発信テーマ", "target": "ターゲット", "tone": "口調",
    "product": "最終的に届ける商品・サービス",
    "cta_method": "誘導方法。pinned=固定投稿へ誘導（本文にURLを入れない）/ url=最後の投稿に cta_url を入れる",
    "cta_url": "誘導先URL（UTAGEの登録経路。cta_method=url のときだけ使う）",
    "cta_text": "誘導の方向性", "topics": "ネタ候補の配列", "rules": "守ること・避けること",
    "format": "投稿の型（フックの作り方・文字数・改行のリズムなど。生成時に最優先で守る）",
    "closing": "最後の投稿の終わりに必ず入れる一文の配列（例 [\"固定に全体像まとめてます👇\"]）。自動チェックで抜けを止める",
    "last_line": "最後の一文を固定したいときの文（例 コメントくれたら診断します）",
    "end_with_question": "true なら最後の一文を質問にする（自動チェックあり）",
    "facts": "使ってよい実績・数字の配列（運営者が確かめた事実）。数字はここにあるものだけ使う",
    "genres": "ネタの種類を順番に回すときの配列（例 [\"失敗談\", \"売れた型\", \"Claude Code時短術\"]）",
    "slots": '投稿枠の配列。例 [{"time": "07:30", "type": "value"}, {"time": "21:00", "type": "cta"}]',
}


class SecretaryError(Exception):
    pass


# ---------------------------------------------------------------- ツール定義
# kind: read=調べるだけ / write=手元の設定・下書きを変える / action=AI生成やThreadsへの予約につながる
TOOLS = [
    {"name": "list_accounts", "kind": "read",
     "desc": "全アカウントの運用設定（テーマ・口調・誘導先・投稿枠など）と接続状況・予約数を見る。", "args": {}},
    {"name": "get_status", "kind": "read",
     "desc": "Botの状態（自動生成の停止有無・生成時刻）を見る。", "args": {}},
    {"name": "list_queue", "kind": "read",
     "desc": "承認待ち・予約済み・失敗の下書きを一覧する。draft_id はここで分かる。",
     "args": {"account_id": "絞り込むアカウントid（省略可）", "status": "pending / approved / failed（省略時は全部）"}},
    {"name": "get_draft", "kind": "read", "desc": "下書き1件の全文・チェック結果・状態を見る。",
     "args": {"draft_id": "下書き番号"}},
    {"name": "list_posted", "kind": "read", "desc": "投稿済みの投稿を見る（URL付き）。",
     "args": {"days": "何日ぶんか（省略時7）", "account_id": "省略可"}},
    {"name": "get_threads_profile", "kind": "read",
     "desc": "Threadsから、接続済みアカウントの今のプロフィール（名前・プロフィール文）とフォロワー数を読む。",
     "args": {"account_id": "対象のid"}},
    {"name": "get_threads_posts", "kind": "read",
     "desc": "Threadsから、接続済みアカウントの過去の投稿（このBotを使う前のものも含む）を新しい順に読む。"
             "閲覧・いいね・返信・リポスト・引用・シェアの数字付き。リポストは除く。運用方針づくりや、伸びた投稿の分析に使う。",
     "args": {"account_id": "対象のid", "limit": "何件か（省略時20、最大50）"}},
    {"name": "update_account", "kind": "write",
     "desc": "アカウントの運用方針を書き換える。指定した項目だけ上書きする。表示名の変更は /コマンドの選択肢にはBot再起動後に反映。",
     "args": {"account_id": "対象のid", "fields": "書き換える項目の辞書。使える項目: "
              + " / ".join(f"{k}（{v}）" for k, v in EDITABLE.items())}},
    {"name": "edit_draft", "kind": "write", "desc": "承認待ちの下書きの本文を、指定した文面に置き換える。",
     "args": {"draft_id": "下書き番号", "posts": f"本文の配列（2つ目以降はツリーの続き、最大{MAX_CHAIN}）"}},
    {"name": "reschedule", "kind": "write", "desc": "承認待ち・予約済みの下書きの投稿時刻を変える。",
     "args": {"draft_id": "下書き番号", "when": "「21:00」「9/30 21:00」「明日 7:30」の形"}},
    {"name": "reject_draft", "kind": "write", "desc": "承認待ちの下書きをボツにする。", "args": {"draft_id": "下書き番号"}},
    {"name": "set_paused", "kind": "write", "desc": "毎日の下書き自動生成を止める／再開する。",
     "args": {"paused": "true で停止、false で再開"}},
    {"name": "revise_draft", "kind": "action", "desc": "承認待ちの下書きを、指示に沿ってAIに書き直させる。",
     "args": {"draft_id": "下書き番号", "instruction": "直し方の指示"}},
    {"name": "generate_drafts", "kind": "action",
     "desc": "下書きを作って #下書き に出す（承認されるまで投稿されない）。1アカウント1〜2分かかる。",
     "args": {"account_id": "省略すると有効な全アカウント", "day": "today か tomorrow（省略時 tomorrow）",
              "topic": "書いてほしいネタ（省略可）"}},
    {"name": "generate_from_x", "kind": "action",
     "desc": "運営者が貼ったXの投稿を参考に、型だけ借りたThreadsの下書きを1つ作って #下書き に出す"
             "（承認されるまで投稿されない）。文面は写さず、ネタはアカウントのテーマに置き換える。1〜2分かかる。",
     "args": {"account_id": "対象のid", "x_post": "運営者が貼ったXの投稿の本文（省略・要約せず、そのまま渡す）",
              "kind": "value（価値提供）か cta（誘導）。省略時 value",
              "when": "「21:00」「9/30 21:00」「明日 7:30」の形（省略するとその種類の次の投稿枠）",
              "note": "運営者の補足（どこを真似したいか等。省略可）"}},
    {"name": "approve_draft", "kind": "action",
     "desc": "下書きを承認して予約を確定する。予約時刻にThreadsへ投稿される。", "args": {"draft_id": "下書き番号"}},
    {"name": "cancel_scheduled", "kind": "action", "desc": "予約済み（承認済み）の投稿を取り消す。",
     "args": {"draft_id": "下書き番号"}},
    {"name": "schedule_post", "kind": "action",
     "desc": "運営者が指定した文面をそのまま予約する（チェックに通れば承認済みになり、時刻にThreadsへ投稿される）。",
     "args": {"account_id": "対象のid", "when": "「21:00」「9/30 21:00」「明日 7:30」の形",
              "posts": "本文の配列（2つ目以降はツリーの続き）"}},
]
TOOLS_BY_NAME = {t["name"]: t for t in TOOLS}
ACTION_TOOLS = [t["name"] for t in TOOLS if t["kind"] == "action"]


def _tools_text():
    lines = []
    for t in TOOLS:
        mark = {"read": "調べる", "write": "手元の設定・下書きを変える", "action": "生成・予約・投稿につながる"}[t["kind"]]
        args = "、".join(f"{k}（{v}）" for k, v in t["args"].items()) or "引数なし"
        lines.append(f"- {t['name']} [{mark}]: {t['desc']}\n  引数: {args}")
    return "\n".join(lines)


SYSTEM = """あなたは「Threads秘書」です。複数のThreadsアカウントを運用し、UTAGEと連携した
公式LINE・メルマガに登録してもらって商品を販売する事業を手伝う相棒として、Discordで運営者と日本語で会話します。

## 人柄
- 短く、具体的に。前置きや定型の挨拶はいりません
- 数字や状況は必ずツールで調べてから答えます。推測で言ってはいけません
- 分からないこと・できないことは正直に言います（例: 接続していないアカウントのThreadsは読めません。他人のアカウントや投稿も読めません）

## 使えるツール
{tools}

## 大事なルール
- 下書きはすべて運営者が承認してから投稿する運用です。「生成・予約・投稿につながる」ツール（{actions}）は、
  運営者がはっきりそれを求めたときだけ使います。雑談や質問、「どう思う？」への返答で勝手に実行してはいけません
- 承認（approve_draft）は、運営者が対象の下書きをはっきり指定して承認を求めたときだけ行います。「全部承認して」と言われたら、
  対象の一覧を示してから実行します
- update_account で運用方針を変えたら、何をどう変えたかを具体的に報告します
- 実行したら、何をしたかを結果に基づいて報告します。ツールがエラーを返したら正直に伝え、成功したことにしてはいけません
- アカウントは id（acc01 など）と表示名のどちらで呼ばれても分かるように、必要なら list_accounts で確かめます
- 運営者がXの投稿を貼って「これをもとに作って」「この型で」と頼んだら generate_from_x を使います。x_post には貼られた本文を
  そのまま渡してください。アカウントや種類が分からなければ先に聞きます。XのURLだけが貼られたときは、中身を読めないので本文を貼ってもらいます

## 返答の形式
必ず次のJSONだけを出力してください。前後に説明文やコードフェンスを付けないこと。

{{"reply": "運営者に見せる返答", "tools": [{{"name": "ツール名", "args": {{}}}}]}}

- 調べてから答えたいときは tools にツールを入れ、reply は空文字にしてください。結果を受け取ったあと、あらためて reply を書けます
- ツールが不要なら tools は [] にして、reply だけ書いてください
- 複数のツールを同時に呼んでも構いません"""


# ---------------------------------------------------------------- ツール実装
def _int(v, name="draft_id"):
    try:
        return int(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name} が整数ではありません: {v!r}")


def _draft(args, *statuses):
    d = storage.get_draft(_int(args.get("draft_id")))
    if not d:
        raise ValueError(f"下書き #{args.get('draft_id')} が見つかりません")
    if statuses and d["status"] not in statuses:
        raise ValueError(f"下書き #{d['id']} は「{d['status']}」なのでこの操作はできません")
    return d


def _account_id(args, required=True):
    aid = (args.get("account_id") or "").strip()
    if not aid and not required:
        return None
    accs = load_accounts()
    if aid in accs:
        return aid
    by_name = [k for k, a in accs.items() if a["name"] == aid]
    if by_name:
        return by_name[0]
    raise ValueError(f"アカウント「{aid}」がありません。使えるid: {sorted(accs)}")


def _posts(v):
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list) or not v:
        raise ValueError("posts は本文の配列で指定してください")
    return [str(p).strip() for p in v if str(p).strip()]


def _brief(d):
    from .bot import STATUS_LABEL, account_name, fmt_time
    return {"draft_id": d["id"], "アカウント": account_name(d["account_id"]), "予定": fmt_time(d["scheduled_at"]),
            "状態": STATUS_LABEL.get(d["status"], d["status"]), "種類": d["kind"],
            "冒頭": d["posts"][0][:60], "チェック": "エラーあり" if checker.has_error(d["issues"]) else "OK"}


async def _list_accounts(args, bot):
    tokens = storage.all_tokens()
    out = []
    for aid, a in load_accounts().items():
        t = tokens.get(aid)
        out.append({
            "id": aid, **{k: a.get(k) for k in EDITABLE},
            "接続": f"@{t['username']}（期限 {(t['expires_at'] or '不明')[:10]}）" if t else "未接続",
            "承認待ち": len(storage.drafts_by_status(["pending"], aid)),
            "予約済み": len(storage.drafts_by_status(["approved"], aid)),
        })
    return {"アカウント": out}


async def _get_status(args, bot):
    return {"毎日の自動生成": "停止中" if storage.get_setting("paused") == "1" else "動作中",
            "生成時刻": f"毎日 {DRAFT_TIME} に翌日分", "投稿時刻のゆらぎ": f"0〜{POST_JITTER_MIN}分",
            "生成中": bot.generating.locked()}


async def _list_queue(args, bot):
    status = (args.get("status") or "").strip()
    statuses = [status] if status in ("pending", "approved", "failed") else ["pending", "approved", "failed"]
    rows = storage.drafts_by_status(statuses, _account_id(args, required=False))
    return {"件数": len(rows), "下書き": [_brief(d) for d in rows[:40]]}


async def _get_draft(args, bot):
    d = _draft(args)
    return {**_brief(d), "本文": d["posts"], "狙い": d["memo"], "チェック結果": d["issues"],
            "投稿URL": d["permalink"], "エラー": d["error"]}


async def _list_posted(args, bot):
    days = int(args.get("days") or 7)
    since = (datetime.now(JST) - timedelta(days=days)).isoformat()
    rows = storage.posted_since(since, _account_id(args, required=False))
    return {"件数": len(rows), "投稿": [{**_brief(d), "URL": d["permalink"]} for d in rows[-40:]]}


def _validate_slots(slots):
    if not isinstance(slots, list):
        raise ValueError("slots は配列で指定してください")
    for s in slots:
        if not (isinstance(s, dict) and re.fullmatch(r"\d{1,2}:\d{2}", str(s.get("time", "")))
                and s.get("type") in ("value", "cta")):
            raise ValueError(f'slots の形が違います: {s!r}（例 {{"time": "07:30", "type": "value"}}）')
    return sorted(slots, key=lambda s: tuple(map(int, s["time"].split(":"))))


async def _update_account(args, bot):
    aid = _account_id(args)
    fields = args.get("fields")
    if not isinstance(fields, dict) or not fields:
        raise ValueError("fields に書き換える項目を辞書で指定してください")
    bad = [k for k in fields if k not in EDITABLE]
    if bad:
        raise ValueError(f"書き換えられない項目です: {bad} / 使える項目: {list(EDITABLE)}")
    if "slots" in fields:
        fields["slots"] = _validate_slots(fields["slots"])
    if "enabled" in fields and isinstance(fields["enabled"], str):
        fields["enabled"] = fields["enabled"].lower() == "true"
    for k in ("closing", "facts", "genres"):
        if k in fields and isinstance(fields[k], str):
            fields[k] = [t.strip() for t in re.split(r"[、,\n/]", fields[k]) if t.strip()]
    if "end_with_question" in fields and isinstance(fields["end_with_question"], str):
        fields["end_with_question"] = fields["end_with_question"].lower() == "true"
    if "topics" in fields and isinstance(fields["topics"], str):
        fields["topics"] = [t.strip() for t in re.split(r"[、,\n/]", fields["topics"]) if t.strip()]
    accs = load_accounts()
    before = {k: accs[aid].get(k) for k in fields}
    accs[aid].update(fields)
    save_accounts(accs)
    return {"更新した": aid, "変更前": before, "変更後": fields}


async def _edit_draft(args, bot):
    d = _draft(args, "pending")
    return {"結果": await bot.edit_draft(d["id"], _posts(args.get("posts")))}


async def _reschedule(args, bot):
    d = _draft(args, "pending", "approved")
    return {"結果": await bot.reschedule(d["id"], str(args.get("when") or ""))}


async def _reject_draft(args, bot):
    d = _draft(args, "pending")
    storage.claim_draft(d["id"], "pending", "rejected")
    await bot.refresh_message(d["id"])
    return {"結果": f"#{d['id']} をボツにしました"}


async def _set_paused(args, bot):
    paused = args.get("paused")
    if isinstance(paused, str):
        paused = paused.lower() == "true"
    storage.set_setting("paused", "1" if paused else "0")
    return {"毎日の自動生成": "停止中" if paused else "動作中"}


async def _revise_draft(args, bot):
    d = _draft(args, "pending")
    return {"結果": await bot.revise_draft(d["id"], str(args.get("instruction") or ""))}


async def _generate_drafts(args, bot):
    if bot.generating.locked():
        raise ValueError("いま別の下書きを生成中です。終わってから試してください")
    aid = _account_id(args, required=False)
    today = datetime.now(JST).date()
    is_today = args.get("day") == "today"
    day = today if is_today else today + timedelta(days=1)
    before = {d["id"] for d in storage.drafts_by_status(["pending"])}
    await bot.generate_all(day, only_future=is_today, account_ids=[aid] if aid else None,
                           topic=args.get("topic"), skip_existing=False)
    made = [d for d in storage.drafts_by_status(["pending"]) if d["id"] not in before]
    if not made:
        raise ValueError("下書きが1件も作られませんでした（#下書き のエラー表示を確認してください）")
    return {"作成した件数": len(made), "下書き": [_brief(d) for d in made],
            "備考": "#下書き に承認ボタン付きで出しました。承認されるまで投稿されません"}


async def _generate_from_x(args, bot):
    from .bot import parse_when
    aid = _account_id(args)
    when = None
    if args.get("when"):
        when = parse_when(str(args["when"]), datetime.now(JST).date())
        if when < datetime.now(JST):
            raise ValueError("その時刻は過ぎています")
    msg = await bot.generate_from_x(aid, str(args.get("x_post") or ""), args.get("kind") or "value", when,
                                    args.get("note"))
    if msg.startswith("⚠️"):
        raise ValueError(msg)
    return {"結果": msg}


async def _approve_draft(args, bot):
    d = _draft(args, "pending")
    msg = await bot.approve(d)
    if storage.get_draft(d["id"])["status"] != "approved":
        raise ValueError(msg)
    return {"結果": msg}


async def _cancel_scheduled(args, bot):
    d = _draft(args, "approved")
    if not storage.claim_draft(d["id"], "approved", "cancelled"):
        raise ValueError("投稿処理が始まっているため取り消せません")
    await bot.refresh_message(d["id"])
    return {"結果": f"#{d['id']} の予約を取り消しました"}


async def _schedule_post(args, bot):
    from .bot import parse_when
    aid = _account_id(args)
    when = parse_when(str(args.get("when") or ""), datetime.now(JST).date())
    if when < datetime.now(JST):
        raise ValueError("その時刻は過ぎています")
    return {"結果": await bot.add_manual(aid, _posts(args.get("posts")), when)}


async def _get_threads_profile(args, bot):
    aid = _account_id(args)
    t = storage.get_token(aid)
    if not t:
        raise ValueError(f"{aid} はThreadsに未接続です（/接続 で登録してください）")
    p = await asyncio.to_thread(threads_api.profile, t["token"])
    try:
        followers = await asyncio.to_thread(threads_api.followers_count, t["user_id"], t["token"])
    except threads_api.ThreadsError as e:
        followers = f"取得できませんでした: {e}"
    return {"account_id": aid, "ユーザー名": p.get("username"), "名前": p.get("name"),
            "プロフィール文": p.get("threads_biography"), "フォロワー数": followers}


async def _get_threads_posts(args, bot):
    aid = _account_id(args)
    t = storage.get_token(aid)
    if not t:
        raise ValueError(f"{aid} はThreadsに未接続です（/接続 で登録してください）")
    limit = max(1, min(int(args.get("limit") or 20), 50))
    raw = await asyncio.to_thread(threads_api.user_posts, t["user_id"], t["token"], limit * 2)
    posts = [p for p in raw if p.get("media_type") != "REPOST_FACADE"][:limit]
    out = []
    for p in posts:
        try:
            stats = await asyncio.to_thread(threads_api.media_insights, p["id"], t["token"])
        except threads_api.ThreadsError:
            stats = {}
        out.append({"日時": p.get("timestamp", "")[:16], "種類": p.get("media_type"), "本文": p.get("text") or "",
                    "URL": p.get("permalink"), **{k: stats.get(k) for k in threads_api.INSIGHT_METRICS}})
    return {"account_id": aid, "件数": len(out), "投稿": out}


IMPL = {
    "get_threads_profile": _get_threads_profile, "get_threads_posts": _get_threads_posts,
    "list_accounts": _list_accounts, "get_status": _get_status, "list_queue": _list_queue,
    "get_draft": _get_draft, "list_posted": _list_posted, "update_account": _update_account,
    "edit_draft": _edit_draft, "reschedule": _reschedule, "reject_draft": _reject_draft,
    "set_paused": _set_paused, "revise_draft": _revise_draft, "generate_drafts": _generate_drafts,
    "generate_from_x": _generate_from_x, "approve_draft": _approve_draft, "cancel_scheduled": _cancel_scheduled, "schedule_post": _schedule_post,
}


async def run_tool(name, args, bot):
    if name not in IMPL:
        return {"error": f"{name} というツールはありません。使えるのは: {sorted(IMPL)}"}
    if not isinstance(args, dict):
        return {"error": f"args が辞書ではありません: {args!r}"}
    try:
        return await IMPL[name](args, bot)
    except Exception as e:  # noqa: BLE001 - ツールの失敗で会話全体を落とさない
        return {"error": str(e)}


# ---------------------------------------------------------------- claude -p
def _parse(text):
    """返答からJSONを取り出す。コードフェンス付きで返ってきても拾う。"""
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S) or re.search(r"\{.*\}", text, re.S)
    if not m:
        raise SecretaryError("返答からJSONを取り出せませんでした")
    try:
        data = json.loads(m.group(1) if m.lastindex else m.group(0))
    except json.JSONDecodeError as e:
        raise SecretaryError(f"返答のJSONが壊れています: {e}")
    tools = data.get("tools") or []
    return data.get("reply") or "", tools if isinstance(tools, list) else []


def _build_prompt(history, user_text, transcript):
    parts = [SYSTEM.format(tools=_tools_text(), actions=" / ".join(ACTION_TOOLS)), "",
             f"（今は {datetime.now(JST):%Y-%m-%d %H:%M} です）", ""]
    if history:
        parts.append("## これまでの会話")
        parts += [f"{'運営者' if role == 'user' else '秘書'}: {content}" for role, content in history]
        parts.append("")
    parts += ["## 今回の運営者の発言", user_text, ""]
    if transcript:
        parts.append("## このターンで実行済みのツールと結果")
        for name, args, result in transcript:
            parts.append(f"- {name}({json.dumps(args, ensure_ascii=False)})")
            parts.append(f"  → {json.dumps(result, ensure_ascii=False, default=str)[:4000]}")
        parts += ["", "この結果をふまえて reply を書いてください。十分な情報がそろっているなら tools は [] にしてください。"]
    return "\n".join(parts)


async def chat(channel_id, user_text, bot, on_progress=None):
    """1往復ぶんの会話を処理して、返答テキストと実行したツール名の一覧を返す。"""
    history = storage.recent_chat(channel_id, HISTORY_TURNS)
    transcript, used, reply = [], [], ""
    for step in range(MAX_STEPS):
        try:
            text = await generator._run_claude(_build_prompt(history, user_text, transcript), CLAUDE_TIMEOUT)
        except generator.GenerationError as e:
            raise SecretaryError(str(e))
        reply, calls = _parse(text)
        if not calls:
            break
        for call in calls:
            name, args = call.get("name"), call.get("args") or {}
            if on_progress:
                await on_progress(name)
            transcript.append((name, args, await run_tool(name, args, bot)))
            used.append(name)
        if step == MAX_STEPS - 1 and not reply:
            reply = "調べましたが、まとめきれませんでした。もう一度聞いてください。"
    reply = reply.strip() or "うまく答えられませんでした。言い方を変えてもう一度お願いします。"
    storage.add_chat(channel_id, "user", user_text)
    storage.add_chat(channel_id, "assistant", reply)
    return reply, used

