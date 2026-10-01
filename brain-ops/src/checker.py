"""公開前の自動チェック。error があると承認ボタンでは公開申請できない。"""
import re
from html import unescape

# 結果を保証する言い回しだけを止める（「必ず確認しましょう」などは対象外）
OUTCOME = r"(成功|稼げ|稼ぐ|儲か|収入|収益|月収|年収|万円|売れ|集客でき|伸び|達成|合格|内定)"
EXAGGERATION = [
    rf"(必ず|絶対(に)?|確実に|間違いなく|誰でも|100[%％])[^。]{{0,10}}{OUTCOME}",
    rf"{OUTCOME}[^。]{{0,6}}(率|確率)[^。]{{0,4}}100[%％]",
    r"(放置|寝てるだけ|ほったらかし)(で|でも)[^。]{0,10}(稼|儲|収入|万円)",
    r"(月|年)(収|商)?\s*\d[\d,]*\s*万円(を)?(稼|達成|突破)",
    r"業界(No\.?1|ナンバーワン|最大級)",
]
FAKE_EXPERIENCE = [
    r"(僕|私|わたし|筆者)(自身)?(も|が|は)?.{0,20}(相談を受け|支援して|支援に関わ|サポートして|見てき|指導して|コンサルして)",
    r"(僕|私|筆者)が(担当|支援|サポート|指導)した",
    r"実際に(僕|私)が",
    r"(僕|私|筆者)(は|も)?(この方法|このやり方|これ)で[^。]{0,15}(稼|万円|達成|成功)",
]
FAKE_REVIEW = [r"(購入者|読者|利用者|ユーザー)の(生の)?声", r"実際の(口コミ|レビュー)", r"(口コミ|レビュー)[:：]"]
STAT_PATTERN = r"(約?\d[\d,，.]*\s*(件|社|人|名)|\d[\d.]*\s*[%％]|平均\d[\d,]*万円)"
ALLOWED_TAGS = {"h2", "h3", "p", "ul", "ol", "li", "strong", "blockquote", "br"}
FIG_MARKER = r"\[\[FIG:([\w-]+)\]\]"
PAYWALL = r"<p>\s*\[\[PAYWALL\]\]\s*</p>"
MIN_FREE, MIN_PAID = 1000, 5000
PRICE_RANGE = (100, 100000)


def text_of(html):
    return unescape(re.sub(r"\[\[PAYWALL\]\]", "", re.sub(FIG_MARKER, "", re.sub(r"<[^>]+>", "", html))))


def split_paywall(html):
    """(無料部分のHTML, 有料部分のHTML)。有料ラインが無ければ有料部分は空。"""
    parts = re.split(PAYWALL, html, maxsplit=1)
    return (parts[0], parts[1]) if len(parts) == 2 else (html, "")


def image_text(data):
    """メイン画像と図に描く文字（画像になっても本文と同じルールで見る）。"""
    thumb = data.get("thumbnail") or {}
    parts = [str(v) for k, v in thumb.items() if k != "visual"]  # visual は絵の指示で、文字としては載らない
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
    if not used:
        issues.append(("warn", "本文に図解がありません"))
    return issues


def check_paywall(html):
    issues = []
    n = len(re.findall(r"\[\[PAYWALL\]\]", html))
    if n == 0:
        return [("error", "有料ライン（[[PAYWALL]]）がありません")]
    if n > 1:
        issues.append(("error", f"有料ラインが{n}個あります（1つだけにしてください）"))
    if not re.search(PAYWALL, html):
        issues.append(("error", "有料ラインが単独の段落 <p>[[PAYWALL]]</p> になっていません"))
        return issues
    free, paid = split_paywall(html)
    nf, np_ = len(text_of(free)), len(text_of(paid))
    if nf < MIN_FREE:
        issues.append(("warn", f"無料部分が短めです（{nf:,}字）。購入の判断材料が足りないかもしれません"))
    if np_ < MIN_PAID:
        issues.append(("error", f"有料部分が短すぎます（{np_:,}字）。価格に見合う内容になっていません"))
    elif nf > np_:
        issues.append(("warn", f"無料部分（{nf:,}字）が有料部分（{np_:,}字）より長くなっています"))
    if not re.search(r"<h2", paid):
        issues.append(("warn", "有料部分に見出し（h2）がありません"))
    return issues


def check(data):
    """[(level, message)] を返す。level は error / warn。"""
    issues = []
    title, html = data["title"], data["body_html"]
    body = text_of(html)
    pictures = image_text(data)

    if len(title) > 100:
        issues.append(("error", f"タイトルが100字を超えています（{len(title)}字）"))
    price = data.get("price") or 0
    if not PRICE_RANGE[0] <= price <= PRICE_RANGE[1]:
        issues.append(("error", f"販売価格 {price:,}円 が想定範囲（{PRICE_RANGE[0]:,}〜{PRICE_RANGE[1]:,}円）外です"))
    if not data.get("category"):
        issues.append(("error", "カテゴリーが未設定です"))

    issues += check_paywall(html)

    for pat in EXAGGERATION:
        m = re.search(pat, title + "\n" + body + "\n" + pictures)
        if m:
            issues.append(("error", f"成果の保証・誇大表現「{m.group(0)}」があります"))
    # 材料から書いた記事は、運営者本人の経験が材料に書かれていることがあるので、止めずに確認を促す
    level = "warn" if data.get("material_id") else "error"
    for pat in FAKE_EXPERIENCE:
        for m in re.finditer(pat, body + "\n" + pictures):
            note = "（材料に書かれた本人の経験か確認してください）" if level == "warn" else ""
            issues.append((level, f"架空の実体験・実績に見える表現: 「{m.group(0)}」{note}"))
    for pat in FAKE_REVIEW:
        if re.search(pat, body + title + pictures):
            issues.append(("error", f"口コミ・購入者の声の提示があります（{pat}）"))

    links = re.findall(r'href="([^"]+)"', html)
    if links:
        issues.append(("error", f"外部リンクがあります: {links[0][:80]}"))
    if re.search(r"(LINE|ライン)(@|公式|登録|追加)|lin\.ee|メルマガ登録", body):
        issues.append(("warn", "LINE・メルマガなど外部への誘導に見える文があります（Brainの審査で止まることがあります）"))

    tags = set(re.findall(r"<\s*([a-zA-Z0-9]+)", html)) - ALLOWED_TAGS
    if tags:
        issues.append(("warn", f"想定外のHTMLタグ: {', '.join(sorted(tags))}"))

    issues += check_figures(data)

    stats = sorted(set(m.group(0) for m in re.finditer(STAT_PATTERN, body + "\n" + pictures)))
    if stats:
        issues.append(("warn", "出典の確認が必要な数値: " + "、".join(stats[:10])))
    if re.search(r"20(1\d|2[0-5])年(版|最新|完全版)", title):
        issues.append(("error", "タイトルに古い年号があります"))
    return issues


def has_error(issues):
    return any(level == "error" for level, _ in issues)
