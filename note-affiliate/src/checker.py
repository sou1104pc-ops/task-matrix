"""公開前の自動チェック。error があると承認ボタンでは公開できない。"""
import re
from html import unescape

from .config import load_programs

PR_LINE = "※本記事はアフィリエイト広告（PR）を含みます。"

# 結果を保証する言い回しだけを止める（「必ず確認しましょう」「綿100%」などは対象外）
OUTCOME = r"(成功|内定|合格|受か|転職でき|年収(が|を)?(上|アップ)|上が|稼げ|採用され)"
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


def text_of(html):
    return unescape(re.sub(r"<[^>]+>", "", html))


def check(data):
    """[(level, message)] を返す。level は error / warn。"""
    issues = []
    title, html = data["title"], data["body_html"]
    body = text_of(html)
    programs = load_programs()

    first_p = re.search(r"<p[^>]*>(.*?)</p>", html, re.S)
    if not first_p or PR_LINE not in text_of(first_p.group(1)):
        issues.append(("error", "冒頭にPR表記がありません"))

    for pat in EXAGGERATION:
        m = re.search(pat, title + "\n" + body)
        if m:
            issues.append(("error", f"断定・誇大表現「{m.group(0)}」があります"))
    for pat in FAKE_EXPERIENCE:
        for m in re.finditer(pat, body):
            issues.append(("error", f"架空の実体験に見える表現: 「{m.group(0)}」"))
    for pat in FAKE_REVIEW:
        if re.search(pat, body + title):
            issues.append(("error", f"口コミ・利用者の声の提示があります（{pat}）"))

    allowed_urls = {programs[p]["url"] for p in data.get("allowed_programs", []) if p in programs}
    links = re.findall(r'href="([^"]+)"', html)
    for url in links:
        if unescape(url) not in allowed_urls:
            issues.append(("error", f"許可されていないリンク: {url[:80]}"))
    if not links:
        issues.append(("error", "案件リンクが1つもありません"))

    tags = set(re.findall(r"<\s*([a-zA-Z0-9]+)", html)) - ALLOWED_TAGS
    if tags:
        issues.append(("warn", f"想定外のHTMLタグ: {', '.join(sorted(tags))}"))

    stats = sorted(set(m.group(0) for m in re.finditer(STAT_PATTERN, body)))
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
