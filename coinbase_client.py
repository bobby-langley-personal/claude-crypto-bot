from __future__ import annotations
"""
Fetch cryptocurrency prices from the Coinbase public API.
No authentication needed for price data - it's freely available.
"""
import time
import requests
import logging

from cost_tracker import cost_tracker

log = logging.getLogger(__name__)

# Coinbase public spot price endpoint (no API key required)
SPOT_URL = "https://api.coinbase.com/v2/prices/{symbol}-USD/spot"


def get_price(symbol: str) -> float | None:
    """
    Fetch the current USD spot price for a coin.

    Args:
        symbol: Ticker like "BTC", "ETH", "SOL", "DOGE"

    Returns:
        Price as a float (e.g. 50000.0), or None if the request fails.
    """
    t0 = time.monotonic()
    try:
        url      = SPOT_URL.format(symbol=symbol)
        response = requests.get(url, timeout=10)
        ms       = (time.monotonic() - t0) * 1000
        response.raise_for_status()
        
        # Track Coinbase API call
        cost_tracker.track_api_call("coinbase")
        
        price    = float(response.json()["data"]["amount"])
        log.debug(f"[Coinbase] {symbol}/USD = ${price:,.4f}  ({ms:.0f} ms)")
        return price
    except requests.exceptions.RequestException as e:
        ms = (time.monotonic() - t0) * 1000
        log.error(f"[Coinbase] Network error fetching {symbol}: {e}  ({ms:.0f} ms)")
        return None
    except (KeyError, ValueError) as e:
        log.error(f"[Coinbase] Unexpected response format for {symbol}: {e}")
        return None


def get_all_prices(symbols: list) -> dict:
    """
    Fetch prices for a list of coins.

    Returns:
        Dict mapping symbol -> price, only includes coins that succeeded.
        Example: {"BTC": 50000.0, "ETH": 3000.0}
    """
    prices = {}
    for symbol in symbols:
        price = get_price(symbol)
        if price is not None:
            prices[symbol] = price
    return prices
