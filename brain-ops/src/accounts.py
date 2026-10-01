"""Brain のアカウント（複数可）と、アカウントごとの特典（公式LINEへの案内）。

config/accounts.json に書く（書き方は config/accounts.example.json）。
- name は #材料 フォーラムのタグ名と同じにする（タグでどのアカウントで出すかが決まる）
- 1つ目のアカウントが既定（タグが無い材料・テーマから作った記事）
- ログイン状態はアカウントごとに data/browser-profile-<id>/ に保存する

特典は Brain の有料部分の「一番はじめ」と「一番うしろ」に、Bot が自動で差し込む（記事本文の生成とは別）。
"""
import json
from html import escape

from .config import AFFILIATE_RATE, AFFILIATE_RATES, DATA, ROOT

ACCOUNTS_FILE = ROOT / "config" / "accounts.json"


def _load():
    if not ACCOUNTS_FILE.exists():
        return [{"id": "main", "name": "メイン"}]
    with open(ACCOUNTS_FILE, encoding="utf-8") as f:
        items = json.load(f)["accounts"]
    ids = [a["id"] for a in items]
    if not items or len(set(ids)) != len(ids):
        raise SystemExit("config/accounts.json: アカウントが空か、id が重複しています")
    for a in items:
        rate = a.get("affiliate_rate", AFFILIATE_RATE)
        if rate not in AFFILIATE_RATES:
            raise SystemExit(f"config/accounts.json: {a['id']} の affiliate_rate は {AFFILIATE_RATES} のどれか")
    return items


ACCOUNTS = _load()
BY_ID = {a["id"]: a for a in ACCOUNTS}
DEFAULT = ACCOUNTS[0]


def get(account_id):
    return BY_ID.get(account_id or "", DEFAULT)


def from_tags(tag_names):
    """#材料 の投稿に付いたタグ名から、アカウントを選ぶ（name か id が一致したもの。無ければ既定）。"""
    for t in tag_names:
        for a in ACCOUNTS:
            if t in (a["name"], a["id"]):
                return a
    return DEFAULT


def profile_dir(account):
    # 1つ目のアカウントだけは、アカウント設定を作る前に作ったログイン状態（data/browser-profile）も使えるようにする
    legacy = DATA / "browser-profile"
    if account is DEFAULT and legacy.exists() and not (DATA / f"browser-profile-{account['id']}").exists():
        return legacy
    return DATA / f"browser-profile-{account['id']}"


def affiliate_rate(account):
    return account.get("affiliate_rate", AFFILIATE_RATE)


def has_reward(account):
    return bool(account.get("line_url"))


def reward_blocks(account, where):
    """有料部分に差し込む特典の行（HTMLの段落のリスト）。where は "top"（はじめ）か "bottom"（うしろ）。"""
    if not has_reward(account):
        return []
    title = escape(account.get("reward_title") or "購入者限定の特典")
    text = account.get(f"reward_{where}_text") or account.get("reward_text") or ""
    button = escape(account.get("reward_link_text") or "公式LINEで特典を受け取る")
    url = escape(account["line_url"], quote=True)
    blocks = [f"<p><strong>🎁 購入者限定特典：{title}</strong></p>"]
    blocks += [f"<p>{escape(line)}</p>" for line in text.split("\n") if line.strip()]
    blocks.append(f'<p><a href="{url}">{button}</a></p>')
    return blocks
