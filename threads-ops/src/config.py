import json
import os
from datetime import timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
load_dotenv(ROOT / ".env")

JST = timezone(timedelta(hours=9))


def env(name, default=None):
    return os.environ.get(name) or default


def env_int(name):
    v = env(name)
    return int(v) if v else None


DISCORD_TOKEN = env("DISCORD_TOKEN")
GUILD_ID = env_int("DISCORD_GUILD_ID")
DRAFT_CHANNEL_ID = env_int("DRAFT_CHANNEL_ID")
REPORT_CHANNEL_ID = env_int("REPORT_CHANNEL_ID")
DRAFT_TIME = env("DRAFT_TIME", "20:00")          # 翌日分の下書きを作る時刻
POST_JITTER_MIN = int(env("POST_JITTER_MIN", "6"))  # 投稿時刻を 0〜N 分ランダムにずらす
CLAUDE_CMD = env("CLAUDE_CMD", "claude")

THREADS_APP_ID = env("THREADS_APP_ID")
THREADS_APP_SECRET = env("THREADS_APP_SECRET")
THREADS_REDIRECT_URI = env("THREADS_REDIRECT_URI")

DB_PATH = DATA / "threads.db"
ACCOUNTS_PATH = ROOT / "config" / "accounts.json"

MAX_POST_CHARS = 500     # Threads の1投稿あたりの上限
MAX_CHAIN = 5            # 1つの下書きでつなげる投稿（ツリー）の上限。Discordの編集画面の欄数に合わせる


def load_accounts():
    """config/accounts.json のアカウント設定を id → dict で返す（enabled=false も含む）。"""
    if not ACCOUNTS_PATH.exists():
        return {}
    with open(ACCOUNTS_PATH, encoding="utf-8") as f:
        accounts = json.load(f)["accounts"]
    for a in accounts:
        a.setdefault("enabled", True)
        a.setdefault("slots", [])
        a.setdefault("topics", [])
    return {a["id"]: a for a in accounts}


def prompt(name):
    return (ROOT / "prompts" / f"{name}.md").read_text(encoding="utf-8")
