"""
Fetch crypto news from two sources:

  PRIMARY  – CryptoPanic API  (https://cryptopanic.com)
             Coin-filtered news feed; free API key available at cryptopanic.com
             Set CRYPTOPANIC_API_KEY in .env to enable.

  FALLBACK – Reddit public JSON
             r/<coin-subreddit> + r/CryptoCurrency; no auth required.
             Used automatically if CryptoPanic is unavailable or unconfigured.

# NewsAPI disabled – 401 with current key format; may re-enable later.
"""
import logging
import requests
from datetime import datetime, timezone
from config import CRYPTOPANIC_API_KEY

log = logging.getLogger(__name__)

# Reddit requires a descriptive User-Agent or it blocks requests
_REDDIT_UA = "crypto-sentiment-bot/1.0 (educational paper trading project)"

# Each coin's most relevant subreddit(s), most specific first
_COIN_SUBREDDITS: dict[str, list[str]] = {
    "BTC":  ["Bitcoin",  "CryptoCurrency"],
    "ETH":  ["ethereum", "CryptoCurrency"],
    "SOL":  ["solana",   "CryptoCurrency"],
    "DOGE": ["dogecoin", "CryptoCurrency"],
}

_CRYPTOPANIC_URL = "https://cryptopanic.com/api/v1/posts/"


# ── CryptoPanic ───────────────────────────────────────────────────────────────

def _fetch_cryptopanic(coin_symbol: str, max_articles: int) -> list[dict]:
    """Fetch coin-specific news from the CryptoPanic API."""
    if not CRYPTOPANIC_API_KEY:
        return []

    try:
        resp = requests.get(
            _CRYPTOPANIC_URL,
            params={
                "auth_token": CRYPTOPANIC_API_KEY,
                "currencies": coin_symbol,   # e.g. "BTC"
                "kind":       "news",
                "public":     "true",
            },
            timeout=10,
        )
        resp.raise_for_status()

        posts = resp.json().get("results", [])[:max_articles]
        return [
            {
                "title":       p.get("title", ""),
                "description": "",                       # CryptoPanic free tier omits body
                "publishedAt": (p.get("published_at") or "")[:10],
                "source":      "CryptoPanic",
            }
            for p in posts
        ]

    except requests.HTTPError as e:
        log.warning(f"CryptoPanic HTTP error for {coin_symbol}: {e}")
        return []
    except Exception as e:
        log.warning(f"CryptoPanic fetch failed for {coin_symbol}: {e}")
        return []


# ── Reddit ────────────────────────────────────────────────────────────────────

def _fetch_reddit(coin_symbol: str, max_articles: int) -> list[dict]:
    """Fetch hot posts from coin-specific subreddits via public JSON API."""
    articles: list[dict] = []
    subreddits = _COIN_SUBREDDITS.get(coin_symbol, ["CryptoCurrency"])

    for sub in subreddits:
        if len(articles) >= max_articles:
            break
        try:
            resp = requests.get(
                f"https://www.reddit.com/r/{sub}/hot.json",
                params={"limit": 15},
                headers={"User-Agent": _REDDIT_UA},
                timeout=10,
            )
            resp.raise_for_status()

            for post in resp.json()["data"]["children"]:
                d = post["data"]
                if d.get("stickied"):      # skip mod/pinned posts
                    continue
                articles.append({
                    "title":       d.get("title", ""),
                    "description": (d.get("selftext") or "")[:250].strip(),
                    "publishedAt": datetime.fromtimestamp(
                        d.get("created_utc", 0), tz=timezone.utc
                    ).strftime("%Y-%m-%d"),
                    "source":      f"r/{sub}",
                })

        except Exception as e:
            log.warning(f"Reddit fetch failed for r/{sub}: {e}")

    return articles[:max_articles]


# ── Public interface ──────────────────────────────────────────────────────────

def get_news(query: str, max_articles: int = 10, coin_symbol: str = None) -> list[dict]:
    """
    Fetch recent news for a coin.

    Args:
        query:        Human-readable label (e.g. "Bitcoin BTC") – used to
                      infer coin_symbol when it isn't passed explicitly.
        max_articles: Max number of articles/posts to return.
        coin_symbol:  Ticker to query (e.g. "BTC"). Inferred from query if omitted.

    Returns:
        List of dicts: {"title", "description", "publishedAt", "source"}
        Returns [] if both sources fail – the bot handles this gracefully.
    """
    if coin_symbol is None:
        for sym in _COIN_SUBREDDITS:
            if sym.upper() in query.upper():
                coin_symbol = sym
                break
        if coin_symbol is None:
            coin_symbol = "BTC"

    # 1. Try CryptoPanic
    articles = _fetch_cryptopanic(coin_symbol, max_articles)
    if articles:
        log.info(f"  {coin_symbol}: {len(articles)} article(s) from CryptoPanic")
        return articles

    # 2. Fall back to Reddit
    log.info(f"  {coin_symbol}: CryptoPanic unavailable – using Reddit")
    articles = _fetch_reddit(coin_symbol, max_articles)
    log.info(f"  {coin_symbol}: {len(articles)} post(s) from Reddit")
    return articles


def format_articles_for_prompt(articles: list[dict]) -> str:
    """Format a list of articles into a readable block for Claude to analyse."""
    if not articles:
        return "No recent news articles found."

    lines = []
    for i, a in enumerate(articles, 1):
        source = a.get("source", "")
        date   = (a.get("publishedAt") or "")[:10]
        title  = a.get("title") or "No title"
        desc   = (a.get("description") or "").strip()

        lines.append(f"{i}. [{date}] [{source}] {title}")
        if desc:
            lines.append(f"   {desc[:200]}")

    return "\n".join(lines)
