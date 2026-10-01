"""Mac mini 上の Claude Code（claude -p）で記事を生成する。"""
import asyncio
import json
import re
import urllib.request
from datetime import datetime

from . import materials
from .config import (
    CLAUDE_CMD, DATA, DEFAULT_CATEGORY, DEFAULT_PRICE, DEFAULT_SUBCATEGORY, JST, LINE_URL, PRICE_SCHEDULE,
    REVIEW_REWARD_TITLE, prompt,
)

CATEGORIES_URL = "https://api.brain-market.com/v2/categories"


class GenerationError(Exception):
    pass


async def _run_claude(text, files=(), timeout=1200):
    cmd = [CLAUDE_CMD, "-p", "--output-format", "json"]
    if files:
        cmd += ["--allowedTools", "Read"]  # 材料の画像・PDFを読むためだけに許可する
    proc = await asyncio.create_subprocess_exec(
        *cmd,
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


def fetch_categories():
    """Brain のカテゴリー一覧 {カテゴリー: [サブカテゴリー]}（ログイン不要）。"""
    with urllib.request.urlopen(CATEGORIES_URL, timeout=20) as r:
        data = json.load(r)["data"]
    return {c["name"]: [s["name"] for s in c.get("subcategories") or []] for c in data}


def _categories_text(cats):
    return "\n".join(f"- {c}: {' / '.join(subs) or '（サブカテゴリーなし）'}" for c, subs in cats.items())


def _pick_category(data, theme, cats):
    """テーマで指定があればそれ、無ければAIが選んだもの（一覧に無ければ既定値）。"""
    cat = theme.get("category") or data.get("category")
    sub = theme.get("subcategory") or data.get("subcategory") or ""
    if cats and cat not in cats:
        cat, sub = DEFAULT_CATEGORY, DEFAULT_SUBCATEGORY
    if cats and sub and sub not in cats.get(cat, []):
        sub = ""
    return cat or DEFAULT_CATEGORY, sub


def price_text(first_price):
    """プロンプトに書く価格の説明（最後の価格に見合う内容にしてもらう）。"""
    steps = [(0, first_price)] + PRICE_SCHEDULE[1:]
    if len(steps) == 1:
        return f"{first_price:,}円"
    later = "、".join(f"{d}日後に{p:,}円" for d, p in steps[1:])
    return f"公開直後は{first_price:,}円、{later}に値上げします。最終価格の{steps[-1][1]:,}円に見合う内容にしてください"


def _reward_text():
    if not LINE_URL:
        return "（今回はレビュー特典はありません。特典について本文に書かないこと）"
    return (f"この記事には「レビュー特典」があります（購入してレビューを書いた人だけが受け取れる）。特典名:「{REVIEW_REWARD_TITLE}」\n"
            "無料部分の「この記事で手に入るもの」の後に、レビュー特典があることを1〜2文で短く書いてください。"
            "受け取り方・URL・LINEの登録方法は本文に書かないこと（特典の欄に別で表示されます）")


async def generate(theme, past_titles, material=None):
    """theme は {theme, persona, price, category, subcategory}。material は #材料 の投稿（あれば中身を使う）。"""
    try:
        cats = await asyncio.to_thread(fetch_categories)
    except Exception:  # noqa: BLE001 - 取れなくても既定のカテゴリーで続ける
        cats = {}
    material_text, files = materials.prompt_text(material["id"]) if material else ("", [])
    if files:
        material_text += "\n\n## 画像・PDFの材料（Read ツールで開いて内容を読んでください）\n" + "\n".join(f"- {f}" for f in files)
    price = int(theme.get("price") or DEFAULT_PRICE)
    text = prompt("article").format(
        theme=theme["theme"],
        persona=theme.get("persona") or "このテーマで悩んでいる人",
        price=price_text(price),
        materials=material_text or "（材料はありません。テーマから一般的に役立つ内容を書いてください）",
        reward=_reward_text(),
        categories=_categories_text(cats) or f"- {DEFAULT_CATEGORY}",
        past_titles="\n".join(f"- {t}" for t in past_titles) or "（なし）",
        year=datetime.now(JST).year,
    )
    data = _parse_article(await _run_claude(text, files))
    data["category"], data["subcategory"] = _pick_category(data, theme, cats)
    data["price"] = price
    return data


async def revise(draft, instruction):
    body = {k: draft.get(k) for k in ("title", "summary", "thumbnail", "figures", "body_html")}
    text = prompt("revise").format(
        instruction=instruction,
        price=price_text(draft["price"]),
        reward=_reward_text(),
        draft_json=json.dumps(body, ensure_ascii=False),
    )
    data = _parse_article(await _run_claude(text))
    data.update({k: draft[k] for k in ("price", "category", "subcategory")})
    return data
