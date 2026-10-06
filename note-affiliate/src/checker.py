"""公開前の自動チェック。error があると承認ボタンでは公開できない。"""
import re
from html import unescape

from .config import NOTE_USER, load_programs

PR_LINE = "※本記事はアフィリエイト広告（PR）を含みます。"

# 結果を保証する言い回しだけを止める（「必ず確認しましょう」「綿100%」などは対象外）
OUTCOME = r"(成功|内定|合格|受か|転職でき|年収(が|を)?(上|アップ)|上が|稼げ|採用され|安くな|下が|節約でき|得(する|になる|をする))"
EXAGGERATION = [
    rf"(必ず|絶対(に)?|確実に|間違いなく|100[%％]の?確率で)[^。]{{0,8}}{OUTCOME}",
    rf"{OUTCOME}[^。]{{0,6}}(率|確率)[^。]{{0,4}}100[%％]",
    r"業界(No\.?1|ナンバーワン|最大級)",
]
FAKE_EXPERIENCE = [
    r"(僕|私|わたし|筆者)(自身)?(も|が|は)?.{0,20}(相談を受け|支援して|支援に関わ|サポートして|見てき)",
    r"(僕|私|筆者)が(担当|支援|サポート)した",
    r"実際に(僕|私)が",
]
FAKE_REVIEW = [r"(利用者|ユーザー)の(生の)?声", r"実際の口コミ", r"口コミ[:：]"]
STAT_PATTERN = r"(約?\d[\d,，.]*\s*(件|社|人|名)|\d[\d.]*\s*[%％]|平均\d[\d,]*万円)"
ALLOWED_TAGS = {"h2", "h3", "p", "ul", "ol", "li", "strong", "a", "blockquote", "br"}


FIG_MARKER = r"\[\[FIG:([\w-]+)\]\]"


def text_of(html):
    return unescape(re.sub(FIG_MARKER, "", re.sub(r"<[^>]+>", "", html)))


def image_text(data):
    """見出し画像と図に描く文字（画像になっても本文と同じルールで見る）。"""
    thumb = data.get("thumbnail") or {}
    parts = [str(v) for v in thumb.values()]
    for f in data.get("figures") or []:
        parts.append(f.get("title") or "")
        parts += f.get("items") or []
        parts += f.get("columns") or []
        for row in f.get("rows") or []:
            parts += row
    return "\n".join(str(p) for p in parts)


def check_figures(data):
    issues = []
    html = data["body_html"]
    used = re.findall(FIG_MARKER, html)
    figs = {f.get("id"): f for f in data.get("figures") or []}
    for fid in used:
        if fid not in figs:
            issues.append(("warn", f"図 {fid} の中身がありません（その位置には何も入りません）"))
    for fid in figs:
        if fid not in used:
            issues.append(("warn", f"図 {fid} は本文のどこにも置かれていません"))
    if re.search(r"</(ul|ol)>\s*<p>\s*" + FIG_MARKER, html):
        issues.append(("warn", "図がリストの直後にあります（noteで位置がずれることがあります）"))
    if not used:
        issues.append(("warn", "本文に図解がありません"))
    return issues


def check_seo(data):
    """検索で見つけてもらうための形のチェック（すべて warn）。"""
    issues = []
    title, html = data["title"], data["body_html"]
    kw = (data.get("main_keyword") or "").strip()
    if not kw:
        issues.append(("warn", "メインキーワードが決まっていません"))
    else:
        words = kw.split()
        if not all(w in title for w in words):
            issues.append(("warn", f"タイトルにメインキーワード「{kw}」が入っていません"))
        elif title.find(words[0]) > 20:
            issues.append(("warn", f"メインキーワード「{words[0]}」がタイトルの後ろのほうにあります"))
    if re.search(r"口コミ|評判", title + kw):
        issues.append(("warn", "タイトルかキーワードに「口コミ」「評判」があります（実際の口コミが無いので中身と食い違います）"))
    if len(title) > 40:
        issues.append(("warn", f"タイトルが長めです（{len(title)}字。検索結果で後ろが切れます）"))
    heads = re.findall(r"<(h2|h3)\b", html)
    if heads and heads[0] == "h3":
        issues.append(("warn", "最初の見出しが小見出し（h3）です。大見出し（h2）から始めてください"))
    if "よくある質問" not in text_of(html):
        issues.append(("warn", "「よくある質問」の章がありません"))
    return issues


def check(data):
    """[(level, message)] を返す。level は error / warn。"""
    issues = []
    title, html = data["title"], data["body_html"]
    body = text_of(html)
    pictures = image_text(data)
    programs = load_programs()

    # PR表記は記事の一番下に置く運用（冒頭には置かない）
    last_p = re.findall(r"<p[^>]*>(.*?)</p>", html, re.S)
    if not last_p or PR_LINE not in text_of(last_p[-1]):
        issues.append(("error", "本文の最後にPR表記がありません"))

    for pat in EXAGGERATION:
        m = re.search(pat, title + "\n" + body + "\n" + pictures)
        if m:
            issues.append(("error", f"断定・誇大表現「{m.group(0)}」があります"))
    for pat in FAKE_EXPERIENCE:
        for m in re.finditer(pat, body + "\n" + pictures):
            issues.append(("error", f"架空の実体験に見える表現: 「{m.group(0)}」"))
    for pat in FAKE_REVIEW:
        if re.search(pat, body + title + pictures):
            issues.append(("error", f"口コミ・利用者の声の提示があります（{pat}）"))

    allowed_urls = {programs[p]["url"] for p in data.get("allowed_programs", []) if p in programs}
    own_prefix = f"https://note.com/{NOTE_USER}/n/"  # 自分の過去記事への内部リンクは可
    links = [unescape(u) for u in re.findall(r'href="([^"]+)"', html)]
    for url in links:
        if url not in allowed_urls and not url.startswith(own_prefix):
            issues.append(("error", f"許可されていないリンク: {url[:80]}"))
    if not any(url in allowed_urls for url in links):
        issues.append(("error", "案件リンクが1つもありません"))

    issues += check_seo(data)

    tags = set(re.findall(r"<\s*([a-zA-Z0-9]+)", html)) - ALLOWED_TAGS
    if tags:
        issues.append(("warn", f"想定外のHTMLタグ: {', '.join(sorted(tags))}"))

    issues += check_figures(data)

    stats = sorted(set(m.group(0) for m in re.finditer(STAT_PATTERN, body + "\n" + pictures)))
    if stats:
        issues.append(("warn", "出典の確認が必要な数値: " + "、".join(stats[:10])))

    if re.search(r"20(1\d|2[0-5])年(版|最新|完全版)", title):
        issues.append(("error", "タイトルに古い年号があります"))
    n = len(body)
    if n < 4000:
        issues.append(("warn", f"本文が短めです（{n}字）"))
    hashtags = data.get("hashtags", [])
    if not 3 <= len(hashtags) <= 10:
        issues.append(("warn", f"ハッシュタグ数が{len(hashtags)}個です"))
    return issues


def has_error(issues):
    return any(level == "error" for level, _ in issues)
