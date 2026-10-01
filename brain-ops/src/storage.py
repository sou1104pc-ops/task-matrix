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
    material_id INTEGER,           -- 材料から作った記事なら materials.id
    live_at TEXT,                  -- Brainで公開された日時（審査が通った日時）
    price_stage INTEGER,           -- PRICE_SCHEDULE の何段目の価格まで反映したか
    submitted_at TEXT,             -- 公開申請（または下書き保存）した日時
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
CREATE TABLE IF NOT EXISTS materials (
    id INTEGER PRIMARY KEY,
    thread_id INTEGER UNIQUE NOT NULL,  -- #材料 フォーラムの投稿（スレッド）
    theme TEXT NOT NULL,           -- 投稿のタイトル
    status TEXT NOT NULL,          -- open（まだ記事にしていない）/ used
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS material_items (
    id INTEGER PRIMARY KEY,
    material_id INTEGER NOT NULL,
    kind TEXT NOT NULL,            -- text / file（画像・PDFなど。Claudeが読む）/ url（取ってきた本文）
    content TEXT NOT NULL,         -- 本文、またはファイルのパス
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


# 古いDBに後から足した列
MIGRATIONS = [("drafts", "material_id", "INTEGER"), ("drafts", "live_at", "TEXT"), ("drafts", "price_stage", "INTEGER"),
              ("drafts", "submitted_at", "TEXT")]


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    for table, col, typ in MIGRATIONS:
        if col not in {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
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
def create_draft(theme_id, data, issues, material_id=None):
    with connect() as c:
        cur = c.execute(
            "INSERT INTO drafts (theme_id, material_id, status, data, issues, created_at, updated_at)"
            " VALUES (?, ?, 'pending', ?, ?, ?, ?)",
            (theme_id, material_id, json.dumps(data, ensure_ascii=False), json.dumps(issues, ensure_ascii=False),
             now(), now()),
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
            "SELECT * FROM drafts WHERE status='published' AND submitted_at>=? ORDER BY id", (iso,)
        ).fetchall()
    return [dict(r, data=json.loads(r["data"])) for r in rows]


def price_watch_drafts():
    """公開申請したが、価格の予定をまだ最後まで反映していない記事。"""
    with connect() as c:
        rows = c.execute(
            "SELECT * FROM drafts WHERE status='published' AND brain_id IS NOT NULL ORDER BY id"
        ).fetchall()
    return [dict(r, data=json.loads(r["data"])) for r in rows]


# ---- 材料（#材料 フォーラム）----
def add_material(thread_id, theme):
    with connect() as c:
        c.execute("INSERT OR IGNORE INTO materials (thread_id, theme, status, created_at) VALUES (?, ?, 'open', ?)",
                  (thread_id, theme, now()))
        return dict(c.execute("SELECT * FROM materials WHERE thread_id=?", (thread_id,)).fetchone())


def get_material(material_id=None, thread_id=None):
    with connect() as c:
        row = (c.execute("SELECT * FROM materials WHERE id=?", (material_id,)) if material_id else
               c.execute("SELECT * FROM materials WHERE thread_id=?", (thread_id,))).fetchone()
        return dict(row) if row else None


def add_material_item(material_id, kind, content):
    with connect() as c:
        c.execute("INSERT INTO material_items (material_id, kind, content, created_at) VALUES (?, ?, ?, ?)",
                  (material_id, kind, content, now()))


def material_items(material_id):
    with connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM material_items WHERE material_id=? ORDER BY id", (material_id,))]


def next_material():
    """まだ記事にしていない材料のうち、一番古いもの（中身が1つ以上あるもの）。"""
    with connect() as c:
        row = c.execute(
            "SELECT m.* FROM materials m WHERE m.status='open'"
            " AND EXISTS (SELECT 1 FROM material_items i WHERE i.material_id=m.id) ORDER BY m.id LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def list_materials(open_only=True, limit=30):
    q = ("SELECT m.*, (SELECT COUNT(*) FROM material_items i WHERE i.material_id=m.id) AS items FROM materials m"
         + (" WHERE m.status='open'" if open_only else "") + " ORDER BY m.id LIMIT ?")
    with connect() as c:
        return [dict(r) for r in c.execute(q, (limit,))]


def set_material_status(material_id, status):
    with connect() as c:
        c.execute("UPDATE materials SET status=? WHERE id=?", (status, material_id))


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
