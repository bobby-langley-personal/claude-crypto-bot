"""
Crypto Sentiment Trading Bot
============================

Runs a loop every 30 minutes that:
  1. Fetches live prices (Coinbase public API)
  2. Validates prices against CoinGecko
  3. Checks open positions for take-profit / stop-loss exits
  4. Fetches recent news (CryptoPanic → Reddit fallback)
  5. Asks Claude AI to rate news sentiment (1–10)
  6. Buys coins scoring above the risk-level threshold
  7. Saves portfolio state to disk

All trades are paper (simulated) by default.  Set PAPER_TRADING = False
in config.py only when you're ready for real money.

Usage:
    python main.py

Stop: Ctrl+C – state is saved automatically.
"""
import logging
import time
import threading
from datetime import datetime, timedelta, timezone

from rich.live import Live

from config import CHECK_INTERVAL_MINUTES, PAPER_TRADING, LOG_FILE, COINS, RISK_LEVEL
from coinbase_client import get_all_prices
from paper_portfolio import PaperPortfolio
from trading_engine import TradingEngine
from dashboard import make_renderable
from log_buffer import LogBuffer, LogBufferHandler
from error_logger import log_error
from health_scheduler import health_scheduler

# ── Log buffer (captures all log records for the dashboard debug panel) ───────
log_buffer = LogBuffer(maxlen=500)

# ── Logging setup ─────────────────────────────────────────────────────────────
# Three destinations:
#   1. bot.log     – persistent file (INFO and above)
#   2. Terminal    – visible while dashboard isn't running (INFO and above)
#   3. log_buffer  – in-memory ring buffer shown in dashboard (DEBUG and above)
_file_handler    = logging.FileHandler(LOG_FILE, encoding="utf-8")
_stream_handler  = logging.StreamHandler()
_buffer_handler  = LogBufferHandler(log_buffer, level=logging.DEBUG)

_file_handler.setLevel(logging.INFO)
_stream_handler.setLevel(logging.INFO)
_buffer_handler.setLevel(logging.DEBUG)

_fmt = logging.Formatter("%(asctime)s  %(levelname)-8s  %(name)-22s  %(message)s")
_file_handler.setFormatter(_fmt)
_stream_handler.setFormatter(_fmt)

logging.basicConfig(
    level=logging.DEBUG,
    handlers=[_file_handler, _stream_handler, _buffer_handler],
)
log = logging.getLogger("main")

# ── Shared state between threads ──────────────────────────────────────────────
state: dict = {
    "prices":     {},
    "status":     "Starting…",
    "next_check": "Calculating…",
    "running":    True,
}


# ── Background threads ────────────────────────────────────────────────────────

def trading_loop(engine: TradingEngine) -> None:
    """
    Runs the full analysis cycle on a timer.
    Sleeps between cycles in 5-second chunks so Ctrl+C is always responsive.
    """
    interval_secs = CHECK_INTERVAL_MINUTES * 60

    while state["running"]:
        state["status"] = "Analysing markets…"
        try:
            result = engine.run_cycle()
            buys   = len(result.get("buys", []))
            sells  = len(result.get("sells", []))
            state["status"] = (
                f"Idle  |  last cycle: {buys} buy(s), {sells} sell(s)"
            )
        except Exception as e:
            log.exception("Unhandled error in trading cycle")
            # Log the error to our error tracking system
            error_id = log_error(e, "trading cycle execution", "error", "main")
            state["status"] = f"ERROR: {str(e)[:50]}... (ID: {error_id})"

        next_dt = datetime.now(timezone.utc) + timedelta(seconds=interval_secs)
        state["next_check"] = next_dt.strftime("%H:%M:%S UTC")

        for _ in range(interval_secs // 5):
            if not state["running"]:
                break
            time.sleep(5)


def price_refresh_loop(symbols: list) -> None:
    """
    Refreshes displayed prices every 60 seconds so the dashboard stays
    current between full analysis cycles (which only run every 30 min).
    """
    while state["running"]:
        try:
            prices = get_all_prices(symbols)
            if prices:
                state["prices"] = prices
        except Exception as e:
            log.warning(f"Price refresh error: {e}")
        time.sleep(60)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    log.info("=" * 60)
    log.info("Crypto Sentiment Bot starting")
    log.info(f"Mode:  {'PAPER TRADING (safe)' if PAPER_TRADING else 'LIVE TRADING'}")
    log.info(f"Risk:  {RISK_LEVEL.upper()}")
    log.info(f"Coins: {', '.join(COINS.keys())}")
    log.info(f"Check interval: {CHECK_INTERVAL_MINUTES} minutes")
    log.info("=" * 60)

    portfolio = PaperPortfolio()
    engine    = TradingEngine(portfolio)
    symbols   = list(COINS.keys())

    # Initial price fetch so dashboard isn't blank on startup
    log.info("Fetching initial prices…")
    state["prices"] = get_all_prices(symbols)
    if state["prices"]:
        log.info(
            "  "
            + "  |  ".join(f"{s}: ${p:,.4f}" for s, p in state["prices"].items())
        )
    else:
        log.warning("Could not fetch initial prices – check your internet connection")

    # Start background threads
    threading.Thread(
        target=trading_loop,
        args=(engine,),
        daemon=True,
        name="TradingLoop",
    ).start()

    threading.Thread(
        target=price_refresh_loop,
        args=(symbols,),
        daemon=True,
        name="PriceRefresh",
    ).start()

    log.info("Background threads started. Dashboard launching…")
    
    # Start health monitoring
    health_scheduler.start()

    # ── Dashboard loop (main thread) ──────────────────────────────────────────
    # rich.live.Live keeps the dashboard updated in-place without flickering.
    try:
        with Live(
            make_renderable(
                portfolio  = portfolio,
                prices     = state["prices"],
                analysis   = engine.last_analysis,
                validation = engine.last_validation,
                log_buffer = log_buffer,
                next_check = state["next_check"],
                status     = state["status"],
            ),
            refresh_per_second = 2,
            vertical_overflow  = "visible",
        ) as live:
            while True:
                live.update(
                    make_renderable(
                        portfolio  = portfolio,
                        prices     = state["prices"],
                        analysis   = engine.last_analysis,
                        validation = engine.last_validation,
                        log_buffer = log_buffer,
                        next_check = state["next_check"],
                        status     = state["status"],
                    )
                )
                time.sleep(0.5)

    except KeyboardInterrupt:
        log.info("\nCtrl+C received – shutting down…")
        state["running"] = False
        health_scheduler.stop()
        time.sleep(1)
        log.info("Bot stopped. Portfolio saved to portfolio.json")


if __name__ == "__main__":
    main()
