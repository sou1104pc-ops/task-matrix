"""メイン画像（サムネ）を ChatGPT（OpenAI の画像生成API）で作る。

サムネに載せる文字（タイトル）は Claude が記事と一緒に考え（data["thumbnail"]）、
ChatGPT にはその文字をそのまま入れたデザインを描いてもらう。
OpenAI は横長の 1536×1024 で返すので、Brain の推奨サイズ 1280×670 に中央を切り抜いて縮小する。
最後に、書き手のアイコン（アカウントの画像）を右下に丸く合成する（ChatGPTに描かせると別人になるため）。
OPENAI_API_KEY が無い・生成に失敗したときは、今までの HTML のサムネ（images.py）で代わりに作る。
"""
import asyncio
import base64
import io
import json
import urllib.error
import urllib.request

from PIL import Image, ImageDraw

from . import images
from .config import DATA, ROOT, OPENAI_API_KEY, OPENAI_IMAGE_MODEL, OPENAI_IMAGE_QUALITY, thumbnail_guide

GEN_SIZE = (1536, 1024)
ICON_SIZE, ICON_RING, ICON_MARGIN = 190, 8, 28
ENDPOINT = "https://api.openai.com/v1/images/generations"


class ThumbnailError(Exception):
    pass


def build_prompt(data):
    label, catch, sub = images.thumbnail_text(data)
    t = data.get("thumbnail") or {}
    lines = [s.strip().replace("【", "").replace("】", "") for s in catch.split("\n") if s.strip()]
    text = "\n".join(f"「{s}」" for s in lines)
    parts = [
        "Brain（日本の有料記事販売サイト）の記事のサムネイル画像を作ってください。横長のバナーです。",
        "",
        "# 画像に入れる文字（一字一句このまま。改行位置も守る。誤字・脱字・余計な文字は禁止）",
        f"メインの見出し（大きく太い日本語の文字）:\n{text}",
    ]
    if sub:
        parts.append(f"小さめの補足の一行: 「{sub}」")
    if label:
        parts.append(f"左上の小さなラベル: 「{label}」")
    badges = [str(b).strip() for b in t.get("badges") or [] if str(b).strip()]
    if badges:
        parts.append("訴求のタグ（それぞれ角丸の帯や吹き出しに入れる）: " + " / ".join(f"「{b}」" for b in badges[:5]))
    parts += [
        "",
        "# デザイン",
        f"- 雰囲気・モチーフ: {t.get('visual') or '記事のテーマに合う、清潔感のあるビジネス向けのイラスト'}",
        "- 文字は画像の上下の端から十分離し、縦方向の中央 70% の範囲に収める（上下は切り抜かれます）",
        "- スマホの一覧で小さく表示されても読めるよう、文字は大きく、背景とのコントラストを強く",
        "- 実在の人物・ロゴ・ブランド名・お金の札束は描かない。指定した文字以外の文字は入れない",
        "- 右下の角（横260px・縦260pxくらい）は、あとでアイコンを合成するので空けておく",
    ]
    design = thumbnail_guide("デザイン")
    if design:
        parts += ["", "# 運営者のデザインのルール（最優先で守る）", design]
    return "\n".join(parts)


def _request(prompt):
    body = json.dumps({
        "model": OPENAI_IMAGE_MODEL, "prompt": prompt, "size": f"{GEN_SIZE[0]}x{GEN_SIZE[1]}",
        "quality": OPENAI_IMAGE_QUALITY, "n": 1,
    }).encode()
    req = urllib.request.Request(ENDPOINT, data=body, headers={
        "Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        raise ThumbnailError(f"OpenAI がエラーを返しました（HTTP {e.code}）: {e.read().decode()[:300]}")
    except urllib.error.URLError as e:
        raise ThumbnailError(f"OpenAI に接続できませんでした: {e}")
    try:
        return base64.b64decode(data["data"][0]["b64_json"])
    except (KeyError, IndexError, TypeError):
        raise ThumbnailError(f"OpenAI の返答に画像がありません: {str(data)[:300]}")


def fit(png_bytes, size=images.THUMB_SIZE):
    """縦の中央を切り抜いて、size（1280×670）にする。"""
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    w, h = img.size
    ratio = size[0] / size[1]
    if w / h > ratio:
        nw = round(h * ratio)
        img = img.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
    else:
        nh = round(w / ratio)
        img = img.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
    out = io.BytesIO()
    img.resize(size, Image.LANCZOS).save(out, "PNG")
    return out.getvalue()


def icon_file(account):
    """そのアカウントのアイコン画像（保存済みなら）のパス。無ければ None。"""
    path = DATA / "icons" / f"{account['id']}.png"
    return path if path.exists() else None


def _download(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


async def ensure_icon(account, brain_image_url=None):
    """アイコンを data/icons/<id>.png に用意する。

    accounts.json の "icon"（画像のパスかURL）があればそれ、無ければ Brain のプロフィール画像（brain_image_url）。
    """
    path = DATA / "icons" / f"{account['id']}.png"
    src = account.get("icon") or brain_image_url
    if not src or (path.exists() and not account.get("icon")):
        return icon_file(account)
    path.parent.mkdir(parents=True, exist_ok=True)
    if src.startswith("http"):
        raw = await asyncio.to_thread(_download, src)
    else:
        local = ROOT / src if not src.startswith("/") else src
        raw = open(local, "rb").read()
    Image.open(io.BytesIO(raw)).convert("RGBA").save(path, "PNG")
    return path


def add_icon(thumb_path, icon_path):
    """サムネの右下に、白いふち付きの丸いアイコンを重ねる。"""
    base = Image.open(thumb_path).convert("RGBA")
    icon = Image.open(icon_path).convert("RGBA")
    side = min(icon.size)
    icon = icon.crop(((icon.width - side) // 2, (icon.height - side) // 2,
                      (icon.width + side) // 2, (icon.height + side) // 2)).resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
    mask = Image.new("L", (ICON_SIZE, ICON_SIZE), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, ICON_SIZE - 1, ICON_SIZE - 1), fill=255)
    outer = ICON_SIZE + ICON_RING * 2
    x, y = base.width - outer - ICON_MARGIN, base.height - outer - ICON_MARGIN
    ring = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ImageDraw.Draw(ring).ellipse((x, y, x + outer - 1, y + outer - 1), fill=(255, 255, 255, 255))
    base = Image.alpha_composite(base, ring)
    base.paste(icon, (x + ICON_RING, y + ICON_RING), mask)
    base.convert("RGB").save(thumb_path, "PNG")


async def make(draft_id, data, icon=None):
    """サムネを作って保存する。(作り方, エラー) を返す。作り方は "chatgpt" か "html"。

    icon はアイコン画像のパス（あれば右下に合成する）。
    """
    path = images.draft_dir(draft_id) / "thumbnail.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    how, error = "html", None
    if OPENAI_API_KEY:
        try:
            raw = await asyncio.to_thread(_request, build_prompt(data))
            path.write_bytes(fit(raw))
            how = "chatgpt"
        except ThumbnailError as e:
            error = str(e)
    else:
        error = "OPENAI_API_KEY が未設定です"
    if how == "html":
        await images.render_thumbnail(path, data)
    path.with_name("thumbnail_raw.png").write_bytes(path.read_bytes())  # アイコンを付ける前（アカウントを変えたとき用）
    if icon:
        add_icon(path, icon)
    return how, error


def apply_icon(draft_id, icon):
    """アイコンだけ付け直す（出すアカウントを変えたとき。ChatGPTには描き直させない）。"""
    path = images.draft_dir(draft_id) / "thumbnail.png"
    raw = path.with_name("thumbnail_raw.png")
    if not raw.exists():
        return
    path.write_bytes(raw.read_bytes())
    if icon:
        add_icon(path, icon)
