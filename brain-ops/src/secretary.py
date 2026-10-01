"""Discord の #秘書 チャンネルで会話する秘書エージェント。

記事生成と同じ Claude Code（claude -p）を頭脳に使い、下で定義したツールだけを実行する。
何でもできるシェル権限は渡さず、ここに書いた操作しかできない。

流れ: ユーザー発言 → claude -p が {reply, tools} を返す → ツールを実行 → 結果を渡して再度呼ぶ
（tools が空になるまで最大 MAX_STEPS 回）。
"""
import asyncio
import json
import re
from datetime import datetime, timedelta

from . import checker, storage
from .brain_client import BrainClient, BrainError, NotLoggedIn
from .config import AFFILIATE_RATE, AUTO_PUBLISH, CLAUDE_CMD, DAILY_REPORT_TIME, DAILY_TIME, DATA, JST

MAX_STEPS = 4          # ツール実行のループ上限
HISTORY_TURNS = 16     # 会話履歴として渡す件数
CLAUDE_TIMEOUT = 240


class SecretaryError(Exception):
    pass


# ---------------------------------------------------------------- ツール定義
# kind: read=調べるだけ / write=手元のDBを変える / action=Brainなど外に出る
TOOLS = [
    {
        "name": "get_sales", "kind": "read",
        "desc": "Brainの売上（累計と、指定日の販売）を調べる。",
        "args": {"date": "YYYY-MM-DD（省略時は今日）"},
    },
    {
        "name": "list_brain_articles", "kind": "read",
        "desc": "Brain上の自分の記事（審査中・公開中・下書きなど）を見る。",
        "args": {},
    },
    {
        "name": "list_themes", "kind": "read",
        "desc": "記事テーマの一覧を見る。",
        "args": {"unused_only": "true なら未使用のみ（省略時 true）"},
    },
    {
        "name": "list_recent_posts", "kind": "read",
        "desc": "最近このBotから公開申請した記事を見る。",
        "args": {"days": "何日ぶんか（省略時7）"},
    },
    {
        "name": "get_status", "kind": "read",
        "desc": "Botの状態（自動生成の停止有無・実行時刻・承認待ちの下書き・紹介料）を見る。",
        "args": {},
    },
    {
        "name": "list_drafts", "kind": "read",
        "desc": "承認待ちの下書きを一覧する。publish_draft に渡す draft_id はここで分かる。",
        "args": {},
    },
    {
        "name": "add_theme", "kind": "write",
        "desc": "記事テーマを追加する。",
        "args": {
            "theme": "テーマ名", "persona": "想定読者（省略可）", "price": "価格（円、省略すると既定）",
            "category": "Brainのカテゴリー名（省略すると既定）", "subcategory": "サブカテゴリー名（省略可）",
        },
    },
    {
        "name": "set_draft_sales", "kind": "write",
        "desc": "承認待ちの下書きの価格・カテゴリーを変える（#下書き の表示はそのまま。投稿時に反映）。",
        "args": {"draft_id": "下書き番号", "price": "価格（省略可）", "category": "カテゴリー（省略可）",
                 "subcategory": "サブカテゴリー（省略可）"},
    },
    {
        "name": "set_paused", "kind": "write",
        "desc": "毎朝の自動生成を止める／再開する。",
        "args": {"paused": "true で停止、false で再開"},
    },
    {
        "name": "generate_draft", "kind": "action",
        "desc": "有料記事の下書きを1本作り、#下書き チャンネルに承認ボタン付きで出す。Brainにはまだ投稿されない。",
        "args": {"theme": "テーマ名（省略するとテーマリストの次のもの）", "price": "価格（省略可）",
                 "category": "カテゴリー（省略可）"},
    },
    {
        "name": "publish_draft", "kind": "action",
        "desc": "承認待ちの下書きをBrainに投稿（公開申請）する。公開前チェックでエラーがある下書きは投稿できない。",
        "args": {"draft_id": "下書き番号（list_drafts で確認）"},
    },
]

TOOLS_BY_NAME = {t["name"]: t for t in TOOLS}


def _tools_text():
    lines = []
    for t in TOOLS:
        mark = {"read": "調べる", "write": "手元のデータを変える", "action": "Brainに反映される"}[t["kind"]]
        args = "、".join(f"{k}（{v}）" for k, v in t["args"].items()) or "引数なし"
        lines.append(f"- {t['name']} [{mark}]: {t['desc']}\n  引数: {args}")
    return "\n".join(lines)


SYSTEM = """あなたは「Brain秘書」です。Brain（brain-market.com）での有料記事の販売を手伝う相棒として、
Discordで運営者と日本語で会話します。

## 人柄
- 短く、具体的に。前置きや定型の挨拶はいりません
- 数字は必ずツールで調べてから答えます。推測で数字を言ってはいけません
- 分からないこと・できないことは正直に言います

## 使えるツール
{tools}

## 大事なルール
- 「Brainに反映される」ツール（generate_draft / publish_draft）は、
  ユーザーがはっきりそれを求めたときだけ使います。雑談や質問で勝手に実行してはいけません
- 実行したら、何をしたかを結果に基づいて報告します
- ツールがエラーを返したら、そのまま正直に伝えます。成功したことにしてはいけません

## 返答の形式
必ず次のJSONだけを出力してください。前後に説明文やコードフェンスを付けないこと。

{{"reply": "ユーザーに見せる返答", "tools": [{{"name": "ツール名", "args": {{}}}}]}}

- 調べてから答えたいときは tools にツールを入れ、reply は空文字にしてください。
  ツールの結果を受け取ったあと、あらためて reply を書く機会があります
- ツールが不要なら tools は [] にして、reply だけ書いてください
- 複数のツールを同時に呼んでも構いません"""


# ---------------------------------------------------------------- ツール実装
def _price(v):
    if v in (None, ""):
        return None
    return int(str(v).replace(",", "").replace("円", ""))


async def _tool_get_sales(args, ctx):
    from .bot import fetch_sales  # bot が secretary を読み込むので循環を避ける
    day = datetime.fromisoformat(args["date"]).date() if args.get("date") else datetime.now(JST).date()
    total, count, sold = await fetch_sales(day)
    return {"日付": day.isoformat(), "その日の販売部数": len(sold), "その日の売上": sum(s["amount"] for s in sold),
            "その日売れた記事": sold[:20], "累計売上": total, "累計部数": count}


async def _tool_list_brain_articles(args, ctx):
    async with ctx.browser_lock:
        async with BrainClient(headless=True) as bc:
            raw = await bc.my_articles()
    items = raw if isinstance(raw, list) else (raw.get("articles") or raw.get("items") or [raw])
    out = []
    for a in items[:30]:
        if isinstance(a, dict):
            out.append({k: a.get(k) for k in ("id", "title", "status", "edit_status", "price", "published_at")
                        if a.get(k) is not None})
    return {"件数": len(items), "記事": out}


async def _tool_list_themes(args, ctx):
    unused = args.get("unused_only", True)
    if isinstance(unused, str):
        unused = unused.lower() != "false"
    themes = storage.list_themes(unused_only=unused, limit=50)
    return {"件数": len(themes),
            "テーマ": [{"id": t["id"], "テーマ": t["theme"], "価格": t["price"] or "既定",
                        "カテゴリー": t["category"] or "既定", "使用日": t["used_at"]} for t in themes]}


async def _tool_list_recent_posts(args, ctx):
    days = int(args.get("days") or 7)
    since = (datetime.now(JST) - timedelta(days=days)).isoformat()
    posts = storage.posts_since(since)
    return {"件数": len(posts),
            "記事": [{"タイトル": p["data"]["title"], "価格": p["data"]["price"], "URL": p["brain_url"],
                      "日時": p["updated_at"]} for p in posts]}


async def _tool_get_status(args, ctx):
    return {
        "毎朝の自動生成": "停止中" if storage.get_setting("paused") == "1" else "動作中",
        "生成時刻": DAILY_TIME, "日次レポート時刻": DAILY_REPORT_TIME,
        "承認後に公開申請まで行う(AUTO_PUBLISH)": AUTO_PUBLISH,
        "紹介料(アフィリエイト)": f"{int(AFFILIATE_RATE * 100)}%" if AFFILIATE_RATE else "なし",
        "承認待ちの下書き数": len(storage.pending_drafts()),
    }


async def _tool_list_drafts(args, ctx):
    out = []
    for did in storage.pending_drafts():
        d = storage.get_draft(did)
        out.append({
            "draft_id": did, "タイトル": d["data"]["title"], "価格": d["data"]["price"],
            "カテゴリー": d["data"]["category"],
            "チェック": "エラーあり（投稿不可）" if checker.has_error(d["issues"]) else "問題なし",
            "作成": d["created_at"],
        })
    return {"件数": len(out), "下書き": out}


async def _tool_add_theme(args, ctx):
    theme = (args.get("theme") or "").strip()
    if not theme:
        return {"error": "theme が空です"}
    try:
        price = _price(args.get("price"))
    except ValueError:
        return {"error": f"price が数字ではありません: {args.get('price')}"}
    ok = storage.add_theme(theme, (args.get("persona") or "").strip(), price,
                           (args.get("category") or "").strip(), (args.get("subcategory") or "").strip())
    return {"追加した": ok, "テーマ": theme, "備考": "" if ok else "同じテーマが既に登録されています"}


async def _tool_set_draft_sales(args, ctx):
    try:
        draft_id = int(args.get("draft_id"))
        price = _price(args.get("price"))
    except (TypeError, ValueError):
        return {"error": "draft_id / price は数字で指定してください"}
    draft = storage.get_draft(draft_id)
    if not draft or draft["status"] != "pending":
        return {"error": f"承認待ちの下書き #{draft_id} が見つかりません"}
    data = dict(draft["data"])
    if price is not None:
        data["price"] = price
    for k in ("category", "subcategory"):
        if args.get(k) is not None:
            data[k] = str(args[k]).strip()
    issues = checker.check(data)
    storage.update_draft(draft_id, data=data, issues=issues)
    return {"変更した": True, "価格": data["price"], "カテゴリー": data["category"],
            "サブカテゴリー": data.get("subcategory"),
            "チェック": [f"{lv}: {msg}" for lv, msg in issues] or "問題なし"}


async def _tool_set_paused(args, ctx):
    paused = args.get("paused")
    if isinstance(paused, str):
        paused = paused.lower() == "true"
    storage.set_setting("paused", "1" if paused else "0")
    return {"毎朝の自動生成": "停止中" if paused else "動作中"}


async def _tool_generate_draft(args, ctx):
    theme_name = (args.get("theme") or "").strip()
    theme = None
    if theme_name:
        try:
            price = _price(args.get("price"))
        except ValueError:
            return {"error": f"price が数字ではありません: {args.get('price')}"}
        theme = {"theme": theme_name, "persona": "", "price": price, "category": args.get("category")}
    try:
        draft_id, reason = await ctx.make_draft(theme)
    except Exception as e:  # noqa: BLE001 - 失敗も会話で伝える
        return {"error": f"生成に失敗しました: {e}"}
    if reason:
        return {"error": f"生成に失敗しました: {reason}"}
    return {"作成した": True, "draft_id": draft_id,
            "備考": "#下書き チャンネルに承認ボタン付きで出しました。Brainにはまだ投稿していません"}


async def _tool_publish_draft(args, ctx):
    try:
        draft_id = int(args.get("draft_id"))
    except (TypeError, ValueError):
        return {"error": f"draft_id が整数ではありません: {args.get('draft_id')}"}
    draft = storage.get_draft(draft_id)
    if not draft:
        return {"error": f"下書き #{draft_id} が見つかりません"}
    if draft["status"] != "pending":
        return {"error": f"下書き #{draft_id} は既に「{draft['status']}」です"}
    if checker.has_error(draft["issues"]):
        return {"error": "公開前チェックでエラーが出ている下書きなので投稿できません。"
                         "先に #下書き の [修正依頼] か [販売設定] で直してください",
                "チェック結果": [f"{lv}: {msg}" for lv, msg in draft["issues"]]}
    try:
        status, url = await ctx.publish(draft)
    except NotLoggedIn as e:
        return {"error": f"Brainにログインできていません: {e}"}
    except BrainError as e:
        return {"error": f"投稿に失敗しました: {e}"}
    await ctx.mark_draft_done(draft, status)
    return {"投稿した": True, "URL": url,
            "状態": "公開申請済み（Brainの審査後に公開）" if status == "published" else "Brainに下書き保存（公開申請はしていない）"}


IMPL = {
    "get_sales": _tool_get_sales,
    "list_brain_articles": _tool_list_brain_articles,
    "list_themes": _tool_list_themes,
    "list_recent_posts": _tool_list_recent_posts,
    "get_status": _tool_get_status,
    "list_drafts": _tool_list_drafts,
    "add_theme": _tool_add_theme,
    "set_draft_sales": _tool_set_draft_sales,
    "set_paused": _tool_set_paused,
    "generate_draft": _tool_generate_draft,
    "publish_draft": _tool_publish_draft,
}


async def run_tool(name, args, ctx):
    tool = TOOLS_BY_NAME.get(name)
    if not tool:
        return {"error": f"{name} というツールはありません。使えるのは: {sorted(TOOLS_BY_NAME)}"}
    if not isinstance(args, dict):
        return {"error": f"args が辞書ではありません: {args!r}"}
    try:
        return await IMPL[name](args, ctx)
    except Exception as e:  # noqa: BLE001 - ツールの失敗で会話全体を落とさない
        return {"error": f"{name} の実行中にエラー: {e}"}


# ---------------------------------------------------------------- claude -p
async def _run_claude(text):
    proc = await asyncio.create_subprocess_exec(
        CLAUDE_CMD, "-p", "--output-format", "json",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=DATA,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(text.encode()), CLAUDE_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        raise SecretaryError("考えるのに時間がかかりすぎました（タイムアウト）")
    if proc.returncode != 0:
        raise SecretaryError(f"Claude Code がエラー終了しました: {err.decode()[-300:]}")
    result = json.loads(out.decode())
    if result.get("is_error"):
        raise SecretaryError(f"Claude Code エラー: {result.get('result')}")
    return result["result"]


def _parse(text):
    """返答からJSONを取り出す。コードフェンス付きで返ってきても拾う。"""
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S) or re.search(r"\{.*\}", text, re.S)
    if not m:
        raise SecretaryError("返答からJSONを取り出せませんでした")
    try:
        data = json.loads(m.group(1) if m.lastindex else m.group(0))
    except json.JSONDecodeError as e:
        raise SecretaryError(f"返答のJSONが壊れています: {e}")
    reply = data.get("reply") or ""
    tools = data.get("tools") or []
    if not isinstance(tools, list):
        tools = []
    return reply, tools


def _build_prompt(history, user_text, transcript):
    parts = [SYSTEM.format(tools=_tools_text()), "",
             f"（今は {datetime.now(JST):%Y-%m-%d %H:%M} です）", ""]
    if history:
        parts.append("## これまでの会話")
        for role, content in history:
            parts.append(f"{'運営者' if role == 'user' else '秘書'}: {content}")
        parts.append("")
    parts += ["## 今回の運営者の発言", user_text, ""]
    if transcript:
        parts.append("## このターンで実行済みのツールと結果")
        for name, args, result in transcript:
            parts.append(f"- {name}({json.dumps(args, ensure_ascii=False)})")
            parts.append(f"  → {json.dumps(result, ensure_ascii=False, default=str)[:3000]}")
        parts += ["", "この結果をふまえて reply を書いてください。"
                  "十分な情報がそろっているなら tools は [] にしてください。"]
    return "\n".join(parts)


async def chat(channel_id, user_text, ctx, on_progress=None):
    """1往復ぶんの会話を処理して、返答テキストと実行したツール名の一覧を返す。"""
    history = storage.recent_chat(str(channel_id), HISTORY_TURNS)
    transcript, used = [], []
    reply = ""
    for step in range(MAX_STEPS):
        reply, calls = _parse(await _run_claude(_build_prompt(history, user_text, transcript)))
        if not calls:
            break
        for call in calls:
            name, args = call.get("name"), call.get("args") or {}
            if on_progress:
                await on_progress(name)
            result = await run_tool(name, args, ctx)
            transcript.append((name, args, result))
            used.append(name)
        if step == MAX_STEPS - 1 and not reply:
            reply = "調べましたが、まとめきれませんでした。もう一度聞いてください。"
    reply = reply.strip() or "うまく答えられませんでした。言い方を変えてもう一度お願いします。"
    storage.add_chat(str(channel_id), "user", user_text)
    storage.add_chat(str(channel_id), "assistant", reply)
    return reply, used
