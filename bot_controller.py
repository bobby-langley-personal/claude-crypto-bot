"""
BotController – thread-safe lifecycle wrapper around the trading bot.

The web server imports this module and calls:
    bot = BotController()
    bot.start()    # begin trading loop
    bot.stop()     # halt trading loop
    bot.get_state()  # snapshot of all live data for WebSocket broadcast

This is separate from main.py (which runs the Rich terminal dashboard).
Both entry points use the same underlying trading engine.
"""
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from config import (
    CHECK_INTERVAL_MINUTES, COINS,
    PAPER_STARTING_CASH, RISK_LEVEL, RISK_DESCRIPTION,
    SENTIMENT_BUY_THRESHOLD, TAKE_PROFIT_PCT, STOP_LOSS_PCT,
    TRADE_AMOUNT_USD, MAX_POSITIONS,
)
from coinbase_client import get_all_prices
from paper_portfolio import PaperPortfolio
from trading_engine import TradingEngine
from log_buffer import LogBuffer, LogBufferHandler

log = logging.getLogger(__name__)


class BotController:
    """Thread-safe wrapper for start/stop control of the trading bot."""

    def __init__(self):
        # ── Logging ──────────────────────────────────────────────────────────
        self.log_buffer = LogBuffer(maxlen=500)
        _handler = LogBufferHandler(self.log_buffer, level=logging.DEBUG)
        logging.getLogger().addHandler(_handler)

        # ── Bot components (lazily initialised on first start) ────────────────
        self.portfolio: PaperPortfolio | None = None
        self.engine: TradingEngine | None = None

        # ── Runtime state ─────────────────────────────────────────────────────
        self._running   = False
        self._status    = "stopped"   # "stopped" | "running" | "analysing" | "error: ..."
        self._next_check = "—"
        self._lock = threading.Lock()

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> bool:
        """
        Start the trading bot. Returns True if started, False if already running.
        Creates portfolio and engine on the first call; reuses them on restarts.
        """
        with self._lock:
            if self._running:
                log.warning("Bot.start() called but already running – ignored")
                return False

            if self.portfolio is None:
                self.portfolio = PaperPortfolio()
                self.engine    = TradingEngine(self.portfolio)

            self._running = True
            self._status  = "running"

        threading.Thread(
            target=self._trading_loop, daemon=True, name="TradingLoop"
        ).start()
        threading.Thread(
            target=self._price_refresh_loop, daemon=True, name="PriceRefresh"
        ).start()

        log.info(
            f"Bot started  [risk={RISK_LEVEL}  "
            f"threshold={SENTIMENT_BUY_THRESHOLD}  "
            f"TP=+{TAKE_PROFIT_PCT}%  SL={STOP_LOSS_PCT}%]"
        )
        return True

    def stop(self) -> bool:
        """Stop the bot. Returns True if stopped, False if already stopped."""
        with self._lock:
            if not self._running:
                log.warning("Bot.stop() called but already stopped – ignored")
                return False
            self._running = False
            self._status  = "stopped"

        log.info("Bot stopped by web console")
        return True

    @property
    def is_running(self) -> bool:
        return self._running

    # ── Background threads ────────────────────────────────────────────────────

    def _trading_loop(self):
        interval = CHECK_INTERVAL_MINUTES * 60

        while self._running:
            self._status = "analysing"
            try:
                result = self.engine.run_cycle()
                buys   = len(result.get("buys",  []))
                sells  = len(result.get("sells", []))
                self._status = f"idle · last cycle: {buys}B {sells}S"
            except Exception as e:
                log.exception("Trading cycle error")
                self._status = f"error: {e}"

            next_dt = datetime.now(timezone.utc) + timedelta(seconds=interval)
            self._next_check = next_dt.strftime("%H:%M:%S UTC")

            # Sleep in small chunks so Ctrl+C / stop() works quickly
            for _ in range(interval // 5):
                if not self._running:
                    break
                time.sleep(5)

    def _price_refresh_loop(self):
        """Update displayed prices every 60 s between full analysis cycles."""
        symbols = list(COINS.keys())
        while self._running:
            try:
                prices = get_all_prices(symbols)
                if prices and self.engine:
                    self.engine.last_prices = prices
            except Exception as e:
                log.warning(f"Price refresh error: {e}")
            time.sleep(60)

    # ── State snapshot ────────────────────────────────────────────────────────

    def get_state(self) -> dict:
        """
        Build a complete JSON-serialisable snapshot of the bot's current state.
        Called from the async WebSocket broadcast loop every 2 seconds.
        Must be fast (reads in-memory data only, no I/O).
        """
        prices     = dict(self.engine.last_prices)     if self.engine   else {}
        analysis   = dict(self.engine.last_analysis)   if self.engine   else {}
        validation = dict(self.engine.last_validation) if self.engine   else {}

        # ── Portfolio ─────────────────────────────────────────────────────────
        cash  = PAPER_STARTING_CASH
        total = PAPER_STARTING_CASH
        if self.portfolio:
            cash  = self.portfolio.cash
            total = self.portfolio.get_total_value(prices)
        pnl     = total - PAPER_STARTING_CASH
        pnl_pct = (pnl / PAPER_STARTING_CASH) * 100

        # ── Open positions ────────────────────────────────────────────────────
        positions: list[dict] = []
        if self.portfolio:
            for sym, pos in self.portfolio.positions.items():
                current = prices.get(sym, pos["entry_price"])
                pnl_usd = pos["quantity"] * (current - pos["entry_price"])
                pnl_pos_pct = (pnl_usd / pos["cost_basis"]) * 100
                positions.append({
                    "symbol":        sym,
                    "quantity":      round(pos["quantity"], 8),
                    "entry_price":   round(pos["entry_price"], 6),
                    "current_price": round(current, 6),
                    "cost_basis":    round(pos["cost_basis"], 2),
                    "current_value": round(pos["quantity"] * current, 2),
                    "pnl_usd":       round(pnl_usd, 2),
                    "pnl_pct":       round(pnl_pos_pct, 2),
                    "entry_time":    pos.get("entry_time", ""),
                    "target_price":  round(pos["entry_price"] * (1 + TAKE_PROFIT_PCT / 100), 6),
                    "stop_price":    round(pos["entry_price"] * (1 + STOP_LOSS_PCT   / 100), 6),
                })

        # ── Trades ────────────────────────────────────────────────────────────
        trades = self.portfolio.get_recent_trades(15) if self.portfolio else []

        return {
            "status":     self._status,
            "next_check": self._next_check,
            "prices":     prices,
            "validation": validation,
            "portfolio": {
                "cash":             round(cash,  2),
                "total":            round(total, 2),
                "pnl_usd":          round(pnl, 2),
                "pnl_pct":          round(pnl_pct, 2),
                "positions_count":  len(positions),
                "starting_cash":    PAPER_STARTING_CASH,
            },
            "positions": positions,
            "analysis":  analysis,
            "trades":    trades,
            "logs":      self.log_buffer.get_recent(80),
            "config": {
                "risk_level":   RISK_LEVEL,
                "description":  RISK_DESCRIPTION,
                "threshold":    SENTIMENT_BUY_THRESHOLD,
                "take_profit":  TAKE_PROFIT_PCT,
                "stop_loss":    STOP_LOSS_PCT,
                "trade_size":   TRADE_AMOUNT_USD,
                "max_positions": MAX_POSITIONS,
            },
        }
