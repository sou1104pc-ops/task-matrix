"""記事の見出し画像（サムネ）と本文の図解を PNG にする。

Claude が出した文字情報（data["thumbnail"] / data["figures"]）を HTML のテンプレートに流し込み、
Playwright のブラウザでスクリーンショットを撮る。外部の画像生成サービスは使わない。
本文中の図は body_html の <p>[[FIG:id]]</p> の位置に入る。
"""
import base64
import re
from html import escape

from playwright.async_api import async_playwright

from .config import DRAFTS_DIR

THUMB_SIZE = (1280, 670)  # note 推奨サイズ
FIG_WIDTH = 1080          # スマホで縮小されても読める幅
MARKER = re.compile(r"<p>\s*\[\[FIG:([\w-]+)\]\]\s*</p>")
FIG_TYPES = {"checklist", "steps", "compare"}

BASE_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: "Hiragino Sans", "Hiragino Kaku Gothic ProN", "Noto Sans JP", sans-serif;
       color: #1f2d3d; background: #fff;
       word-break: auto-phrase; line-break: strict; }  /* 日本語を文節で折り返す（単語の途中で切らない） */
"""

THUMB_CSS = BASE_CSS + """
#card { width: 1280px; height: 670px; background: #f4f7f6; position: relative; overflow: hidden;
        display: flex; flex-direction: column; justify-content: center; padding: 0 110px; }
#card::before { content: ""; position: absolute; right: -140px; top: -140px; width: 520px; height: 520px;
                border-radius: 50%; background: #41c9b4; opacity: .18; }
#card::after { content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 28px; background: #1f3a5f; }
.label { align-self: flex-start; background: #1f3a5f; color: #fff; font-weight: 700; font-size: 38px;
         padding: 10px 28px; border-radius: 999px; margin-bottom: 40px; }
.catch { font-weight: 800; font-size: 76px; line-height: 1.35; letter-spacing: .01em; }
.catch em { font-style: normal; color: #139a86; }
.sub { font-weight: 700; font-size: 40px; line-height: 1.5; color: #4a5a6a; margin-top: 28px; }
"""

FIG_CSS = BASE_CSS + """
#card { width: 1080px; padding: 64px 64px 72px; background: #fff; border-top: 16px solid #41c9b4; }
h1 { font-size: 50px; font-weight: 800; line-height: 1.4; margin-bottom: 44px; }
.item { display: flex; align-items: flex-start; gap: 28px; font-size: 42px; line-height: 1.5;
        font-weight: 600; padding: 26px 32px; background: #f4f7f6; border-radius: 20px; margin-bottom: 20px; }
.mark { flex: none; width: 60px; height: 60px; border-radius: 50%; background: #41c9b4; color: #fff;
        font-size: 36px; font-weight: 800; display: flex; align-items: center; justify-content: center; margin-top: 2px; }
.steps .mark { background: #1f3a5f; }
.arrow { text-align: center; color: #41c9b4; font-size: 36px; line-height: 1; margin: -6px 0 14px; }
table { width: 100%; border-collapse: separate; border-spacing: 8px; font-size: 36px; line-height: 1.45; }
th:empty { background: none; }
th { background: #1f3a5f; color: #fff; font-weight: 700; padding: 22px 16px; border-radius: 14px; }
td { background: #f4f7f6; padding: 22px 20px; border-radius: 14px; font-weight: 600; vertical-align: middle; }
td.head { background: #e1f5f1; font-weight: 800; }
"""


def _page(css, inner):
    # lang="ja" が無いと auto-phrase（文節での折り返し）が効かない
    return (f"<!doctype html><html lang='ja'><meta charset='utf-8'><style>{css}</style>"
            f"<body><div id='card'>{inner}</div></body></html>")


def _catch_html(text):
    """「【】」で囲んだ部分を強調色にし、改行は <br> にする。"""
    parts = []
    for line in text.split("\n"):
        line = escape(line.strip())
        parts.append(re.sub(r"【(.+?)】", r"<em>\1</em>", line))
    return "<br>".join(p for p in parts if p)


def thumbnail_text(data):
    """(label, catch, sub) を返す。Claude が別名のキーで返したときや、無いときはタイトルから作る。"""
    t = data.get("thumbnail") or {}
    label = t.get("label") or ""
    catch = t.get("catch") or t.get("title") or t.get("text") or ""
    sub = t.get("sub") or t.get("subtitle") or ""
    if not catch:
        m = re.match(r"【(.+?)】\s*(.+)", data["title"])
        catch = m.group(2) if m else data["title"]
        label = label or (m.group(1) if m else "")
    return label, catch, sub


def thumbnail_html(data):
    label, catch, sub = thumbnail_text(data)
    label_html = f"<div class='label'>{escape(label)}</div>" if label else ""
    sub_html = f"<div class='sub'>{escape(sub)}</div>" if sub else ""
    return _page(THUMB_CSS, f"{label_html}<div class='catch'>{_catch_html(catch)}</div>{sub_html}")


def figure_html(fig):
    title = f"<h1>{escape(fig.get('title', ''))}</h1>" if fig.get("title") else ""
    kind = fig.get("type")
    if kind == "compare":
        cols = fig.get("columns") or []
        head = "".join(f"<th>{escape(c)}</th>" for c in cols)
        rows = "".join(
            "<tr>" + "".join(
                f"<td class='head'>{escape(c)}</td>" if i == 0 else f"<td>{escape(c)}</td>"
                for i, c in enumerate(r)
            ) + "</tr>"
            for r in fig.get("rows") or []
        )
        return _page(FIG_CSS, f"{title}<table><tr>{head}</tr>{rows}</table>")
    items = fig.get("items") or []
    if kind == "steps":
        blocks = []
        for i, it in enumerate(items, 1):
            if i > 1:
                blocks.append("<div class='arrow'>▼</div>")
            blocks.append(f"<div class='item'><div class='mark'>{i}</div><div>{escape(it)}</div></div>")
        return _page(FIG_CSS, f"{title}<div class='steps'>{''.join(blocks)}</div>")
    body = "".join(f"<div class='item'><div class='mark'>✓</div><div>{escape(it)}</div></div>" for it in items)
    return _page(FIG_CSS, f"{title}{body}")


# 見出し画像の文字が枠からはみ出す間、大きい文字から順に小さくする
FIT_JS = """
() => {
  const card = document.getElementById('card');
  const catchEl = card.querySelector('.catch'), sub = card.querySelector('.sub');
  let size = 76, subSize = 40;
  const fits = () => {
    let h = 0;
    for (const c of card.children) {
      const cs = getComputedStyle(c);
      h += c.getBoundingClientRect().height + parseFloat(cs.marginTop) + parseFloat(cs.marginBottom);
    }
    return h <= card.clientHeight - 100;
  };
  while (!fits() && size > 44) {
    size -= 4; catchEl.style.fontSize = size + 'px';
    if (sub && subSize > 30) { subSize -= 2; sub.style.fontSize = subSize + 'px'; }
  }
}
"""


def figure_ids(html):
    return MARKER.findall(html)


def draft_dir(draft_id):
    return DRAFTS_DIR / f"draft-{draft_id}"


async def render(draft_id, data):
    """見出し画像と図を描いて保存し、{"thumbnail": Path, "figures": {id: Path}} を返す。"""
    out = draft_dir(draft_id)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.png"):
        old.unlink()
    figs = {f["id"]: f for f in data.get("figures") or [] if f.get("id") and f.get("type") in FIG_TYPES}
    result = {"thumbnail": out / "thumbnail.png", "figures": {}}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": THUMB_SIZE[0], "height": THUMB_SIZE[1]})
        await page.set_content(thumbnail_html(data))
        await page.evaluate(FIT_JS)
        await page.locator("#card").screenshot(path=str(result["thumbnail"]))
        for fid in figure_ids(data["body_html"]):
            if fid not in figs or fid in result["figures"]:
                continue
            path = out / f"{fid}.png"
            await page.set_viewport_size({"width": FIG_WIDTH, "height": 800})
            await page.set_content(figure_html(figs[fid]))
            await page.locator("#card").screenshot(path=str(path))
            result["figures"][fid] = path
        await browser.close()
    return result


def split_body(html):
    """本文を [html, fig_id, html, fig_id, ...] に分ける（投稿時に図の位置で区切って貼るため）。"""
    return MARKER.split(html)


def embed_images(html, images):
    """プレビュー用に、図のマーカーを data URI の <img> に置き換える。"""
    def repl(m):
        path = images["figures"].get(m.group(1))
        if not path:
            return ""
        b64 = base64.b64encode(path.read_bytes()).decode()
        return f"<p><img src='data:image/png;base64,{b64}' style='width:100%;border-radius:8px'></p>"
    return MARKER.sub(repl, html)
