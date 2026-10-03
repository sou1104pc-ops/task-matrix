"""Playwright で KDP（Kindle ダイレクト・パブリッシング）の画面を操作して、Kindle 本を登録・出版する。

KDP には公式の登録APIがないため、Mac mini 上のブラウザ（ログイン状態を保存したプロファイル）を操作する。
※ Amazon は画面の自動操作を認めていないため、見つかると KDP アカウントが停止されるおそれがある（README 参照）。

初回だけ `python -m src.kdp_client login` でブラウザを開いて手動ログインする（2段階認証もここで）。
動作確認は `python -m src.kdp_client try <本の番号>`（全部入力して出版の直前で止まる。ブラウザは開いたまま）。
画面の場所は config/kdp_selectors.json。失敗した画面のスクショと入力欄の一覧が data/screenshots/ に残る。
"""
import asyncio
import re
import sys
from datetime import datetime

from playwright.async_api import TimeoutError as PlaywrightTimeout, async_playwright

from .config import (
    AUTHOR, BROWSER_PROFILE, JST, KDP_SELECT, KEYWORD_MAX_CHARS, PRICE_JPY, SCREENSHOTS_DIR, env, load_selectors,
)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
KDP_DRM = env("KDP_DRM", "true").lower() == "true"

# 画面上の入力欄・ボタンの一覧（セレクターを直すときの手がかり）
FIELDS_JS = """
() => [...document.querySelectorAll('input, select, textarea, button, iframe, [role=button]')]
  .filter(e => e.offsetParent !== null || e.type === 'file')
  .map(e => {
    const lab = e.id && document.querySelector(`label[for="${CSS.escape(e.id)}"]`);
    return [e.tagName.toLowerCase(), e.type || '', '#' + (e.id || ''), 'name=' + (e.name || ''),
            'label=' + ((lab && lab.innerText) || e.getAttribute('aria-label') || '').trim().slice(0, 40),
            'text=' + (e.innerText || e.value || '').trim().slice(0, 40)].join(' | ');
  }).join('\\n')
"""

SET_DESCRIPTION_JS = """
(html) => {
  if (window.CKEDITOR && Object.keys(CKEDITOR.instances).length) {
    for (const k in CKEDITOR.instances) { CKEDITOR.instances[k].setData(html); }
    return 'ckeditor';
  }
  const frame = document.querySelector('iframe.cke_wysiwyg_frame, iframe[title*="説明"], iframe[title*="Rich"]');
  if (frame && frame.contentDocument) { frame.contentDocument.body.innerHTML = html; return 'iframe'; }
  return '';
}
"""


class KdpError(Exception):
    pass


class NotLoggedIn(KdpError):
    pass


def _kdp_description(html):
    """KDP の紹介文で使えるタグ（b i u br p ul ol li h4〜h6）だけにする。"""
    html = re.sub(r"<\s*(/?)\s*strong\b[^>]*>", r"<\1b>", html or "")
    return re.sub(r"<(?!/?(b|i|u|br|p|ul|ol|li|h4|h5|h6)\b)[^>]+>", "", html)


class KdpClient:
    def __init__(self, headless=True, tag="kdp"):
        self.headless = headless
        self.sel = load_selectors()
        self.tag = tag
        self.log = []

    async def __aenter__(self):
        self._pw = await async_playwright().start()
        self.ctx = await self._pw.chromium.launch_persistent_context(
            str(BROWSER_PROFILE), headless=self.headless, locale="ja-JP", viewport={"width": 1280, "height": 1000},
            user_agent=USER_AGENT,
        )
        self.page = self.ctx.pages[0] if self.ctx.pages else await self.ctx.new_page()
        return self

    async def __aexit__(self, *exc):
        await self.ctx.close()
        await self._pw.stop()

    # ---------------------------------------------------------------- helpers
    async def snapshot(self, step):
        """スクショと入力欄の一覧を保存する。"""
        SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(JST).strftime("%Y%m%d-%H%M%S")
        base = SCREENSHOTS_DIR / f"{self.tag}-{stamp}-{step}"
        try:
            await self.page.screenshot(path=f"{base}.png", full_page=True)
            fields = await self.page.evaluate(FIELDS_JS)
            (base.parent / f"fields-{base.name}.txt").write_text(f"{self.page.url}\n{fields}", encoding="utf-8")
        except Exception:  # noqa: BLE001 - 記録に失敗しても本来のエラーを優先する
            pass
        return f"{base}.png"

    def _locator(self, cand):
        p = self.page
        if cand.startswith("label:"):
            return p.get_by_label(cand[6:], exact=False)
        if cand.startswith("role:"):
            _, role, name = cand.split(":", 2)
            return p.get_by_role(role, name=name)
        return p.locator(cand)

    async def find(self, candidates, timeout=4000, visible=True):
        """候補を上から試して、最初に見つかった要素を返す（無ければ None）。"""
        if isinstance(candidates, str):
            candidates = [candidates]
        per = max(500, timeout // max(1, len(candidates)))
        for cand in candidates:
            loc = self._locator(cand).first
            try:
                await loc.wait_for(state="visible" if visible else "attached", timeout=per)
                return loc
            except (PlaywrightTimeout, Exception):  # noqa: BLE001 - 次の候補を試す
                continue
        return None

    async def need(self, section, key, **kw):
        loc = await self.find(self.sel[section][key], **kw)
        if not loc:
            shot = await self.snapshot(f"{section}-{key}")
            raise KdpError(f"KDPの画面で「{section}.{key}」が見つかりません（スクショ: {shot}）")
        return loc

    async def fill(self, section, key, value, required=True):
        if not value:
            return
        loc = await self.find(self.sel[section][key])
        if not loc:
            if required:
                await self.need(section, key)
            self.log.append(f"⚠️ {key} の欄が見つからず空欄のままです")
            return
        await loc.fill(value)

    async def check(self, section, key, required=True):
        loc = await self.find(self.sel[section][key], visible=False)
        if not loc:
            if required:
                await self.need(section, key)
            self.log.append(f"⚠️ {key} が見つからず未設定です")
            return
        try:
            await loc.check(force=True)
        except Exception:  # noqa: BLE001 - チェックボックスでない（リンクやラベル）ならクリックする
            await loc.click(force=True)

    async def click(self, section, key):
        loc = await self.need(section, key)
        await loc.click()

    async def save_continue(self, section, next_url_part):
        await self.click(section, "save_continue")
        try:
            await self.page.wait_for_url(re.compile(next_url_part), timeout=60000)
        except PlaywrightTimeout:
            shot = await self.snapshot(f"{section}-save")
            raise KdpError(f"「保存して続行」の後に次の画面へ進めませんでした。入力エラーがないかスクショを確認してください: {shot}")
        await self.page.wait_for_load_state("domcontentloaded")

    # ---------------------------------------------------------------- steps
    async def ensure_login(self):
        await self.page.goto(self.sel["urls"]["bookshelf"], wait_until="domcontentloaded")
        if "signin" in self.page.url or "/ap/" in self.page.url:
            raise NotLoggedIn("KDP にログインしていません。Mac mini で `python -m src.kdp_client login` を実行してください")

    async def fill_details(self, data):
        d = "details"
        await self.page.goto(self.sel["urls"]["new_ebook"], wait_until="domcontentloaded")
        if "signin" in self.page.url:
            raise NotLoggedIn("KDP のログインが切れています。`python -m src.kdp_client login` で入り直してください")
        lang = await self.find(self.sel[d]["language"], timeout=3000)
        if lang and await lang.evaluate("e => e.tagName") == "SELECT":
            try:
                await lang.select_option(label=self.sel[d]["language_option"])
            except Exception:  # noqa: BLE001 - 既に日本語になっていれば問題ない
                self.log.append("⚠️ 言語を選べませんでした（既定のまま）")
        await self.fill(d, "title", data["title"])
        await self.fill(d, "title_kana", data.get("title_kana"), required=False)
        await self.fill(d, "subtitle", data.get("subtitle"), required=False)
        await self.fill(d, "subtitle_kana", data.get("subtitle_kana"), required=False)
        await self.fill(d, "author_last", AUTHOR["last"])
        await self.fill(d, "author_first", AUTHOR["first"], required=False)
        for k in ("last_kana", "first_kana", "last_romaji", "first_romaji"):
            await self.fill(d, f"author_{k}", AUTHOR[k], required=False)

        how = await self.page.evaluate(SET_DESCRIPTION_JS, _kdp_description(data["description_html"]))
        if not how:
            await self.fill(d, "description_textarea", _kdp_description(data["description_html"]))

        await self.check(d, "rights_own")
        await self.check(d, "adult_no", required=False)

        boxes = self.page.locator(", ".join(self.sel[d]["keyword_inputs"]))
        n = await boxes.count()
        for i, kw in enumerate([k.strip() for k in data.get("keywords") or [] if k.strip()][: min(7, n)]):
            box = boxes.nth(i)
            limit = await box.get_attribute("maxlength")
            await box.fill(kw[: int(limit) if limit and limit.isdigit() else KEYWORD_MAX_CHARS])
        if n == 0:
            self.log.append("⚠️ キーワード欄が見つかりませんでした")

        await self.set_categories(data.get("categories") or [])
        await self.snapshot("details")
        await self.save_continue(d, r"/content")

    async def set_categories(self, categories):
        """カテゴリーの選択画面で「親 > 子 > 孫」の順に文字をクリックしていく（KDPの画面変更に弱いので失敗しても止めない）。"""
        d = "details"
        btn = await self.find(self.sel[d]["categories_button"], timeout=3000)
        if not btn:
            self.log.append("⚠️ カテゴリーのボタンが見つからず未設定です（KDPで手で選んでください）")
            return
        await btn.click()
        await self.page.wait_for_timeout(1500)
        done = []
        for path in categories[:3]:
            parts = [p.strip() for p in re.split(r"[>＞/]", path) if p.strip()]
            try:
                for part in parts:
                    target = self.page.get_by_role("dialog").get_by_text(part, exact=True).first
                    if await target.count() == 0:
                        target = self.page.get_by_text(part, exact=True).first
                    await target.click(timeout=4000)
                    await self.page.wait_for_timeout(700)
                done.append(path)
            except Exception:  # noqa: BLE001 - そのカテゴリーは諦めて次へ
                self.log.append(f"⚠️ カテゴリー「{path}」を選べませんでした")
        await self.snapshot("categories")
        save = await self.find(self.sel[d]["categories_save"], timeout=3000)
        if save:
            await save.click()
            await self.page.wait_for_timeout(1000)
        if not done:
            self.log.append("⚠️ カテゴリーが1つも選べていません（KDPで手で選んでください）")

    async def upload(self, section, input_key, done_key, path, what, timeout=600000):
        inp = await self.find(self.sel[section][input_key], visible=False, timeout=8000)
        if not inp:
            await self.need(section, input_key, visible=False)
        await inp.set_input_files(str(path))
        if not await self.find(self.sel[section][done_key], timeout=timeout):
            shot = await self.snapshot(f"{section}-{done_key}")
            raise KdpError(f"{what}のアップロードが終わりませんでした（スクショ: {shot}）")

    async def fill_content(self, epub, cover_jpg):
        c = "content"
        await self.check(c, "drm_yes" if KDP_DRM else "drm_no", required=False)
        await self.upload(c, "manuscript_input", "manuscript_done", epub, "原稿")
        own = await self.find(self.sel[c]["cover_own"], timeout=4000)
        if own:
            await own.click()
        await self.upload(c, "cover_input", "cover_done", cover_jpg, "表紙", timeout=180000)
        await self.answer_ai()
        await self.snapshot("content")
        await self.save_continue(c, r"/pricing")

    async def answer_ai(self):
        """AI生成コンテンツの質問に正直に答える（文章は AI、表紙の絵は使っていない）。"""
        c = "content"
        yes = await self.find(self.sel[c]["ai_yes"], visible=False, timeout=3000)
        if not yes:
            self.log.append("⚠️ AI生成コンテンツの質問が見つかりませんでした（KDPで確認してください）")
            return
        await yes.check(force=True)
        await self.page.wait_for_timeout(500)
        for key, opt_key in (("ai_text_level", "ai_text_option"), ("ai_image_level", "ai_image_option")):
            sel = await self.find(self.sel[c][key], timeout=2000)
            if not sel:
                self.log.append(f"⚠️ {key} の欄が見つかりませんでした")
                continue
            labels = await sel.evaluate("e => [...e.options].map(o => o.label)")
            match = next((l for l in labels if self.sel[opt_key] in l), None)
            if match:
                await sel.select_option(label=match)
            else:
                self.log.append(f"⚠️ {key} に「{self.sel[opt_key]}」を含む選択肢がありません: {labels}")
        await self.fill(c, "ai_text_tool", "Claude", required=False)

    async def fill_pricing(self, publish):
        p = "pricing"
        if KDP_SELECT:
            await self.check(p, "select_checkbox", required=False)
        await self.check(p, "territory_worldwide", required=False)
        await self.check(p, "royalty_70", required=False)
        await self.fill(p, "price_jp", str(PRICE_JPY), required=False)
        await self.page.wait_for_timeout(1000)
        await self.snapshot("pricing")
        if publish is None:  # try: 押さずに止める
            return "出版の直前で止めました"
        await self.click(p, "publish" if publish else "save_draft")
        await self.page.wait_for_timeout(5000)
        shot = await self.snapshot("published" if publish else "saved")
        return f"{'出版を申請しました' if publish else '下書き保存しました'}（スクショ: {shot}）"

    async def create(self, data, epub, cover_jpg, publish=False):
        """本を登録する。publish=True で出版申請、False で KDP の下書き保存、None で入力だけして止める。"""
        await self.ensure_login()
        await self.fill_details(data)
        await self.fill_content(epub, cover_jpg)
        return await self.fill_pricing(publish)


# ---------------------------------------------------------------- CLI
async def _login():
    async with KdpClient(headless=False) as kc:
        await kc.page.goto(kc.sel["urls"]["bookshelf"])
        print("ブラウザで KDP にログインしてください（2段階認証も済ませる）。本棚が表示されたらここで Enter を押します。")
        await asyncio.to_thread(input)
        await kc.ensure_login()
        print("ログイン状態を保存しました")


async def _dump():
    async with KdpClient(headless=False, tag="dump") as kc:
        await kc.ensure_login()
        await kc.page.goto(kc.sel["urls"]["new_ebook"])
        print("KDPの画面を開きました。調べたい画面まで手で進めて Enter を押すと、その画面の入力欄の一覧を保存します（終わりは q）。")
        while (await asyncio.to_thread(input)).strip() != "q":
            print("保存しました:", await kc.snapshot("dump"))


async def _try(book_id):
    from . import storage
    from .bot import build_files

    storage.init()
    book = storage.get_book(book_id)
    if not book:
        sys.exit(f"本 #{book_id} がありません")
    epub, cover_jpg, _ = await build_files(book_id, book["data"])
    async with KdpClient(headless=False, tag=f"try{book_id}") as kc:
        try:
            print(await kc.create(book["data"], epub, cover_jpg, publish=None))
        except KdpError as e:
            print("⚠️", e)
        print("\n".join(kc.log) or "（注意点なし）")
        print("ブラウザで入力内容を確認してください。Enter でブラウザを閉じます（出版はされません）。")
        await asyncio.to_thread(input)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "login":
        asyncio.run(_login())
    elif cmd == "dump":
        asyncio.run(_dump())
    elif cmd == "try" and len(sys.argv) > 2:
        asyncio.run(_try(int(sys.argv[2])))
    else:
        print("使い方: python -m src.kdp_client login | dump | try <本の番号>")


if __name__ == "__main__":
    main()
