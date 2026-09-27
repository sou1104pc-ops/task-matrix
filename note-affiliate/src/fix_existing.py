"""既存記事の修正（fixes/<key>.json）を読み込み、Discordでの確認と note への反映を行う。

python -m src.fix_existing preview        … 全記事の修正前後を fixes/fixed/ と fixes/REPORT.md に書き出す
python -m src.fix_existing try <key>      … ブラウザを表示して修正を入力し、更新ボタンは押さずに止める
python -m src.fix_existing apply <key>    … 修正して更新する（Discordの [この内容でnoteを更新] と同じ）
"""
import asyncio
import difflib
import io
import json
import re
import sys
from dataclasses import dataclass, field
from html import unescape

from .checker import PR_LINE
from .config import ROOT

FIXES = ROOT / "fixes"


def html_to_text(html):
    return unescape(re.sub(r"<[^>]+>", "", html))


def _edge_clean(fragment):
    """タグの途中から始まる/終わる断片も含めて、画面に表示される文字だけにする。"""
    fragment = re.sub(r"^[^<]*?>", "", fragment) if re.match(r"^[^<]*>", fragment) else fragment
    fragment = re.sub(r"<[^>]*$", "", fragment)
    return html_to_text(fragment).strip()


def _blocks_to_lines(html):
    html = re.sub(r"</(p|h[1-6]|li|figure|blockquote)>", "\n", html)
    return [line for line in html_to_text(html).split("\n") if line.strip()]


@dataclass
class Fix:
    key: str
    url: str
    title_before: str
    title_after: str | None
    hashtags_before: list
    hashtags_after: list
    replacements: list = field(default_factory=list)
    append_html: str | None = None
    append_reason: str | None = None

    @property
    def original_html(self):
        return (FIXES / "original" / f"{self.key}.html").read_text(encoding="utf-8")

    def fixed_html(self):
        html = self.original_html
        for r in self.replacements:
            html = html.replace(r["old"], r["new"], 1)
        return f"<p>{PR_LINE}</p>" + html + (self.append_html or "")

    def editor_edits(self):
        """エディタ上で行う (旧テキスト, 新テキスト, ブロックごと削除か, 何番目の出現か) のリスト。

        記事の後ろの修正から順に並べる。前の方の文章は変わらないので、
        「同じ文が何番目に出てくるか」を元の記事で数えた値がそのまま使える。"""
        html = self.original_html
        edits = []
        for r in self.replacements:
            pos = html.index(r["old"])
            old_text, new_text = _edge_clean(r["old"]), _edge_clean(r["new"])
            if not old_text:  # 画像やカードだけの要素は画面上の文字で探せない
                edits.append((pos, (r["old"][:80], None, None, 0)))
                continue
            before = html_to_text(re.sub(r"<[^>]*$", "", html[:pos]))
            nth = before.count(old_text)
            whole_block = bool(re.match(r"\s*<(p|figure|h[23]|li|blockquote)\b", r["old"]))
            edits.append((pos, (old_text, new_text, whole_block and not new_text, nth)))
        return [e for _, e in sorted(edits, key=lambda x: x[0], reverse=True)]

    def embed(self):
        import discord

        count = len(self.replacements) + 1 + bool(self.append_html)
        e = discord.Embed(title=f"修正 {count}件", color=0x5B8DEF)
        if self.title_after:
            e.add_field(name="タイトル", value=f"{self.title_before}\n→ **{self.title_after}**"[:1000], inline=False)
        e.add_field(
            name="ハッシュタグ",
            value=f"{' '.join(self.hashtags_before)}\n→ {' '.join(self.hashtags_after)}"[:1000], inline=False,
        )
        reasons = ["・冒頭にPR表記を追加"] + [f"・{r['reason']}" for r in self.replacements]
        if self.append_reason:
            reasons.append(f"・{self.append_reason}")
        text = "\n".join(reasons)
        e.add_field(name="主な修正", value=text if len(text) <= 1000 else text[:990] + "\n…", inline=False)
        e.set_footer(text="添付の diff で修正前（-）と修正後（+）を確認できます")
        return e

    def diff_text(self):
        before, after = _blocks_to_lines(self.original_html), _blocks_to_lines(self.fixed_html())
        head = []
        if self.title_after:
            head += [f"- タイトル: {self.title_before}", f"+ タイトル: {self.title_after}", ""]
        body = difflib.unified_diff(before, after, "修正前", "修正後", lineterm="", n=1)
        return "\n".join(head + list(body))

    def diff_file(self):
        import discord

        return discord.File(io.BytesIO(self.diff_text().encode()), filename=f"{self.key}.diff")

    async def apply(self, nc, save=True):
        edits = self.editor_edits()
        skipped = [e[0] for e in edits if e[1] is None]
        not_found = await nc.edit_in_place(
            self.key, [e for e in edits if e[1] is not None], insert_top=PR_LINE,
            title=self.title_after, hashtags=self.hashtags_after, append_html=self.append_html, save=save,
        )
        return {"not_found": skipped + not_found}


def _articles():
    return {a["key"]: a for a in json.loads((FIXES / "articles.json").read_text(encoding="utf-8"))}


def load(key):
    a = _articles()[key]
    spec = json.loads((FIXES / f"{key}.json").read_text(encoding="utf-8"))
    return Fix(
        key=key, url=a["url"], title_before=a["title"], title_after=spec.get("title"),
        hashtags_before=a["hashtags"], hashtags_after=spec.get("hashtags") or a["hashtags"],
        replacements=spec.get("replacements", []),
        append_html=spec.get("append_html"), append_reason=spec.get("append_reason"),
    )


def load_all():
    return [load(k) for k in _articles() if (FIXES / f"{k}.json").exists()]


def preview():
    out = FIXES / "fixed"
    out.mkdir(exist_ok=True)
    report = ["# 既存記事の修正一覧", ""]
    for fix in load_all():
        (out / f"{fix.key}.html").write_text(fix.fixed_html(), encoding="utf-8")
        report += [
            f"## {fix.title_before}", f"{fix.url}", "",
            f"- タイトル: {fix.title_after or '変更なし'}",
            f"- ハッシュタグ: {' '.join(fix.hashtags_before)} → {' '.join(fix.hashtags_after)}",
            "- 冒頭にPR表記を追加",
        ]
        report += [f"- {r['reason']}" for r in fix.replacements]
        if fix.append_reason:
            report.append(f"- {fix.append_reason}")
        report += ["", "```diff", fix.diff_text(), "```", ""]
    (FIXES / "REPORT.md").write_text("\n".join(report), encoding="utf-8")
    print(f"{FIXES / 'REPORT.md'} に書き出しました")


async def _run(cmd, key):
    from .note_client import NoteClient

    fix = load(key)
    async with NoteClient(headless=False) as nc:
        result = await fix.apply(nc, save=(cmd == "apply"))
        if cmd == "try":
            print("入力まで完了しました（更新はしていません）。ブラウザで確認後 Enter で閉じます。")
            await asyncio.get_event_loop().run_in_executor(None, sys.stdin.readline)
    print("見つからなかった箇所:", result["not_found"] or "なし")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["preview"]:
        preview()
    elif len(args) == 2 and args[0] in ("try", "apply"):
        asyncio.run(_run(args[0], args[1]))
    else:
        print(__doc__)
