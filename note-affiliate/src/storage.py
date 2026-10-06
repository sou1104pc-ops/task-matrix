"""SQLite でテーマ・下書き・投稿・成約を管理する。"""
import json
import sqlite3
from datetime import datetime

from .config import DB_PATH, JST, load_programs, load_seed_themes

SCHEMA = """
CREATE TABLE IF NOT EXISTS themes (
    id INTEGER PRIMARY KEY,
    theme TEXT UNIQUE NOT NULL,
    persona TEXT,
    programs TEXT,
    used_at TEXT
);
CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY,
    theme_id INTEGER,
    status TEXT NOT NULL,          -- pending / published / saved（note下書き保存のみ）/ rejected
    data TEXT NOT NULL,            -- 生成結果のJSON
    issues TEXT,                   -- チェック結果のJSON
    message_id INTEGER,
    note_url TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversions (
    id INTEGER PRIMARY KEY,
    program TEXT,
    amount INTEGER,
    note_url TEXT,
    memo TEXT,
    created_at TEXT NOT NULL
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
CREATE TABLE IF NOT EXISTS note_stats (
    date TEXT PRIMARY KEY,         -- JSTの日付 YYYY-MM-DD
    total_pv INTEGER,              -- 累計PV（その日の記録時点）
    total_like INTEGER,
    total_comment INTEGER,
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


def init():
    with connect() as c:
        for t in load_seed_themes():
            c.execute(
                "INSERT OR IGNORE INTO themes (theme, persona, programs) VALUES (?, ?, ?)",
                (t["theme"], t["persona"], t["programs"]),
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
def add_theme(theme, persona, programs):
    with connect() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO themes (theme, persona, programs) VALUES (?, ?, ?)",
            (theme, persona, programs),
        )
        return cur.rowcount > 0


def next_theme():
    """次に書くテーマを1つ返す。

    同じ案件の記事が続かないよう、案件を順番に回す。テーマのメインの案件（A8リンクが設定された最初の案件）を、
    programs.json の group（同じ話題のまとまり）と weight（回ってくる多さ）に沿って、
    「最後に書いてから何本たったか × 重み」が大きいまとまりから選ぶ。
    未使用が無くなったら、一番昔に使ったテーマを再利用する。
    A8リンクが設定された案件を1つも持たないテーマは飛ばす（リンク待ちの案件で毎朝失敗しないように）。
    """
    programs = load_programs()

    def main_program(row):
        return next((p for p in (row["programs"] or "").split("|") if programs.get(p, {}).get("url")), None)

    def group(p):  # 同じ話題の案件（例: 光回線とホームルーター）は1つのまとまりとして回す
        return programs[p].get("group") or p

    with connect() as c:
        unused = c.execute("SELECT * FROM themes WHERE used_at IS NULL ORDER BY id").fetchall()
        used = c.execute("SELECT * FROM themes WHERE used_at IS NOT NULL ORDER BY used_at").fetchall()
    used = [r for r in used if main_program(r)]
    last_pos = {}   # 案件・まとまり → 最後に使ったのが何本目か
    last_used = {}  # 案件 → 最後に使った日時
    for i, r in enumerate(used):
        p = main_program(r)
        last_pos[group(p)] = i
        last_used[p] = r["used_at"]
    weight = {}  # まとまりの重み（programs.json の weight。大きいほど多く回ってくる）
    for p, prog in programs.items():
        weight[group(p)] = max(weight.get(group(p), 0), float(prog.get("weight", 1)))

    candidates = [r for r in unused if main_program(r)]
    if not candidates:
        row = used[0] if used else None
        return dict(row) if row else None

    def key(r):
        p = main_program(r)
        g = group(p)
        # 「最後に書いてから何本たったか × 重み」が大きいまとまりほど先。まだ一度も書いていなければ、全部より前に書いた扱い
        since = len(used) - last_pos.get(g, -1)
        return (-since * weight[g], -weight[g], last_used.get(p, ""), r["id"])

    return dict(min(candidates, key=key))


def get_theme(theme_id):
    with connect() as c:
        row = c.execute("SELECT * FROM themes WHERE id=?", (theme_id,)).fetchone()
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
            "SELECT data FROM drafts WHERE status='published' ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [json.loads(r["data"])["title"] for r in rows]


def posts_since(iso):
    with connect() as c:
        rows = c.execute(
            "SELECT * FROM drafts WHERE status='published' AND updated_at>=? ORDER BY id", (iso,)
        ).fetchall()
    return [dict(r, data=json.loads(r["data"])) for r in rows]


# ---- conversions ----
def add_conversion(program, amount, note_url=None, memo=None):
    with connect() as c:
        c.execute(
            "INSERT INTO conversions (program, amount, note_url, memo, created_at) VALUES (?, ?, ?, ?, ?)",
            (program, amount, note_url, memo, now()),
        )


def conversions_since(iso):
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM conversions WHERE created_at>=?", (iso,))]


# ---- note統計のスナップショット ----
def save_note_stats(date, total_pv, total_like, total_comment):
    """その日の累計値を記録する（同じ日に複数回呼ばれたら上書き）。"""
    with connect() as c:
        c.execute(
            "INSERT OR REPLACE INTO note_stats (date, total_pv, total_like, total_comment, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (date, total_pv, total_like, total_comment, now()),
        )


def previous_note_stats(date):
    """指定日より前で一番新しいスナップショットを返す（差分の計算に使う）。"""
    with connect() as c:
        row = c.execute(
            "SELECT * FROM note_stats WHERE date < ? ORDER BY date DESC LIMIT 1", (date,)
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
