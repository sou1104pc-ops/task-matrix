"""Brain の売上APIの返り値から、レポートに使う数字を取り出す。

売上APIの中身（項目名）は公開されていないので、ありそうな名前を順に探す。
初回は `python -m src.brain_client sales` で実際の中身を見て、合わなければ下の候補を直す。
"""
from datetime import datetime

from .config import JST

TOTAL_KEYS = ("total_sales", "total_amount", "total_price", "amount", "total", "sales")
COUNT_KEYS = ("total_count", "total_sales_count", "sales_count", "sold_count", "count")
DATE_KEYS = ("sold_at", "purchased_at", "paid_at", "created_at", "sales_date", "date")
AMOUNT_KEYS = ("sales_amount", "amount", "price", "sale_price", "payment_amount")
TITLE_KEYS = ("article_title", "title", "name")


def _pick(d, keys):
    if not isinstance(d, dict):
        return None
    for k in keys:
        if d.get(k) is not None:
            return d[k]
    for v in d.values():  # {"article": {"title": ...}} のような入れ子
        if isinstance(v, dict):
            found = _pick(v, keys)
            if found is not None:
                return found
    return None


def _to_int(v):
    try:
        return int(float(str(v).replace(",", "")))
    except (TypeError, ValueError):
        return None


def totals(raw):
    """total_sales の返り値から (累計売上, 累計部数)。分からない値は None。"""
    if isinstance(raw, (int, float, str)):
        return _to_int(raw), None
    return _to_int(_pick(raw, TOTAL_KEYS)), _to_int(_pick(raw, COUNT_KEYS))


def history_items(raw):
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for k in ("sales_histories", "histories", "items", "list", "data"):
            if isinstance(raw.get(k), list):
                return raw[k]
    return []


def sales_on(raw, day):
    """sales_histories の返り値から、指定日（JST）の販売を [{title, amount}] で返す。"""
    out = []
    for item in history_items(raw):
        when = _pick(item, DATE_KEYS)
        if not when:
            continue
        try:
            d = datetime.fromisoformat(str(when).replace("Z", "+00:00").replace("/", "-"))
        except ValueError:
            continue
        if d.tzinfo:
            d = d.astimezone(JST)
        if d.date() == day:
            out.append({"title": str(_pick(item, TITLE_KEYS) or "（記事名不明）"),
                        "amount": _to_int(_pick(item, AMOUNT_KEYS)) or 0})
    return out
