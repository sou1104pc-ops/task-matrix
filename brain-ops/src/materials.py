"""#材料 フォーラムに送られた材料を保存し、記事生成のプロンプトに渡す形にする。

フォーラムの投稿1つ = 記事1本ぶんの材料。投稿のタイトルがテーマになる。
投稿の本文と、そのあとの返信がすべて材料になる。
- 文章 → そのまま
- URL → ページの本文を取ってくる
- テキストファイル（.txt .md .csv など）→ 中身を読む
- 画像・PDF → 保存して、Claude Code に読んでもらう
"""
import asyncio
import re
import urllib.request
from html import unescape

from . import storage
from .config import MATERIALS_DIR

TEXT_EXT = {".txt", ".md", ".csv", ".json", ".tsv", ".html", ".srt", ".vtt"}
FILE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf"}
URL_RE = re.compile(r"https?://[^\s<>）」]+")
MAX_TEXT = 20000      # 1つの材料として持つ最大文字数
MAX_PROMPT = 60000    # プロンプトに入れる材料の合計文字数


def _page_text(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read(3_000_000).decode(r.headers.get_content_charset() or "utf-8", errors="ignore")
    m = re.search(r"<title[^>]*>(.*?)</title>", raw, re.S | re.I)
    raw = re.sub(r"<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
    text = unescape(re.sub(r"<[^>]+>", " ", raw))
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    title = unescape(m.group(1)).strip() if m else ""
    return f"{title}\n{text}"[:MAX_TEXT]


async def save_message(material, message):
    """Discord のメッセージ1件を材料として保存する。保存できなかったものの説明のリストを返す。"""
    skipped = []
    text = message.content.strip()
    if text:
        storage.add_material_item(material["id"], "text", text[:MAX_TEXT])
    for url in URL_RE.findall(text):
        try:
            page = await asyncio.to_thread(_page_text, url)
            storage.add_material_item(material["id"], "url", f"{url}\n{page}")
        except Exception as e:  # noqa: BLE001 - 取れないページがあっても他の材料は保存する
            skipped.append(f"{url}（ページを読めませんでした: {e}）")
    folder = MATERIALS_DIR / str(material["thread_id"])
    for att in message.attachments:
        ext = ("." + att.filename.rsplit(".", 1)[-1].lower()) if "." in att.filename else ""
        if ext in TEXT_EXT or (att.content_type or "").startswith("text/"):
            body = (await att.read()).decode("utf-8", errors="ignore")
            storage.add_material_item(material["id"], "text", f"（ファイル {att.filename}）\n{body[:MAX_TEXT]}")
        elif ext in FILE_EXT:
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{message.id}-{att.filename}"
            await att.save(path)
            storage.add_material_item(material["id"], "file", str(path))
        else:
            skipped.append(f"{att.filename}（この形式は読めません。文字起こしやテキストにして送ってください）")
    return skipped


def prompt_text(material_id):
    """プロンプトに入れる材料の文章と、Claude に読ませるファイルのパスを返す。"""
    parts, files, total = [], [], 0
    for i, item in enumerate(storage.material_items(material_id), 1):
        if item["kind"] == "file":
            files.append(item["content"])
            continue
        label = "URLの本文" if item["kind"] == "url" else "メモ"
        chunk = f"## 材料{i}（{label}）\n{item['content']}"
        if total + len(chunk) > MAX_PROMPT:
            parts.append("（残りの材料は長すぎるため省略しました）")
            break
        parts.append(chunk)
        total += len(chunk)
    return "\n\n".join(parts), files
