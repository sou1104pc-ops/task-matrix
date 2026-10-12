import csv
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
    return os.environ.get(name, default)


def env_int(name, default=None):
    v = env(name)
    return int(v) if v else default


DISCORD_TOKEN = env("DISCORD_TOKEN")
GUILD_ID = env_int("DISCORD_GUILD_ID")
DRAFT_CHANNEL_ID = env_int("DRAFT_CHANNEL_ID")
REPORT_CHANNEL_ID = env_int("REPORT_CHANNEL_ID")
DAILY_TIME = env("DAILY_TIME", "07:00")
DAILY_REPORT_TIME = env("DAILY_REPORT_TIME", "21:00")
CLAUDE_CMD = env("CLAUDE_CMD", "claude")
AUTO_PUBLISH = env("AUTO_PUBLISH", "false").lower() == "true"

AUTHOR = {
    "last": env("AUTHOR_LAST", ""),
    "first": env("AUTHOR_FIRST", ""),
    "last_kana": env("AUTHOR_LAST_KANA", ""),
    "first_kana": env("AUTHOR_FIRST_KANA", ""),
    "last_romaji": env("AUTHOR_LAST_ROMAJI", ""),
    "first_romaji": env("AUTHOR_FIRST_ROMAJI", ""),
}
AUTHOR_NAME = f"{AUTHOR['last']} {AUTHOR['first']}".strip()
PRICE_JPY = env_int("PRICE_JPY", 500)
KDP_SELECT = env("KDP_SELECT", "true").lower() == "true"
BOOK_CHARS = env_int("BOOK_CHARS", 25000)
KEYWORD_MAX_CHARS = env_int("KEYWORD_MAX_CHARS", 50)

# ---- 公式LINE（UTAGE連携）----
UTAGE_API_KEY = env("UTAGE_API_KEY", "")
UTAGE_ACCOUNT_ID = env("UTAGE_ACCOUNT_ID", "")
UTAGE_SCENARIO_ID = env("UTAGE_SCENARIO_ID", "")
UTAGE_FUNNEL_IDS = [f.strip() for f in env("UTAGE_FUNNEL_IDS", "").split(",") if f.strip()]
LINE_URL = env("LINE_URL", "")                      # 登録経路を作れなかったときに使う共通の友だち追加URL
LINE_GIFT_TITLE = env("LINE_GIFT_TITLE", "")        # 特典の名前（全部の本で共通）
LINE_GIFT_TEXT = env("LINE_GIFT_TEXT", "")          # 特典の説明（1〜3文）

DB_PATH = DATA / "kindle.db"
BROWSER_PROFILE = DATA / "browser-profile"
BOOKS_DIR = DATA / "books"
SCREENSHOTS_DIR = DATA / "screenshots"


def load_seed_themes():
    with open(ROOT / "config" / "themes.csv", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_selectors():
    with open(ROOT / "config" / "kdp_selectors.json", encoding="utf-8") as f:
        return json.load(f)


def prompt(name):
    return (ROOT / "prompts" / f"{name}.md").read_text(encoding="utf-8")
