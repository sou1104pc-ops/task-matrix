"""Threadsアカウントの接続（トークンの保存・更新）。

  python -m src.accounts list                 # 設定済みアカウントと接続状況
  python -m src.accounts token <id> <トークン>  # Metaの「ユーザートークン生成ツール」で出したトークンを登録
  python -m src.accounts connect <id>         # ブラウザで許可 → 戻り先URLを貼って登録
  python -m src.accounts check <id>           # トークンが使えるか確認（投稿はしない）
  python -m src.accounts refresh              # 期限が近いトークンを延長（Botが毎日自動で行う）
"""
import secrets
import sys
import urllib.parse
from datetime import datetime, timedelta

from . import storage, threads_api
from .config import JST, load_accounts
from .threads_api import ThreadsError

REFRESH_BEFORE_DAYS = 20   # 期限まで残りこの日数を切ったら延長する


def _expires(seconds):
    return (datetime.now(JST) + timedelta(seconds=int(seconds))).isoformat(timespec="seconds")


def register_token(account_id, token):
    """短期でも長期でも受け取り、長期トークンにして保存する。保存した行を返す。"""
    if account_id not in load_accounts():
        raise ThreadsError(f"config/accounts.json に id「{account_id}」がありません")
    try:
        long = threads_api.to_long_lived(token)
    except ThreadsError:
        # すでに長期トークンのときは交換できないので、延長を試して期限を得る
        try:
            long = threads_api.refresh(token)
        except ThreadsError:
            long = {"access_token": token, "expires_in": None}
    profile = threads_api.me(long["access_token"])
    expires = _expires(long["expires_in"]) if long.get("expires_in") else None
    storage.save_token(account_id, profile["id"], profile.get("username"), long["access_token"], expires)
    return storage.get_token(account_id)


def code_from(pasted):
    """戻り先URL全体でもコードだけでも受け付ける。末尾に付く #_ は取り除く。"""
    pasted = pasted.strip()
    if pasted.startswith("http"):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(pasted).query)
        if "error" in q:
            raise ThreadsError(f"許可されませんでした: {q.get('error_description', q['error'])[0]}")
        pasted = q.get("code", [""])[0]
    return pasted.split("#")[0]


def refresh_due():
    """期限が近いトークンを延長する。[(account_id, 結果メッセージ)] を返す。"""
    results = []
    limit = datetime.now(JST) + timedelta(days=REFRESH_BEFORE_DAYS)
    for aid, t in storage.all_tokens().items():
        if t["expires_at"] and datetime.fromisoformat(t["expires_at"]) > limit:
            continue
        try:
            r = threads_api.refresh(t["token"])
        except ThreadsError as e:
            results.append((aid, f"延長に失敗: {e}"))
            continue
        storage.save_token(aid, t["user_id"], t["username"], r["access_token"], _expires(r["expires_in"]))
        results.append((aid, "延長しました"))
    return results


def status_rows():
    """アカウントごとの (id, 表示名, 状態テキスト)。"""
    tokens = storage.all_tokens()
    rows = []
    for aid, a in load_accounts().items():
        t = tokens.get(aid)
        if not t:
            state = "未接続"
        else:
            exp = t["expires_at"][:10] if t["expires_at"] else "不明"
            state = f"@{t['username']}（トークン期限 {exp}）"
        if not a["enabled"]:
            state += " ※停止中"
        rows.append((aid, a["name"], state))
    return rows


def main(argv):
    if not argv or argv[0] == "list":
        for aid, name, state in status_rows():
            print(f"{aid:10} {name:16} {state}")
        return
    cmd = argv[0]
    try:
        if cmd == "token" and len(argv) == 3:
            t = register_token(argv[1], argv[2])
            print(f"✅ {argv[1]} を @{t['username']} として登録しました（期限 {t['expires_at'] or '不明'}）")
        elif cmd == "connect" and len(argv) == 2:
            print("このURLをブラウザで開き、対象のThreadsアカウントで許可してください:\n")
            print(threads_api.authorize_url(secrets.token_urlsafe(8)))
            print("\n許可したあとに移動した先のURL（アドレスバーの内容）をまるごと貼り付けて Enter:")
            short = threads_api.exchange_code(code_from(input("> ")))
            t = register_token(argv[1], short)
            print(f"✅ {argv[1]} を @{t['username']} として登録しました（期限 {t['expires_at'] or '不明'}）")
        elif cmd == "check" and len(argv) == 2:
            t = storage.get_token(argv[1])
            if not t:
                sys.exit(f"{argv[1]} は未接続です")
            p = threads_api.me(t["token"])
            print(f"✅ 使えます: @{p.get('username')}（id {p['id']}）")
        elif cmd == "refresh":
            for aid, msg in refresh_due() or [("-", "延長が必要なトークンはありません")]:
                print(f"{aid}: {msg}")
        else:
            sys.exit(__doc__)
    except ThreadsError as e:
        sys.exit(f"⚠️ {e}")


if __name__ == "__main__":
    main(sys.argv[1:])
