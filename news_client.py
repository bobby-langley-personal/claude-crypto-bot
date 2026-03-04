from __future__ import annotations
"""
Fetch crypto news headlines and market sentiment.

  PRIMARY  – RSS feeds from CoinTelegraph, Decrypt, Bitcoinist (no API key needed)
             Filtered by coin name / ticker in the headline.

  MARKET   – Alternative.me Fear & Greed Index (free, no key)
             Injected as context line into the formatted prompt.

  LEGACY   – CryptoPanic / Reddit kept as stubs but both are currently down.
"""
import logging
import threading
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import re

import requests

from cost_tracker import cost_tracker

log = logging.getLogger(__name__)


def _strip_html(text: str) -> str:
    """Remove HTML tags and normalise whitespace."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&[a-z]+;", " ", text)   # &amp; &nbsp; etc.
    return re.sub(r"\s+", " ", text).strip()

_UA = "Mozilla/5.0 crypto-bot/1.0 (educational paper trading)"

# ── RSS sources ───────────────────────────────────────────────────────────────

_RSS_FEEDS = [
    ("CoinTelegraph", "https://cointelegraph.com/rss"),
    ("Decrypt",       "https://decrypt.co/feed"),
    ("Bitcoinist",    "https://bitcoinist.com/feed/"),
]

# coin symbol → names/keywords to search headlines for
_COIN_KEYWORDS: dict[str, list[str]] = {
    "BTC":   ["bitcoin", "btc"],
    "ETH":   ["ethereum", "eth", "ether"],
    "SOL":   ["solana", "sol"],
    "DOGE":  ["dogecoin", "doge"],
    "SHIB":  ["shiba", "shib"],
    "PEPE":  ["pepe"],
    "BONK":  ["bonk"],
    "WIF":   ["dogwifhat", "wif"],
    "FLOKI": ["floki"],
    "ADA":   ["cardano", "ada"],
    "AVAX":  ["avalanche", "avax"],
    "LINK":  ["chainlink", "link"],
    "DOT":   ["polkadot", "dot"],
    "XRP":   ["ripple", "xrp"],
    "LTC":   ["litecoin", "ltc"],
    "ARB":   ["arbitrum", "arb"],
    "OP":    ["optimism"],
    "SUI":   ["sui"],
}

# Shared RSS cache: feed_url → (timestamp, list[dict])
_rss_cache: dict[str, tuple[float, list[dict]]] = {}
_rss_lock = threading.Lock()
_RSS_TTL = 600   # seconds


def register_coin_subreddits(symbol: str, subreddits: list[str]) -> None:
    """Kept for backwards-compat; Reddit is currently blocked (403)."""
    pass


# ── Fear & Greed ──────────────────────────────────────────────────────────────

_fg_cache: tuple[float, dict] | None = None
_fg_lock = threading.Lock()
_FG_TTL = 1800   # 30 min


def _get_fear_greed() -> dict | None:
    """Return the latest Fear & Greed data dict, cached 30 min."""
    global _fg_cache
    import time
    with _fg_lock:
        if _fg_cache and time.time() - _fg_cache[0] < _FG_TTL:
            return _fg_cache[1]
        try:
            r = requests.get(
                "https://api.alternative.me/fng/?limit=1",
                timeout=6,
            )
            r.raise_for_status()
            
            # Track API call (Fear & Greed is free but good to track usage)
            cost_tracker.track_api_call("fear_greed")
            
            data = r.json()["data"][0]
            _fg_cache = (time.time(), data)
            return data
        except Exception as e:
            log.debug(f"Fear&Greed fetch failed: {e}")
            return _fg_cache[1] if _fg_cache else None


# ── RSS helpers ───────────────────────────────────────────────────────────────

def _fetch_feed(name: str, url: str) -> list[dict]:
    """Fetch and parse one RSS feed, with a 10-minute cache."""
    import time
    with _rss_lock:
        cached = _rss_cache.get(url)
        if cached and time.time() - cached[0] < _RSS_TTL:
            return cached[1]

    try:
        r = requests.get(url, headers={"User-Agent": _UA}, timeout=10)
        r.raise_for_status()
        
        # Track RSS feed fetch (free but good to track usage)
        cost_tracker.track_api_call("news_rss")
        
        root = ET.fromstring(r.text)
        articles = []
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            desc  = (item.findtext("description") or "").strip()
            pub   = (item.findtext("pubDate") or "")
            if not title:
                continue
            # Parse date
            try:
                dt = datetime.strptime(pub, "%a, %d %b %Y %H:%M:%S %z")
                date = dt.strftime("%Y-%m-%d")
            except Exception:
                date = ""
            articles.append({
                "title":       _strip_html(title),
                "description": _strip_html(desc)[:300],
                "publishedAt": date,
                "source":      name,
            })

        with _rss_lock:
            _rss_cache[url] = (time.time(), articles)
        return articles

    except Exception as e:
        log.warning(f"RSS fetch failed ({name}): {e}")
        with _rss_lock:
            if url in _rss_cache:
                return _rss_cache[url][1]   # return stale on error
        return []


def _fetch_rss(coin_symbol: str, max_articles: int) -> list[dict]:
    """Fetch all RSS feeds and filter by coin relevance."""
    keywords = _COIN_KEYWORDS.get(coin_symbol.upper(), [coin_symbol.lower()])
    # Always include the bare symbol (e.g. "SOL") as a keyword
    if coin_symbol.lower() not in keywords:
        keywords = [coin_symbol.lower()] + keywords

    matched: list[dict] = []
    general: list[dict] = []   # crypto-general articles as fallback

    for name, url in _RSS_FEEDS:
        articles = _fetch_feed(name, url)
        for a in articles:
            text = (a["title"] + " " + a["description"]).lower()
            if any(kw in text for kw in keywords):
                matched.append(a)
            else:
                general.append(a)

    # Return coin-specific first, pad with general crypto news if short
    results = matched[:max_articles]
    if len(results) < max_articles:
        needed = max_articles - len(results)
        results += general[:needed]

    return results[:max_articles]


# ── Public interface ───────────────────────────────────────────────────────────

def get_news(query: str, max_articles: int = 10, coin_symbol: str = None) -> list[dict]:
    """
    Fetch recent news for a coin from RSS feeds.

    Args:
        query:        Human-readable label (e.g. "Bitcoin BTC")
        max_articles: Max number of articles to return.
        coin_symbol:  Ticker (e.g. "BTC"). Inferred from query if omitted.

    Returns:
        List of dicts: {"title", "description", "publishedAt", "source"}
    """
    if coin_symbol is None:
        for sym in _COIN_KEYWORDS:
            if sym.upper() in query.upper():
                coin_symbol = sym
                break
        if coin_symbol is None:
            coin_symbol = "BTC"

    articles = _fetch_rss(coin_symbol, max_articles)
    log.info(f"  {coin_symbol}: {len(articles)} article(s) from RSS feeds")
    return articles


def format_articles_for_prompt(articles: list[dict]) -> str:
    """Format articles + Fear & Greed index into a block for Claude to analyse."""
    lines = []

    # Inject market sentiment index at the top
    fg = _get_fear_greed()
    if fg:
        lines.append(
            f"[MARKET SENTIMENT] Fear & Greed Index: {fg['value']}/100 "
            f"({fg['value_classification']}) - overall market mood context."
        )
        lines.append("")

    if not articles:
        lines.append("No recent news articles found.")
        return "\n".join(lines)

    for i, a in enumerate(articles, 1):
        source = a.get("source", "")
        date   = (a.get("publishedAt") or "")[:10]
        title  = a.get("title") or "No title"
        desc   = (a.get("description") or "").strip()

        lines.append(f"{i}. [{date}] [{source}] {title}")
        if desc:
            lines.append(f"   {desc[:200]}")

    return "\n".join(lines)
