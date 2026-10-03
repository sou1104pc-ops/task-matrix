"""Mac mini 上の Claude Code（claude -p）で記事を生成する。"""
import asyncio
import json
import re
from datetime import datetime

from .config import CLAUDE_CMD, DATA, DEFAULT_PERSONA, JST, load_programs, prompt


class GenerationError(Exception):
    pass


def _programs_text(program_ids):
    programs = load_programs()
    lines = []
    for pid in program_ids:
        p = programs.get(pid)
        if p:
            lines.append(
                f"- id: {p['id']} / 名前: {p['name']} / URL: {p['url']}\n"
                f"  対象: {p['target']}\n  特徴: {p['selling_points']}\n  CTA文言: {p['cta']}"
            )
    return "\n".join(lines)


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


def _parse_article(text):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise GenerationError("生成結果にJSONが見つかりません")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise GenerationError(f"生成結果のJSONが壊れています: {e}")
    missing = {"title", "hashtags", "body_html"} - data.keys()
    if missing:
        raise GenerationError(f"生成結果に必要な項目がありません: {missing}")
    data.setdefault("program_ids", [])
    data.setdefault("summary", "")
    data.setdefault("thumbnail", {})
    data.setdefault("figures", [])
    return data


async def generate(theme, past_titles):
    programs = load_programs()
    # A8リンクが未設定の案件は使わない（記事に空リンクが入るのを防ぐ）
    program_ids = [p for p in (theme.get("programs") or "").split("|") if programs.get(p, {}).get("url")]
    if not program_ids:
        raise GenerationError(f"テーマの案件（{theme.get('programs')}）のA8リンクが config/programs.json に設定されていません")
    text = prompt("article").format(
        theme=theme["theme"],
        persona=theme.get("persona") or DEFAULT_PERSONA,
        programs=_programs_text(program_ids),
        past_titles="\n".join(f"- {t}" for t in past_titles) or "（なし）",
        year=datetime.now(JST).year,
    )
    data = _parse_article(await _run_claude(text))
    data["allowed_programs"] = program_ids
    return data


async def revise(draft, instruction):
    allowed = draft.get("allowed_programs", [])
    body = {k: draft.get(k) for k in ("title", "hashtags", "program_ids", "summary", "thumbnail", "figures", "body_html")}
    text = prompt("revise").format(
        instruction=instruction,
        programs=_programs_text(allowed),
        draft_json=json.dumps(body, ensure_ascii=False),
    )
    data = _parse_article(await _run_claude(text))
    data["allowed_programs"] = allowed
    return data
