from __future__ import annotations
"""
Live (real-money) trading portfolio.

Mirrors the PaperPortfolio interface exactly so TradingEngine works
unchanged with either portfolio type.

How state is split:
  - Position METADATA (entry price, cost basis, entry time) is stored locally
    in live_positions.json, because Coinbase only tracks balances/quantities
    but not why you bought or at what price.
  - Actual USD CASH is fetched live from Coinbase (cached for 30 s to avoid
    hammering the API on every WebSocket broadcast).
  - Trade history is stored locally in live_trades.json.

IMPORTANT: This module is only imported when PAPER_TRADING = False.
"""
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from config import LIVE_POSITIONS_FILE, LIVE_TRADES_FILE

log = logging.getLogger(__name__)

# USD balance cache TTL (seconds) — avoids calling the API every 2 s
_CASH_CACHE_TTL = 30


class LivePortfolio:
    """
    Real-money portfolio backed by Coinbase Advanced Trade API.

    Attributes:
        positions:     Open position metadata  {symbol -> {quantity, entry_price, ...}}
        trade_history: All completed trades (list of dicts)
        cash:          Current USD balance (live Coinbase, cached 30 s)
    """

    def __init__(self, trader):
        """
        Args:
            trader: CoinbaseTrader instance for placing real orders.
        """
        self._trader         = trader
        self.positions:      dict = {}
        self.trade_history:  list = []
        self._cached_cash:   float = 0.0
        self._cash_fetched:  float = 0.0   # epoch timestamp of last fetch
        self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self):
        """Load local position metadata and trade history from disk."""
        if Path(LIVE_POSITIONS_FILE).exists():
            try:
                with open(LIVE_POSITIONS_FILE) as f:
                    data = json.load(f)
                self.positions = data.get("positions", {})
                log.info(
                    f"[Live] Loaded {len(self.positions)} open position(s) "
                    "from live_positions.json"
                )
            except Exception as e:
                log.error(f"[Live] Could not load live_positions.json: {e}")

        if Path(LIVE_TRADES_FILE).exists():
            try:
                with open(LIVE_TRADES_FILE) as f:
                    self.trade_history = json.load(f)
            except Exception as e:
                log.error(f"[Live] Could not load live_trades.json: {e}")

    def _save(self):
        """Persist position metadata and trade history to disk."""
        data = {"positions": self.positions, "last_updated": _now_iso()}
        with open(LIVE_POSITIONS_FILE, "w") as f:
            json.dump(data, f, indent=2)

        with open(LIVE_TRADES_FILE, "w") as f:
            json.dump(self.trade_history[-200:], f, indent=2)

    # ── Cash property (live from Coinbase, cached 30 s) ───────────────────────

    @property
    def cash(self) -> float:
        """Current USD balance from Coinbase. Cached for 30 s."""
        if time.time() - self._cash_fetched > _CASH_CACHE_TTL:
            try:
                self._cached_cash  = self._trader.get_usd_balance()
                self._cash_fetched = time.time()
            except Exception as e:
                log.warning(f"[Live] Could not refresh USD balance: {e}")
        return self._cached_cash

    # ── Trading actions ───────────────────────────────────────────────────────

    def buy(
        self,
        symbol:          str,
        price:           float,
        amount_usd:      float,
        sentiment_score: float | None = None,
        reasoning:       str | None = None,
    ) -> dict | None:
        """
        Place a real market buy order on Coinbase.

        Args:
            symbol:          Coin ticker, e.g. "BTC"
            price:           Current market price (used as fallback estimate
                             before the actual fill price is confirmed)
            amount_usd:      Dollar amount to spend
            sentiment_score: Claude's sentiment score (stored for AI learner)

        Returns:
            Trade record dict if successful, None on failure.
        """
        if symbol in self.positions:
            log.info(f"[Live] Already holding {symbol} — skipping buy")
            return None

        product_id = f"{symbol}-USD"
        try:
            result = self._trader.market_buy(product_id, amount_usd)
        except Exception as e:
            log.error(f"[Live] BUY failed for {symbol}: {e}")
            return None

        # Use confirmed fill price if available, fall back to passed-in price
        fill_price  = result.get("fill_price")  or price
        filled_size = result.get("filled_size") or (amount_usd / fill_price)
        total_spent = result.get("total_usd")   or amount_usd

        self.positions[symbol] = {
            "quantity":    filled_size,
            "entry_price": fill_price,
            "cost_basis":  total_spent,
            "entry_time":  _now_iso(),
            "order_id":    result.get("order_id", ""),
            "reasoning":   reasoning or "",
        }

        trade: dict = {
            "action":    "BUY",
            "symbol":    symbol,
            "price":     fill_price,
            "quantity":  filled_size,
            "total_usd": total_spent,
            "order_id":  result.get("order_id", ""),
            "timestamp": _now_iso(),
        }
        if sentiment_score is not None:
            trade["sentiment_score"] = round(sentiment_score, 1)
        if reasoning:
            trade["reasoning"] = reasoning

        self.trade_history.append(trade)
        self._cash_fetched = 0.0   # invalidate cash cache after order
        self._save()

        log.info(
            f"[LIVE BUY]  {symbol}: {filled_size:.6f} units @ ${fill_price:,.4f} "
            f"= ${total_spent:,.2f}"
        )
        return trade

    def sell(
        self,
        symbol:        str,
        price:         float,
        reason:        str = "manual",
        reason_detail: str | None = None,
    ) -> dict | None:
        """
        Place a real market sell order on Coinbase for the full position.

        Args:
            symbol: Coin ticker
            price:  Current market price (fallback if fill price unknown)
            reason: Why we're selling ("take_profit", "stop_loss", "manual")

        Returns:
            Trade record dict if successful, None if no position or order fails.
        """
        if symbol not in self.positions:
            log.warning(f"[Live] No open position in {symbol}")
            return None

        pos      = self.positions[symbol]
        quantity = pos["quantity"]
        cost     = pos["cost_basis"]

        product_id = f"{symbol}-USD"
        try:
            result = self._trader.market_sell(product_id, quantity)
        except Exception as e:
            log.error(f"[Live] SELL failed for {symbol}: {e}")
            return None

        fill_price = result.get("fill_price") or price
        proceeds   = result.get("total_usd")  or (quantity * fill_price)
        pnl_usd    = proceeds - cost
        pnl_pct    = (pnl_usd / cost) * 100

        del self.positions[symbol]

        buy_reasoning = pos.get("reasoning", "")
        trade: dict = {
            "action":    "SELL",
            "symbol":    symbol,
            "price":     fill_price,
            "quantity":  quantity,
            "total_usd": proceeds,
            "pnl_usd":   pnl_usd,
            "pnl_pct":   pnl_pct,
            "reason":    reason,
            "order_id":  result.get("order_id", ""),
            "timestamp": _now_iso(),
        }
        if reason_detail:
            trade["reason_detail"] = reason_detail
        if buy_reasoning:
            trade["buy_reasoning"] = buy_reasoning
        self.trade_history.append(trade)
        self._cash_fetched = 0.0   # invalidate cash cache after order
        self._save()

        icon = "+" if pnl_usd >= 0 else ""
        log.info(
            f"[LIVE SELL] {symbol}: {icon}${pnl_usd:,.2f} ({icon}{pnl_pct:.1f}%) "
            f"reason={reason}"
        )
        return trade

    # ── Queries ───────────────────────────────────────────────────────────────

    def get_position_pnl(self, symbol: str, current_price: float) -> dict | None:
        """Return current unrealised P&L for one open position."""
        pos = self.positions.get(symbol)
        if pos is None:
            return None

        current_value = pos["quantity"] * current_price
        pnl_usd       = current_value - pos["cost_basis"]
        pnl_pct       = (pnl_usd / pos["cost_basis"]) * 100

        return {
            "symbol":        symbol,
            "quantity":      pos["quantity"],
            "entry_price":   pos["entry_price"],
            "current_price": current_price,
            "cost_basis":    pos["cost_basis"],
            "current_value": current_value,
            "pnl_usd":       pnl_usd,
            "pnl_pct":       pnl_pct,
        }

    def get_total_value(self, current_prices: dict) -> float:
        """Total portfolio value = live USD cash + sum of open position values."""
        position_value = sum(
            pos["quantity"] * current_prices.get(sym, pos["entry_price"])
            for sym, pos in self.positions.items()
        )
        return self.cash + position_value

    def get_recent_trades(self, n: int = 8) -> list:
        """Return the N most recent trades, newest first."""
        return list(reversed(self.trade_history[-n:]))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
