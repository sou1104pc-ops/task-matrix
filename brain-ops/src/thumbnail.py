"""メイン画像（サムネ）を ChatGPT（OpenAI の画像生成API）で作る。

サムネに載せる文字（タイトル）は Claude が記事と一緒に考え（data["thumbnail"]）、
ChatGPT にはその文字をそのまま入れたデザインを描いてもらう。
OpenAI は横長の 1536×1024 で返すので、Brain の推奨サイズ 1280×670 に中央を切り抜いて縮小する。
OPENAI_API_KEY が無い・生成に失敗したときは、今までの HTML のサムネ（images.py）で代わりに作る。
"""
import asyncio
import base64
import io
import json
import urllib.error
import urllib.request

from PIL import Image

from . import images
from .config import OPENAI_API_KEY, OPENAI_IMAGE_MODEL, OPENAI_IMAGE_QUALITY, thumbnail_guide

GEN_SIZE = (1536, 1024)
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
    parts += [
        "",
        "# デザイン",
        f"- 雰囲気・モチーフ: {t.get('visual') or '記事のテーマに合う、清潔感のあるビジネス向けのイラスト'}",
        "- 文字は画像の上下の端から十分離し、縦方向の中央 70% の範囲に収める（上下は切り抜かれます）",
        "- スマホの一覧で小さく表示されても読めるよう、文字は大きく、背景とのコントラストを強く",
        "- 実在の人物・ロゴ・ブランド名・お金の札束・誇大な煽り文句は描かない",
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


async def make(draft_id, data):
    """サムネを作って保存する。(作り方, エラー) を返す。作り方は "chatgpt" か "html"。"""
    path = images.draft_dir(draft_id) / "thumbnail.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    if OPENAI_API_KEY:
        try:
            raw = await asyncio.to_thread(_request, build_prompt(data))
            path.write_bytes(fit(raw))
            return "chatgpt", None
        except ThumbnailError as e:
            error = str(e)
    else:
        error = "OPENAI_API_KEY が未設定です"
    await images.render_thumbnail(path, data)
    return "html", error
