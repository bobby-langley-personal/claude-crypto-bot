"""
Data validation module – guards against inaccurate data before trades.

WHY THIS EXISTS
---------------
Two main concerns:

1. Price accuracy
   The bot fetches prices from Coinbase only. If that source has a data
   error or stale value, a bad trade could be made. This module fetches
   the same prices from CoinGecko (independent free API, no key needed)
   and compares them. If they disagree by more than PRICE_DIVERGENCE_PCT,
   a warning badge is shown on the dashboard and logged.

2. AI "hallucination" / repetitive outputs
   Claude scores news sentiment 1–10. There's a small risk that Claude
   could return a default score without actually reading the news
   (e.g., always returning 5.0). This module tracks score history per
   coin and flags when the same score has been returned too many times
   in a row. It also warns when there aren't enough articles to support
   a confident opinion.

RESULTS
-------
Each function returns a dict with:
  - "ok"       : bool  – True = data looks healthy
  - "badge"    : str   – Short indicator for the dashboard ("✓", "⚠", "?")
  - "warnings" : list  – Human-readable warning strings
"""
import logging
import requests

log = logging.getLogger(__name__)

# Flag price divergence above this threshold (2 % by default)
PRICE_DIVERGENCE_PCT = 2.0

# Map coin tickers to CoinGecko IDs
_CG_IDS: dict[str, str] = {
    "BTC":  "bitcoin",
    "ETH":  "ethereum",
    "SOL":  "solana",
    "DOGE": "dogecoin",
}

# Rolling score history – last 10 scores per coin
_score_history: dict[str, list[float]] = {}


# ── Price validation ──────────────────────────────────────────────────────────

def _fetch_coingecko(symbols: list[str]) -> dict[str, float]:
    """Fetch USD spot prices from CoinGecko. Returns {} on failure."""
    ids = ",".join(_CG_IDS[s] for s in symbols if s in _CG_IDS)
    if not ids:
        return {}
    try:
        resp = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": ids, "vs_currencies": "usd"},
            headers={"Accept": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            sym: float(data[cg_id]["usd"])
            for sym, cg_id in _CG_IDS.items()
            if cg_id in data
        }
    except Exception as e:
        log.warning(f"[DataValidator] CoinGecko fetch failed: {e}")
        return {}


def validate_prices(coinbase_prices: dict[str, float]) -> dict[str, dict]:
    """
    Compare Coinbase prices against CoinGecko and flag divergences.

    Args:
        coinbase_prices: {symbol: price} from coinbase_client.get_all_prices()

    Returns:
        {
          symbol: {
            "coinbase":       float,
            "coingecko":      float | None,
            "divergence_pct": float | None,   # None if CoinGecko unavailable
            "ok":             bool,
            "badge":          str,            # "✓" | "⚠" | "?"
            "warnings":       list[str],
          }
        }
    """
    cg = _fetch_coingecko(list(coinbase_prices.keys()))
    results: dict[str, dict] = {}

    for sym, cb_price in coinbase_prices.items():
        cg_price  = cg.get(sym)
        warnings: list[str] = []

        if cg_price is not None:
            diff_pct = abs(cb_price - cg_price) / cb_price * 100
            ok       = diff_pct <= PRICE_DIVERGENCE_PCT
            badge    = "✓" if ok else "⚠"

            if not ok:
                msg = (
                    f"{sym} price divergence {diff_pct:.1f}%: "
                    f"Coinbase ${cb_price:,.4f} vs CoinGecko ${cg_price:,.4f}"
                )
                warnings.append(msg)
                log.warning(f"[DataValidator] PRICE ALERT – {msg}")
        else:
            diff_pct = None
            ok       = True   # can't verify, but not confirmed bad
            badge    = "?"
            warnings.append(f"{sym}: CoinGecko unavailable (single source)")

        results[sym] = {
            "coinbase":       cb_price,
            "coingecko":      cg_price,
            "divergence_pct": diff_pct,
            "ok":             ok,
            "badge":          badge,
            "warnings":       warnings,
        }

    return results


# ── Sentiment validation ──────────────────────────────────────────────────────

def validate_sentiment(
    symbol: str,
    score: float,
    articles_count: int,
) -> dict:
    """
    Sanity-check a Claude sentiment score before trusting it for trading.

    Checks performed:
      1. Was there enough news to justify the score?
         (0 articles = no basis, 1-2 = weak basis)
      2. Has Claude been returning the same score repeatedly?
         (5+ identical scores in a row may indicate it isn't reading the news)

    Args:
        symbol:         Coin ticker ("BTC", etc.)
        score:          Claude's sentiment score (1.0–10.0)
        articles_count: Number of news articles that were fed to Claude

    Returns:
        {
          "ok":         bool,
          "confidence": "high" | "medium" | "low",
          "badge":      str,          # "✓" | "⚠"
          "warnings":   list[str],
        }
    """
    warnings: list[str] = []

    # Track rolling score history
    history = _score_history.setdefault(symbol, [])
    history.append(score)
    if len(history) > 10:
        history.pop(0)

    # Check 1: not enough articles
    if articles_count == 0:
        warnings.append("No news found – score has no basis")
    elif articles_count < 3:
        warnings.append(f"Only {articles_count} article(s) – thin data")

    # Check 2: score stuck (possible repetitive/hallucinated output)
    if len(history) >= 5 and len(set(history[-5:])) == 1:
        warnings.append(
            f"Score stuck at {score:.1f} for last 5 cycles – may not be reading news"
        )
        log.warning(
            f"[DataValidator] SENTIMENT ALERT {symbol}: "
            f"score has been {score:.1f}/10 for 5+ consecutive cycles"
        )

    if warnings:
        confidence = "low" if articles_count == 0 else "medium"
        badge = "⚠"
    else:
        confidence = "high"
        badge = "✓"

    return {
        "ok":         not warnings,
        "confidence": confidence,
        "badge":      badge,
        "warnings":   warnings,
    }


def validate_news_freshness(articles: list[dict]) -> dict:
    """
    Check how fresh the fetched news articles are.

    Returns:
        {
          "total":       int,
          "fresh_count": int,   # articles from last 24 h
          "oldest":      str,   # date string of oldest article
          "ok":          bool,
        }
    """
    from datetime import datetime, timezone, timedelta

    now   = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)
    fresh  = 0
    oldest_dt = None

    for a in articles:
        date_str = (a.get("publishedAt") or "")[:10]
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if dt >= cutoff:
                fresh += 1
            if oldest_dt is None or dt < oldest_dt:
                oldest_dt = dt
        except ValueError:
            pass

    return {
        "total":       len(articles),
        "fresh_count": fresh,
        "oldest":      oldest_dt.strftime("%Y-%m-%d") if oldest_dt else "unknown",
        "ok":          fresh > 0 or len(articles) == 0,
    }
