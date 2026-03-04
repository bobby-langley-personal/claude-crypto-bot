from __future__ import annotations
"""
Trading engine – the brain of the bot.

Each call to run_cycle() does one full loop:
  1. Fetch current prices for all watched coins.
  2. Validate prices against CoinGecko (cross-source check).
  3. Check open positions for take-profit / stop-loss / overbought exits.
  4. For coins we don't hold, fetch news and ask Claude for a sentiment score.
  5. Check RSI/MACD/Bollinger Bands — block buy if technically overbought.
  6. Validate the sentiment score before trusting it.
  7. Buy any coin whose score is >= threshold (and passes all checks).

Risk parameters and the coin watchlist are set via update_params() /
update_coins() so the dashboard can change them at runtime without restart.
"""
import logging
from datetime import datetime, timezone

from config import COINS
from coinbase_client import get_all_prices
from news_client import get_news, format_articles_for_prompt
from sentiment_analyzer import analyze_sentiment
from paper_portfolio import PaperPortfolio
from data_validator import validate_prices, validate_sentiment, _CG_IDS
from technical_indicators import get_signals

log = logging.getLogger(__name__)

# RSI threshold above which we block new entries (overbought)
RSI_OVERBOUGHT_BLOCK = 72
# RSI threshold above which we exit existing positions early
RSI_OVERBOUGHT_EXIT  = 80


class TradingEngine:
    def __init__(self, portfolio: PaperPortfolio, params: dict, coins: dict = None):
        """
        Args:
            portfolio: PaperPortfolio (or LivePortfolio) instance
            params:    Risk-profile dict (see config.RISK_PROFILES)
            coins:     Watchlist dict {symbol: {product_id, news_query}}.
                       Defaults to config.COINS if None.
        """
        self.portfolio        = portfolio
        self._coins           = dict(coins) if coins else dict(COINS)
        self.last_analysis:   dict = {}
        self.last_prices:     dict = {}
        self.last_validation: dict = {}
        self.strategy_mode    = "balanced"  # Current learning strategy mode
        self._apply_params(params)

    # ── Runtime updates ───────────────────────────────────────────────────────

    def _apply_params(self, params: dict) -> None:
        self.threshold        = params["sentiment_buy_threshold"]
        self.take_profit_pct  = params["take_profit_pct"]
        self.stop_loss_pct    = params["stop_loss_pct"]
        self.trade_amount_usd = params["trade_amount_usd"]
        self.max_positions    = params["max_positions"]

    def update_params(self, params: dict) -> None:
        """Hot-reload risk parameters without restarting the bot."""
        self._apply_params(params)
        log.info(
            f"[Engine] Params updated: threshold={self.threshold}  "
            f"TP=+{self.take_profit_pct}%  SL={self.stop_loss_pct}%  "
            f"size=${self.trade_amount_usd}  max={self.max_positions}"
        )
    
    def set_strategy_mode(self, mode: str) -> None:
        """Set the learning strategy mode for experimental trading."""
        self.strategy_mode = mode
        log.info(f"[Engine] Strategy mode set to: {mode}")
    
    def sell_single_position_for_proof(self, target_profit_pct: float = 5.0) -> dict | None:
        """
        Sell the first profitable position at the earliest profit margin 
        to provide proof that selling functionality works. One-time demonstration.
        
        Args:
            target_profit_pct: Minimum profit percentage to sell (default 5%)
            
        Returns:
            Trade record if a sell occurred, None otherwise
        """
        if not self.portfolio.positions:
            log.info("No open positions to sell for proof")
            return None
            
        # Find the most profitable position above the target
        best_candidate = None
        best_pnl = 0
        
        for symbol, pos in self.portfolio.positions.items():
            price = self.last_prices.get(symbol)
            if not price:
                continue
                
            pnl = self.portfolio.get_position_pnl(symbol, price)
            if not pnl:
                continue
                
            pnl_pct = pnl["pnl_pct"]
            if pnl_pct >= target_profit_pct and pnl_pct > best_pnl:
                best_candidate = symbol
                best_pnl = pnl_pct
                
        if best_candidate:
            price = self.last_prices[best_candidate]
            log.info(f"🎯 PROOF SELL: {best_candidate} at +{best_pnl:.1f}% profit for demonstration")
            trade = self.portfolio.sell(
                best_candidate, price, reason="proof_demonstration",
                reason_detail=f"Early profit-taking demonstration at +{best_pnl:.1f}% (target was {target_profit_pct}%)",
                trigger_price=price,
                trigger_conditions={
                    "proof_sell": True, 
                    "target_profit_pct": target_profit_pct,
                    "actual_pct": best_pnl,
                    "demonstration": "First sell to prove functionality works"
                }
            )
            return trade
        else:
            log.info(f"No positions currently above {target_profit_pct}% profit for proof sell")
            return None
    
    def _get_effective_threshold(self, score: float, symbol: str) -> float:
        """Calculate effective threshold based on current strategy mode."""
        base_threshold = self.threshold
        
        if not hasattr(self.portfolio, 'paper_trading') or not self.portfolio.paper_trading:
            # In live mode, always use base threshold
            return base_threshold
        
        # Aggressive paper trading adjustments based on strategy mode
        if self.strategy_mode == "chaos_mode":
            # Make risky trades to learn failure modes - much lower threshold
            return max(4.0, base_threshold - 2.0)
        elif self.strategy_mode == "micro_gains":
            # Focus on small consistent wins - higher threshold
            return min(8.5, base_threshold + 1.0)
        elif self.strategy_mode == "momentum_chase":
            # Chase trends aggressively - lower threshold for trending coins
            tech = self.last_analysis.get(symbol, {}).get("technical", {})
            if tech.get("macd", {}).get("bullish"):
                return max(5.0, base_threshold - 1.5)
            return base_threshold
        elif self.strategy_mode == "contrarian":
            # Go against sentiment - inverted scoring
            return max(4.0, 10.0 - score + 1.0)
        elif self.strategy_mode == "technical_pure":
            # Rely purely on technical indicators - ignore sentiment somewhat
            tech = self.last_analysis.get(symbol, {}).get("technical", {})
            if tech.get("buy_points", 0) >= 2:  # Strong technical signal
                return max(5.0, base_threshold - 1.0)
            return min(9.0, base_threshold + 1.0)
        
        return base_threshold

    def update_coins(self, coins: dict) -> None:
        """Hot-reload the watchlist without restarting the bot."""
        self._coins = dict(coins)
        log.info(f"[Engine] Watchlist updated: {list(self._coins.keys())}")

    # ── Main cycle ────────────────────────────────────────────────────────────

    def run_cycle(self) -> dict:
        """
        Run one full analysis + trading cycle.

        Returns a summary dict:
            {
              "timestamp":  str,
              "prices":     {symbol: float},
              "validation": {symbol: {...}},
              "sells":      [trade, ...],
              "buys":       [trade, ...],
              "analyses":   {symbol: {...}},
            }
        """
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        log.info("=" * 60)
        log.info(f"Cycle start: {ts}")

        symbols = list(self._coins.keys())
        prices  = get_all_prices(symbols)

        if not prices:
            log.error("Could not fetch any prices – skipping cycle")
            return {"error": "No prices available", "timestamp": ts}

        self.last_prices = prices

        # ── Price cross-validation (Coinbase vs CoinGecko) ────────────────────
        log.info("Validating prices against CoinGecko…")
        self.last_validation = validate_prices(prices)
        for sym, v in self.last_validation.items():
            if v["warnings"]:
                for w in v["warnings"]:
                    log.warning(f"  [Validation] {w}")
            else:
                div = f"{v['divergence_pct']:.2f}%" if v["divergence_pct"] is not None else "n/a"
                log.info(f"  {sym} price OK  (divergence: {div})")

        summary = {
            "timestamp":  ts,
            "prices":     prices,
            "validation": self.last_validation,
            "sells":      [],
            "buys":       [],
            "analyses":   {},
        }

        # ── Step 1: Check exits for open positions ────────────────────────────
        for symbol in list(self.portfolio.positions.keys()):
            price = prices.get(symbol)
            if price is None:
                continue

            pnl = self.portfolio.get_position_pnl(symbol, price)
            if pnl is None:
                continue

            pct = pnl["pnl_pct"]
            log.info(f"  {symbol} P&L: {pct:+.2f}%")

            if pct >= self.take_profit_pct:
                target_price = pnl["entry_price"] * (1 + self.take_profit_pct / 100)
                log.info(f"  -> TAKE PROFIT ({pct:.1f}% >= +{self.take_profit_pct}%)")
                trade = self.portfolio.sell(
                    symbol, price, reason="take_profit",
                    reason_detail=f"+{pct:.1f}% reached take-profit target (+{self.take_profit_pct}%)",
                    trigger_price=target_price,
                    trigger_conditions={"profit_target_pct": self.take_profit_pct, "actual_pct": pct}
                )
                if trade:
                    summary["sells"].append(trade)

            elif pct <= self.stop_loss_pct:
                stop_price = pnl["entry_price"] * (1 + self.stop_loss_pct / 100)
                log.info(f"  -> STOP LOSS ({pct:.1f}% <= {self.stop_loss_pct}%)")
                trade = self.portfolio.sell(
                    symbol, price, reason="stop_loss",
                    reason_detail=f"{pct:.1f}% triggered stop-loss ({self.stop_loss_pct}%)",
                    trigger_price=stop_price,
                    trigger_conditions={"stop_loss_pct": self.stop_loss_pct, "actual_pct": pct}
                )
                if trade:
                    summary["sells"].append(trade)

            else:
                # Check RSI overbought exit
                cg_id  = _CG_IDS.get(symbol)
                sigs   = get_signals(symbol, price, cg_id=cg_id)
                rsi    = sigs.get("rsi")
                if rsi is not None and rsi > RSI_OVERBOUGHT_EXIT:
                    log.info(
                        f"  -> OVERBOUGHT EXIT  {symbol}  RSI={rsi:.0f} "
                        f"> {RSI_OVERBOUGHT_EXIT}"
                    )
                    trade = self.portfolio.sell(
                        symbol, price, reason="overbought",
                        reason_detail=f"RSI {rsi:.0f} exceeded overbought threshold ({RSI_OVERBOUGHT_EXIT})",
                        trigger_conditions={"rsi": rsi, "rsi_threshold": RSI_OVERBOUGHT_EXIT, "technical_signals": sigs}
                    )
                    if trade:
                        summary["sells"].append(trade)

        # ── Step 2: Look for buy opportunities ────────────────────────────────
        open_slots       = self.max_positions - len(self.portfolio.positions)
        coins_to_analyse = [s for s in symbols if s not in self.portfolio.positions]

        if open_slots <= 0:
            log.info("Max positions reached – skipping buy analysis")
            return summary

        if not coins_to_analyse:
            log.info("All positions filled – nothing to analyse")
            return summary

        log.info(
            f"Analysing {len(coins_to_analyse)} coin(s), "
            f"{open_slots} slot(s) available"
        )

        scored: list[tuple[str, float, str]] = []   # (symbol, score, reasoning)

        for symbol in coins_to_analyse:
            coin_cfg = self._coins[symbol]
            cg_id    = _CG_IDS.get(symbol)

            # ── Technical indicators ──────────────────────────────────────────
            price = prices.get(symbol)
            sigs  = get_signals(symbol, price or 0, cg_id=cg_id)
            rsi   = sigs.get("rsi")

            if rsi is not None and rsi > RSI_OVERBOUGHT_BLOCK:
                log.info(
                    f"  {symbol}: RSI {rsi:.0f} > {RSI_OVERBOUGHT_BLOCK} "
                    f"(overbought) — skipping buy analysis"
                )
                self.last_analysis[symbol] = {
                    "score":          None,
                    "reasoning":      f"Skipped — RSI {rsi:.0f} overbought",
                    "articles_count": 0,
                    "source":         "—",
                    "validation":     {"ok": True, "confidence": "medium", "badge": "⚠", "warnings": []},
                    "technical":      sigs,
                    "timestamp":      datetime.now(timezone.utc).strftime("%H:%M:%S"),
                }
                continue

            if sigs.get("warnings"):
                for w in sigs["warnings"]:
                    log.warning(f"  [Technical] {w}")

            # ── Fetch news ────────────────────────────────────────────────────
            articles  = get_news(coin_cfg["news_query"], coin_symbol=symbol)
            news_text = format_articles_for_prompt(articles)
            source    = articles[0]["source"] if articles else "none"
            log.info(f"  {symbol}: {len(articles)} article(s) from {source}")

            # ── Ask Claude for a sentiment score ──────────────────────────────
            sentiment = analyze_sentiment(symbol, news_text)
            score     = sentiment["score"]
            sent_reason = sentiment["reasoning"]
            log.info(f"  {symbol} Claude score: {score:.1f}/10 — {sent_reason[:80]}")

            # ── Validate the sentiment result ─────────────────────────────────
            val = validate_sentiment(symbol, score, len(articles))
            log.info(
                f"  {symbol} sentiment validation: "
                f"{val['confidence'].upper()} confidence"
                + (f" — {val['warnings'][0]}" if val["warnings"] else "")
            )

            # ── Build rich reasoning string ───────────────────────────────────
            macd_dir = ""
            if sigs.get("macd"):
                macd_dir = "▲ bullish" if sigs["macd"]["bullish"] else "▼ bearish"
            bb_txt = ""
            if sigs.get("bollinger"):
                pb = sigs["bollinger"]["pct_b"]
                bb_txt = f"BB {pb:.0%} ({'oversold' if pb < 0.2 else 'overbought' if pb > 0.8 else 'midrange'})"

            tech_summary = sigs.get("summary", "no technicals")
            reasoning = (
                f"Sentiment {score:.1f}/10 · {tech_summary} · "
                f"{len(articles)} article(s) from {source} · "
                f"{sent_reason[:120]}"
            )

            # ── Store for dashboard ───────────────────────────────────────────
            self.last_analysis[symbol] = {
                "score":          score,
                "reasoning":      sent_reason,
                "articles_count": len(articles),
                "source":         source,
                "validation":     val,
                "technical":      sigs,
                "timestamp":      datetime.now(timezone.utc).strftime("%H:%M:%S"),
            }

            summary["analyses"][symbol] = {"score": score, "reasoning": reasoning}
            scored.append((symbol, score, reasoning))

        # Sort by score descending — buy best opportunity first
        scored.sort(key=lambda x: x[1], reverse=True)

        for symbol, score, reasoning in scored:
            if open_slots <= 0:
                break

            val   = self.last_analysis[symbol]["validation"]
            price = prices.get(symbol)

            if price is None:
                log.warning(f"  {symbol}: no price available, skipping")
                continue

            # Apply strategy mode adjustments for paper trading experimentation
            effective_threshold = self._get_effective_threshold(score, symbol)
            
            if score >= effective_threshold:
                if val["confidence"] == "low":
                    log.warning(
                        f"  {symbol}: score {score:.1f} meets threshold but "
                        f"data confidence is LOW – skipping buy"
                    )
                    continue

                sigs = self.last_analysis[symbol].get("technical", {})
                log.info(
                    f"  BUY: {symbol} scored {score:.1f}/10  {sigs.get('summary', '')}  "
                    f"(threshold: {self.threshold}, confidence: {val['confidence']})"
                )
                trade = self.portfolio.buy(
                    symbol, price, self.trade_amount_usd,
                    sentiment_score=score,
                    reasoning=reasoning,
                )
                if trade:
                    summary["buys"].append(trade)
                    open_slots -= 1
            else:
                log.info(
                    f"  PASS: {symbol} scored {score:.1f}/10 "
                    f"< threshold {self.threshold}"
                )

        return summary

    def run_shadow_cycle(self, prices: dict, pre_analysis: dict) -> dict:
        """
        Run a cycle using pre-fetched prices and pre-computed analysis.
        Used by shadow portfolios so no extra API calls are made — the same
        sentiment scores from the main engine cycle are reused, but each
        shadow applies its own TP/SL/threshold parameters.
        """
        summary: dict = {"sells": [], "buys": []}

        # ── Step 1: Check exits on open positions ─────────────────────────────
        for symbol in list(self.portfolio.positions.keys()):
            price = prices.get(symbol)
            if price is None:
                continue
            pnl = self.portfolio.get_position_pnl(symbol, price)
            if pnl is None:
                continue
            pct = pnl["pnl_pct"]

            if pct >= self.take_profit_pct:
                trade = self.portfolio.sell(
                    symbol, price, reason="take_profit",
                    reason_detail=f"+{pct:.1f}% reached take-profit (+{self.take_profit_pct}%)",
                )
                if trade:
                    summary["sells"].append(trade)
            elif pct <= self.stop_loss_pct:
                trade = self.portfolio.sell(
                    symbol, price, reason="stop_loss",
                    reason_detail=f"{pct:.1f}% triggered stop-loss ({self.stop_loss_pct}%)",
                )
                if trade:
                    summary["sells"].append(trade)
            else:
                tech = (pre_analysis.get(symbol) or {}).get("technical") or {}
                rsi = tech.get("rsi")
                if rsi is not None and rsi > RSI_OVERBOUGHT_EXIT:
                    trade = self.portfolio.sell(
                        symbol, price, reason="overbought",
                        reason_detail=f"RSI {rsi:.0f} exceeded overbought threshold",
                    )
                    if trade:
                        summary["sells"].append(trade)

        # ── Step 2: Look for buy opportunities ────────────────────────────────
        open_slots = self.max_positions - len(self.portfolio.positions)
        if open_slots <= 0:
            return summary

        scored: list[tuple[str, float, str]] = []
        for symbol, analysis in pre_analysis.items():
            if symbol in self.portfolio.positions:
                continue
            score = analysis.get("score")
            if score is None:
                continue
            tech = (analysis.get("technical") or {})
            rsi = tech.get("rsi")
            if rsi is not None and rsi > RSI_OVERBOUGHT_BLOCK:
                continue
            scored.append((symbol, score, analysis.get("reasoning", "")))

        scored.sort(key=lambda x: x[1], reverse=True)

        for symbol, score, reasoning in scored:
            if open_slots <= 0:
                break
            if score < self.threshold:
                continue
            price = prices.get(symbol)
            if price is None:
                continue
            trade = self.portfolio.buy(
                symbol, price, self.trade_amount_usd,
                sentiment_score=score, reasoning=reasoning,
            )
            if trade:
                summary["buys"].append(trade)
                open_slots -= 1

        return summary
