from __future__ import annotations
"""
Technical indicators for crypto trading signals.

Calculates RSI, MACD, and Bollinger Bands from hourly price history
fetched from CoinGecko's free public API (no key required).

These are used as a second layer of validation alongside sentiment scores:
  - RSI > 72 blocks a buy (overbought — wait for pullback)
  - RSI > 80 triggers an early sell (extremely overbought)
  - MACD and Bollinger position provide supporting context

Data source: CoinGecko /coins/{id}/market_chart (hourly, last 48 h)
No extra dependencies — all maths are pure Python.
"""
import logging
import math
import time
from functools import lru_cache

import requests

log = logging.getLogger(__name__)

# CoinGecko requires a real user-agent or it rate-limits aggressively
_UA = "crypto-sentiment-bot/1.0 (educational)"

# Simple in-process price-history cache — keyed by (cg_id, hour_bucket)
# so we don't hit CoinGecko more than once per hour per coin.
_history_cache: dict = {}
_CACHE_TTL = 3600  # seconds


# ── Price history ──────────────────────────────────────────────────────────────

def fetch_price_history(cg_id: str, hours: int = 48) -> list[float]:
    """
    Fetch hourly close prices from CoinGecko for the last `hours` hours.

    Returns a list of floats (oldest first), or [] on failure.
    Result is cached for 1 hour so repeated calls within a cycle are free.
    """
    bucket = int(time.time() // _CACHE_TTL)
    cache_key = (cg_id, bucket, hours)
    if cache_key in _history_cache:
        return _history_cache[cache_key]

    try:
        # CoinGecko free tier: omit 'interval' and use days=2-90 to get
        # automatic hourly granularity (interval=hourly is Enterprise-only).
        days = max(2, hours // 24 + 1)
        resp = requests.get(
            f"https://api.coingecko.com/api/v3/coins/{cg_id}/market_chart",
            params={"vs_currency": "usd", "days": days},
            headers={"Accept": "application/json", "User-Agent": _UA},
            timeout=10,
        )
        resp.raise_for_status()
        prices_raw = resp.json().get("prices", [])
        # Each entry is [timestamp_ms, price]; take last `hours` data points
        closes = [float(p[1]) for p in prices_raw][-hours:]
        _history_cache[cache_key] = closes
        return closes
    except Exception as e:
        log.warning(f"[TechIndicators] price history fetch failed for {cg_id}: {e}")
        return []


# ── EMA helper ────────────────────────────────────────────────────────────────

def _ema(values: list[float], period: int) -> list[float]:
    """Exponential moving average over a list of values."""
    if not values or period <= 0:
        return []
    k = 2.0 / (period + 1)
    emas = [values[0]]
    for v in values[1:]:
        emas.append(v * k + emas[-1] * (1 - k))
    return emas


# ── RSI ───────────────────────────────────────────────────────────────────────

def calc_rsi(closes: list[float], period: int = 14) -> float | None:
    """
    Relative Strength Index (0–100).
    Returns None if there aren't enough data points.

    Interpretation:
      < 30  — oversold (potential buy)
      30–70 — neutral
      > 70  — overbought (avoid buying, consider selling)
    """
    if len(closes) < period + 1:
        return None

    gains, losses = [], []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))

    # Initial averages
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    # Wilder's smoothing for subsequent values
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)


# ── MACD ──────────────────────────────────────────────────────────────────────

def calc_macd(
    closes: list[float],
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> dict | None:
    """
    Moving Average Convergence Divergence.
    Returns None if there aren't enough data points.

    Returns:
        {
          "value":     float,   # MACD line (EMA12 - EMA26)
          "signal":    float,   # Signal line (EMA9 of MACD)
          "histogram": float,   # MACD - Signal
          "bullish":   bool,    # True when MACD > Signal (bullish momentum)
        }
    """
    if len(closes) < slow + signal_period:
        return None

    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)

    # Both EMAs are the same length (initialized from closes[0]), subtract directly
    macd_line = [ema_fast[i] - ema_slow[i] for i in range(len(ema_slow))]

    signal_line = _ema(macd_line, signal_period)

    macd_val    = macd_line[-1]
    signal_val  = signal_line[-1]
    histogram   = macd_val - signal_val

    return {
        "value":     round(macd_val,   6),
        "signal":    round(signal_val, 6),
        "histogram": round(histogram,  6),
        "bullish":   macd_val > signal_val,
    }


# ── Bollinger Bands ───────────────────────────────────────────────────────────

def calc_bollinger(
    closes: list[float],
    period: int = 20,
    stddev: float = 2.0,
) -> dict | None:
    """
    Bollinger Bands.
    Returns None if there aren't enough data points.

    Returns:
        {
          "upper":  float,
          "middle": float,   # SMA(20)
          "lower":  float,
          "pct_b":  float,   # 0.0 = at lower band, 1.0 = at upper band
                             # < 0.2 = near lower (oversold signal)
                             # > 0.8 = near upper (overbought signal)
        }
    """
    if len(closes) < period:
        return None

    window = closes[-period:]
    sma    = sum(window) / period
    variance = sum((p - sma) ** 2 for p in window) / period
    sd     = math.sqrt(variance)

    upper = sma + stddev * sd
    lower = sma - stddev * sd

    price  = closes[-1]
    band_w = upper - lower
    pct_b  = (price - lower) / band_w if band_w > 0 else 0.5

    return {
        "upper":  round(upper, 6),
        "middle": round(sma,   6),
        "lower":  round(lower, 6),
        "pct_b":  round(pct_b, 3),
    }


# ── Combined signal ───────────────────────────────────────────────────────────

def get_signals(
    symbol: str,
    current_price: float,
    cg_id: str | None = None,
) -> dict:
    """
    Fetch price history and compute RSI, MACD, and Bollinger Bands.

    Args:
        symbol:        Coin ticker (e.g. "BTC") — used for CoinGecko ID lookup
        current_price: Latest price (used for Bollinger %B if history unavailable)
        cg_id:         CoinGecko ID override (auto-looked-up from data_validator if None)

    Returns:
        {
          "rsi":       float | None,
          "macd":      dict  | None,
          "bollinger": dict  | None,
          "signal":    "BUY" | "HOLD" | "SELL",
          "summary":   str,   # e.g. "RSI 45 · MACD ▲ · BB 32%"
          "warnings":  list[str],
          "error":     str | None,
        }
    """
    # Resolve CoinGecko ID
    if not cg_id:
        try:
            from data_validator import _CG_IDS, lookup_coingecko_id
            cg_id = _CG_IDS.get(symbol.upper()) or lookup_coingecko_id(symbol)
        except Exception:
            pass

    if not cg_id:
        return {
            "rsi": None, "macd": None, "bollinger": None,
            "signal": "HOLD", "summary": "no CoinGecko ID",
            "warnings": [f"No CoinGecko ID for {symbol} — skipping technicals"],
            "error": f"no CoinGecko ID for {symbol}",
        }

    closes = fetch_price_history(cg_id, hours=48)
    if len(closes) < 28:
        return {
            "rsi": None, "macd": None, "bollinger": None,
            "signal": "HOLD", "summary": "insufficient history",
            "warnings": [f"{symbol}: Collecting price history ({len(closes)} of 28 points) — indicators available soon"],
            "error": None,
        }

    rsi        = calc_rsi(closes)
    macd       = calc_macd(closes)
    bollinger  = calc_bollinger(closes)
    warnings: list[str] = []

    # ── Determine composite signal ────────────────────────────────────────────
    buy_points  = 0
    sell_points = 0

    if rsi is not None:
        if rsi < 35:
            buy_points  += 2   # Strong oversold
        elif rsi < 50:
            buy_points  += 1   # Mild oversold
        elif rsi > 75:
            sell_points += 2   # Strong overbought
        elif rsi > 65:
            sell_points += 1   # Mild overbought
        if rsi > 72:
            warnings.append(f"RSI {rsi:.0f} — overbought, caution on new entries")

    if macd:
        if macd["bullish"]:
            buy_points  += 1
        else:
            sell_points += 1

    if bollinger:
        pb = bollinger["pct_b"]
        if pb < 0.2:
            buy_points  += 1   # Near lower band — potential bounce
        elif pb > 0.8:
            sell_points += 1   # Near upper band — potential reversal
        if pb > 0.85:
            warnings.append(f"BB {pb:.0%} — price at upper Bollinger band")

    if sell_points >= 3:
        signal = "SELL"
    elif buy_points >= 2:
        signal = "BUY"
    else:
        signal = "HOLD"

    # ── Build summary string ──────────────────────────────────────────────────
    parts = []
    if rsi is not None:
        rsi_icon = "↓" if rsi < 35 else ("↑" if rsi > 65 else "→")
        parts.append(f"RSI {rsi:.0f}{rsi_icon}")
    if macd:
        parts.append(f"MACD {'▲' if macd['bullish'] else '▼'}")
    if bollinger:
        parts.append(f"BB {bollinger['pct_b']:.0%}")

    summary = " · ".join(parts) if parts else "no data"

    return {
        "rsi":       rsi,
        "macd":      macd,
        "bollinger": bollinger,
        "signal":    signal,
        "summary":   summary,
        "warnings":  warnings,
        "error":     None,
    }
