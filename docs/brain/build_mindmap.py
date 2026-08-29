#!/usr/bin/env python3
"""Markdown のアウトラインから XMind 用ファイル（.opml / .xmind）を生成する。

使い方:
    python3 docs/brain/build_mindmap.py docs/brain/第11章_商品化.md

Markdown 側のルール:
    - `# 見出し` がルートトピック（1ファイルに1つ）
    - `- 項目` のネストしたリストが子トピック（インデント2スペースで1階層）
"""

import json
import sys
import uuid
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

INDENT = 2


def parse(md_text):
    """Markdown を {title, children} のツリーに変換する。"""
    root = None
    stack = []  # (depth, node)
    for raw in md_text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.startswith("# "):
            root = {"title": line[2:].strip(), "children": []}
            stack = [(-1, root)]
            continue
        stripped = line.lstrip(" ")
        if not stripped.startswith("- "):
            continue
        if root is None:
            raise ValueError("`# 見出し` のルートトピックが見つかりません")
        depth = (len(line) - len(stripped)) // INDENT
        node = {"title": stripped[2:].strip(), "children": []}
        while stack and stack[-1][0] >= depth:
            stack.pop()
        stack[-1][1]["children"].append(node)
        stack.append((depth, node))
    if root is None:
        raise ValueError("`# 見出し` のルートトピックが見つかりません")
    return root


def to_opml(root):
    def outline(node, level):
        pad = "  " * level
        text = escape(node["title"], {'"': "&quot;"})
        if not node["children"]:
            return f'{pad}<outline text="{text}"/>\n'
        out = f'{pad}<outline text="{text}">\n'
        for child in node["children"]:
            out += outline(child, level + 1)
        return out + f"{pad}</outline>\n"

    body = "".join(outline(c, 3) for c in root["children"])
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<opml version="2.0">\n'
        f"  <head>\n    <title>{escape(root['title'])}</title>\n  </head>\n"
        "  <body>\n"
        f'    <outline text="{escape(root["title"])}">\n'
        f"{body}"
        "    </outline>\n"
        "  </body>\n"
        "</opml>\n"
    )


def to_topic(node, root=False):
    topic = {"id": uuid.uuid4().hex, "class": "topic", "title": node["title"]}
    if root:
        topic["structureClass"] = "org.xmind.ui.logic.right"
    if node["children"]:
        topic["children"] = {"attached": [to_topic(c) for c in node["children"]]}
    return topic


def write_xmind(root, path):
    content = [
        {
            "id": uuid.uuid4().hex,
            "class": "sheet",
            "title": root["title"],
            "rootTopic": to_topic(root, root=True),
        }
    ]
    metadata = {"creator": {"name": "build_mindmap.py", "version": "1.0"}}
    manifest = {
        "file-entries": {
            "content.json": {},
            "metadata.json": {},
            "metadata.xml": {},
        }
    }
    dump = lambda obj: json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("content.json", dump(content))
        z.writestr("metadata.json", dump(metadata))
        z.writestr("manifest.json", dump(manifest))
        z.writestr("META-INF/manifest.xml", dump(manifest))


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 1
    src = Path(sys.argv[1])
    root = parse(src.read_text(encoding="utf-8"))
    opml = src.with_suffix(".opml")
    xmind = src.with_suffix(".xmind")
    opml.write_text(to_opml(root), encoding="utf-8")
    write_xmind(root, xmind)
    print(f"生成しました:\n  {opml}\n  {xmind}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
