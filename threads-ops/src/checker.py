"""投稿前の自動チェック。error があると承認できない。warn は確認を促すだけ。"""
import difflib
import re

from .config import MAX_CHAIN, MAX_POST_CHARS

URL_RE = re.compile(r"https?://[^\s）)」』]+")
HASHTAG_RE = re.compile(r"(?:^|\s)#[^\s#]+")
# 収入・効果を約束する表現（景品表示法・特商法で問題になりやすい）
RISKY = ["絶対", "必ず稼げ", "誰でも簡単", "確実に", "100%", "放置で", "寝てるだけ", "月収100万", "元本保証"]
SIMILAR_WARN = 0.6
SIMILAR_ERROR = 0.85
# 参考にしたXの投稿との近さ。型だけ借りるので、文がそのまま残っていたら止める
REF_SAME_RUN_ERROR = 25   # 同じ文字の並びがこの字数以上続いたらエラー
REF_SAME_RUN_WARN = 15
REF_SIMILAR_WARN = 0.45


def _similarity(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio()


def _norm(text):
    return re.sub(r"\s+", "", text)


def _reference_issues(posts, reference):
    body, ref = _norm("".join(posts)), _norm(reference)
    if not body or not ref:
        return []
    sm = difflib.SequenceMatcher(None, body, ref, autojunk=False)
    m = sm.find_longest_match(0, len(body), 0, len(ref))
    run = body[m.a:m.a + m.size]
    if m.size >= REF_SAME_RUN_ERROR:
        return [("error", f"参考にしたXの投稿と同じ文が{m.size}字続いています: 「{run[:30]}…」")]
    issues = []
    if m.size >= REF_SAME_RUN_WARN:
        issues.append(("warn", f"参考にしたXの投稿と同じ言い回しがあります: 「{run}」"))
    ratio = sm.ratio()
    if ratio >= REF_SIMILAR_WARN:
        issues.append(("warn", f"参考にしたXの投稿と文面が似ています（類似度{ratio:.0%}）"))
    return issues


def check(account, posts, recent=(), reference=None):
    """account: accounts.json の1件 / posts: 本文リスト / recent: [(account_id, 本文)]
    reference: 参考にしたXの投稿（あれば、文面を写していないかも見る）"""
    issues = []
    if not posts or not any(p.strip() for p in posts):
        return [("error", "本文が空です")]
    if len(posts) > MAX_CHAIN:
        issues.append(("error", f"ツリーは{MAX_CHAIN}投稿までです（{len(posts)}投稿あります）"))
    # 固定投稿へ誘導するアカウントは、本文にURLを一切入れない
    allowed_urls = [] if account.get("cta_method") == "pinned" else [u for u in [account.get("cta_url")] if u]
    for i, text in enumerate(posts, 1):
        label = f"{i}投稿目" if len(posts) > 1 else "本文"
        if not text.strip():
            issues.append(("error", f"{label}が空です"))
        if len(text) > MAX_POST_CHARS:
            issues.append(("error", f"{label}が{len(text)}字です（上限{MAX_POST_CHARS}字）"))
        for url in URL_RE.findall(text):
            if not any(url.startswith(a) for a in allowed_urls):
                issues.append(("error", f"{label}に登録されていないURLがあります: {url[:60]}"))
        if len(HASHTAG_RE.findall(text)) > 1:
            issues.append(("warn", f"{label}にハッシュタグが複数あります（Threadsでは1つ目しかタグになりません）"))
        for w in RISKY:
            if w in text:
                issues.append(("warn", f"{label}に誇大と取られやすい表現「{w}」があります"))
    head = posts[0]
    best, best_acc = 0.0, None
    for acc_id, other in recent:
        r = _similarity(head, other)
        if r > best:
            best, best_acc = r, acc_id
    if best >= SIMILAR_ERROR:
        issues.append(("error", f"{best_acc} の最近の投稿とほぼ同じ文面です（類似度{best:.0%}）"))
    elif best >= SIMILAR_WARN:
        issues.append(("warn", f"{best_acc} の最近の投稿と似ています（類似度{best:.0%}）"))
    if reference:
        issues += _reference_issues(posts, reference)
    issues += closing_issues(account, posts)
    return issues


def closing_issues(account, posts):
    """アカウントで決めた締め（必ず入れる一文・最後の一文・質問で終える）を守っているか。"""
    last = posts[-1].strip() if posts else ""
    lines = [ln.strip() for ln in last.splitlines() if ln.strip()]
    issues = []
    for phrase in account.get("closing") or []:
        if _norm(phrase) not in _norm(last):
            issues.append(("error", f"最後の投稿に「{phrase}」がありません"))
    final = account.get("last_line")
    if final and (not lines or _norm(final) not in _norm(lines[-1])):
        issues.append(("error", f"最後の一文が「{final}」になっていません"))
    if account.get("end_with_question") and not (lines and lines[-1].rstrip("👇🙏😊✨ ").endswith(("？", "?"))):
        issues.append(("error", "最後が質問で終わっていません"))
    return issues


def has_error(issues):
    return any(lv == "error" for lv, _ in issues)
