"""UTAGE（公式LINE・メール配信）のリスト数を取る。読み取りだけ。

UTAGE の API（https://docs.utage-system.com/）を APIキーで呼ぶ。
名前が UTAGE_ACCOUNT_PREFIX（既定「【Brain】」）で始まる配信アカウントを対象にして、
読者数・LINE友だち数・ブロック数と、登録経路（どの Brain 記事から来たか）ごとのアクセス数・登録数を集める。
"""
import asyncio
import json
import urllib.error
import urllib.parse
import urllib.request

from .config import UTAGE_ACCOUNT_PREFIX, UTAGE_API_KEY

API = "https://api.utage-system.com/v1"


class UtageError(Exception):
    pass


def _get(path, params=None):
    if not UTAGE_API_KEY:
        raise UtageError("UTAGE_API_KEY が未設定です（UTAGE の右上メニュー →「API設定」で発行して .env に書く）")
    url = f"{API}{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {UTAGE_API_KEY}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="ignore")[:200]
        if e.code == 401:
            raise UtageError("UTAGE の APIキーが無効です（.env の UTAGE_API_KEY を確認）")
        raise UtageError(f"UTAGE {path} が失敗しました（HTTP {e.code}）: {body}")
    except urllib.error.URLError as e:
        raise UtageError(f"UTAGE に接続できませんでした: {e}")


def _total(path, params=None):
    """一覧の件数（meta.total）だけを取る。"""
    return int((_get(path, {**(params or {}), "per_page": 1}).get("meta") or {}).get("total") or 0)


def _list_all(path, params=None, max_pages=20):
    out = []
    for page in range(1, max_pages + 1):
        d = _get(path, {**(params or {}), "page": page, "per_page": 100})
        out += d.get("data") or []
        meta = d.get("meta") or {}
        if not meta or page * 100 >= int(meta.get("total") or 0):
            break
    return out


def target_accounts():
    return [a for a in _list_all("/accounts") if (a.get("name") or "").startswith(UTAGE_ACCOUNT_PREFIX)]


def account_stats(account, day):
    """1アカウントぶんの数字。day（date）はその日の登録経路の集計に使う。"""
    aid = account["id"]
    s = {"id": aid, "name": account["name"], "type": account.get("type"), "readers": _total(f"/accounts/{aid}/readers"),
         "line": None, "line_blocked": None, "routes": []}
    if account.get("type") in ("line", "mail_line"):
        try:
            s["line"] = _total(f"/accounts/{aid}/line/friends")
            s["line_blocked"] = _total(f"/accounts/{aid}/line/friends", {"is_blocked": "true"})
        except UtageError:
            pass  # LINE未連携のアカウント
    for sc in _list_all(f"/accounts/{aid}/scenarios"):
        try:
            rows = _get(f"/accounts/{aid}/scenarios/{sc['id']}/trackings/stats",
                        {"date_from": day.isoformat(), "date_to": day.isoformat()}).get("data") or []
        except UtageError:
            continue
        for r in rows:
            if r.get("pv") or r.get("registration_count"):
                s["routes"].append({"name": r.get("name") or "（名前なし）", "pv": int(r.get("pv") or 0),
                                    "uu": int(r.get("uu") or 0), "registrations": int(r.get("registration_count") or 0)})
    s["routes"].sort(key=lambda r: (r["registrations"], r["pv"]), reverse=True)
    return s


async def collect(day):
    """対象アカウントすべての数字（アカウントごとの dict のリスト）。"""
    accounts = await asyncio.to_thread(target_accounts)
    return [await asyncio.to_thread(account_stats, a, day) for a in accounts]
