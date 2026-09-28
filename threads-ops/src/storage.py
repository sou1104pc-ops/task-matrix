"""SQLite でアカウントのトークン・下書き・予約投稿を管理する。

下書きの状態:
  pending   承認待ち（Discordで確認中）
  approved  承認済み。scheduled_at になったら投稿される
  posting   投稿処理中
  posted    投稿済み
  failed    投稿に失敗して止まっている（再試行ボタンで approved に戻せる）
  rejected  ボツ
  cancelled 承認後に予約を取り消した
"""
import json
import sqlite3
from datetime import datetime

from .config import DB_PATH, JST

SCHEMA = """
CREATE TABLE IF NOT EXISTS tokens (
    account_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    username TEXT,
    token TEXT NOT NULL,
    expires_at TEXT,               -- 長期トークンの期限（JST ISO）
    refreshed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY,
    account_id TEXT NOT NULL,
    kind TEXT NOT NULL,            -- value（価値提供）/ cta（LINE・メルマガ誘導）/ manual（手書き）
    status TEXT NOT NULL,
    posts TEXT NOT NULL,           -- 投稿本文のJSON配列。2つ目以降は1つ目へのリプライとしてつなげる
    memo TEXT,                     -- 生成時の狙いメモ
    issues TEXT,                   -- チェック結果のJSON
    scheduled_at TEXT NOT NULL,    -- 投稿予定（JST ISO）
    message_id INTEGER,
    channel_id INTEGER,
    published_ids TEXT,            -- 投稿済みのメディアIDのJSON配列（ツリー途中で失敗したときの続きに使う）
    permalink TEXT,
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    posted_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS drafts_status ON drafts (status, scheduled_at);
CREATE INDEX IF NOT EXISTS drafts_message ON drafts (message_id);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def now():
    return datetime.now(JST).isoformat(timespec="seconds")


def connect():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


# ---- settings ----
def get_setting(key, default=None):
    with connect() as c:
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key, value):
    with connect() as c:
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))


# ---- tokens ----
def save_token(account_id, user_id, username, token, expires_at):
    with connect() as c:
        c.execute(
            "INSERT OR REPLACE INTO tokens (account_id, user_id, username, token, expires_at, refreshed_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (account_id, str(user_id), username, token, expires_at, now()),
        )


def get_token(account_id):
    with connect() as c:
        row = c.execute("SELECT * FROM tokens WHERE account_id=?", (account_id,)).fetchone()
        return dict(row) if row else None


def all_tokens():
    with connect() as c:
        return {r["account_id"]: dict(r) for r in c.execute("SELECT * FROM tokens")}


# ---- drafts ----
def _row(row):
    if not row:
        return None
    d = dict(row)
    d["posts"] = json.loads(d["posts"])
    d["issues"] = json.loads(d["issues"] or "[]")
    d["published_ids"] = json.loads(d["published_ids"] or "[]")
    return d


def create_draft(account_id, kind, posts, scheduled_at, issues, memo="", status="pending"):
    with connect() as c:
        cur = c.execute(
            "INSERT INTO drafts (account_id, kind, status, posts, memo, issues, scheduled_at, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (account_id, kind, status, json.dumps(posts, ensure_ascii=False), memo,
             json.dumps(issues, ensure_ascii=False), scheduled_at, now(), now()),
        )
        return cur.lastrowid


def update_draft(draft_id, **fields):
    for k in ("posts", "issues", "published_ids"):
        if k in fields:
            fields[k] = json.dumps(fields[k], ensure_ascii=False)
    fields["updated_at"] = now()
    cols = ", ".join(f"{k}=?" for k in fields)
    with connect() as c:
        c.execute(f"UPDATE drafts SET {cols} WHERE id=?", (*fields.values(), draft_id))


def claim_draft(draft_id, from_status, to_status):
    """状態が from_status のときだけ to_status に変える。ボタンの二度押しや投稿の重複を防ぐ。"""
    with connect() as c:
        cur = c.execute(
            "UPDATE drafts SET status=?, updated_at=? WHERE id=? AND status=?",
            (to_status, now(), draft_id, from_status),
        )
        return cur.rowcount == 1


def get_draft(draft_id):
    with connect() as c:
        return _row(c.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone())


def draft_by_message(message_id):
    with connect() as c:
        return _row(c.execute("SELECT * FROM drafts WHERE message_id=?", (message_id,)).fetchone())


def due_drafts(until_iso):
    """投稿時刻を過ぎた承認済みの下書き（古い順）。"""
    with connect() as c:
        rows = c.execute(
            "SELECT * FROM drafts WHERE status='approved' AND scheduled_at<=? ORDER BY scheduled_at",
            (until_iso,),
        ).fetchall()
    return [_row(r) for r in rows]


def drafts_by_status(statuses, account_id=None, since=None, limit=100):
    q = f"SELECT * FROM drafts WHERE status IN ({','.join('?' * len(statuses))})"
    params = list(statuses)
    if account_id:
        q += " AND account_id=?"
        params.append(account_id)
    if since:
        q += " AND scheduled_at>=?"
        params.append(since)
    q += " ORDER BY scheduled_at LIMIT ?"
    params.append(limit)
    with connect() as c:
        return [_row(r) for r in c.execute(q, params)]


def has_drafts_for(account_id, date_str):
    """その日の生成済み下書き（ボツ以外）があるか。同じ日の分を二重に作らないため。"""
    with connect() as c:
        row = c.execute(
            "SELECT 1 FROM drafts WHERE account_id=? AND kind!='manual' AND status!='rejected'"
            " AND substr(scheduled_at, 1, 10)=? LIMIT 1",
            (account_id, date_str),
        ).fetchone()
        return row is not None


def recent_texts(account_id=None, limit=30, exclude_id=None):
    """最近の投稿・予約済み・承認待ちの1投稿目の本文（新しい順）。ネタかぶりの防止とチェックに使う。"""
    q = "SELECT id, account_id, posts FROM drafts WHERE status IN ('pending','approved','posting','posted')"
    params = []
    if account_id:
        q += " AND account_id=?"
        params.append(account_id)
    q += " ORDER BY scheduled_at DESC LIMIT ?"
    params.append(limit)
    with connect() as c:
        rows = c.execute(q, params).fetchall()
    return [(r["account_id"], json.loads(r["posts"])[0]) for r in rows if r["id"] != exclude_id]
