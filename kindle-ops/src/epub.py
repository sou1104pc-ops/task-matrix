"""本文から KDP にアップロードする EPUB3 を作る（外部ライブラリなし）。

構成: 表紙 → 扉 → 目次 → はじめに → 各章 → おわりに → 奥付。横書き・リフロー型。
"""
import re
import uuid
import zipfile
from datetime import datetime, timezone
from html import escape

from .config import AUTHOR_NAME, JST
from .cover import book_dir

CSS = """
body { font-family: serif; line-height: 1.8; margin: 0 4%; }
h1 { font-size: 1.6em; line-height: 1.4; margin: 2em 0 .5em; }
h2 { font-size: 1.35em; line-height: 1.4; margin: 1.5em 0 1em; padding-bottom: .3em; border-bottom: 2px solid #333; page-break-before: always; }
h3 { font-size: 1.1em; line-height: 1.5; margin: 1.8em 0 .6em; padding-left: .5em; border-left: 4px solid #333; }
p { margin: 0 0 1em; text-indent: 0; }
ul, ol { margin: 0 0 1em 1.5em; padding: 0; }
li { margin-bottom: .4em; }
.title-page { text-align: center; margin-top: 30%; }
.title-page .sub { font-size: 1em; margin-top: 1em; }
.title-page .author { margin-top: 3em; }
.colophon { margin-top: 30%; font-size: .9em; }
.cover { text-align: center; margin: 0; padding: 0; }
.cover img { max-width: 100%; height: auto; }
"""

ALLOWED = {"h2", "h3", "p", "ul", "ol", "li", "b", "br"}


def _xhtml(title, body, cls=""):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="ja" xml:lang="ja">\n'
        f'<head><meta charset="UTF-8"/><title>{escape(title)}</title>'
        '<link rel="stylesheet" type="text/css" href="style.css"/></head>\n'
        f'<body class="{cls}">\n{body}\n</body></html>\n'
    )


def clean_html(html):
    """許可したタグ以外を外し、XHTML として壊れないように整える。"""
    html = re.sub(r"<(script|style)\b.*?</\1>", "", html or "", flags=re.S | re.I)

    def tag(m):
        closing, name = m.group(1), m.group(2).lower()
        if name not in ALLOWED:
            return ""
        if name == "br":
            return "<br/>"
        return f"<{closing}{name}>"
    html = re.sub(r"<\s*(/?)\s*([a-zA-Z0-9]+)[^>]*>", tag, html)
    # & のうち実体参照でないものをエスケープ
    html = re.sub(r"&(?!#?\w+;)", "&amp;", html)
    return html.strip()


def _first_h2(html, fallback):
    m = re.search(r"<h2>(.*?)</h2>", html, re.S)
    return re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else fallback


def sections(data):
    """[(ファイル名, 見出し, XHTML本文)]"""
    out = [("intro.xhtml", None, data.get("intro_html") or "")]
    for i, ch in enumerate(data["chapters"], 1):
        html = ch.get("html") or ""
        if "<h2" not in html:
            html = f"<h2>{escape(ch['title'])}</h2>\n{html}"
        out.append((f"chapter{i:02d}.xhtml", ch["title"], html))
    out.append(("outro.xhtml", None, data.get("outro_html") or ""))
    result = []
    for name, title, html in out:
        html = clean_html(html)
        if not html:
            continue
        result.append((name, _first_h2(html, title or ""), html))
    return result


def build(book_id, data, cover_jpg):
    """EPUB を作ってパスを返す。"""
    path = book_dir(book_id) / "book.epub"
    book_uuid = uuid.uuid5(uuid.NAMESPACE_URL, f"kindle-ops/book/{book_id}")  # 作り直しても同じ本は同じID
    title, subtitle = data["title"], data.get("subtitle") or ""
    now = datetime.now(JST)
    secs = sections(data)

    title_page = _xhtml(title, (
        f'<div class="title-page"><h1>{escape(title)}</h1>'
        + (f'<p class="sub">{escape(subtitle)}</p>' if subtitle else "")
        + f'<p class="author">{escape(AUTHOR_NAME)}</p></div>'
    ))
    toc_items = "".join(f'<li><a href="{n}">{escape(t)}</a></li>' for n, t, _ in secs)
    nav = _xhtml("目次", f'<nav epub:type="toc" id="toc"><h1>目次</h1><ol>{toc_items}</ol></nav>')
    colophon = _xhtml("奥付", (
        f'<div class="colophon"><p><b>{escape(title)}</b></p>'
        + (f"<p>{escape(subtitle)}</p>" if subtitle else "")
        + f"<p>{now.year}年{now.month}月{now.day}日　発行</p><p>著者　{escape(AUTHOR_NAME)}</p>"
        "<p>本書の内容は執筆時点の情報にもとづいています。制度・サービスの最新情報は公式サイトでご確認ください。</p></div>"
    ))
    cover_page = _xhtml("表紙", '<div class="cover"><img src="cover.jpg" alt="表紙"/></div>', "cover")

    spine_files = ["cover.xhtml", "title.xhtml", "nav.xhtml"] + [n for n, _, _ in secs] + ["colophon.xhtml"]
    manifest = [
        '<item id="css" href="style.css" media-type="text/css"/>',
        '<item id="cover-image" href="cover.jpg" media-type="image/jpeg" properties="cover-image"/>',
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
    ]
    for f in spine_files:
        if f != "nav.xhtml":
            manifest.append(f'<item id="{f[:-6]}" href="{f}" media-type="application/xhtml+xml"/>')
    spine = "".join(f'<itemref idref="{"nav" if f == "nav.xhtml" else f[:-6]}"/>' for f in spine_files)
    opf = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang="ja">\n'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f'<dc:identifier id="bookid">urn:uuid:{book_uuid}</dc:identifier>'
        f"<dc:title>{escape(title)}</dc:title><dc:language>ja</dc:language>"
        f"<dc:creator>{escape(AUTHOR_NAME)}</dc:creator>"
        f'<meta property="dcterms:modified">{datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ}</meta>'
        '<meta name="cover" content="cover-image"/>'
        f"</metadata>\n<manifest>{''.join(manifest)}</manifest>\n"
        f'<spine toc="ncx" page-progression-direction="ltr">{spine}</spine>\n'
        '<guide><reference type="toc" title="目次" href="nav.xhtml"/>'
        f'<reference type="text" title="本文" href="{secs[0][0] if secs else "title.xhtml"}"/></guide>\n'
        "</package>\n"
    )
    nav_points = "".join(
        f'<navPoint id="p{i}" playOrder="{i}"><navLabel><text>{escape(t)}</text></navLabel>'
        f'<content src="{n}"/></navPoint>'
        for i, (n, t, _) in enumerate(secs, 1)
    )
    ncx = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1" xml:lang="ja">'
        f'<head><meta name="dtb:uid" content="urn:uuid:{book_uuid}"/></head>'
        f"<docTitle><text>{escape(title)}</text></docTitle><navMap>{nav_points}</navMap></ncx>\n"
    )
    container = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>'
        "</container>\n"
    )

    with zipfile.ZipFile(path, "w") as z:
        # mimetype は先頭・無圧縮でないと EPUB として読めない
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        files = {
            "META-INF/container.xml": container,
            "OEBPS/content.opf": opf,
            "OEBPS/toc.ncx": ncx,
            "OEBPS/style.css": CSS,
            "OEBPS/nav.xhtml": nav,
            "OEBPS/cover.xhtml": cover_page,
            "OEBPS/title.xhtml": title_page,
            "OEBPS/colophon.xhtml": colophon,
        }
        files.update({f"OEBPS/{n}": _xhtml(t or title, html) for n, t, html in secs})
        for name, text in files.items():
            z.writestr(name, text, compress_type=zipfile.ZIP_DEFLATED)
        z.write(cover_jpg, "OEBPS/cover.jpg", compress_type=zipfile.ZIP_DEFLATED)
    return path
