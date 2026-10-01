"""SQLite でテーマ・下書き・売上の記録・秘書の会話を管理する。"""
import json
import sqlite3
from datetime import datetime

from .config import DB_PATH, JST, load_seed_themes

SCHEMA = """
CREATE TABLE IF NOT EXISTS themes (
    id INTEGER PRIMARY KEY,
    theme TEXT UNIQUE NOT NULL,
    persona TEXT,
    price INTEGER,                 -- 空ならDEFAULT_PRICE
    category TEXT,                 -- 空ならDEFAULT_CATEGORY
    subcategory TEXT,
    used_at TEXT
);
CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY,
    theme_id INTEGER,
    status TEXT NOT NULL,          -- pending / published（公開申請済み）/ saved（Brain下書き保存のみ）/ rejected
    data TEXT NOT NULL,            -- 生成結果のJSON（価格・カテゴリも含む）
    issues TEXT,                   -- チェック結果のJSON
    message_id INTEGER,
    brain_id TEXT,                 -- Brain側の記事ID
    brain_url TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS chat_log (
    id INTEGER PRIMARY KEY,
    channel_id TEXT NOT NULL,
    role TEXT NOT NULL,            -- user / assistant
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sales_stats (
    date TEXT PRIMARY KEY,         -- JSTの日付 YYYY-MM-DD
    total_sales INTEGER,           -- 累計売上（その日の記録時点）
    total_count INTEGER,           -- 累計販売部数
    created_at TEXT NOT NULL
);
"""


def now():
    return datetime.now(JST).isoformat(timespec="seconds")


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _int_or_none(v):
    try:
        return int(v) if str(v).strip() else None
    except (TypeError, ValueError):
        return None


def init():
    with connect() as c:
        for t in load_seed_themes():
            c.execute(
                "INSERT OR IGNORE INTO themes (theme, persona, price, category, subcategory) VALUES (?, ?, ?, ?, ?)",
                (t["theme"].strip(), t.get("persona"), _int_or_none(t.get("price")),
                 t.get("category") or None, t.get("subcategory") or None),
            )


# ---- settings ----
def get_setting(key, default=None):
    with connect() as c:
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key, value):
    with connect() as c:
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))


# ---- themes ----
def add_theme(theme, persona="", price=None, category=None, subcategory=None):
    with connect() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO themes (theme, persona, price, category, subcategory) VALUES (?, ?, ?, ?, ?)",
            (theme, persona, price, category or None, subcategory or None),
        )
        return cur.rowcount > 0


def next_theme():
    """未使用のテーマを登録順に1つ返す。使い切ったら一番昔に使ったものを再利用する。"""
    with connect() as c:
        row = c.execute("SELECT * FROM themes WHERE used_at IS NULL ORDER BY id LIMIT 1").fetchone()
        if row is None:
            row = c.execute("SELECT * FROM themes ORDER BY used_at LIMIT 1").fetchone()
        return dict(row) if row else None


def mark_theme_used(theme_id):
    with connect() as c:
        c.execute("UPDATE themes SET used_at=? WHERE id=?", (now(), theme_id))


def list_themes(unused_only=True, limit=20):
    q = "SELECT * FROM themes" + (" WHERE used_at IS NULL" if unused_only else "") + " ORDER BY id LIMIT ?"
    with connect() as c:
        return [dict(r) for r in c.execute(q, (limit,))]


# ---- drafts ----
def create_draft(theme_id, data, issues):
    with connect() as c:
        cur = c.execute(
            "INSERT INTO drafts (theme_id, status, data, issues, created_at, updated_at) VALUES (?, 'pending', ?, ?, ?, ?)",
            (theme_id, json.dumps(data, ensure_ascii=False), json.dumps(issues, ensure_ascii=False), now(), now()),
        )
        return cur.lastrowid


def update_draft(draft_id, **fields):
    for k in ("data", "issues"):
        if k in fields:
            fields[k] = json.dumps(fields[k], ensure_ascii=False)
    fields["updated_at"] = now()
    cols = ", ".join(f"{k}=?" for k in fields)
    with connect() as c:
        c.execute(f"UPDATE drafts SET {cols} WHERE id=?", (*fields.values(), draft_id))


def get_draft(draft_id):
    with connect() as c:
        row = c.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["data"] = json.loads(d["data"])
    d["issues"] = json.loads(d["issues"] or "[]")
    return d


def pending_drafts():
    with connect() as c:
        return [r["id"] for r in c.execute("SELECT id FROM drafts WHERE status='pending'")]


def published_titles(limit=100):
    with connect() as c:
        rows = c.execute(
            "SELECT data FROM drafts WHERE status IN ('published', 'saved') ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [json.loads(r["data"])["title"] for r in rows]


def posts_since(iso):
    with connect() as c:
        rows = c.execute(
            "SELECT * FROM drafts WHERE status='published' AND updated_at>=? ORDER BY id", (iso,)
        ).fetchall()
    return [dict(r, data=json.loads(r["data"])) for r in rows]


# ---- 売上のスナップショット ----
def save_sales_stats(date, total_sales, total_count):
    """その日の累計値を記録する（同じ日に複数回呼ばれたら上書き）。"""
    with connect() as c:
        c.execute(
            "INSERT OR REPLACE INTO sales_stats (date, total_sales, total_count, created_at) VALUES (?, ?, ?, ?)",
            (date, total_sales, total_count, now()),
        )


def previous_sales_stats(date):
    """指定日より前で一番新しいスナップショットを返す（差分の計算に使う）。"""
    with connect() as c:
        row = c.execute(
            "SELECT * FROM sales_stats WHERE date < ? ORDER BY date DESC LIMIT 1", (date,)
        ).fetchone()
        return dict(row) if row else None


# ---- 秘書との会話履歴 ----
def add_chat(channel_id, role, content):
    with connect() as c:
        c.execute(
            "INSERT INTO chat_log (channel_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (channel_id, role, content, now()),
        )


def recent_chat(channel_id, limit=16):
    """古い順に (role, content) を返す。"""
    with connect() as c:
        rows = c.execute(
            "SELECT role, content FROM chat_log WHERE channel_id=? ORDER BY id DESC LIMIT ?",
            (channel_id, limit),
        ).fetchall()
    return [(r["role"], r["content"]) for r in reversed(rows)]


def clear_chat(channel_id):
    with connect() as c:
        cur = c.execute("DELETE FROM chat_log WHERE channel_id=?", (channel_id,))
        return cur.rowcount
