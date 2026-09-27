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
NOTE_USER = env("NOTE_USER", "marketingtanuki")
CLAUDE_CMD = env("CLAUDE_CMD", "claude")
AUTO_PUBLISH = env("AUTO_PUBLISH", "true").lower() == "true"

DB_PATH = DATA / "affiliate.db"
BROWSER_PROFILE = DATA / "browser-profile"
DRAFTS_DIR = DATA / "drafts"
SCREENSHOTS_DIR = DATA / "screenshots"


def load_programs():
    with open(ROOT / "config" / "programs.json", encoding="utf-8") as f:
        return {p["id"]: p for p in json.load(f)["programs"]}


def load_seed_themes():
    with open(ROOT / "config" / "themes.csv", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_selectors():
    with open(ROOT / "config" / "note_selectors.json", encoding="utf-8") as f:
        return json.load(f)


def prompt(name):
    return (ROOT / "prompts" / f"{name}.md").read_text(encoding="utf-8")
