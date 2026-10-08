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


def _cta_method_text(a):
    if a.get("cta_method") == "pinned":
        return ("固定投稿へ誘導する。UTAGEの登録リンクは、プロフィールの固定投稿の返信欄にある。"
                "本文にURLは入れず、「固定投稿を見てね」のように固定投稿へ案内する")
    if a.get("cta_url"):
        return f"最後の投稿に誘導先URL（UTAGEの登録経路）を1回だけ入れる: {a['cta_url']}"
    return "（未設定）URLは入れず、プロフィールへ案内する"


def _account_text(a):
    return (
        f"- 表示名: {a['name']}\n- 発信テーマ: {a.get('theme', '')}\n- ターゲット: {a.get('target', '')}\n"
        f"- 口調: {a.get('tone', '')}\n- 最終的に届けたい商品・サービス: {a.get('product', '')}\n"
        f"- 誘導方法: {_cta_method_text(a)}\n"
        f"- 誘導の方向性: {a.get('cta_text', '')}\n"
        f"- ネタの候補: {' / '.join(a.get('topics', [])) or '（指定なし）'}\n"
        f"- 守ってほしいこと: {a.get('rules', '') or '（特になし）'}"
        + _style_text(a)
    )


def _style_text(a):
    """アカウント独自の「投稿の型」。あれば、プロンプトの一般的な書き方より優先させる。"""
    out = ""
    if a.get("format"):
        out += f"\n- 投稿の型（最優先で守る）: {a['format']}"
    closing = list(a.get("closing") or [])
    order = []
    if closing:
        order.append("「" + "」「".join(closing) + "」を最後の投稿の終わりに入れる")
    if a.get("end_with_question"):
        order.append("最後の一文は読者への質問にする（？で終える）")
    if a.get("last_line"):
        order.append(f"最後の一文は「{a['last_line']}」にする")
    if order:
        out += "\n- 締め（毎投稿必ず。この順番で）: " + " → ".join(order)
    if a.get("facts"):
        out += "\n- 使ってよい実績・数字（運営者が確かめた事実。数字はここにあるものだけ使う）: " + " / ".join(a["facts"])
    return out


def _recent_text(recent):
    return "\n".join(f"- {t[:80].replace(chr(10), ' ')}" for _, t in recent) or "（なし）"


async def generate_day(account, slots, recent_own, recent_others, topic=None):
    """slots: [{"time": "07:30", "type": "value"|"cta"}] → [{"time", "type", "posts", "memo"}]"""
    slot_text = "\n".join(
        f"- {s['time']}（{'誘導' if s['type'] == 'cta' else '価値提供'}"
        + (f"・ネタの種類: {s['genre']}" if s.get("genre") else "") + "）"
        for s in slots
    )
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


async def generate_from_reference(account, reference, kind, recent_own, recent_others, note=None):
    """Xの投稿を参考に、型だけを借りたThreads投稿を1つ作る → {"posts", "memo"}"""
    text = prompt("xref").format(
        account=_account_text(account), kind="誘導" if kind == "cta" else "価値提供",
        reference=reference, note=note or "（特になし）", max_chars=MAX_POST_CHARS, max_chain=MAX_CHAIN,
        recent_own=_recent_text(recent_own), recent_others=_recent_text(recent_others),
    )
    data = _json(await _run_claude(text))
    return {"posts": _posts(data), "memo": str(data.get("memo", ""))[:200]}


def _reference_text(reference):
    if not reference:
        return ""
    return (
        "\n# 参考にしたXの投稿（型だけを借りている。書き直しても、この文・言い回し・具体例を使わないこと）\n"
        f"{reference}\n"
    )


async def revise(account, posts, kind, instruction, reference=None):
    text = prompt("revise").format(
        account=_account_text(account), kind="誘導" if kind == "cta" else "価値提供",
        instruction=instruction, max_chars=MAX_POST_CHARS, max_chain=MAX_CHAIN,
        posts_json=json.dumps({"posts": posts}, ensure_ascii=False), reference=_reference_text(reference),
    )
    return _posts(_json(await _run_claude(text)))
