"""Alpha Trading Floor — Configuration.

All paths, constants, and settings for the pipeline.
Brand: Alpha Trading Floor
Tagline: Trade Smart. Manage Risk. Compound Wealth.
"""
import os
from pathlib import Path

# ─── Project Paths ──────────────────────────────────────────────────────
PROJECT_DIR = Path(os.getenv("ALPHA_YOUTUBE_PROJECT_DIR", str(Path(__file__).resolve().parents[1]))).expanduser()
CONFIG_DIR = PROJECT_DIR / "config"
SRC_DIR = PROJECT_DIR / "app"
AUDIO_DIR = PROJECT_DIR / "audio"
CHARTS_DIR = PROJECT_DIR / "charts"
VIDEOS_DIR = PROJECT_DIR / "videos"
LOGS_DIR = PROJECT_DIR / "logs"
MUSIC_DIR = PROJECT_DIR / "music"
THUMBNAILS_DIR = PROJECT_DIR / "thumbnails"
DATA_DIR = PROJECT_DIR / "data"

for d in [AUDIO_DIR, CHARTS_DIR, VIDEOS_DIR, LOGS_DIR, MUSIC_DIR, THUMBNAILS_DIR, DATA_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ─── Brand ───────────────────────────────────────────────────────────────
BRAND_NAME = "Alpha Trading Floor"
TAGLINE = "Trade Smart. Manage Risk. Compound Wealth."
CHANNEL_ID = "UCLkZz11WQY_DzSsFFSNy7oQ"

# ─── Default Tickers (fallback when StockTwits unavailable) ──────────────
DEFAULT_TICKERS = ["NVDA", "TSLA", "AAPL", "META", "AMD",
                   "PLTR", "AMZN", "GOOG", "MSFT", "SPY"]

# ─── Market Index Tickers (for regime detection) ────────────────────────
MARKET_INDEX_TICKERS = ["SPY", "QQQ", "IWM", "^VIX"]

# ─── StockTwits Trending ─────────────────────────────────────────────────
STOCKTWITS_TRENDING_URL = "https://api.stocktwits.com/api/2/tstreams/trending.json"
STOCKTWITS_TOP_N = 10

# ─── Discord CTA ─────────────────────────────────────────────────────────
DISCORD_INVITE = "https://discord.gg/alphatradelevels"
DISCORD_CTA = "Join our Discord for daily levels, live trade ideas, and community — link in the description."

# ─── Video Settings ──────────────────────────────────────────────────────
VIDEO_RESOLUTION = (1920, 1080)
VIDEO_FPS = 24
CHART_BG_COLOR = "#0d1117"       # GitHub dark
CHART_TEXT_COLOR = "#c9d1d9"
CHART_GREEN = "#3fb950"
CHART_RED = "#f85149"
CHART_BLUE = "#58a6ff"
CHART_GRID_COLOR = "#21262d"
CHART_ACCENT = "#58a6ff"         # Blue accent for Alpha branding

# ─── Voice Settings (macOS TTS) ─────────────────────────────────────────
VOICE_PRIMARY = "Evan (Enhanced)"
VOICE_FALLBACK_1 = "Nathan (Enhanced)"
VOICE_FALLBACK_2 = "Alex"
VOICE_RATE = 145                   # Words per minute (natural pacing with breath pauses)
VOICE_STYLE = "CALM, CONFIDENT, ANALYTICAL, PROFESSIONAL, MEASURED"

# ─── Background Music ────────────────────────────────────────────────────
MUSIC_VOLUME_DB = -28              # Background music level in dB

# ─── LLM Settings ────────────────────────────────────────────────────────
CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
CODEX_CHAT_MODEL = "gpt-5.6-sol"

# ─── Google Auth (shared with money-machines) ────────────────────────────
GOOGLE_TOKEN_PATH = Path(os.getenv("GOOGLE_TOKEN_PATH", str(Path.home() / "money-machines" / "shared" / ".google_token.json"))).expanduser()
GOOGLE_ENV_PATH = Path(os.getenv("GOOGLE_ENV_PATH", str(Path.home() / "money-machines" / ".env"))).expanduser()

# ─── MySQL Database ──────────────────────────────────────────────────────
MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "alpha_trading_floor")

# ─── Cron Schedule ───────────────────────────────────────────────────────
# 4:30 PM ET = 1:30 PM PST (market close + buffer)
CRON_SCHEDULE = "30 13 * * 1-5"  # Mon-Fri at 1:30 PM PST
CRON_WEEKEND_SCHEDULE = "0 9 * * 0,6"  # Sat-Sun at 9 AM PST for advice content

# ─── Alpha Score Weights ────────────────────────────────────────────────
ALPHA_SCORE_WEIGHTS = {
    "momentum": 0.25,
    "relative_volume": 0.20,
    "technical_structure": 0.20,
    "catalyst_strength": 0.15,
    "sentiment": 0.10,
    "risk_quality": 0.10,
}

# ─── Alpha Score Risk Penalties ──────────────────────────────────────────
RISK_PENALTIES = {
    "rsi_overbought": 5,          # RSI > 80
    "extended_above_vwap": 8,     # Price > 8% above VWAP
    "low_relative_volume": 5,     # Rel vol < 0.70
    "no_catalyst": 3,            # No catalyst found
    "extreme_bullish_overbought": 7,  # Extreme bullish + RSI > 75
    "near_resistance": 5,        # Resistance within 1%
    "negative_catalyst": 10,     # Negative catalyst present
}

# ─── Alpha Score Classifications ─────────────────────────────────────────
ALPHA_CLASSIFICATIONS = {
    (90, 100): "EXCEPTIONAL",
    (80, 89): "STRONG",
    (70, 79): "CONSTRUCTIVE",
    (60, 69): "NEUTRAL",
    (50, 59): "WEAK",
    (0, 49): "HIGH RISK",
}

# ─── Market Regime Thresholds ───────────────────────────────────────────
REGIME_THRESHOLDS = {
    "vix_fear": 25,               # VIX above this = risk off
    "vix_complacent": 14,         # VIX below this = risk on
    "spy_sma_offset_pct": 2,      # SPY > 2% above SMA50 = risk on
    "qqq_iwm_ratio_trend": 0.02,  # QQQ/IWM improving = risk on
}

# ─── Pronunciation Dictionary Path ───────────────────────────────────────
PRONUNCIATION_PATH = CONFIG_DIR / "pronunciation.json"

# ─── Script QC Thresholds ────────────────────────────────────────────────
SCRIPT_MIN_WORDS = 1450
SCRIPT_MAX_WORDS = 1800
SCRIPT_MAX_DUPLICATE_RATIO = 0.15  # Max 15% similarity to previous script
