"""UTAGE API（https://docs.utage-system.com）で、公式LINEの登録経路を作り、登録数・ファネルの数値・売上を取る。

- 本ごとに LINE の登録経路を1つ作り、その URL を本の中の特典案内に入れる → どの本から何人登録したかがわかる
- 登録経路の作成は公開ドキュメントに無い（MCP の message_tracking_create と同じ形で呼ぶ）。失敗したら LINE_URL を使う
- 集計系のAPIは UTAGE 側で「プレビュー機能」。仕様が変わったら、ここを直す
"""
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from .config import UTAGE_ACCOUNT_ID, UTAGE_API_KEY, UTAGE_FUNNEL_IDS, UTAGE_SCENARIO_ID

log = logging.getLogger("utage")
BASE = "https://api.utage-system.com/v1"
SINCE_START = "2020-01-01"  # 「累計」の集計開始日


class UtageError(Exception):
    pass


def enabled():
    return bool(UTAGE_API_KEY and UTAGE_ACCOUNT_ID and UTAGE_SCENARIO_ID)


def _call(method, path, params=None, body=None):
    if not UTAGE_API_KEY:
        raise UtageError(".env の UTAGE_API_KEY が空です")
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v})
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {UTAGE_API_KEY}", "Content-Type": "application/json", "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e)["error"]["message"]
        except Exception:  # noqa: BLE001 - エラー本文が読めなくてもコードは返す
            msg = e.reason
        raise UtageError(f"UTAGE API {e.code}: {msg}") from e
    except urllib.error.URLError as e:
        raise UtageError(f"UTAGE に接続できません: {e.reason}") from e


def _scenario_path(suffix=""):
    return f"/accounts/{UTAGE_ACCOUNT_ID}/scenarios/{UTAGE_SCENARIO_ID}{suffix}"


# ---- 登録経路 ----
def create_tracking(name):
    """本用の LINE 登録経路を作って {"id", "url"} を返す。"""
    res = _call("POST", _scenario_path("/trackings"), body={"name": name[:255], "type": "line"})
    t = res.get("data") or {}
    if not t.get("url"):
        raise UtageError(f"登録経路は作れましたが URL が返ってきませんでした: {res}")
    return {"id": t.get("id"), "url": t["url"]}


def tracking_stats(date_from=SINCE_START, date_to=None):
    """{tracking_id: {"name", "pv", "uu", "registration_count"}}"""
    res = _call("GET", _scenario_path("/trackings/stats"), {"date_from": date_from, "date_to": date_to})
    return {s["tracking_id"]: s for s in res.get("data") or []}


def reader_total():
    """シナリオの読者数（登録経路を問わない合計）。"""
    res = _call("GET", _scenario_path("/readers/tracking-summary"))
    return sum(r.get("count") or 0 for r in res.get("data") or [])


# ---- ファネル ----
def funnel_name(funnel_id):
    try:
        for page in range(1, 11):
            res = _call("GET", "/funnels", {"page": page})
            for f in res.get("data") or []:
                if f.get("id") == funnel_id:
                    return f.get("name") or funnel_id
            meta = res.get("meta") or {}
            if page * (meta.get("per_page") or 20) >= (meta.get("total") or 0):
                break
    except UtageError:
        pass
    return funnel_id


def funnel_totals(funnel_id, date_from, date_to):
    """ファネル全体の合計 {"pv", "uu", "registration_count", "sale_count", "sale_amount"}。
    PV・UU・登録数は最初のステップ（LP）、売上は全ステップの合計で出す。"""
    steps = _call("GET", f"/funnels/{funnel_id}/stats", {"date_from": date_from, "date_to": date_to}).get("data") or []
    first = steps[0] if steps else {}
    return {
        "pv": first.get("pv") or 0,
        "uu": first.get("uu") or 0,
        "registration_count": first.get("registration_count") or 0,
        "sale_count": sum(s.get("sale_count") or 0 for s in steps),
        "sale_amount": sum(s.get("sale_amount") or 0 for s in steps),
    }


def funnels(date_from, date_to):
    """[(名前, 合計)]。.env の UTAGE_FUNNEL_IDS に書いたファネルだけ。"""
    return [(funnel_name(fid), funnel_totals(fid, date_from, date_to)) for fid in UTAGE_FUNNEL_IDS]
