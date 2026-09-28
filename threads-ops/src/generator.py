"""Mac mini 上の Claude Code（claude -p）で投稿文を作る。1アカウント1日分をまとめて1回で生成する。"""
import asyncio
import json
import re

from .config import CLAUDE_CMD, DATA, MAX_CHAIN, MAX_POST_CHARS, prompt


class GenerationError(Exception):
    pass


async def _run_claude(text, timeout=600):
    proc = await asyncio.create_subprocess_exec(
        CLAUDE_CMD, "-p", "--output-format", "json",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=DATA,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(text.encode()), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise GenerationError("Claude Code の応答がタイムアウトしました")
    if proc.returncode != 0:
        raise GenerationError(f"Claude Code がエラー終了しました: {err.decode()[-500:]}")
    result = json.loads(out.decode())
    if result.get("is_error"):
        raise GenerationError(f"Claude Code エラー: {result.get('result')}")
    return result["result"]


def _json(text):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise GenerationError("生成結果にJSONが見つかりません")
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise GenerationError(f"生成結果のJSONが壊れています: {e}")


def _posts(item):
    posts = item.get("posts")
    if isinstance(posts, str):
        posts = [posts]
    if not isinstance(posts, list) or not posts:
        raise GenerationError("生成結果に posts がありません")
    return [str(p).strip() for p in posts][:MAX_CHAIN]


def _account_text(a):
    return (
        f"- 表示名: {a['name']}\n- 発信テーマ: {a.get('theme', '')}\n- ターゲット: {a.get('target', '')}\n"
        f"- 口調: {a.get('tone', '')}\n- 最終的に届けたい商品・サービス: {a.get('product', '')}\n"
        f"- 誘導先URL（UTAGEの登録経路）: {a.get('cta_url') or '（未設定）'}\n"
        f"- 誘導の方向性: {a.get('cta_text', '')}\n"
        f"- ネタの候補: {' / '.join(a.get('topics', [])) or '（指定なし）'}\n"
        f"- 守ってほしいこと: {a.get('rules', '') or '（特になし）'}"
    )


def _recent_text(recent):
    return "\n".join(f"- {t[:80].replace(chr(10), ' ')}" for _, t in recent) or "（なし）"


async def generate_day(account, slots, recent_own, recent_others, topic=None):
    """slots: [{"time": "07:30", "type": "value"|"cta"}] → [{"time", "type", "posts", "memo"}]"""
    slot_text = "\n".join(f"- {s['time']}（{'誘導' if s['type'] == 'cta' else '価値提供'}）" for s in slots)
    text = prompt("post").format(
        account=_account_text(account), slots=slot_text, max_chars=MAX_POST_CHARS, max_chain=MAX_CHAIN,
        recent_own=_recent_text(recent_own), recent_others=_recent_text(recent_others),
        topic=topic or "（指定なし。ネタの候補と過去の投稿から、かぶらないものを選ぶ）",
    )
    data = _json(await _run_claude(text))
    items = data.get("drafts")
    if not isinstance(items, list) or len(items) != len(slots):
        raise GenerationError(f"投稿枠{len(slots)}つに対して、{len(items or [])}件しか生成されませんでした")
    return [
        {"time": s["time"], "type": s["type"], "posts": _posts(item), "memo": str(item.get("memo", ""))[:200]}
        for s, item in zip(slots, items)
    ]


async def revise(account, posts, kind, instruction):
    text = prompt("revise").format(
        account=_account_text(account), kind="誘導" if kind == "cta" else "価値提供",
        instruction=instruction, max_chars=MAX_POST_CHARS, max_chain=MAX_CHAIN,
        posts_json=json.dumps({"posts": posts}, ensure_ascii=False),
    )
    return _posts(_json(await _run_claude(text)))
