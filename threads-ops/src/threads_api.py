"""Threads API（graph.threads.net）を呼ぶ。標準ライブラリだけで書き、呼び出し側は asyncio.to_thread で使う。

投稿は「コンテナ作成 → 処理完了を待つ → 公開」の2段階。
ツリー（連投）は2つ目以降を reply_to_id で1つ前の投稿へのリプライにする。
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .config import THREADS_APP_ID, THREADS_APP_SECRET, THREADS_REDIRECT_URI

BASE = "https://graph.threads.net"
VERSION = "v1.0"
# 分析（③）で使う権限も最初からもらっておき、あとで再接続しなくて済むようにする
SCOPES = "threads_basic,threads_content_publish,threads_manage_insights,threads_read_replies,threads_manage_replies"


class ThreadsError(Exception):
    pass


def _call(method, path, params):
    url = f"{BASE}/{path}"
    data = None
    if method == "GET":
        url += "?" + urllib.parse.urlencode(params)
    else:
        data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            err = json.loads(body).get("error", {})
            msg = err.get("error_user_msg") or err.get("message") or body
        except (json.JSONDecodeError, AttributeError):
            msg = body
        raise ThreadsError(f"HTTP {e.code}: {msg[:300]}") from None
    except urllib.error.URLError as e:
        raise ThreadsError(f"接続できませんでした: {e.reason}") from None


# ---------------------------------------------------------------- 認証
def authorize_url(state):
    if not (THREADS_APP_ID and THREADS_REDIRECT_URI):
        raise ThreadsError(".env に THREADS_APP_ID と THREADS_REDIRECT_URI を設定してください")
    return "https://threads.net/oauth/authorize?" + urllib.parse.urlencode({
        "client_id": THREADS_APP_ID, "redirect_uri": THREADS_REDIRECT_URI,
        "scope": SCOPES, "response_type": "code", "state": state,
    })


def exchange_code(code):
    """認可コード → 短期トークン。"""
    return _call("POST", "oauth/access_token", {
        "client_id": THREADS_APP_ID, "client_secret": THREADS_APP_SECRET,
        "grant_type": "authorization_code", "redirect_uri": THREADS_REDIRECT_URI, "code": code,
    })["access_token"]


def to_long_lived(short_token):
    """短期トークン → 長期トークン（60日）。{access_token, expires_in} を返す。"""
    if not THREADS_APP_SECRET:
        raise ThreadsError(".env に THREADS_APP_SECRET を設定してください")
    return _call("GET", "access_token", {
        "grant_type": "th_exchange_token", "client_secret": THREADS_APP_SECRET, "access_token": short_token,
    })


def refresh(long_token):
    """長期トークンの期限を延ばす（発行から24時間以上たっていて、期限内のものだけ）。"""
    return _call("GET", "refresh_access_token", {"grant_type": "th_refresh_token", "access_token": long_token})


def me(token):
    return _call("GET", f"{VERSION}/me", {"fields": "id,username", "access_token": token})


# ---------------------------------------------------------------- 投稿
def _wait_container(container_id, token, timeout=90):
    deadline = time.monotonic() + timeout
    while True:
        st = _call("GET", f"{VERSION}/{container_id}", {"fields": "status,error_message", "access_token": token})
        status = st.get("status")
        if status in (None, "FINISHED", "PUBLISHED"):
            return
        if status in ("ERROR", "EXPIRED"):
            raise ThreadsError(f"投稿の準備に失敗しました: {st.get('error_message') or status}")
        if time.monotonic() > deadline:
            raise ThreadsError("投稿の準備がタイムアウトしました")
        time.sleep(3)


def publish_text(user_id, token, text, reply_to=None):
    """テキスト投稿を1つ公開して、メディアIDを返す。"""
    params = {"media_type": "TEXT", "text": text, "access_token": token}
    if reply_to:
        params["reply_to_id"] = reply_to
    container = _call("POST", f"{VERSION}/{user_id}/threads", params)["id"]
    _wait_container(container, token)
    return _call("POST", f"{VERSION}/{user_id}/threads_publish",
                 {"creation_id": container, "access_token": token})["id"]


def permalink(media_id, token):
    return _call("GET", f"{VERSION}/{media_id}", {"fields": "permalink", "access_token": token}).get("permalink")
