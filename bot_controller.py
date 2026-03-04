from __future__ import annotations
"""
BotController – thread-safe lifecycle wrapper around the trading bot.

The web server imports this module and calls:
    bot = BotController()
    bot.start()              # begin trading loop
    bot.stop()               # halt trading loop
    bot.set_risk("high")     # change risk profile at runtime
    bot.add_coin("PEPE", ...)
    bot.remove_coin("DOGE")
    bot.trigger_learning()   # run AI strategy analysis immediately
    bot.get_state()          # full snapshot for WebSocket broadcast

This is separate from main.py (which runs the Rich terminal dashboard).
Both entry points use the same underlying trading engine.
"""
import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from cost_tracker import cost_tracker
from config import (
    AUTO_DISCOVER_COINS, CDP_KEY_FILE, CHECK_INTERVAL_MINUTES, COINS,
    EMERGENCY_DROP_PCT, LEARNING_EVERY_N_CYCLES, MAX_WATCHLIST_COINS,
    PAPER_STARTING_CASH, PAPER_TRADING, RISK_LEVEL, RISK_PROFILES,
)
from coinbase_client import get_all_prices
from paper_portfolio import PaperPortfolio
from trading_engine import TradingEngine
from strategy_learner import StrategyLearner
from log_buffer import LogBuffer, LogBufferHandler
from data_validator import register_coin, lookup_coingecko_id
from news_client import register_coin_subreddits

log = logging.getLogger(__name__)


class BotController:
    """Thread-safe wrapper for start/stop/configure of the trading bot."""

    def __init__(self):
        # ── Logging ──────────────────────────────────────────────────────────
        self.log_buffer = LogBuffer(maxlen=500)
        _handler = LogBufferHandler(self.log_buffer, level=logging.DEBUG)
        logging.getLogger().addHandler(_handler)

        # ── Risk ──────────────────────────────────────────────────────────────
        self._risk_level  = RISK_LEVEL
        self._risk_params = dict(RISK_PROFILES[RISK_LEVEL])

        # ── Coin watchlist ────────────────────────────────────────────────────
        self._coins = dict(COINS)

        # ── Trading mode ──────────────────────────────────────────────────────
        self._paper_trading   = PAPER_TRADING
        self._starting_value  = PAPER_STARTING_CASH  # updated on live start

        # ── Bot components (lazily initialised on first start) ────────────────
        self.portfolio = None   # PaperPortfolio | LivePortfolio
        self.engine:    TradingEngine   | None = None
        self.learner:   StrategyLearner        = StrategyLearner()
        
        # Sync strategy mode from learner to engine on initialization
        if hasattr(self.learner, '_current_strategy_mode'):
            self._current_strategy_mode = self.learner._current_strategy_mode

        # ── Shadow portfolios (paper mode only) ───────────────────────────────
        # One per non-active risk profile; share coin data, skip API calls
        self._shadow_portfolios: dict[str, PaperPortfolio] = {}
        self._shadow_engines:    dict[str, TradingEngine]  = {}

        # ── Runtime state ─────────────────────────────────────────────────────
        self._running     = False
        self._status      = "stopped"
        self._next_check  = "—"
        self._cycle_count = 0
        self._lock        = threading.Lock()

        # ── Learning ──────────────────────────────────────────────────────────
        self._learning_running  = False
        self._last_insight: dict | None = self.learner.get_latest()

        # ── Emergency stop ────────────────────────────────────────────────────
        self._emergency_mode    = False
        self._last_btc_price:   float | None = None  # for market health check

        # ── Always On ─────────────────────────────────────────────────────────
        self._always_on:        bool  = False
        self._watchdog_active:  bool  = False
        self._state_file = Path("bot_state.json")
        self._load_persisted_state()   # may auto-start bot after 3 s

        # ── API status cache ──────────────────────────────────────────────────
        self._api_status:       dict = {}
        self._api_status_ts:    float = 0.0

    # ── Start / Stop ──────────────────────────────────────────────────────────

    def start(self) -> bool:
        """Start the trading bot. Returns True if started, False if already running."""
        with self._lock:
            if self._running:
                log.warning("Bot.start() called but already running – ignored")
                return False

            if self.portfolio is None:
                if self._paper_trading:
                    self.portfolio = PaperPortfolio()
                    log.info("[Bot] Paper trading mode — no real orders will be placed")
                else:
                    from coinbase_trader import CoinbaseTrader
                    from live_portfolio import LivePortfolio
                    trader = CoinbaseTrader(key_file=CDP_KEY_FILE)
                    info   = trader.verify_connection()
                    self.portfolio       = LivePortfolio(trader)
                    self._starting_value = self.portfolio.get_total_value({})
                    log.info(
                        f"[Bot] LIVE trading mode  "
                        f"USD balance: ${info['usd_balance']:,.2f}  "
                        f"positions: {len(self.portfolio.positions)}"
                    )

                self.engine = TradingEngine(
                    self.portfolio,
                    params=self._risk_params,
                    coins=self._coins,
                )
                
                # Set initial strategy mode from learner
                if hasattr(self.learner, '_current_strategy_mode'):
                    self.engine.set_strategy_mode(self.learner._current_strategy_mode)

                # Initialise shadow portfolios (paper mode only, once per lifetime)
                if self._paper_trading and not self._shadow_portfolios:
                    for level, params in RISK_PROFILES.items():
                        if level == self._risk_level:
                            continue
                        shadow_p = PaperPortfolio(
                            portfolio_file=f"shadow_{level}_portfolio.json",
                            trades_file=f"shadow_{level}_trades.json",
                        )
                        shadow_e = TradingEngine(
                            shadow_p, params=dict(params), coins=self._coins
                        )
                        self._shadow_portfolios[level] = shadow_p
                        self._shadow_engines[level]    = shadow_e
                    log.info(
                        f"[Shadow] Initialized {len(self._shadow_portfolios)} "
                        "shadow portfolio(s): " + ", ".join(self._shadow_portfolios)
                    )
            else:
                # Restarted — re-apply current settings to existing engine
                self.engine.update_params(self._risk_params)
                self.engine.update_coins(self._coins)

            self._running = True
            self._status  = "running"

        threading.Thread(
            target=self._trading_loop, daemon=True, name="TradingLoop"
        ).start()
        threading.Thread(
            target=self._price_refresh_loop, daemon=True, name="PriceRefresh"
        ).start()

        log.info(
            f"Bot started  [risk={self._risk_level}  "
            f"threshold={self._risk_params['sentiment_buy_threshold']}  "
            f"TP=+{self._risk_params['take_profit_pct']}%  "
            f"SL={self._risk_params['stop_loss_pct']}%  "
            f"coins={list(self._coins.keys())}]"
        )
        return True

    def stop(self) -> bool:
        """Stop the bot. Returns True if stopped, False if already stopped.
        Also disables Always On so the watchdog doesn't immediately restart."""
        with self._lock:
            if not self._running:
                log.warning("Bot.stop() called but already stopped – ignored")
                return False
            self._running = False
            self._status  = "stopped"
            self._next_check = "—"

        # Disable always-on when manually stopped — prevents instant restart
        if self._always_on:
            self._always_on = False
            self._watchdog_active = False
            self._save_state()
            log.info("Bot stopped by web console · Always On disabled")
        else:
            log.info("Bot stopped by web console")
        return True

    @property
    def is_running(self) -> bool:
        return self._running

    # ── Risk management ───────────────────────────────────────────────────────

    def set_risk(self, level: str) -> bool:
        """Switch risk profile at runtime. Returns True if changed."""
        if level not in RISK_PROFILES:
            log.warning(f"Unknown risk level '{level}' – ignored")
            return False

        self._risk_level  = level
        self._risk_params = dict(RISK_PROFILES[level])

        if self.engine:
            self.engine.update_params(self._risk_params)

        log.info(
            f"Risk level changed to '{level}'  "
            f"(threshold={self._risk_params['sentiment_buy_threshold']}  "
            f"TP=+{self._risk_params['take_profit_pct']}%  "
            f"SL={self._risk_params['stop_loss_pct']}%)"
        )
        return True

    def get_risk_profiles(self) -> dict:
        """Return all available risk profiles for the dashboard selector."""
        return RISK_PROFILES

    # ── Coin management ───────────────────────────────────────────────────────

    def add_coin(
        self,
        symbol: str,
        name: str = "",
        coingecko_id: str | None = None,
    ) -> dict:
        """
        Add a coin to the watchlist.

        Args:
            symbol:       Ticker, e.g. "PEPE"
            name:         Human-readable name, e.g. "Pepe"
            coingecko_id: CoinGecko ID for price validation. Auto-looked-up
                          from CoinGecko search if not provided.

        Returns:
            {"ok": bool, "message": str, "coin": dict | None}
        """
        sym = symbol.upper().strip()

        if sym in self._coins:
            return {"ok": False, "message": f"{sym} is already on the watchlist"}

        # Auto-lookup CoinGecko ID if not provided
        if not coingecko_id:
            coingecko_id = lookup_coingecko_id(sym)
            if coingecko_id:
                log.info(f"[Coins] CoinGecko ID for {sym}: {coingecko_id}")

        if coingecko_id:
            register_coin(sym, coingecko_id)

        news_query = f"{name or sym} {sym}".strip()
        coin_cfg = {"product_id": f"{sym}-USD", "news_query": news_query}
        self._coins[sym] = coin_cfg

        # Register sensible subreddits (fallback to CryptoCurrency)
        register_coin_subreddits(sym, [sym.lower(), "CryptoCurrency"])

        if self.engine:
            self.engine.update_coins(self._coins)
        for se in self._shadow_engines.values():
            se.update_coins(self._coins)

        log.info(f"[Coins] Added {sym} to watchlist (cg_id={coingecko_id or 'unknown'})")
        return {
            "ok":      True,
            "message": f"{sym} added to watchlist",
            "coin":    {"symbol": sym, **coin_cfg},
        }

    def remove_coin(self, symbol: str) -> dict:
        """Remove a coin from the watchlist."""
        sym = symbol.upper()
        if sym not in self._coins:
            return {"ok": False, "message": f"{sym} is not on the watchlist"}

        del self._coins[sym]

        if self.engine:
            self.engine.update_coins(self._coins)
        for se in self._shadow_engines.values():
            se.update_coins(self._coins)

        log.info(f"[Coins] Removed {sym} from watchlist")
        return {"ok": True, "message": f"{sym} removed from watchlist"}

    def get_trending_coins(self) -> list[dict]:
        """
        Fetch top-7 trending coins from CoinGecko.
        Returns list of {symbol, name, coingecko_id, rank, already_watching}.
        """
        try:
            resp = requests.get(
                "https://api.coingecko.com/api/v3/search/trending",
                headers={"Accept": "application/json"},
                timeout=10,
            )
            resp.raise_for_status()
            
            # Track CoinGecko API call
            cost_tracker.track_api_call("coingecko")
            
            coins = []
            for item in resp.json().get("coins", []):
                i = item.get("item", {})
                sym = i.get("symbol", "").upper()
                coins.append({
                    "symbol":          sym,
                    "name":            i.get("name", ""),
                    "coingecko_id":    i.get("id", ""),
                    "market_cap_rank": i.get("market_cap_rank"),
                    "thumb":           i.get("thumb", ""),
                    "already_watching": sym in self._coins,
                })
            return coins
        except Exception as e:
            log.warning(f"[Coins] Could not fetch trending coins: {e}")
            return []

    # ── AI Learning ───────────────────────────────────────────────────────────

    def trigger_learning(self, auto_apply: bool = False) -> dict:
        """
        Run an AI strategy learning cycle immediately.
        Safe to call while the bot is running (runs in a background thread).
        Returns immediately; use get_state() to see the result.
        """
        if self._learning_running:
            return {"ok": False, "message": "Learning cycle already running"}

        def _run():
            self._learning_running = True
            try:
                trades = self.portfolio.trade_history if self.portfolio else []
                insight = self.learner.run_learning_cycle(
                    trades=trades,
                    current_params=self._risk_params,
                    risk_level=self._risk_level,
                    coins_watching=list(self._coins.keys()),
                    auto_apply=auto_apply,
                )
                self._last_insight = insight

                # Auto-apply returned new params
                if auto_apply and insight.get("auto_applied") and insight.get("new_params"):
                    self._risk_params = insight["new_params"]
                    if self.engine:
                        self.engine.update_params(self._risk_params)
                    log.info(
                        f"[Learner] Auto-applied {len(insight['auto_applied'])} "
                        "param adjustment(s)"
                    )
            finally:
                self._learning_running = False

        threading.Thread(target=_run, daemon=True, name="Learner").start()
        return {"ok": True, "message": "Learning cycle started"}

    # ── Manual cycle trigger ───────────────────────────────────────────────────

    def run_cycle_now(self) -> dict:
        """Trigger an immediate analysis cycle outside the normal schedule."""
        if not self._running:
            return {"ok": False, "message": "Bot is not running — start it first"}
        if self._status == "analysing":
            return {"ok": False, "message": "A cycle is already in progress"}

        def _run():
            self._status = "analysing"
            try:
                result = self.engine.run_cycle()
                buys  = len(result.get("buys",  []))
                sells = len(result.get("sells", []))
                if self._running:
                    self._status = f"idle · last cycle: {buys}B {sells}S"
            except Exception as e:
                log.exception("Manual cycle error")
                if self._running:
                    self._status = f"error: {e}"

        threading.Thread(target=_run, daemon=True, name="ManualCycle").start()
        return {"ok": True, "message": "Cycle triggered"}

    # ── Emergency stop ─────────────────────────────────────────────────────────

    def emergency_stop(self, reason: str = "manual") -> dict:
        """
        Immediately stop the bot and sell all open positions at market price.
        Used when the market is crashing or the user manually triggers it.
        """
        log.warning(f"[EMERGENCY STOP] Triggered — reason: {reason}")
        self._emergency_mode = True
        self._always_on = False
        self._watchdog_active = False
        self._save_state()

        # Stop the trading loop first
        with self._lock:
            self._running = False
            self._status  = f"EMERGENCY STOPPED: {reason}"
            self._next_check = "—"

        # Sell all positions
        sold = []
        if self.portfolio and self.engine:
            prices = self.engine.last_prices or {}
            for symbol in list(self.portfolio.positions.keys()):
                price = prices.get(symbol)
                if not price:
                    from coinbase_client import get_all_prices
                    price = (get_all_prices([symbol]) or {}).get(symbol)
                if price:
                    trade = self.portfolio.sell(
                        symbol, price, reason="emergency_stop",
                        reason_detail=f"Emergency stop: {reason}",
                    )
                    if trade:
                        sold.append(symbol)
                        log.warning(
                            f"[EMERGENCY] Sold {symbol} @ ${price:,.4f}  "
                            f"P&L: {trade.get('pnl_pct', 0):+.1f}%"
                        )

        log.warning(f"[EMERGENCY STOP] Complete — sold {len(sold)} position(s): {sold}")
        return {
            "ok":     True,
            "reason": reason,
            "sold":   sold,
            "message": f"Emergency stop complete. Sold {len(sold)} position(s).",
        }

    def clear_emergency(self) -> None:
        """Reset emergency mode so the bot can be restarted."""
        self._emergency_mode = False
        self._status = "stopped"

    # ── Market health check ────────────────────────────────────────────────────

    def check_market_health(self, current_prices: dict) -> bool:
        """
        Check if BTC has dropped more than EMERGENCY_DROP_PCT since the last cycle.
        Returns True if healthy, False if emergency stop was triggered.
        """
        btc_price = current_prices.get("BTC")
        if btc_price is None or self._last_btc_price is None:
            self._last_btc_price = btc_price
            return True

        drop_pct = (btc_price - self._last_btc_price) / self._last_btc_price * 100
        self._last_btc_price = btc_price

        if drop_pct <= EMERGENCY_DROP_PCT:
            log.warning(
                f"[MarketHealth] BTC dropped {drop_pct:.1f}% this cycle "
                f"(threshold: {EMERGENCY_DROP_PCT}%) — triggering emergency stop"
            )
            self.emergency_stop(reason=f"BTC dropped {drop_pct:.1f}% in one cycle")
            return False

        return True

    # ── Auto-discover coins ────────────────────────────────────────────────────

    def auto_discover_coins(self) -> list[str]:
        """
        If the watchlist has fewer than MAX_WATCHLIST_COINS coins, fetch
        CoinGecko trending coins and auto-add eligible ones.

        Returns list of newly added symbols.
        """
        if not AUTO_DISCOVER_COINS:
            return []

        slots = MAX_WATCHLIST_COINS - len(self._coins)
        if slots <= 0:
            return []

        added: list[str] = []
        try:
            resp = requests.get(
                "https://api.coingecko.com/api/v3/search/trending",
                headers={"Accept": "application/json"},
                timeout=10,
            )
            resp.raise_for_status()
            
            # Track CoinGecko API call
            cost_tracker.track_api_call("coingecko")

            for item in resp.json().get("coins", []):
                if len(added) >= slots:
                    break
                coin = item.get("item", {})
                sym  = coin.get("symbol", "").upper()
                name = coin.get("name", "")
                cg_id = coin.get("id", "")

                if not sym or sym in self._coins:
                    continue

                # Quick check: does Coinbase have a price for this coin?
                from coinbase_client import get_all_prices
                test_price = get_all_prices([sym])
                if not test_price or sym not in test_price:
                    log.debug(f"[AutoDiscover] {sym} not available on Coinbase — skipping")
                    continue

                result = self.add_coin(sym, name=name, coingecko_id=cg_id)
                if result.get("ok"):
                    added.append(sym)
                    log.info(
                        f"[AutoDiscover] Added {sym} ({name}) — "
                        f"rank #{coin.get('market_cap_rank', '?')}"
                    )

        except Exception as e:
            log.warning(f"[AutoDiscover] Error fetching trending coins: {e}")

        return added

    # ── API status check ───────────────────────────────────────────────────────

    def get_api_status(self) -> dict:
        """
        Check connectivity to all external APIs.
        Results are cached for 60 seconds to avoid hammering APIs.
        """
        import time as _time
        if _time.time() - self._api_status_ts < 60 and self._api_status:
            return self._api_status

        status: dict = {}
        errors: dict = {}

        # Coinbase public prices
        try:
            from coinbase_client import get_all_prices
            p = get_all_prices(["BTC"])
            if p and "BTC" in p:
                status["coinbase"] = True
            else:
                status["coinbase"] = False
                errors["coinbase"] = "No BTC price returned — API may be rate-limited or down"
        except Exception as e:
            status["coinbase"] = False
            errors["coinbase"] = str(e)

        # CoinGecko
        try:
            r = requests.get(
                "https://api.coingecko.com/api/v3/ping",
                timeout=5, headers={"Accept": "application/json"},
            )
            if r.status_code == 200:
                # Track CoinGecko API call
                cost_tracker.track_api_call("coingecko")
                status["coingecko"] = True
            else:
                status["coingecko"] = False
                errors["coingecko"] = f"HTTP {r.status_code} — {r.text[:200]}"
        except Exception as e:
            status["coingecko"] = False
            errors["coingecko"] = str(e)

        # RSS news feeds (CoinTelegraph, Decrypt, Bitcoinist)
        try:
            r = requests.get(
                "https://cointelegraph.com/rss",
                headers={"User-Agent": "crypto-bot/1.0"},
                timeout=5,
            )
            if r.status_code == 200:
                status["news_rss"] = True
            else:
                status["news_rss"] = False
                errors["news_rss"] = f"HTTP {r.status_code} from CoinTelegraph RSS — {r.text[:200]}"
        except Exception as e:
            status["news_rss"] = False
            errors["news_rss"] = str(e)

        # Fear & Greed index
        try:
            r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5)
            if r.status_code == 200:
                status["fear_greed"] = True
            else:
                status["fear_greed"] = False
                errors["fear_greed"] = f"HTTP {r.status_code} — {r.text[:200]}"
        except Exception as e:
            status["fear_greed"] = False
            errors["fear_greed"] = str(e)

        # Anthropic (check if key is set — don't make a real API call to save credits)
        from config import ANTHROPIC_API_KEY
        status["anthropic"] = bool(ANTHROPIC_API_KEY)
        if not ANTHROPIC_API_KEY:
            errors["anthropic"] = "ANTHROPIC_API_KEY not set in .env — Claude cannot run"

        status["errors"] = errors

        self._api_status    = status
        self._api_status_ts = _time.time()
        return status

    # ── Trade highlights ───────────────────────────────────────────────────────

    def get_highlights(self) -> dict:
        """
        Return notable trades: big winners and big losers from trade history.
        """
        if not self.portfolio:
            return {"winners": [], "losers": []}

        sells = [t for t in self.portfolio.trade_history if t.get("action") == "SELL"]
        tp = self._risk_params.get("take_profit_pct", 20)
        sl = self._risk_params.get("stop_loss_pct",  -6)

        winners = sorted(
            [t for t in sells if (t.get("pnl_pct") or 0) >= tp * 0.8],
            key=lambda t: t.get("pnl_pct", 0), reverse=True,
        )
        losers = sorted(
            [t for t in sells if (t.get("pnl_pct") or 0) <= sl * 1.2],
            key=lambda t: t.get("pnl_pct", 0),
        )
        return {"winners": winners[:10], "losers": losers[:10]}

    # ── Always On ─────────────────────────────────────────────────────────────

    def _load_persisted_state(self) -> None:
        """Restore always_on from disk; auto-start the bot after 3 s if it was on."""
        try:
            if self._state_file.exists():
                data = json.loads(self._state_file.read_text())
                if data.get("always_on"):
                    self._always_on = True
                    log.info("[AlwaysOn] Restored from saved state — auto-starting in 3 s")
                    threading.Timer(3.0, self._autostart_after_restore).start()
        except Exception as e:
            log.warning(f"[AlwaysOn] Could not load state: {e}")

    def _autostart_after_restore(self) -> None:
        if self._always_on and not self._running and not self._emergency_mode:
            self.start()
            self._ensure_watchdog()

    def _save_state(self) -> None:
        """Persist always_on flag to disk."""
        try:
            self._state_file.write_text(json.dumps({"always_on": self._always_on}))
        except Exception as e:
            log.warning(f"[AlwaysOn] Could not save state: {e}")

    def set_always_on(self, enabled: bool) -> dict:
        """Enable or disable Always On mode."""
        self._always_on = enabled
        self._save_state()

        if enabled:
            self._ensure_watchdog()
            # Start the bot immediately if it isn't running
            if not self._running and not self._emergency_mode:
                self.start()
            log.info("[AlwaysOn] Enabled — bot will auto-restart on any unexpected stop")
        else:
            self._watchdog_active = False
            log.info("[AlwaysOn] Disabled")

        return {"ok": True, "always_on": enabled}

    def _ensure_watchdog(self) -> None:
        """Start the watchdog thread if it isn't already running."""
        if not self._watchdog_active:
            self._watchdog_active = True
            threading.Thread(
                target=self._watchdog_loop, daemon=True, name="Watchdog"
            ).start()

    def _watchdog_loop(self) -> None:
        """Restart the bot whenever it stops unexpectedly while Always On is active."""
        log.info("[Watchdog] Started")
        while self._watchdog_active and self._always_on:
            time.sleep(10)
            if (
                self._watchdog_active
                and self._always_on
                and not self._running
                and not self._emergency_mode
            ):
                log.info("[Watchdog] Bot is not running — restarting in 5 s…")
                self._status = "restarting…"
                time.sleep(5)
                # Re-check; user might have disabled always_on in those 5 s
                if self._always_on and not self._running and not self._emergency_mode:
                    log.info("[Watchdog] Restarting bot now")
                    self.start()
        self._watchdog_active = False
        log.info("[Watchdog] Stopped")

    # ── Background threads ────────────────────────────────────────────────────

    def _trading_loop(self):
        interval = CHECK_INTERVAL_MINUTES * 60

        while self._running:
            self._status = "analysing"
            try:
                # Auto-discover new coins before the cycle if watchlist has room
                if AUTO_DISCOVER_COINS:
                    newly_added = self.auto_discover_coins()
                    if newly_added:
                        log.info(f"[AutoDiscover] Added {len(newly_added)} new coin(s): {newly_added}")

                result = self.engine.run_cycle()
                buys   = len(result.get("buys",  []))
                sells  = len(result.get("sells", []))

                # Shadow cycles — reuse prices + analysis, no extra API calls
                if self._shadow_engines and result.get("prices"):
                    for level, shadow_engine in self._shadow_engines.items():
                        try:
                            shadow_engine.run_shadow_cycle(
                                result["prices"],
                                dict(self.engine.last_analysis),
                            )
                        except Exception as e:
                            log.debug(f"[Shadow:{level}] cycle error: {e}")

                # Market health check — may trigger emergency stop
                if not self.check_market_health(result.get("prices", {})):
                    break  # emergency stop was triggered

                # Only update status if stop() wasn't called mid-cycle
                if self._running:
                    self._status = f"idle · last cycle: {buys}B {sells}S"

                self._cycle_count += 1

                # Check hourly active learning (aggressive paper trading)
                if self._paper_trading and not self._learning_running:
                    hourly_result = self.learner.check_hourly_learning(
                        trades=self.portfolio.trade_history,
                        current_params=self._risk_params,
                        risk_level=self._risk_level,
                        coins_watching=list(self._coins.keys())
                    )
                    if hourly_result:
                        log.info(f"[Learner] Hourly strategy change: {hourly_result['new_strategy']}")
                        # Apply the new strategy mode to the trading engine
                        if self.engine:
                            self.engine.set_strategy_mode(hourly_result['new_strategy'])

                # Trigger AI learning every N cycles (non-blocking)
                if (
                    self._cycle_count % LEARNING_EVERY_N_CYCLES == 0
                    and not self._learning_running
                ):
                    log.info(
                        f"[Learner] Triggering learning after cycle {self._cycle_count}"
                    )
                    self.trigger_learning(auto_apply=self._paper_trading)  # Auto-apply in paper mode

            except Exception as e:
                log.exception("Trading cycle error")
                if self._running:
                    self._status = f"error: {e}"

            if not self._running:
                break

            next_dt = datetime.now(timezone.utc) + timedelta(seconds=interval)
            self._next_check = next_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

            # Sleep in small chunks so stop() responds quickly
            for _ in range(interval // 5):
                if not self._running:
                    break
                time.sleep(5)

    def _price_refresh_loop(self):
        """Update displayed prices every 60 s."""
        while self._running:
            try:
                symbols = list(self._coins.keys())   # re-read for dynamic coin additions
                prices  = get_all_prices(symbols)
                if prices and self.engine:
                    self.engine.last_prices = prices
            except Exception as e:
                log.warning(f"Price refresh error: {e}")
            time.sleep(60)

    # ── Shadow comparison ─────────────────────────────────────────────────────

    def get_shadow_comparison(self, prices: dict) -> list[dict]:
        """
        Return a comparison row for every risk profile (active + shadows),
        sorted low → degen so the dashboard can render a stable leaderboard.
        """
        starting = self._starting_value
        rows: list[dict] = []

        all_portfolios: dict[str, object] = {}
        if self.portfolio:
            all_portfolios[self._risk_level] = self.portfolio
        all_portfolios.update(self._shadow_portfolios)

        for level in ("low", "medium", "high", "degen"):
            portfolio = all_portfolios.get(level)
            if portfolio is None:
                continue
            total   = portfolio.get_total_value(prices)
            pnl     = total - starting
            pnl_pct = (pnl / starting * 100) if starting else 0.0
            profile = RISK_PROFILES[level]
            rows.append({
                "level":       level,
                "label":       profile["label"],
                "color":       profile["color"],
                "active":      level == self._risk_level,
                "total":       round(total,   2),
                "pnl_usd":     round(pnl,     2),
                "pnl_pct":     round(pnl_pct, 2),
                "positions":   len(portfolio.positions),
                "max_pos":     profile["max_positions"],
                "trade_count": len(portfolio.trade_history),
                "threshold":   profile["sentiment_buy_threshold"],
            })
        return rows

    # ── State snapshot ────────────────────────────────────────────────────────

    def get_state(self) -> dict:
        """
        Build a complete JSON-serialisable snapshot of the bot's current state.
        Called from the async WebSocket broadcast loop every 2 seconds.
        Must be fast — reads in-memory data only, no I/O.
        """
        prices     = dict(self.engine.last_prices)     if self.engine else {}
        analysis   = dict(self.engine.last_analysis)   if self.engine else {}
        validation = dict(self.engine.last_validation) if self.engine else {}

        # ── Portfolio ─────────────────────────────────────────────────────────
        starting = self._starting_value
        cash  = starting
        total = starting
        if self.portfolio:
            cash  = self.portfolio.cash
            total = self.portfolio.get_total_value(prices)
        pnl     = total - starting
        pnl_pct = (pnl / starting * 100) if starting else 0.0

        # ── Open positions ────────────────────────────────────────────────────
        tp = self._risk_params["take_profit_pct"]
        sl = self._risk_params["stop_loss_pct"]
        positions: list[dict] = []
        if self.portfolio:
            for sym, pos in self.portfolio.positions.items():
                current     = prices.get(sym, pos["entry_price"])
                pnl_usd     = pos["quantity"] * (current - pos["entry_price"])
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
                    "target_price":  round(pos["entry_price"] * (1 + tp / 100), 6),
                    "stop_price":    round(pos["entry_price"] * (1 + sl / 100), 6),
                    "reasoning":     pos.get("reasoning", ""),
                })

        trades = self.portfolio.get_recent_trades(15) if self.portfolio else []

        # ── Learning summary ──────────────────────────────────────────────────
        insight = self._last_insight or {}

        return {
            "status":     self._status,
            "next_check": self._next_check,
            "prices":     prices,
            "validation": validation,
            "portfolio": {
                "cash":            round(cash,  2),
                "total":           round(total, 2),
                "pnl_usd":         round(pnl,   2),
                "pnl_pct":         round(pnl_pct, 2),
                "positions_count": len(positions),
                "starting_cash":   round(starting, 2),
                "paper_trading":   self._paper_trading,
                "trade_count":     len(self.portfolio.trade_history) if self.portfolio else 0,
            },
            "emergency_mode": self._emergency_mode,
            "always_on":      self._always_on,
            "positions": positions,
            "analysis":  analysis,
            "trades":    trades,
            "logs":      self.log_buffer.get_recent(80),
            "config": {
                "risk_level":    self._risk_level,
                "description":   self._risk_params.get("description", ""),
                "ideal_for":     self._risk_params.get("ideal_for", ""),
                "threshold":     self._risk_params["sentiment_buy_threshold"],
                "take_profit":   self._risk_params["take_profit_pct"],
                "stop_loss":     self._risk_params["stop_loss_pct"],
                "trade_size":    self._risk_params["trade_amount_usd"],
                "max_positions": self._risk_params["max_positions"],
            },
            "risk_profiles": {
                k: {
                    "label":       v["label"],
                    "description": v["description"],
                    "ideal_for":   v["ideal_for"],
                    "threshold":   v["sentiment_buy_threshold"],
                    "take_profit": v["take_profit_pct"],
                    "stop_loss":   v["stop_loss_pct"],
                    "trade_size":  v["trade_amount_usd"],
                    "max_positions": v["max_positions"],
                    "color":       v["color"],
                }
                for k, v in RISK_PROFILES.items()
            },
            "coins": {
                sym: {
                    "symbol":     sym,
                    "price":      prices.get(sym),
                    "product_id": cfg["product_id"],
                    "technical":  analysis.get(sym, {}).get("technical"),
                }
                for sym, cfg in self._coins.items()
            },
            "learning": {
                "running":     self._learning_running,
                "cycle_count": self._cycle_count,
                "next_in":     LEARNING_EVERY_N_CYCLES - (self._cycle_count % LEARNING_EVERY_N_CYCLES),
                "last_run":    insight.get("timestamp"),
                "analysis":    insight.get("analysis", ""),
                "key_insight": insight.get("key_insight", ""),
                "win_rate":    insight.get("stats", {}).get("win_rate_pct"),
                "suggestions": insight.get("suggestions", []),
                "auto_applied": insight.get("auto_applied", []),
                "patterns":    insight.get("patterns", []),
                "strategy_mode": getattr(self.learner, '_current_strategy_mode', 'balanced'),
                "performance_change": insight.get("performance_change", {"change": 0, "direction": "neutral"}),
                "timeline": self.learner.get_performance_timeline(),
                "insights_count": len(self.learner.get_insights()),
                "all_insights": self.learner.get_insights()[:10],  # Last 10 for UI
            },
            "shadows": self.get_shadow_comparison(prices),
        }
