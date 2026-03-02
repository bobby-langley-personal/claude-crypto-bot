"""
Central configuration for the crypto trading bot.

HOW TO ADJUST TRADING AGGRESSIVENESS
--------------------------------------
Change RISK_LEVEL (line ~30) to one of three values:

  "conservative"  — Strict: only buy on strong signals, tight stop loss, small trades
  "moderate"      — Balanced: the sensible default (recommended to start)
  "aggressive"    — Loose: buy on weaker signals, hold longer, bigger trades

All other trading parameters (thresholds, stop loss, trade size) are set
automatically from the risk profile you choose.
"""
import os
from dotenv import load_dotenv

load_dotenv()  # Read from .env file

# ── API Keys ─────────────────────────────────────────────────────────────────
COINBASE_API_KEY    = os.getenv("COINBASE_API_KEY", "")
COINBASE_API_SECRET = os.getenv("COINBASE_API_SECRET", "")
ANTHROPIC_API_KEY   = os.getenv("ANTHROPIC_API_KEY", "")
CRYPTOPANIC_API_KEY = os.getenv("CRYPTOPANIC_API_KEY", "")   # https://cryptopanic.com
# NEWS_API_KEY      = os.getenv("NEWS_API_KEY", "")          # disabled – 401 issues

# ── Coins to watch and trade ──────────────────────────────────────────────────
# news_query is a human-readable label; coin symbol is used for API lookups
COINS = {
    "BTC":  {"product_id": "BTC-USD",  "news_query": "Bitcoin BTC"},
    "ETH":  {"product_id": "ETH-USD",  "news_query": "Ethereum ETH"},
    "SOL":  {"product_id": "SOL-USD",  "news_query": "Solana SOL"},
    "DOGE": {"product_id": "DOGE-USD", "news_query": "Dogecoin DOGE"},
}

# ── RISK LEVEL ───────────────────────────────────────────────────────────────
# !! Change this to adjust how the bot trades !!
#
# "conservative" – Safer.  Score >= 8 to buy, -3% stop loss, $250/trade, max 2 positions
# "moderate"     – Default. Score >= 7 to buy, -5% stop loss, $500/trade, max 4 positions
# "aggressive"   – Riskier. Score >= 6 to buy, -8% stop loss, $1000/trade, max 4 positions
RISK_LEVEL = "aggressive"

_RISK_PROFILES = {
    "conservative": {
        "sentiment_buy_threshold": 8.0,   # Only buy on very strong bullish signals
        "take_profit_pct":         8.0,   # Sell when up +8%
        "stop_loss_pct":          -3.0,   # Sell when down -3%
        "trade_amount_usd":       250.0,  # Spend $250 per trade
        "max_positions":           2,     # Hold at most 2 coins at a time
        "description":            "Strict signals · tight stops · small trades",
    },
    "moderate": {
        "sentiment_buy_threshold": 7.0,
        "take_profit_pct":        12.0,   # Sell when up +12%
        "stop_loss_pct":          -5.0,   # Sell when down -5%
        "trade_amount_usd":       500.0,
        "max_positions":           4,
        "description":            "Balanced approach · recommended starting point",
    },
    "aggressive": {
        "sentiment_buy_threshold": 6.0,   # Buy on moderately positive signals
        "take_profit_pct":        25.0,   # Hold for larger gains (+25%)
        "stop_loss_pct":          -8.0,   # Wider stop loss (-8%)
        "trade_amount_usd":      1000.0,
        "max_positions":           4,
        "description":            "Loose signals · wide stops · large trades",
    },
}

if RISK_LEVEL not in _RISK_PROFILES:
    raise ValueError(
        f"RISK_LEVEL='{RISK_LEVEL}' is invalid. "
        f"Choose from: {list(_RISK_PROFILES.keys())}"
    )

# These are loaded from the active risk profile.
# The rest of the codebase uses these variable names directly.
_profile = _RISK_PROFILES[RISK_LEVEL]
SENTIMENT_BUY_THRESHOLD  = _profile["sentiment_buy_threshold"]
TAKE_PROFIT_PCT          = _profile["take_profit_pct"]
STOP_LOSS_PCT            = _profile["stop_loss_pct"]
TRADE_AMOUNT_USD         = _profile["trade_amount_usd"]
MAX_POSITIONS            = _profile["max_positions"]
RISK_DESCRIPTION         = _profile["description"]

# ── Paper trading ─────────────────────────────────────────────────────────────
PAPER_TRADING       = True       # True = simulate, False = real money (be careful!)
PAPER_STARTING_CASH = 10_000.0   # Virtual starting balance in USD

# ── Timing ────────────────────────────────────────────────────────────────────
CHECK_INTERVAL_MINUTES = 30   # How often the bot runs a full analysis cycle
NEWS_LOOKBACK_HOURS    = 6    # Fetch news from the last N hours

# ── File paths ────────────────────────────────────────────────────────────────
LOG_FILE       = "bot.log"
PORTFOLIO_FILE = "portfolio.json"
TRADES_FILE    = "trades.json"
