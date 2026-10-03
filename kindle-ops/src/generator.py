"""Mac mini 上の Claude Code（claude -p）で本を生成する。

1冊（2〜3万字）を1回で書かせると途中で切れたり質が落ちたりするので、
① 企画（タイトル・キーワード・紹介文・表紙・目次・はじめに・おわりに）→ ② 章ごとの本文、の順に何回かに分けて呼ぶ。
"""
import asyncio
import json
import logging
import re
from datetime import datetime

from .config import BOOK_CHARS, CLAUDE_CMD, DATA, JST, KEYWORD_MAX_CHARS, prompt

log = logging.getLogger("generator")

PLAN_KEYS = ("title", "title_kana", "subtitle", "subtitle_kana", "main_keyword", "keywords", "categories",
             "description_html", "cover", "chapters", "intro_html", "outro_html")


class GenerationError(Exception):
    pass


async def _run_claude(text, timeout=900):
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


def _parse_json(text, required):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise GenerationError("生成結果にJSONが見つかりません")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise GenerationError(f"生成結果のJSONが壊れています: {e}")
    missing = set(required) - data.keys()
    if missing:
        raise GenerationError(f"生成結果に必要な項目がありません: {missing}")
    return data


def _strip_html(text):
    """コードフェンスや前置きを外して、HTML部分だけにする。"""
    text = re.sub(r"^```(?:html)?\s*|\s*```$", "", text.strip())
    i = text.find("<")
    return text[i:] if i >= 0 else f"<p>{text}</p>"


def toc_text(data):
    return "\n".join(f"- {c['title']}" for c in data["chapters"])


def _theme_fields(theme):
    rivals = [r.strip() for r in (theme.get("rivals") or "").split("|") if r.strip()]
    return {
        "theme": theme["theme"],
        "persona": theme.get("persona") or "このテーマを調べている初心者",
        "memo": theme.get("memo") or "（なし。著者の体験としては何も書かない）",
        "rivals": "\n".join(f"- {r}" for r in rivals) or "（指定なし。このテーマで検索されそうな言葉から考える）",
    }


async def write_chapter(data, index, instruction=""):
    ch = data["chapters"][index]
    extra = f"\n# 今回の書き直しの指示\n{instruction}\n" if instruction else ""
    text = prompt("chapter").format(
        chapter_title=ch["title"],
        title=data["title"],
        subtitle=data.get("subtitle", ""),
        persona=data["theme"]["persona"],
        toc=toc_text(data),
        points="\n".join(f"- {p}" for p in ch.get("points") or []),
        memo=data["theme"]["memo"],
        instruction=extra,
        chars=max(2000, BOOK_CHARS // max(1, len(data["chapters"]))),
    )
    return _strip_html(await _run_claude(text))


async def write_chapters(data, indexes, instruction="", progress=None):
    """指定した章を順に書く（Claude Code を同時にたくさん動かさない）。"""
    for n, i in enumerate(indexes, 1):
        if progress:
            await progress(f"本文を書いています（{n}/{len(indexes)}）: {data['chapters'][i]['title']}")
        data["chapters"][i]["html"] = await write_chapter(data, i, instruction)


async def generate(theme, past_titles, progress=None):
    fields = _theme_fields(theme)
    now = datetime.now(JST)
    if progress:
        await progress("企画（タイトル・キーワード・表紙・目次）を考えています")
    text = prompt("plan").format(
        **fields,
        past_titles="\n".join(f"- {t}" for t in past_titles) or "（なし）",
        as_of=f"{now.year}年{now.month}月{now.day}日",
        keyword_max=KEYWORD_MAX_CHARS,
        book_chars=BOOK_CHARS,
    )
    data = _parse_json(await _run_claude(text), PLAN_KEYS)
    if not data["chapters"]:
        raise GenerationError("章立てが空です")
    data["theme"] = fields
    await write_chapters(data, range(len(data["chapters"])), progress=progress)
    return data


async def revise(data, instruction, progress=None):
    if progress:
        await progress("企画を直しています")
    plan = {k: data.get(k) for k in PLAN_KEYS}
    plan["chapters"] = [{"title": c["title"], "points": c.get("points") or []} for c in data["chapters"]]
    text = prompt("revise").format(
        instruction=instruction, keyword_max=KEYWORD_MAX_CHARS,
        plan_json=json.dumps(plan, ensure_ascii=False),
    )
    new = _parse_json(await _run_claude(text), PLAN_KEYS)
    old_html = {c["title"]: c.get("html") for c in data["chapters"]}
    rewrite = {int(n) - 1 for n in new.pop("rewrite_chapters", []) or [] if str(n).isdigit()}
    notes = new.pop("chapter_notes", "") or instruction
    # 題名が同じ章は本文を引き継ぐ。新しい章・本文が無い章は書き直す
    for i, ch in enumerate(new["chapters"]):
        ch["html"] = old_html.get(ch["title"])
        if not ch["html"]:
            rewrite.add(i)
    new["theme"] = data["theme"]
    targets = sorted(i for i in rewrite if 0 <= i < len(new["chapters"]))
    await write_chapters(new, targets, notes, progress)
    return new


async def redo_cover(data, instruction):
    text = prompt("cover").format(
        title=data["title"], subtitle=data.get("subtitle", ""), main_keyword=data.get("main_keyword", ""),
        toc=toc_text(data), cover_json=json.dumps(data.get("cover") or {}, ensure_ascii=False),
        instruction=instruction or "もっとクリックしたくなる表紙にしてください（言葉の選び方・強調する言葉・色を変える）",
    )
    return _parse_json(await _run_claude(text, timeout=300), ("catch",))
