"""Playwright で note のエディタを操作して投稿・更新する。

note には公式の投稿APIがないため、Mac mini 上のブラウザ（ログイン状態を保存したプロファイル）を操作する。
初回だけ `python -m src.note_client login` でブラウザを開いて手動ログインする。
動作確認は `python -m src.note_client test`（下書き保存のみで公開はしない）。
"""
import asyncio
import re
import sys
from datetime import datetime
from difflib import SequenceMatcher

from playwright.async_api import async_playwright

from .config import BROWSER_PROFILE, JST, NOTE_USER, SCREENSHOTS_DIR, load_selectors

PASTE_JS = """
([el, html]) => {
  el.focus();
  const dt = new DataTransfer();
  dt.setData('text/html', html);
  dt.setData('text/plain', html.replace(/<[^>]+>/g, ''));
  el.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true}));
}
"""


# エディタ内の全テキスト（太字やリンクをまたいで連結したもの）を扱う
FIND_TEXT_JS = """
([root, needle, nth]) => {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let full = '';
  while (walker.nextNode()) full += walker.currentNode.data;
  let idx = -1;
  for (let i = 0; i <= nth; i++) {
    idx = full.indexOf(needle, idx + 1);
    if (idx < 0) return -1;
  }
  return idx;
}
"""

SELECT_RANGE_JS = """
([root, a, b]) => {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes = []; let len = 0;
  while (walker.nextNode()) { nodes.push([walker.currentNode, len]); len += walker.currentNode.data.length; }
  const locate = (pos, isEnd) => {
    for (const [n, start] of nodes) {
      const l = n.data.length;
      if (pos >= start && (isEnd ? pos <= start + l : pos < start + l)) return [n, pos - start];
    }
    return null;
  };
  const s = locate(a, a === b), e = locate(b, true);
  if (!s || !e) return false;
  const r = document.createRange(); r.setStart(s[0], s[1]); r.setEnd(e[0], e[1]);
  const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(r);
  return true;
}
"""

SELECT_START_JS = """
(root) => {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const first = walker.nextNode();
  if (!first) return false;
  const r = document.createRange(); r.setStart(first, 0); r.collapse(true);
  const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(r);
  return true;
}
"""


def diff_ops(old, new):
    """old を new にするための (開始, 終了, 挿入文字列) を後ろから順に返す。
    変わらない部分（リンクや太字の文字）は触らないので書式が残る。"""
    if not new:
        return [(0, len(old), "")]
    sm = SequenceMatcher(None, old, new, autojunk=False)
    ops = [(i1, i2, new[j1:j2]) for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal"]
    return list(reversed(ops))

SELECT_END_JS = """
(root) => {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let last = null;
  while (walker.nextNode()) last = walker.currentNode;
  if (!last) return false;
  const r = document.createRange(); r.setStart(last, last.data.length); r.collapse(true);
  const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(r);
  return true;
}
"""


class NoteError(Exception):
    pass


class NotLoggedIn(NoteError):
    pass


class NoteClient:
    def __init__(self, headless=True):
        self.headless = headless
        self.sel = load_selectors()

    async def __aenter__(self):
        self._pw = await async_playwright().start()
        self.ctx = await self._pw.chromium.launch_persistent_context(
            str(BROWSER_PROFILE), headless=self.headless, locale="ja-JP", viewport={"width": 1280, "height": 900}
        )
        self.page = self.ctx.pages[0] if self.ctx.pages else await self.ctx.new_page()
        return self

    async def __aexit__(self, *exc):
        await self.ctx.close()
        await self._pw.stop()

    # ---- helpers ----
    async def _find(self, name, timeout=15000):
        candidates = self.sel[name]
        deadline = asyncio.get_event_loop().time() + timeout / 1000
        while asyncio.get_event_loop().time() < deadline:
            for css in candidates:
                loc = self.page.locator(css).first
                if await loc.count() and await loc.is_visible():
                    return loc
            await asyncio.sleep(0.5)
        raise NoteError(f"画面上に「{name}」が見つかりません（config/note_selectors.json を確認）")

    async def _screenshot(self, label):
        SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        path = SCREENSHOTS_DIR / f"{datetime.now(JST):%Y%m%d-%H%M%S}-{label}.png"
        await self.page.screenshot(path=str(path), full_page=True)
        return path

    def _check_login(self):
        if self.sel["login_url_part"] in self.page.url:
            raise NotLoggedIn("noteにログインしていません。`python -m src.note_client login` を実行してください")

    def _key_from_url(self):
        m = re.search(r"/notes/(n[0-9a-f]+)", self.page.url)
        return m.group(1) if m else None

    async def _set_title(self, title):
        loc = await self._find("title")
        await loc.click()
        await loc.press("ControlOrMeta+A")
        await loc.fill(title)

    async def _set_body(self, html, replace):
        loc = await self._find("body")
        await loc.click()
        if replace:
            await self.page.keyboard.press("ControlOrMeta+A")
            await self.page.keyboard.press("Delete")
        await self.page.evaluate(PASTE_JS, [await loc.element_handle(), html])
        await asyncio.sleep(2)
        expected = len(re.sub(r"<[^>]+>", "", html))
        actual = len(await loc.inner_text())
        if actual < expected * 0.8:
            raise NoteError(f"本文の貼り付けが不完全です（{actual}/{expected}字）")

    async def _set_hashtags(self, hashtags, clear):
        try:
            inp = await self._find("hashtag_input", timeout=8000)
        except NoteError:
            return False
        if clear:
            for css in self.sel["hashtag_remove"]:
                btns = self.page.locator(css)
                while await btns.count():
                    await btns.first.click()
                    await asyncio.sleep(0.2)
        for tag in hashtags:
            await inp.fill(tag.lstrip("#"))
            await inp.press("Enter")
            await asyncio.sleep(0.3)
        return True

    # ---- public API ----
    async def open_login(self):
        await self.page.goto("https://note.com/login")
        print("ブラウザでnoteにログインしてください。ログインできたらこの画面で Enter を押します。")
        await asyncio.get_event_loop().run_in_executor(None, sys.stdin.readline)

    async def stats(self, period="all", max_pages=20):
        """ダッシュボードの閲覧数(PV)を取る。

        公開API（/api/v2/creators/.../contents）にはPVが無いため、ログインが要る統計APIを使う。
        period は all / day / week / month / year。day は「今日」。
        戻り値: {total_pv, total_like, total_comment, start_date_str, end_date_str, last_calculate_at, notes}
        notes の各要素は {id, name, read_count（PV）, like_count, comment_count}。
        """
        try:
            await self.page.goto("https://note.com/")
            totals, notes, page_no = None, [], 1
            while page_no <= max_pages:
                r = await self.page.request.get(
                    f"https://note.com/api/v1/stats/pv?filter={period}&page={page_no}&sort=pv"
                )
                if r.status in (401, 403):
                    raise NotLoggedIn(
                        "noteにログインしていません。`python -m src.note_client login` を実行してください"
                    )
                if r.status != 200:
                    raise NoteError(f"note統計の取得に失敗しました（HTTP {r.status} / filter={period}）")
                d = (await r.json())["data"]
                if totals is None:
                    totals = {
                        k: d.get(k) for k in
                        ("total_pv", "total_like", "total_comment",
                         "start_date_str", "end_date_str", "last_calculate_at")
                    }
                notes += d.get("note_stats") or []
                if d.get("last_page"):
                    break
                page_no += 1
            return {**(totals or {}), "notes": notes}
        except (NotLoggedIn, NoteError):
            raise
        except Exception as e:
            raise NoteError(f"note統計の取得に失敗しました: {e}") from e

    async def create(self, title, html, hashtags, publish=True):
        """新規記事を作る。publish=False なら下書き保存で止める。記事URLを返す。"""
        try:
            await self.page.goto(self.sel["new_note_url"])
            await self.page.wait_for_load_state("networkidle")
            self._check_login()
            await self._set_title(title)
            await self._set_body(html, replace=False)
            key = self._key_from_url()
            if not publish:
                await (await self._find("save_draft")).click()
                await asyncio.sleep(3)
                return f"https://editor.note.com/notes/{key}/edit/"
            await (await self._find("go_publish")).click()
            await asyncio.sleep(2)
            await self._set_hashtags(hashtags, clear=False)
            await (await self._find("publish")).click()
            await asyncio.sleep(5)
            key = key or self._key_from_url()
            return f"https://note.com/{NOTE_USER}/n/{key}"
        except NotLoggedIn:
            raise
        except Exception as e:
            shot = await self._screenshot("create-failed")
            raise NoteError(f"{e}（スクリーンショット: {shot}）") from e

    async def edit_in_place(self, key, edits, insert_top=None, title=None, hashtags=None, append_html=None, save=True):
        """既存記事を部分修正する（画像やリンクカードは触らない）。

        edits: [(old_text, new_text, remove_block, nth)]。nth は同じ文の何番目の出現か（0始まり）。old_text と new_text の差分だけを選択して打ち替える
        （リンクや太字を壊さないため）。見つからなかった old_text を返す。
        save=False なら更新ボタンを押さずに止める（動作確認用）。
        """
        try:
            await self.page.goto(self.sel["edit_url"].format(key=key))
            await self.page.wait_for_load_state("networkidle")
            self._check_login()
            body = await self._find("body")
            handle = await body.element_handle()
            await body.click()
            full_text = await body.inner_text()
            if insert_top and insert_top not in full_text:
                if await self.page.evaluate(SELECT_START_JS, handle):
                    await asyncio.sleep(0.3)
                    await self.page.keyboard.insert_text(insert_top)
                    await self.page.keyboard.press("Enter")
            not_found = []
            for old, new, remove_block, nth in edits:
                found = await self.page.evaluate(FIND_TEXT_JS, [handle, old, nth])
                if found < 0:
                    not_found.append(old)
                    continue
                for a, b, insert in diff_ops(old, new):
                    await self.page.evaluate(SELECT_RANGE_JS, [handle, found + a, found + b])
                    await asyncio.sleep(0.2)
                    if insert:
                        await self.page.keyboard.insert_text(insert)
                    else:
                        await self.page.keyboard.press("Backspace")
                if remove_block:
                    await self.page.keyboard.press("Backspace")
                await asyncio.sleep(0.3)
            if append_html:
                await self.page.evaluate(SELECT_END_JS, handle)
                await self.page.keyboard.press("Enter")
                await self.page.evaluate(PASTE_JS, [handle, append_html])
                await asyncio.sleep(1)
                first_line = re.sub(r"<[^>]+>", "\n", append_html).strip().split("\n")[0]
                if first_line not in await body.inner_text():
                    not_found.append(f"末尾への追記（手動で追加してください）: {first_line}")
            if title:
                await self._set_title(title)
            await asyncio.sleep(2)
            if not save:
                await self._screenshot(f"preview-{key}")
                return not_found
            await (await self._find("go_publish")).click()
            await asyncio.sleep(2)
            if hashtags:
                await self._set_hashtags(hashtags, clear=True)
            await (await self._find("update")).click()
            await asyncio.sleep(5)
            return not_found
        except NotLoggedIn:
            raise
        except Exception as e:
            shot = await self._screenshot(f"edit-{key}-failed")
            raise NoteError(f"{e}（スクリーンショット: {shot}）") from e

    async def update(self, key, title, html, hashtags):
        """既存記事の本文・タイトル・ハッシュタグを差し替えて更新する。"""
        try:
            await self.page.goto(self.sel["edit_url"].format(key=key))
            await self.page.wait_for_load_state("networkidle")
            self._check_login()
            if title:
                await self._set_title(title)
            await self._set_body(html, replace=True)
            await (await self._find("go_publish")).click()
            await asyncio.sleep(2)
            if hashtags:
                await self._set_hashtags(hashtags, clear=True)
            await (await self._find("update")).click()
            await asyncio.sleep(5)
            return f"https://note.com/{NOTE_USER}/n/{key}"
        except NotLoggedIn:
            raise
        except Exception as e:
            shot = await self._screenshot(f"update-{key}-failed")
            raise NoteError(f"{e}（スクリーンショット: {shot}）") from e


async def _main(cmd):
    if cmd == "login":
        async with NoteClient(headless=False) as nc:
            await nc.open_login()
        print("ログイン状態を保存しました。")
    elif cmd == "test":
        html = "<p>※本記事はアフィリエイト広告（PR）を含みます。</p><h2>テスト見出し</h2><p>自動投稿のテストです。この下書きは削除してください。</p>"
        async with NoteClient(headless=False) as nc:
            url = await nc.create("【テスト】自動投稿の動作確認", html, ["#テスト"], publish=False)
        print("下書きを保存しました:", url)
    else:
        print("使い方: python -m src.note_client [login|test]")


if __name__ == "__main__":
    asyncio.run(_main(sys.argv[1] if len(sys.argv) > 1 else ""))
