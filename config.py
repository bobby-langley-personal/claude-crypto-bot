from __future__ import annotations
"""
Central configuration for the crypto trading bot.

RISK LEVELS (change at runtime via the dashboard, or set RISK_LEVEL below as default):

  "low"    – Safety first. High confidence signals only, tight stops.
             Good for: BTC, ETH. Protects capital.

  "medium" – Balanced. Standard swing-trading parameters.
             Good for: Large-caps + established alts. Recommended starting point.

  "high"   – Growth focused. Wider stops let crypto volatility play out.
             Good for: Mid-cap alts, momentum plays.

  "degen"  – Max risk/reward. For volatile meme coins and micro-caps.
             Small positions but many of them; aims for 2x+ winners.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── API Keys ─────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY   = os.getenv("ANTHROPIC_API_KEY", "")
CRYPTOPANIC_API_KEY = os.getenv("CRYPTOPANIC_API_KEY", "")

# Coinbase CDP key file (from developer.coinbase.com → API Keys → Create API Key)
# Contains "name" and "privateKey" fields. NEVER commit this file.
# Used only when PAPER_TRADING = False.
CDP_KEY_FILE = os.getenv("CDP_KEY_FILE", "cdp_api_key.json")

# Legacy Advanced Trade API keys (kept for backwards compat, not used with CDP keys)
COINBASE_API_KEY    = os.getenv("COINBASE_API_KEY", "")
COINBASE_API_SECRET = os.getenv("COINBASE_API_SECRET", "")

# ── Default watchlist ─────────────────────────────────────────────────────────
# Editable at runtime via the dashboard ("Add Coin" / "Remove" buttons).
COINS = {
    # Large-caps — high liquidity, tight spreads
    "BTC":  {"product_id": "BTC-USD",  "news_query": "Bitcoin BTC"},
    "ETH":  {"product_id": "ETH-USD",  "news_query": "Ethereum ETH"},
    "SOL":  {"product_id": "SOL-USD",  "news_query": "Solana SOL"},
    # Mid-tier — strong communities, good news coverage
    "DOGE": {"product_id": "DOGE-USD", "news_query": "Dogecoin DOGE"},
    "ADA":  {"product_id": "ADA-USD",  "news_query": "Cardano ADA"},
    # Penny/meme coins — high volatility, good for data gathering
    "SHIB": {"product_id": "SHIB-USD", "news_query": "Shiba Inu SHIB"},
    "PEPE": {"product_id": "PEPE-USD", "news_query": "Pepe PEPE"},
    "BONK": {"product_id": "BONK-USD", "news_query": "Bonk BONK"},
}

# ── Risk profiles ─────────────────────────────────────────────────────────────
# These are loaded at runtime by BotController. The dashboard lets you switch
# between them without restarting.
RISK_PROFILES = {
    "low": {
        "label":                   "Low",
        "sentiment_buy_threshold": 8.0,   # Only buy on strong bullish signals
        "take_profit_pct":        10.0,   # Take profits at +10%
        "stop_loss_pct":          -4.0,   # Cut losses at -4%
        "trade_amount_usd":       250.0,  # Small position size
        "max_positions":           2,     # Few positions = less exposure
        "description":            "Safety first · high-confidence signals only · tight stops",
        "ideal_for":              "BTC & ETH · capital preservation",
        "color":                  "emerald",
    },
    "medium": {
        "label":                   "Medium",
        "sentiment_buy_threshold": 7.0,
        "take_profit_pct":        20.0,   # Standard swing-trade target
        "stop_loss_pct":          -6.0,   # Allows normal crypto volatility
        "trade_amount_usd":       500.0,
        "max_positions":          10,
        "description":            "Balanced · standard swing-trading parameters",
        "ideal_for":              "Large-caps + established alts",
        "color":                  "amber",
    },
    "high": {
        "label":                   "High",
        "sentiment_buy_threshold": 6.0,   # Lower bar — act on moderately positive news
        "take_profit_pct":        40.0,   # Hold for bigger moves
        "stop_loss_pct":         -10.0,   # Wide stop — crypto can drop 10% intraday
        "trade_amount_usd":       750.0,
        "max_positions":          10,
        "description":            "Growth focused · wide stops let volatility play out",
        "ideal_for":              "Mid-cap alts · momentum plays",
        "color":                  "orange",
    },
    "degen": {
        "label":                   "Degen",
        "sentiment_buy_threshold": 5.0,   # Any net-positive sentiment
        "take_profit_pct":       100.0,   # Aiming for the 2x
        "stop_loss_pct":         -20.0,   # Meme coins can wick -20% in minutes
        "trade_amount_usd":       150.0,  # Small per trade but many positions
        "max_positions":          10,
        "description":            "Max risk/reward · meme coins & micro-caps · small bets",
        "ideal_for":              "Meme coins · new launches · high volatility",
        "color":                  "red",
    },
}

# ── Active risk level ─────────────────────────────────────────────────────────
# Default. The dashboard lets you change this at runtime without restarting.
RISK_LEVEL = "medium"

if RISK_LEVEL not in RISK_PROFILES:
    raise ValueError(
        f"RISK_LEVEL='{RISK_LEVEL}' is invalid. "
        f"Choose from: {list(RISK_PROFILES.keys())}"
    )

# Module-level exports for backwards compat (terminal dashboard / main.py).
# BotController manages its own copy of these at runtime.
_profile             = RISK_PROFILES[RISK_LEVEL]
SENTIMENT_BUY_THRESHOLD = _profile["sentiment_buy_threshold"]
TAKE_PROFIT_PCT         = _profile["take_profit_pct"]
STOP_LOSS_PCT           = _profile["stop_loss_pct"]
TRADE_AMOUNT_USD        = _profile["trade_amount_usd"]
MAX_POSITIONS           = _profile["max_positions"]
RISK_DESCRIPTION        = _profile["description"]

# ── Trading mode ──────────────────────────────────────────────────────────────
#
# PAPER_TRADING = True   → all trades are simulated; your real money is safe
# PAPER_TRADING = False  → real orders placed on Coinbase via CDP API
#
# Before switching to False:
#   1. Create an isolated Coinbase Portfolio with only the funds you want to risk
#   2. Generate a CDP API key scoped to that portfolio (Trade permission only, NO Withdrawal)
#   3. Save the key as cdp_api_key.json in this directory
#   4. Run paper trading for a few weeks first to validate the strategy
#
PAPER_TRADING       = True
PAPER_STARTING_CASH = 10_000.0   # virtual cash for paper mode

# ── Timing ────────────────────────────────────────────────────────────────────
CHECK_INTERVAL_MINUTES  = 30   # How often the bot runs a full analysis cycle
NEWS_LOOKBACK_HOURS     = 6    # Fetch news from the last N hours
LEARNING_EVERY_N_CYCLES = 5    # Run AI strategy review after this many cycles

# ── Coin discovery ────────────────────────────────────────────────────────────
AUTO_DISCOVER_COINS = True   # Auto-add trending/high-potential coins to fill watchlist
MAX_WATCHLIST_COINS = 10     # Cap on total coins watched at any one time

# ── Emergency stop ────────────────────────────────────────────────────────────
# If BTC drops more than this % compared to the previous cycle price,
# trigger an emergency stop (sells all positions and halts the bot)
EMERGENCY_DROP_PCT = -7.0

# ── File paths ────────────────────────────────────────────────────────────────
LOG_FILE            = "bot.log"
PORTFOLIO_FILE      = "portfolio.json"
TRADES_FILE         = "trades.json"
LEARNING_FILE       = "learning.json"
LIVE_POSITIONS_FILE = "live_positions.json"
LIVE_TRADES_FILE    = "live_trades.json"
