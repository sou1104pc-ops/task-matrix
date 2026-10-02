"""Brain の売上をレポート用にまとめる。

Brain の売上APIは「振込前の残高」は返すが、日ごとの売上は（販売履歴の形がまだ分からないため）使わない。
代わりに、自分の記事一覧にある記事ごとの販売部数（sold_count）を毎回記録し、前回との差を「売れた部数」とする。
金額は「売れた部数 × その時点の価格」の目安（割引リンク経由などで実際の金額と違うことがある）。
"""
from . import storage


def balance(total_raw):
    """total_sales の返り値から、振込前の売上残高（円）。読めなければ None。"""
    try:
        return int(total_raw["jpy"]["unpaid_sales_of_all_period"])
    except (KeyError, TypeError, ValueError):
        return None


def summarize(account_id, articles, day):
    """記事一覧を記録して、前回の記録からの増分をまとめる。

    戻り値: {"sold": [{title, count, amount}], "count": 合計部数, "amount": 合計金額の目安,
             "total_count": 全期間の販売部数, "since": 比べた記録の日付（初回は None）}
    """
    prev = storage.previous_article_sales(day.isoformat(), account_id)
    sold, total = [], 0
    for a in articles:
        count = int(a.get("sold_count") or 0)
        total += count
        before = prev.get(str(a.get("id")))
        if before is not None and count > before:
            n = count - before
            sold.append({"title": a.get("title") or "（無題）", "count": n, "amount": n * int(a.get("price") or 0)})
    storage.save_article_sales(day.isoformat(), account_id,
                               [(str(a.get("id")), int(a.get("sold_count") or 0), int(a.get("price") or 0))
                                for a in articles])
    sold.sort(key=lambda s: s["amount"], reverse=True)
    return {"sold": sold, "count": sum(s["count"] for s in sold), "amount": sum(s["amount"] for s in sold),
            "total_count": total, "since": storage.previous_article_sales_date(day.isoformat(), account_id)}
