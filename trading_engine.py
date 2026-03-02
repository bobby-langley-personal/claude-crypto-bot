"""
Trading engine – the brain of the bot.

Each call to run_cycle() does one full loop:
  1. Fetch current prices for all watched coins.
  2. Validate prices against CoinGecko (cross-source check).
  3. Check open positions for take-profit / stop-loss exits.
  4. For coins we don't hold, fetch news and ask Claude for a sentiment score.
  5. Validate the sentiment score before trusting it.
  6. Buy any coin whose score is >= SENTIMENT_BUY_THRESHOLD (and passes validation).
"""
import logging
from datetime import datetime, timezone
from config import (
    COINS,
    SENTIMENT_BUY_THRESHOLD,
    TAKE_PROFIT_PCT,
    STOP_LOSS_PCT,
    TRADE_AMOUNT_USD,
    MAX_POSITIONS,
)
from coinbase_client import get_all_prices
from news_client import get_news, format_articles_for_prompt
from sentiment_analyzer import analyze_sentiment
from paper_portfolio import PaperPortfolio
from data_validator import validate_prices, validate_sentiment

log = logging.getLogger(__name__)


class TradingEngine:
    def __init__(self, portfolio: PaperPortfolio):
        self.portfolio        = portfolio
        self.last_analysis:   dict = {}  # symbol -> full analysis record for dashboard
        self.last_prices:     dict = {}  # symbol -> float
        self.last_validation: dict = {}  # symbol -> price validation result

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

        symbols = list(COINS.keys())
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

            if pct >= TAKE_PROFIT_PCT:
                log.info(f"  -> TAKE PROFIT ({pct:.1f}% >= +{TAKE_PROFIT_PCT}%)")
                trade = self.portfolio.sell(symbol, price, reason="take_profit")
                if trade:
                    summary["sells"].append(trade)

            elif pct <= STOP_LOSS_PCT:
                log.info(f"  -> STOP LOSS ({pct:.1f}% <= {STOP_LOSS_PCT}%)")
                trade = self.portfolio.sell(symbol, price, reason="stop_loss")
                if trade:
                    summary["sells"].append(trade)

        # ── Step 2: Look for buy opportunities ────────────────────────────────
        open_slots       = MAX_POSITIONS - len(self.portfolio.positions)
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

        scored: list[tuple[str, float]] = []

        for symbol in coins_to_analyse:
            coin_cfg = COINS[symbol]

            # Fetch news
            articles  = get_news(coin_cfg["news_query"], coin_symbol=symbol)
            news_text = format_articles_for_prompt(articles)
            source    = articles[0]["source"] if articles else "none"
            log.info(f"  {symbol}: {len(articles)} article(s) from {source}")

            # Ask Claude for a sentiment score
            sentiment = analyze_sentiment(symbol, news_text)
            score     = sentiment["score"]
            reasoning = sentiment["reasoning"]
            log.info(f"  {symbol} Claude score: {score:.1f}/10 — {reasoning[:80]}")

            # Validate the sentiment result
            val = validate_sentiment(symbol, score, len(articles))
            log.info(
                f"  {symbol} sentiment validation: "
                f"{val['confidence'].upper()} confidence"
                + (f" — {val['warnings'][0]}" if val["warnings"] else "")
            )

            # Store everything for the dashboard
            self.last_analysis[symbol] = {
                "score":          score,
                "reasoning":      reasoning,
                "articles_count": len(articles),
                "source":         source,
                "validation":     val,
                "timestamp":      datetime.now(timezone.utc).strftime("%H:%M:%S"),
            }

            summary["analyses"][symbol] = {"score": score, "reasoning": reasoning}
            scored.append((symbol, score))

        # Sort by score, buy the best opportunities first
        scored.sort(key=lambda x: x[1], reverse=True)

        for symbol, score in scored:
            if open_slots <= 0:
                break

            val   = self.last_analysis[symbol]["validation"]
            price = prices.get(symbol)

            if price is None:
                log.warning(f"  {symbol}: no price available, skipping")
                continue

            if score >= SENTIMENT_BUY_THRESHOLD:
                # Warn but still allow the trade if confidence is medium.
                # Block trade only if confidence is low (zero articles).
                if val["confidence"] == "low":
                    log.warning(
                        f"  {symbol}: score {score:.1f} meets threshold but "
                        f"data confidence is LOW – skipping buy"
                    )
                    continue

                log.info(
                    f"  BUY: {symbol} scored {score:.1f}/10 "
                    f"(threshold: {SENTIMENT_BUY_THRESHOLD}, "
                    f"confidence: {val['confidence']})"
                )
                trade = self.portfolio.buy(symbol, price, TRADE_AMOUNT_USD)
                if trade:
                    summary["buys"].append(trade)
                    open_slots -= 1
            else:
                log.info(
                    f"  PASS: {symbol} scored {score:.1f}/10 "
                    f"< threshold {SENTIMENT_BUY_THRESHOLD}"
                )

        return summary
