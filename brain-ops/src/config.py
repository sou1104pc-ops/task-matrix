import csv
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


def env_int(name):
    v = env(name)
    return int(v) if v else None


DISCORD_TOKEN = env("DISCORD_TOKEN")
GUILD_ID = env_int("DISCORD_GUILD_ID")
DRAFT_CHANNEL_ID = env_int("DRAFT_CHANNEL_ID")
REPORT_CHANNEL_ID = env_int("REPORT_CHANNEL_ID")
SECRETARY_CHANNEL_ID = env_int("SECRETARY_CHANNEL_ID")
DAILY_TIME = env("DAILY_TIME", "07:00")
DAILY_REPORT_TIME = env("DAILY_REPORT_TIME", "21:00")
CLAUDE_CMD = env("CLAUDE_CMD", "claude")
# true: 承認したら公開申請まで行う（Brainの審査後に公開）/ false: Brainの下書き保存で止める
AUTO_PUBLISH = env("AUTO_PUBLISH", "true").lower() == "true"

# テーマに指定が無いときの販売設定
DEFAULT_PRICE = int(env("DEFAULT_PRICE", "980"))
DEFAULT_CATEGORY = env("DEFAULT_CATEGORY", "ビジネス")
DEFAULT_SUBCATEGORY = env("DEFAULT_SUBCATEGORY", "")
# 紹介料（Brainのアフィリエイト）。0 で紹介なし、0.1〜0.5（10〜50%）
AFFILIATE_RATE = float(env("AFFILIATE_RATE", "0"))
AFFILIATE_RATES = (0, 0.1, 0.2, 0.3, 0.4, 0.5)
if AFFILIATE_RATE not in AFFILIATE_RATES:
    raise SystemExit(f"AFFILIATE_RATE は {AFFILIATE_RATES} のどれかにしてください（今: {AFFILIATE_RATE}）")

DB_PATH = DATA / "brain.db"
BROWSER_PROFILE = DATA / "browser-profile"
DRAFTS_DIR = DATA / "drafts"
SCREENSHOTS_DIR = DATA / "screenshots"


def load_seed_themes():
    with open(ROOT / "config" / "themes.csv", encoding="utf-8") as f:
        return [t for t in csv.DictReader(f) if (t.get("theme") or "").strip()]


def prompt(name):
    return (ROOT / "prompts" / f"{name}.md").read_text(encoding="utf-8")
