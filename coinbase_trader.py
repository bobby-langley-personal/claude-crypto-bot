from __future__ import annotations
"""
Coinbase Advanced Trade / CDP API wrapper for real order execution.

Used only when PAPER_TRADING = False. The PaperPortfolio is used instead
when paper mode is on, so this file is never imported in paper mode.

Authentication:
    Uses the cdp_api_key.json file from developer.coinbase.com.
    Format: {"name": "organizations/.../apiKeys/...", "privateKey": "-----BEGIN EC PRIVATE KEY-----..."}

What this module does:
    - Verifies the API key works on startup (test connection)
    - Reads account balances (USD cash + coin holdings)
    - Places market buy orders (spend N USD to buy a coin)
    - Places market sell orders (sell all of a coin position)
    - Fetches the actual fill price after an order completes

This does NOT manage strategy logic — that stays in trading_engine.py.
"""
import logging
import time
import uuid
from pathlib import Path

log = logging.getLogger(__name__)

# Lazy import so paper-trading mode never requires coinbase-advanced-py
_client = None


def _get_client(key_file: str):
    """Return a lazily-created RESTClient, or raise if the key file is missing."""
    global _client
    if _client is not None:
        return _client

    if not Path(key_file).exists():
        raise FileNotFoundError(
            f"CDP key file not found: '{key_file}'\n"
            "  1. Go to developer.coinbase.com → API Keys → Create API Key\n"
            "  2. Scope it to your isolated trading portfolio\n"
            "  3. Grant 'Trade' permission only (NOT Withdrawal)\n"
            "  4. Save the downloaded JSON as cdp_api_key.json in the project root"
        )

    try:
        from coinbase.rest import RESTClient
        _client = RESTClient(key_file=key_file)
        log.info(f"[Trader] Coinbase CDP client initialised from {key_file}")
        return _client
    except ImportError:
        raise ImportError(
            "coinbase-advanced-py is not installed.\n"
            "Run: pip install coinbase-advanced-py"
        )


class CoinbaseTrader:
    """
    Thin wrapper around the Coinbase Advanced Trade REST API.

    Call verify_connection() once on startup to confirm the key works.
    """

    def __init__(self, key_file: str = "cdp_api_key.json"):
        self._key_file = key_file
        self._client   = _get_client(key_file)

    # ── Connection check ──────────────────────────────────────────────────────

    def verify_connection(self) -> dict:
        """
        Confirm the API key is valid and return account summary.
        Raises on failure.

        Returns:
            {"usd_balance": float, "accounts": int, "key_name": str}
        """
        try:
            accounts = self._client.get_accounts()
            usd = self._get_usd_balance_from(accounts)
            n   = len(accounts.accounts)
            log.info(
                f"[Trader] Connection verified — "
                f"{n} account(s), USD balance: ${usd:,.2f}"
            )
            return {
                "usd_balance": usd,
                "accounts":    n,
                "key_name":    self._key_file,
            }
        except Exception as e:
            raise RuntimeError(f"Coinbase API connection failed: {e}") from e

    # ── Balance queries ───────────────────────────────────────────────────────

    def get_usd_balance(self) -> float:
        """Return available USD balance across all accounts."""
        try:
            accounts = self._client.get_accounts()
            return self._get_usd_balance_from(accounts)
        except Exception as e:
            log.error(f"[Trader] Could not fetch USD balance: {e}")
            return 0.0

    def get_coin_balances(self) -> dict[str, float]:
        """
        Return non-zero, non-USD coin balances.

        Returns:
            {"BTC": 0.0012, "ETH": 0.5, ...}
        """
        try:
            accounts = self._client.get_accounts()
            balances = {}
            for acct in accounts.accounts:
                currency = acct.currency
                if currency in ("USD", "USDC", "USDT"):
                    continue
                bal = float(acct.available_balance.value)
                if bal > 0:
                    balances[currency] = bal
            return balances
        except Exception as e:
            log.error(f"[Trader] Could not fetch coin balances: {e}")
            return {}

    # ── Order placement ───────────────────────────────────────────────────────

    def market_buy(
        self,
        product_id: str,
        quote_size: float,
    ) -> dict:
        """
        Place a market buy order.

        Args:
            product_id: e.g. "BTC-USD"
            quote_size: USD amount to spend, e.g. 500.0

        Returns:
            {"order_id": str, "fill_price": float, "filled_size": float,
             "total_usd": float, "product_id": str}

        Raises:
            RuntimeError if the order is rejected or fails to fill.
        """
        client_order_id = str(uuid.uuid4())
        log.info(
            f"[Trader] Placing MARKET BUY  {product_id}  "
            f"${quote_size:.2f}  (order_id={client_order_id[:8]}…)"
        )

        try:
            response = self._client.market_order_buy(
                client_order_id=client_order_id,
                product_id=product_id,
                quote_size=f"{quote_size:.2f}",
            )
        except Exception as e:
            raise RuntimeError(f"BUY order failed for {product_id}: {e}") from e

        return self._parse_order_response(response, product_id, "BUY", quote_size)

    def market_sell(
        self,
        product_id: str,
        base_size: float,
    ) -> dict:
        """
        Place a market sell order for a specific quantity of a coin.

        Args:
            product_id: e.g. "BTC-USD"
            base_size:  Coin quantity to sell, e.g. 0.00718 (BTC)

        Returns:
            {"order_id": str, "fill_price": float, "filled_size": float,
             "total_usd": float, "product_id": str}

        Raises:
            RuntimeError if the order is rejected or fails to fill.
        """
        client_order_id = str(uuid.uuid4())
        log.info(
            f"[Trader] Placing MARKET SELL {product_id}  "
            f"{base_size:.8f} units  (order_id={client_order_id[:8]}…)"
        )

        try:
            response = self._client.market_order_sell(
                client_order_id=client_order_id,
                product_id=product_id,
                base_size=f"{base_size:.8f}",
            )
        except Exception as e:
            raise RuntimeError(f"SELL order failed for {product_id}: {e}") from e

        return self._parse_order_response(response, product_id, "SELL")

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _parse_order_response(
        self,
        response,
        product_id: str,
        side: str,
        quote_size: float | None = None,
    ) -> dict:
        """
        Extract fill details from an order response.
        Retries get_order() up to 5 times to wait for the fill.
        """
        order_id = None
        try:
            # The response object has success_response or error_response
            sr = getattr(response, "success_response", None)
            if sr:
                order_id = getattr(sr, "order_id", None)
            if not order_id:
                raise RuntimeError(
                    f"Order rejected: {getattr(response, 'error_response', response)}"
                )
        except AttributeError:
            # Fallback: response might be a dict
            if isinstance(response, dict):
                order_id = response.get("order_id") or response.get("success_response", {}).get("order_id")
            if not order_id:
                raise RuntimeError(f"Unexpected order response format: {response}")

        # Poll for fill (market orders fill in < 1 s but we allow up to 10 s)
        fill_price   = None
        filled_size  = None
        total_filled = None

        for attempt in range(10):
            time.sleep(1)
            try:
                order = self._client.get_order(order_id=order_id)
                o = getattr(order, "order", order)

                status = str(getattr(o, "status", "")).upper()
                if status in ("FILLED", "DONE"):
                    avg_price  = getattr(o, "average_filled_price", None)
                    filled_qty = getattr(o, "filled_size", None)
                    filled_val = getattr(o, "filled_value", None)

                    fill_price   = float(avg_price)  if avg_price  else None
                    filled_size  = float(filled_qty) if filled_qty else None
                    total_filled = float(filled_val) if filled_val else (
                        quote_size if side == "BUY" else (
                            (fill_price * filled_size) if fill_price and filled_size else None
                        )
                    )
                    break
                elif status in ("CANCELLED", "EXPIRED", "FAILED"):
                    raise RuntimeError(f"Order {order_id} ended with status: {status}")
            except RuntimeError:
                raise
            except Exception as e:
                log.debug(f"[Trader] Order poll attempt {attempt+1}: {e}")

        if fill_price is None:
            log.warning(
                f"[Trader] Could not confirm fill price for {order_id} — "
                "using last known price"
            )

        result = {
            "order_id":    order_id,
            "fill_price":  fill_price,
            "filled_size": filled_size,
            "total_usd":   total_filled or quote_size,
            "product_id":  product_id,
        }
        log.info(
            f"[Trader] {side} FILLED  {product_id}  "
            f"price=${fill_price or '?'}  qty={filled_size or '?'}  "
            f"value=${total_filled or '?'}"
        )
        return result

    @staticmethod
    def _get_usd_balance_from(accounts) -> float:
        for acct in accounts.accounts:
            if acct.currency in ("USD", "USDC"):
                return float(acct.available_balance.value)
        return 0.0
