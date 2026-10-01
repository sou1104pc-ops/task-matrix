"""Brain（brain-market.com）に記事を投稿する。

Brain の画面は裏で api.brain-market.com を呼んでいるので、画面のボタンは押さずに同じAPIを直接呼ぶ
（画面の見た目が変わっても壊れにくい）。ログイン情報は、Playwright のブラウザに保存したログイン状態
（Cookie `_brain-market-v2` に入っているトークン）をそのまま使う。

初回だけ `python -m src.brain_client login` でブラウザを開いて手動ログインする。
動作確認は `python -m src.brain_client test`（Brainに下書きを1本保存するだけで、公開申請はしない）。

投稿の流れ（画面の［下書き保存］→［販売設定に進む］→［有料エリアの設定に進む］→［公開申請］と同じ）:
  POST  /v2/articles/draft            下書きを作る → 記事ID
  POST  /v2/articles/{id}/eyecatch    メイン画像
  POST  /v2/items                     本文の画像 → URL
  PATCH /v2/articles/{id}/draft       タイトルと本文を保存
  POST  /v2/articles/{id}/publish     価格・カテゴリ・有料ラインを付けて公開申請（Brainの審査後に公開）
"""
import asyncio
import json
import re
import sys
from urllib.parse import quote, unquote

from playwright.async_api import async_playwright

from . import images
from .config import BROWSER_PROFILE

SITE = "https://brain-market.com"
API = "https://api.brain-market.com"
COOKIE = "_brain-market-v2"
PAYWALL = re.compile(r"<p>\s*\[\[PAYWALL\]\]\s*</p>")
PAY_LINE = "<pay-article-line />"
VOID_TAGS = {"br", "img", "hr"}
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)


class BrainError(Exception):
    pass


class NotLoggedIn(BrainError):
    pass


# ---------------------------------------------------------------- 本文の変換
def split_blocks(html):
    """本文HTMLを一番外側の要素ごとに分ける（Brainは本文を「行」の並びとして扱い、有料ラインは行の間に入る）。"""
    blocks, depth, start = [], 0, None
    for m in re.finditer(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)[^>]*?(/?)>", html):
        closing, tag, selfclose = m.group(1), m.group(2).lower(), m.group(3)
        if depth == 0 and not closing:
            start = m.start()
        if tag in VOID_TAGS or selfclose:
            if depth == 0:
                blocks.append(html[start:m.end()])
            continue
        depth += -1 if closing else 1
        if depth == 0 and start is not None:
            blocks.append(html[start:m.end()])
            start = None
    return [b for b in blocks if b.strip()]


def brain_blocks(body_html, image_urls):
    """生成した本文を Brain の形式の行リストにする。(行リスト, 有料ラインの位置) を返す。

    - <p>[[FIG:id]]</p> → アップロード済みの <img>
    - <p>[[PAYWALL]]</p> → 有料ラインの位置（その行より前が無料部分）
    - <h2> <h3> は Brain のエディタと同じ class と番号を付ける（目次に使われる）
    """
    blocks, pay_index, h2, h3 = [], None, 0, 0
    for b in split_blocks(body_html):
        if PAYWALL.fullmatch(b.strip()):
            pay_index = len(blocks)
            continue
        fig = images.MARKER.fullmatch(b.strip())
        if fig:
            url = image_urls.get(fig.group(1))
            if url:
                blocks.append(f'<img src="{url}">')
            continue
        m = re.match(r"<h([23])[^>]*>(.*)</h\1>$", b.strip(), re.S)
        if m and m.group(1) == "2":
            h2, h3 = h2 + 1, 0
            b = f'<h2 class="heading-h2" data-number="{h2}">{m.group(2)}</h2>'
        elif m:
            h3 += 1
            b = f'<h3 class="heading-h3" data-parent="{h2}" data-number="{h3}">{m.group(2)}</h3>'
        blocks.append(b)
    return blocks, pay_index


def _encode_cookie(value):
    return quote(json.dumps(value, ensure_ascii=False, separators=(",", ":")), safe="!'()*-._~")


# ---------------------------------------------------------------- client
class BrainClient:
    def __init__(self, headless=True):
        self.headless = headless
        self._auth = None

    async def __aenter__(self):
        self._pw = await async_playwright().start()
        self.ctx = await self._pw.chromium.launch_persistent_context(
            str(BROWSER_PROFILE), headless=self.headless, locale="ja-JP", viewport={"width": 1280, "height": 900},
            user_agent=USER_AGENT,
        )
        self.page = self.ctx.pages[0] if self.ctx.pages else await self.ctx.new_page()
        return self

    async def __aexit__(self, *exc):
        await self.ctx.close()
        await self._pw.stop()

    # ---- ログイン情報 ----
    async def _cookie(self):
        for c in await self.ctx.cookies(SITE):
            if c["name"] == COOKIE:
                return c
        return None

    async def _headers(self):
        if self._auth is None:
            c = await self._cookie()
            try:
                v = json.loads(unquote(c["value"])) if c else {}
            except json.JSONDecodeError:
                v = {}
            h = v.get("headers") or {}
            if not v.get("isSignedIn") or not h.get("access-token"):
                raise NotLoggedIn("Brainにログインしていません。`python -m src.brain_client login` を実行してください")
            self._auth = {k: h[k] for k in ("access-token", "client", "uid") if h.get(k)}
        return {**self._auth, "Accept": "application/json", "Origin": SITE, "Referer": f"{SITE}/"}

    async def _save_new_token(self, resp):
        """トークンが更新されて返ってきたら、ブラウザ側のCookieにも書き戻す（ログインが切れないように）。"""
        token = resp.headers.get("access-token")
        if not token or token == self._auth.get("access-token"):
            return
        self._auth["access-token"] = token
        for k in ("client", "uid"):
            if resp.headers.get(k):
                self._auth[k] = resp.headers[k]
        c = await self._cookie()
        if not c:
            return
        try:
            v = json.loads(unquote(c["value"]))
        except json.JSONDecodeError:
            return
        v["headers"] = dict(self._auth)
        c["value"] = _encode_cookie(v)
        await self.ctx.add_cookies([c])

    async def api(self, method, path, params=None, body=None, multipart=None):
        kw = {"method": method, "headers": await self._headers(), "timeout": 60000}
        if params:
            kw["params"] = params
        if body is not None:
            kw["data"] = body
        if multipart is not None:
            kw["multipart"] = multipart
        resp = await self.ctx.request.fetch(f"{API}{path}", **kw)
        await self._save_new_token(resp)
        text = await resp.text()
        try:
            data = json.loads(text) if text else {}
        except json.JSONDecodeError:
            data = {"raw": text[:300]}
        if resp.status == 401:
            raise NotLoggedIn("Brainのログインが切れています。`python -m src.brain_client login` をやり直してください")
        if resp.status >= 400:
            msgs = data.get("error_messages") or data.get("errors") or data
            raise BrainError(f"{method} {path} が失敗しました（HTTP {resp.status}）: {msgs}")
        return data.get("data", data) if isinstance(data, dict) else data

    # ---- public API ----
    async def open_login(self):
        await self.page.goto(SITE)
        print("ブラウザでBrainにログインしてください。ログインできたらこの画面で Enter を押します。")
        await asyncio.get_event_loop().run_in_executor(None, sys.stdin.readline)

    async def current_user(self):
        return await self.api("GET", "/v2/users/current")

    async def categories(self):
        return await self.api("GET", "/v2/categories")

    async def my_articles(self):
        return await self.api("GET", "/v2/current/articles")

    async def total_sales(self):
        return await self.api("GET", "/v2/sales/total_sales")

    async def sales_histories(self, month, page=1):
        """month は 'YYYY/MM'。（"cnannel_type" はBrain側の綴りのまま）"""
        return await self.api("GET", "/v2/sales/sales_histories",
                              params={"month": month, "cnannel_type": "all", "currency": "JPY", "page": page})

    async def _upload(self, path, endpoint):
        data = await self.api("POST", endpoint, multipart={
            "image": {"name": path.name, "mimeType": "image/png", "buffer": path.read_bytes()},
        })
        url = data.get("url") if isinstance(data, dict) else None
        if not url:
            raise BrainError(f"画像のアップロード結果にURLがありません: {path.name}")
        return url

    async def check_categories(self, category, subcategory):
        cats = {c["name"]: [s["name"] for s in c.get("subcategories") or []] for c in await self.categories()}
        if category not in cats:
            raise BrainError(f"カテゴリー「{category}」はBrainにありません。使えるもの: {list(cats)}")
        if subcategory and subcategory not in cats[category]:
            raise BrainError(f"「{category}」にサブカテゴリー「{subcategory}」はありません。使えるもの: {cats[category]}")

    async def create(self, data, thumbnail, figures, publish=True, affiliate_rate=0):
        """記事を作る。publish=False なら Brain の下書き保存で止める。

        data には title / body_html / price / category / subcategory が入っている。
        figures は {図id: PNG}。戻り値は (記事ID, URL)。
        """
        _, pay_index = brain_blocks(data["body_html"], {k: "-" for k in figures})
        if publish and not pay_index:
            raise BrainError("本文に有料ライン（[[PAYWALL]]）が無いか、先頭にあります")
        if publish:
            await self.check_categories(data["category"], data.get("subcategory"))
        draft = await self.api("POST", "/v2/articles/draft")
        article_id = str(draft["id"])
        await self._upload(thumbnail, f"/v2/articles/{article_id}/eyecatch")
        urls = {fid: await self._upload(path, "/v2/items") for fid, path in figures.items()}
        blocks, pay_index = brain_blocks(data["body_html"], urls)
        await self.api("PATCH", f"/v2/articles/{article_id}/draft",
                       body={"article": {"title": data["title"], "body": "".join(blocks)}})
        if not publish:
            return article_id, f"{SITE}/a/{article_id}/edit"
        limit = await self.api("GET", f"/v2/articles/{article_id}/publish_rate_limit")
        if isinstance(limit, dict) and limit.get("is_allowed") is False:
            raise BrainError(f"公開申請の回数制限にかかっています（下書きは保存済み: {SITE}/a/{article_id}/edit）: {limit}")
        body = blocks[:pay_index] + [PAY_LINE] + blocks[pay_index:]
        await self.api("POST", f"/v2/articles/{article_id}/publish", body={
            "price": int(data["price"]),
            "sales_count": 0,
            "is_sales_unlimited": True,
            "affiliate_rate": affiliate_rate,
            "affiliatable_only_purchaser": False,
            "category": data["category"],
            "subcategory": data.get("subcategory") or None,
            "reserve_publish_type": "immediate",  # 審査がおり次第すぐ公開
            "published_at": None,
            "tags": [],
            "body": "".join(body),
            "review_rewards": [],
            "review_reward_receiving_method": None,
            "recommendation_rewards": [],
            "recommendation_reward_receiving_method": None,
        })
        user = await self.current_user()
        account = user.get("account") if isinstance(user, dict) else None
        url = f"{SITE}/u/{account}/a/{article_id}" if account else f"{SITE}/a/{article_id}/edit"
        return article_id, url


async def _main(cmd):
    if cmd == "login":
        async with BrainClient(headless=False) as bc:
            await bc.open_login()
            user = await bc.current_user()
        print("ログイン状態を保存しました:", user.get("account") or user.get("name") or user)
    elif cmd == "test":
        from pathlib import Path
        from tempfile import TemporaryDirectory
        data = {"title": "【テスト】自動投稿の動作確認", "body_html": (
            "<p>自動投稿のテストです。この下書きは削除してください。</p><p>[[PAYWALL]]</p><h2>有料部分</h2><p>テスト</p>")}
        with TemporaryDirectory() as tmp:
            imgs = await images.render_to(Path(tmp), {"title": data["title"], "body_html": "", "figures": []})
            async with BrainClient(headless=True) as bc:
                aid, url = await bc.create(data, imgs["thumbnail"], {}, publish=False)
        print("Brainに下書きを保存しました（公開申請はしていません）:", url)
    elif cmd in ("sales", "articles", "categories", "me"):
        async with BrainClient(headless=True) as bc:
            from datetime import datetime
            fn = {"sales": lambda: bc.sales_histories(datetime.now().strftime("%Y/%m")),
                  "articles": bc.my_articles, "categories": bc.categories, "me": bc.current_user}[cmd]
            out = await fn()
            if cmd == "sales":
                out = {"total_sales": await bc.total_sales(), "sales_histories": out}
        print(json.dumps(out, ensure_ascii=False, indent=2)[:6000])
    else:
        print("使い方: python -m src.brain_client [login|test|me|categories|articles|sales]")


if __name__ == "__main__":
    asyncio.run(_main(sys.argv[1] if len(sys.argv) > 1 else ""))
