"""SQLite でテーマ・本の下書き・出版履歴を管理する。"""
import json
import sqlite3
from datetime import datetime

from .config import DB_PATH, JST, load_seed_themes

SCHEMA = """
CREATE TABLE IF NOT EXISTS themes (
    id INTEGER PRIMARY KEY,
    theme TEXT UNIQUE NOT NULL,
    persona TEXT,
    memo TEXT,                     -- 本人の体験・入れてほしい内容（本に使ってよい事実）
    rivals TEXT,                   -- 同じテーマで売れている本のタイトル（コバンザメ戦略用、| 区切り）
    used_at TEXT
);
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY,
    theme_id INTEGER,
    status TEXT NOT NULL,          -- pending / published（出版申請済み）/ saved（KDP下書き保存のみ）/ rejected
    data TEXT NOT NULL,            -- 企画と本文のJSON
    issues TEXT,                   -- チェック結果のJSON
    message_id INTEGER,
    kdp_note TEXT,                 -- 出版時のメモ（KDP側の状態など）
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def now():
    return datetime.now(JST).isoformat(timespec="seconds")


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def init():
    with connect() as c:
        for t in load_seed_themes():
            c.execute(
                "INSERT OR IGNORE INTO themes (theme, persona, memo, rivals) VALUES (?, ?, ?, ?)",
                (t["theme"], t.get("persona", ""), t.get("memo", ""), t.get("rivals", "")),
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
def add_theme(theme, persona="", memo="", rivals=""):
    with connect() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO themes (theme, persona, memo, rivals) VALUES (?, ?, ?, ?)",
            (theme, persona, memo, rivals),
        )
        return cur.rowcount > 0


def next_theme():
    """未使用のテーマを登録順に1つ返す。使い切ったら None（同じ本を二度出さない）。"""
    with connect() as c:
        row = c.execute("SELECT * FROM themes WHERE used_at IS NULL ORDER BY id LIMIT 1").fetchone()
    return dict(row) if row else None


def mark_theme_used(theme_id):
    with connect() as c:
        c.execute("UPDATE themes SET used_at=? WHERE id=?", (now(), theme_id))


def list_themes(limit=20):
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM themes WHERE used_at IS NULL ORDER BY id LIMIT ?", (limit,))]


# ---- books ----
def create_book(theme_id, data, issues):
    with connect() as c:
        cur = c.execute(
            "INSERT INTO books (theme_id, status, data, issues, created_at, updated_at) VALUES (?, 'pending', ?, ?, ?, ?)",
            (theme_id, json.dumps(data, ensure_ascii=False), json.dumps(issues, ensure_ascii=False), now(), now()),
        )
        return cur.lastrowid


def update_book(book_id, **fields):
    for k in ("data", "issues"):
        if k in fields:
            fields[k] = json.dumps(fields[k], ensure_ascii=False)
    fields["updated_at"] = now()
    cols = ", ".join(f"{k}=?" for k in fields)
    with connect() as c:
        c.execute(f"UPDATE books SET {cols} WHERE id=?", (*fields.values(), book_id))


def _row(row):
    d = dict(row)
    d["data"] = json.loads(d["data"])
    d["issues"] = json.loads(d["issues"] or "[]")
    return d


def get_book(book_id):
    with connect() as c:
        row = c.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    return _row(row) if row else None


def book_by_message(message_id):
    with connect() as c:
        row = c.execute("SELECT * FROM books WHERE message_id=? AND status='pending'", (message_id,)).fetchone()
    return _row(row) if row else None


def books_with_status(*statuses):
    q = f"SELECT * FROM books WHERE status IN ({','.join('?' * len(statuses))}) ORDER BY id"
    with connect() as c:
        return [_row(r) for r in c.execute(q, statuses)]


def published_titles(limit=200):
    with connect() as c:
        rows = c.execute(
            "SELECT data FROM books WHERE status IN ('published', 'saved') ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [json.loads(r["data"])["title"] for r in rows]


def published_since(iso):
    with connect() as c:
        rows = c.execute(
            "SELECT * FROM books WHERE status IN ('published', 'saved') AND updated_at>=? ORDER BY id", (iso,)
        ).fetchall()
    return [_row(r) for r in rows]
