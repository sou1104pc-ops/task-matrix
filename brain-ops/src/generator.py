"""Mac mini 上の Claude Code（claude -p）で記事を生成する。"""
import asyncio
import json
import re
from datetime import datetime

from .config import CLAUDE_CMD, DATA, DEFAULT_CATEGORY, DEFAULT_PRICE, DEFAULT_SUBCATEGORY, JST, prompt


class GenerationError(Exception):
    pass


async def _run_claude(text, timeout=1200):
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


def _parse_article(text):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise GenerationError("生成結果にJSONが見つかりません")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise GenerationError(f"生成結果のJSONが壊れています: {e}")
    missing = {"title", "body_html"} - data.keys()
    if missing:
        raise GenerationError(f"生成結果に必要な項目がありません: {missing}")
    data.setdefault("summary", "")
    data.setdefault("thumbnail", {})
    data.setdefault("figures", [])
    return data


def sales_settings(theme):
    """テーマに書かれた販売設定。無ければ .env の既定値。"""
    return {
        "price": int(theme.get("price") or DEFAULT_PRICE),
        "category": theme.get("category") or DEFAULT_CATEGORY,
        "subcategory": theme.get("subcategory") or DEFAULT_SUBCATEGORY,
    }


async def generate(theme, past_titles):
    sales = sales_settings(theme)
    text = prompt("article").format(
        theme=theme["theme"],
        persona=theme.get("persona") or "このテーマで悩んでいる人",
        price=f"{sales['price']:,}",
        past_titles="\n".join(f"- {t}" for t in past_titles) or "（なし）",
        year=datetime.now(JST).year,
    )
    data = _parse_article(await _run_claude(text))
    data.update(sales)
    return data


async def revise(draft, instruction):
    body = {k: draft.get(k) for k in ("title", "summary", "thumbnail", "figures", "body_html")}
    text = prompt("revise").format(
        instruction=instruction,
        price=f"{draft['price']:,}",
        draft_json=json.dumps(body, ensure_ascii=False),
    )
    data = _parse_article(await _run_claude(text))
    data.update({k: draft[k] for k in ("price", "category", "subcategory")})
    return data
