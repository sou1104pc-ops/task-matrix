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
MATERIAL_CHANNEL_ID = env_int("MATERIAL_CHANNEL_ID")  # 材料を送るフォーラムチャンネル
DAILY_TIME = env("DAILY_TIME", "07:00")
DAILY_REPORT_TIME = env("DAILY_REPORT_TIME", "21:00")
CLAUDE_CMD = env("CLAUDE_CMD", "claude")
# true: 承認したら公開申請まで行う（Brainの審査後に公開）/ false: Brainの下書き保存で止める
AUTO_PUBLISH = env("AUTO_PUBLISH", "true").lower() == "true"


def _schedule(text):
    """「0:100,5:1980,7:2980」→ [(0, 100), (5, 1980), (7, 2980)]（公開から何日目に何円にするか）"""
    steps = sorted((int(d), int(p)) for d, p in (s.split(":") for s in text.replace(" ", "").split(",") if s))
    if not steps or steps[0][0] != 0:
        raise SystemExit(f"PRICE_SCHEDULE は 0日目から書いてください（今: {text}）")
    return steps


# 価格の自動変更。公開（審査が通った日時）から数えて、何日目に何円にするか
PRICE_SCHEDULE = _schedule(env("PRICE_SCHEDULE", "0:100,5:1980,7:2980"))
DEFAULT_PRICE = PRICE_SCHEDULE[0][1]
# カテゴリーはAIが記事に合わせて選ぶ。選べなかったときの既定値
DEFAULT_CATEGORY = env("DEFAULT_CATEGORY", "ビジネス")
DEFAULT_SUBCATEGORY = env("DEFAULT_SUBCATEGORY", "")
# 紹介料（Brainのアフィリエイト）。0 で紹介なし、0.1〜0.5（10〜50%）。アカウントごとに config/accounts.json で変えられる
AFFILIATE_RATE = float(env("AFFILIATE_RATE", "0.5"))
AFFILIATE_RATES = (0, 0.1, 0.2, 0.3, 0.4, 0.5)
if AFFILIATE_RATE not in AFFILIATE_RATES:
    raise SystemExit(f"AFFILIATE_RATE は {AFFILIATE_RATES} のどれかにしてください（今: {AFFILIATE_RATE}）")

# メイン画像（サムネ）は ChatGPT の画像生成で作る。OPENAI_API_KEY が空なら今までのHTMLのサムネ
OPENAI_API_KEY = env("OPENAI_API_KEY", "")
OPENAI_IMAGE_MODEL = env("OPENAI_IMAGE_MODEL", "gpt-image-1")
OPENAI_IMAGE_QUALITY = env("OPENAI_IMAGE_QUALITY", "high")

DB_PATH = DATA / "brain.db"
DRAFTS_DIR = DATA / "drafts"
SCREENSHOTS_DIR = DATA / "screenshots"
MATERIALS_DIR = DATA / "materials"


def load_seed_themes():
    with open(ROOT / "config" / "themes.csv", encoding="utf-8") as f:
        return [t for t in csv.DictReader(f) if (t.get("theme") or "").strip()]


def prompt(name):
    return (ROOT / "prompts" / f"{name}.md").read_text(encoding="utf-8")
