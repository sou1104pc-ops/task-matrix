"""出版前の自動チェック。error があると承認ボタンでは出版できない。"""
import re
from html import unescape

from .config import AUTHOR, KEYWORD_MAX_CHARS

OUTCOME = r"(成功|稼げ|稼ぐ|儲か|痩せ|治|合格|受か|年収(が|を)?(上|アップ)|上が|お金持ち|月\d+万)"
EXAGGERATION = [
    rf"(必ず|絶対(に)?|確実に|間違いなく|100[%％](の確率で)?|誰でも)[^。]{{0,8}}{OUTCOME}",
    r"業界(No\.?1|ナンバーワン|最大級)",
]
FAKE_EXPERIENCE = [
    r"(僕|私|わたし|筆者|著者)(自身)?(も|が|は)?[^。]{0,20}(稼ぎ|稼い|万円|達成し|成功し|フォロワー[^。]{0,6}(人|名))",
    r"(読者|受講生|クライアント)(さん)?(の|から)(声|感想|口コミ)",
]
# KDP のキーワード欄で禁止されている言葉（他の著者名・本のタイトルは機械では見分けられないので Claude に任せる）
BANNED_KEYWORDS = ["kindle unlimited", "kindleunlimited", "無料", "ベストセラー", "best seller", "bestseller",
                   "新刊", "セール", "amazon", "kdp", "キンドル アンリミテッド", "読み放題"]
STAT_PATTERN = r"(約?\d[\d,，.]*\s*(万人|人|名|社|件)|\d[\d.]*\s*[%％])"
DESC_TAGS = {"p", "b", "br", "ul", "li"}
BODY_TAGS = {"h2", "h3", "p", "ul", "ol", "li", "b", "br"}


def text_of(html):
    return unescape(re.sub(r"<[^>]+>", "", html or ""))


def body_html(data):
    return "\n".join([data.get("intro_html") or ""] + [c.get("html") or "" for c in data["chapters"]]
                     + [data.get("outro_html") or ""])


def body_chars(data):
    return len(text_of("\n".join(c.get("html") or "" for c in data["chapters"])))


def cover_text(data):
    c = data.get("cover") or {}
    return "\n".join([c.get("top") or "", c.get("catch") or "", c.get("sub") or "", c.get("badge") or ""]
                     + list(c.get("points") or []))


def check_title(data):
    issues = []
    title = data["title"]
    kw = (data.get("main_keyword") or "").strip()
    if not kw:
        issues.append(("warn", "検索キーワード（main_keyword）が決まっていません"))
    else:
        first = kw.split()[0]
        if first not in title:
            issues.append(("error", f"タイトルに検索キーワード「{first}」が入っていません"))
        elif title.find(first) > 6:
            issues.append(("warn", f"検索キーワード「{first}」がタイトルの前のほうにありません"))
        if first not in (data.get("cover") or {}).get("catch", "").replace("【", "").replace("】", "").replace("\n", ""):
            issues.append(("warn", f"表紙の大きな文字に検索キーワード「{first}」が入っていません"))
    if re.search(r"20\d\d年", title + data.get("subtitle", "")):
        issues.append(("warn", "タイトルに年号があります（古く見えるようになります）"))
    if len(title) + len(data.get("subtitle", "")) > 190:
        issues.append(("error", "タイトルとサブタイトルの合計が200字近くあります（KDPの上限）"))
    for k in ("title_kana", "subtitle_kana"):
        v = data.get(k) or ""
        if (k == "title_kana" or data.get("subtitle")) and not v:
            issues.append(("error", f"{'タイトル' if k == 'title_kana' else 'サブタイトル'}の読み（カタカナ）がありません"))
        elif v and re.search(r"[^゠-ヿ\s・ー　]", v):
            issues.append(("warn", f"読みにカタカナ以外の文字があります: {v[:30]}"))
    return issues


def check_keywords(data):
    issues = []
    kws = [k.strip() for k in data.get("keywords") or [] if k.strip()]
    if len(kws) < 7:
        issues.append(("warn", f"キーワード欄が{len(kws)}個しか埋まっていません（7個とも使う）"))
    if len(kws) > 7:
        issues.append(("warn", f"キーワードが{len(kws)}個あります（8個目以降は入りません）"))
    for k in kws[:7]:
        if len(k) > KEYWORD_MAX_CHARS:
            issues.append(("warn", f"キーワードが{len(k)}字で長すぎます（{KEYWORD_MAX_CHARS}字で切ります）: {k[:20]}…"))
        low = k.lower()
        for b in BANNED_KEYWORDS:
            if b in low:
                issues.append(("error", f"キーワードにKDPで禁止の言葉「{b}」があります: {k[:30]}"))
    return issues


def check_description(data):
    issues = []
    html = data.get("description_html") or ""
    n = len(text_of(html))
    if n > 4000:
        issues.append(("error", f"紹介文が{n}字あります（KDPの上限は4,000字）"))
    elif n < 600:
        issues.append(("warn", f"紹介文が短めです（{n}字）"))
    tags = set(t.lower() for t in re.findall(r"<\s*([a-zA-Z0-9]+)", html)) - DESC_TAGS
    if tags:
        issues.append(("warn", f"紹介文に使えないタグ: {', '.join(sorted(tags))}"))
    if "目次" not in text_of(html):
        issues.append(("warn", "紹介文に目次がありません"))
    if re.search(r"(レビュー|星5|★5).{0,10}(お願い|書いて|特典)", text_of(html)):
        issues.append(("error", "紹介文でレビューをお願いしています（KDPの規約違反）"))
    return issues


def check(data):
    """[(level, message)] を返す。level は error / warn。"""
    issues = []
    body = text_of(body_html(data))
    desc = text_of(data.get("description_html"))
    all_text = "\n".join([data["title"], data.get("subtitle", ""), desc, cover_text(data), body])

    if not AUTHOR["last"] and not AUTHOR["first"]:
        issues.append(("error", ".env の著者名（AUTHOR_LAST / AUTHOR_FIRST）が空です"))
    issues += check_title(data)
    issues += check_keywords(data)
    issues += check_description(data)
    if len([c for c in data.get("categories") or [] if c]) == 0:
        issues.append(("warn", "カテゴリーの候補がありません"))

    for pat in EXAGGERATION:
        m = re.search(pat, all_text)
        if m:
            issues.append(("error", f"断定・誇大表現「{m.group(0)}」があります"))
    memo = data.get("theme", {}).get("memo", "")
    for pat in FAKE_EXPERIENCE:
        for m in re.finditer(pat, all_text):
            level = "warn" if memo and "なし" not in memo[:4] else "error"
            msg = "著者メモの体験か確認してください" if level == "warn" else "架空の体験・実績に見えます"
            issues.append((level, f"{msg}: 「{m.group(0)}」"))
    if re.search(r"https?://|www\.", body + desc):
        issues.append(("error", "本文か紹介文にURLがあります"))

    missing = [c["title"] for c in data["chapters"] if not c.get("html")]
    if missing:
        issues.append(("error", f"本文が無い章があります: {'、'.join(missing)[:200]}"))
    tags = set(t.lower() for t in re.findall(r"<\s*([a-zA-Z0-9]+)", body_html(data))) - BODY_TAGS
    if tags:
        issues.append(("warn", f"本文に想定外のタグ: {', '.join(sorted(tags))}"))

    stats = sorted(set(m.group(0) for m in re.finditer(STAT_PATTERN, all_text)))
    if stats:
        issues.append(("warn", "出典の確認が必要な数値: " + "、".join(stats[:10])))
    n = body_chars(data)
    if n < 10000:
        issues.append(("warn", f"本文が短めです（{n:,}字）"))
    return issues


def has_error(issues):
    return any(level == "error" for level, _ in issues)
