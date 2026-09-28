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


def _similarity(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio()


def check(account, posts, recent=()):
    """account: accounts.json の1件 / posts: 本文リスト / recent: [(account_id, 本文)]"""
    issues = []
    if not posts or not any(p.strip() for p in posts):
        return [("error", "本文が空です")]
    if len(posts) > MAX_CHAIN:
        issues.append(("error", f"ツリーは{MAX_CHAIN}投稿までです（{len(posts)}投稿あります）"))
    allowed_urls = [u for u in [account.get("cta_url")] if u]
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
    return issues


def has_error(issues):
    return any(lv == "error" for lv, _ in issues)
