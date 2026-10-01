"""公開後の自動値上げと、レビュー特典（公式LINEへの案内）。

公開申請した記事を1時間ごとに見に行き、
1. Brain の審査が通って公開されたら、その日時（published_at）を記録する
2. 公開から PRICE_SCHEDULE の日数が経ったら、価格を次の段階に上げる（例: 5日後 1,980円、7日後 2,980円）
"""
from datetime import datetime

from . import storage
from .config import JST, LINE_URL, PRICE_SCHEDULE, REVIEW_REWARD_CONTENT, REVIEW_REWARD_METHOD, REVIEW_REWARD_TITLE


def review_reward():
    """公開申請に付けるレビュー特典。LINE_URL が無ければ None（特典なし）。"""
    if not LINE_URL:
        return None
    return {
        "title": REVIEW_REWARD_TITLE,
        "content": REVIEW_REWARD_CONTENT or f"公式LINEで「{REVIEW_REWARD_TITLE}」をお渡ししています。",
        "method": REVIEW_REWARD_METHOD.format(line_url=LINE_URL),
    }


def _parse(ts):
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=JST)


def target_stage(live_at, now):
    """公開からの経過日数で、今いるべき価格の段階（PRICE_SCHEDULE の添字）。"""
    days = (now - live_at).total_seconds() / 86400
    return max(i for i, (d, _) in enumerate(PRICE_SCHEDULE) if days >= d)


def is_live(article, now):
    """Brainで実際に公開されているか（審査中・要再審査・公開日前は False）。"""
    published_at = _parse(article.get("published_at"))
    return (article.get("status") == "published" and article.get("inspect_status") not in ("review", "rejected")
            and published_at is not None and published_at <= now)


async def check_all(client):
    """値上げなどを実行して、#レポート に出す報告文のリストを返す。"""
    now = datetime.now(JST)
    events = []
    last = len(PRICE_SCHEDULE) - 1
    for d in storage.price_watch_drafts():
        if (d["price_stage"] or 0) >= last:
            continue
        title = d["data"]["title"][:40]
        if not d["live_at"]:
            a = await client.find_article(d["brain_id"])
            if not a:
                continue
            if a.get("inspect_status") == "rejected" and storage.get_setting(f"rejected:{d['id']}") != "1":
                storage.set_setting(f"rejected:{d['id']}", "1")
                events.append(f"⚠️ 「{title}」が Brain の審査で差し戻されました（要再審査）。Brainの画面で理由を確認してください")
            if not is_live(a, now):
                continue
            storage.update_draft(d["id"], live_at=a["published_at"], price_stage=0)
            d["live_at"], d["price_stage"] = a["published_at"], 0
            plan = "、".join(f"{day}日後に{p:,}円" for day, p in PRICE_SCHEDULE[1:])
            events.append(f"🎉 「{title}」が Brain で公開されました（{d['data']['price']:,}円）。{plan}に値上げします\n{d['brain_url']}")
        stage = target_stage(_parse(d["live_at"]), now)
        if stage <= (d["price_stage"] or 0):
            continue
        price = PRICE_SCHEDULE[stage][1]
        try:
            await client.set_price(d["brain_id"], price)
        except Exception as e:  # noqa: BLE001 - 1本の失敗で他の記事を止めない。次の回にもう一度試す
            events.append(f"⚠️ 「{title}」を{price:,}円に値上げできませんでした（1時間後にもう一度試します）: {e}")
            continue
        storage.update_draft(d["id"], price_stage=stage)
        events.append(f"💴 「{title}」を{price:,}円に値上げしました（公開から{PRICE_SCHEDULE[stage][0]}日）\n{d['brain_url']}")
    return events
