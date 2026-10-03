"""Kindle の表紙を JPEG にする。

Claude が出した文字（data["cover"]）を HTML のテンプレートに流し込み、Playwright のブラウザでスクリーンショットを撮る。
KDP の推奨サイズは 1600×2560（縦横比 1.6）。Amazon の検索結果ではとても小さく表示されるので、
大きな文字（catch）を表紙の半分近くまで使い、検索キーワードを目立たせる。
"""
import base64
import re
from html import escape

from playwright.async_api import async_playwright

from .config import AUTHOR_NAME, BOOKS_DIR

SIZE = (1600, 2560)

# (地の色, 濃い色, 強調色, 強調の文字色)
PALETTES = {
    "orange": ("#fff4e0", "#e8590c", "#ffd43b", "#1f1f1f"),
    "blue": ("#e7f3ff", "#1864ab", "#ffd43b", "#1f1f1f"),
    "green": ("#ebfbee", "#2b8a3e", "#ffe066", "#1f1f1f"),
    "red": ("#fff0f0", "#c92a2a", "#ffe066", "#1f1f1f"),
    "navy": ("#edf2ff", "#1b2a4e", "#ffd43b", "#1f1f1f"),
    "pink": ("#fff0f6", "#c2255c", "#fff3bf", "#1f1f1f"),
}

CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: "Hiragino Sans", "Hiragino Kaku Gothic ProN", "Noto Sans JP", sans-serif;
       word-break: auto-phrase; line-break: strict; }
#cover { width: 1600px; height: 2560px; background: var(--bg); position: relative; overflow: hidden;
         display: flex; flex-direction: column; }
.top { background: var(--main); color: #fff; font-weight: 800; font-size: 92px; line-height: 1.35;
       padding: 110px 120px 90px; text-align: center; }
.has-badge .top { text-align: left; padding-right: 470px; min-height: 400px; display: flex; align-items: center; }
.badge { position: absolute; right: 60px; top: 40px; width: 360px; height: 360px; border-radius: 50%;
         background: var(--accent); color: var(--accent-text); font-weight: 900; font-size: 96px; line-height: 1.15;
         display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center;
         border: 14px solid #fff; box-shadow: 0 12px 0 rgba(0,0,0,.12); transform: rotate(8deg); }
.badge .line { white-space: nowrap; }
.main { flex: 1; display: flex; flex-direction: column; justify-content: center; padding: 60px 110px 40px; }
.catch { font-weight: 900; font-size: 250px; line-height: 1.18; color: #1f1f1f; letter-spacing: -.01em; }
.catch .l { display: block; white-space: nowrap; }
.catch em { font-style: normal; color: var(--main);
            background: linear-gradient(transparent 62%, var(--accent) 62%); }
.sub { font-weight: 800; font-size: 96px; line-height: 1.4; color: var(--main); margin-top: 70px;
       border-left: 26px solid var(--main); padding-left: 44px; }
.points { margin: 0 90px 60px; background: #fff; border-radius: 48px; padding: 56px 70px;
          border: 10px solid var(--main); }
.point { font-weight: 800; font-size: 84px; line-height: 1.35; color: #1f1f1f; display: flex; gap: 30px;
         align-items: flex-start; padding: 14px 0; }
.point .mark { flex: none; color: var(--main); }
.author { background: var(--main); color: #fff; text-align: center; font-weight: 700; font-size: 76px;
          padding: 56px 0 64px; letter-spacing: .08em; }
"""

# 文字が枠からはみ出す間、大きい文字から順に小さくする
FIT_JS = """
() => {
  const cover = document.getElementById('cover');
  for (const line of cover.querySelectorAll('.badge .line')) {
    let s = parseFloat(getComputedStyle(line).fontSize);
    while (line.scrollWidth > 300 && s > 40) { s -= 4; line.style.fontSize = s + 'px'; }
  }
  const main = cover.querySelector('.main'), catchEl = cover.querySelector('.catch');
  let size = parseFloat(getComputedStyle(catchEl).fontSize);
  const overflow = () => cover.scrollHeight > cover.clientHeight || main.scrollHeight > main.clientHeight
                         || catchEl.scrollWidth > catchEl.clientWidth;
  while (overflow() && size > 110) { size -= 8; catchEl.style.fontSize = size + 'px'; }
  for (const el of cover.querySelectorAll('.point, .sub, .top')) {
    let s = parseFloat(getComputedStyle(el).fontSize);
    while (overflow() && s > 48) { s -= 4; el.style.fontSize = s + 'px'; }
  }
}
"""


def _catch_html(text):
    """「【】」で囲んだ部分を強調する。行は Claude が決めた改行のまま（はみ出すときは文字を小さくする）。"""
    lines = [re.sub(r"【(.+?)】", r"<em>\1</em>", escape(l.strip())) for l in text.split("\n")]
    return "".join(f"<span class='l'>{l}</span>" for l in lines if l)


def cover_html(data):
    c = data.get("cover") or {}
    bg, main, accent, accent_text = PALETTES.get(c.get("color"), PALETTES["orange"])
    catch = c.get("catch") or data["title"]
    parts = []
    if c.get("top") or c.get("badge"):  # バッジは上の帯に重ねるので、文字が無くても帯は出す
        parts.append(f"<div class='top'>{escape(c.get('top') or '')}</div>")
    if c.get("badge"):
        lines = "".join(f"<div class='line'>{escape(l.strip())}</div>" for l in c["badge"].split("\n") if l.strip())
        parts.append(f"<div class='badge'>{lines}</div>")
    sub = f"<div class='sub'>{escape(c['sub'])}</div>" if c.get("sub") else ""
    parts.append(f"<div class='main'><div class='catch'>{_catch_html(catch)}</div>{sub}</div>")
    points = [p for p in c.get("points") or [] if p][:3]
    if points:
        items = "".join(f"<div class='point'><span class='mark'>✓</span><span>{escape(p)}</span></div>" for p in points)
        parts.append(f"<div class='points'>{items}</div>")
    parts.append(f"<div class='author'>{escape(AUTHOR_NAME or '著者名')}</div>")
    cls = "has-badge" if c.get("badge") else ""
    style = f"--bg:{bg};--main:{main};--accent:{accent};--accent-text:{accent_text};"
    return (f"<!doctype html><html lang='ja'><meta charset='utf-8'><style>{CSS}</style>"
            f"<body><div id='cover' class='{cls}' style='{style}'>{''.join(parts)}</div></body></html>")


def book_dir(book_id):
    d = BOOKS_DIR / f"book-{book_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


async def render(book_id, data):
    """表紙（KDP用のJPEG）と Discord 用の小さいPNGを保存して (jpeg, png) を返す。"""
    out = book_dir(book_id)
    jpg, png = out / "cover.jpg", out / "cover-small.png"
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": SIZE[0], "height": SIZE[1]})
        await page.set_content(cover_html(data))
        await page.evaluate("document.fonts.ready")
        await page.evaluate(FIT_JS)
        await page.locator("#cover").screenshot(path=str(jpg), type="jpeg", quality=92)
        # Discord でスマホからも見やすいように縮小版も作る（検索結果での見え方の確認用）
        small = await browser.new_page(viewport={"width": 400, "height": 640}, device_scale_factor=1)
        await small.set_content(
            f"<body style='margin:0'><img src='data:image/jpeg;base64,"
            f"{base64.b64encode(jpg.read_bytes()).decode()}' style='width:400px;height:640px'></body>"
        )
        await small.screenshot(path=str(png))
        await browser.close()
    return jpg, png
