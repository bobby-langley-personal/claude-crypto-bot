"""
Paper (simulated) trading portfolio.

Tracks virtual cash, open positions, and trade history without touching
real money. State is saved to portfolio.json / trades.json so it
persists across bot restarts.
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from config import PAPER_STARTING_CASH, PORTFOLIO_FILE, TRADES_FILE

log = logging.getLogger(__name__)


class PaperPortfolio:
    """
    Simulates a brokerage account for paper trading.

    Attributes:
        cash:          Available USD balance
        positions:     Open positions  {symbol -> {quantity, entry_price, ...}}
        trade_history: All completed trades (list of dicts)
    """

    def __init__(self):
        self.cash: float = PAPER_STARTING_CASH
        self.positions: dict = {}
        self.trade_history: list = []
        self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self):
        """Load saved state from disk (if it exists)."""
        if Path(PORTFOLIO_FILE).exists():
            try:
                with open(PORTFOLIO_FILE) as f:
                    data = json.load(f)
                self.cash      = data.get("cash", PAPER_STARTING_CASH)
                self.positions = data.get("positions", {})
                log.info(
                    f"Portfolio loaded: ${self.cash:,.2f} cash, "
                    f"{len(self.positions)} open position(s)"
                )
            except Exception as e:
                log.error(f"Could not load portfolio file: {e}")

        if Path(TRADES_FILE).exists():
            try:
                with open(TRADES_FILE) as f:
                    self.trade_history = json.load(f)
            except Exception as e:
                log.error(f"Could not load trades file: {e}")

    def _save(self):
        """Persist current state to disk."""
        portfolio_data = {
            "cash":         self.cash,
            "positions":    self.positions,
            "last_updated": _now_iso(),
        }
        with open(PORTFOLIO_FILE, "w") as f:
            json.dump(portfolio_data, f, indent=2)

        # Keep only the most recent 200 trades on disk
        with open(TRADES_FILE, "w") as f:
            json.dump(self.trade_history[-200:], f, indent=2)

    # ── Trading actions ───────────────────────────────────────────────────────

    def buy(self, symbol: str, price: float, amount_usd: float) -> dict | None:
        """
        Simulate buying a coin.

        Args:
            symbol:     Coin ticker, e.g. "BTC"
            price:      Current market price
            amount_usd: Dollar amount to spend

        Returns:
            Trade record dict if successful, None otherwise.
        """
        if symbol in self.positions:
            log.info(f"Already holding {symbol} - skipping buy")
            return None

        if self.cash < amount_usd:
            log.warning(
                f"Not enough cash (${self.cash:,.2f}) "
                f"for ${amount_usd:,.2f} {symbol} buy"
            )
            return None

        quantity    = amount_usd / price
        self.cash  -= amount_usd

        self.positions[symbol] = {
            "quantity":    quantity,
            "entry_price": price,
            "cost_basis":  amount_usd,
            "entry_time":  _now_iso(),
        }

        trade = {
            "action":    "BUY",
            "symbol":    symbol,
            "price":     price,
            "quantity":  quantity,
            "total_usd": amount_usd,
            "timestamp": _now_iso(),
        }
        self.trade_history.append(trade)
        self._save()

        log.info(
            f"[PAPER BUY]  {symbol}: {quantity:.6f} units @ ${price:,.4f} "
            f"= ${amount_usd:,.2f}"
        )
        return trade

    def sell(self, symbol: str, price: float, reason: str = "manual") -> dict | None:
        """
        Simulate selling an entire position.

        Args:
            symbol: Coin ticker
            price:  Current market price
            reason: Why we're selling ("take_profit", "stop_loss", "manual")

        Returns:
            Trade record dict if successful, None if no position exists.
        """
        if symbol not in self.positions:
            log.warning(f"No open position in {symbol}")
            return None

        pos      = self.positions.pop(symbol)
        quantity = pos["quantity"]
        proceeds = quantity * price
        cost     = pos["cost_basis"]
        pnl_usd  = proceeds - cost
        pnl_pct  = (pnl_usd / cost) * 100

        self.cash += proceeds

        trade = {
            "action":    "SELL",
            "symbol":    symbol,
            "price":     price,
            "quantity":  quantity,
            "total_usd": proceeds,
            "pnl_usd":   pnl_usd,
            "pnl_pct":   pnl_pct,
            "reason":    reason,
            "timestamp": _now_iso(),
        }
        self.trade_history.append(trade)
        self._save()

        icon = "+" if pnl_usd >= 0 else ""
        log.info(
            f"[PAPER SELL] {symbol}: {icon}${pnl_usd:,.2f} ({icon}{pnl_pct:.1f}%) "
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
        """Total portfolio value = cash + sum of open position values."""
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
