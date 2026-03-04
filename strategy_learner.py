from __future__ import annotations
"""
AI Strategy Learner – uses Claude to analyse the bot's own trade history
and suggest (or automatically apply) strategy improvements.

How it works
------------
After every LEARNING_EVERY_N_CYCLES trading cycles, BotController calls
run_learning_cycle(). This module:

  1. Reads the last N completed trades (wins + losses) from the portfolio.
  2. Calculates performance stats (win rate, avg P&L, score correlation).
  3. Sends everything to Claude with a structured prompt.
  4. Parses Claude's JSON response into actionable suggestions.
  5. Optionally auto-applies safe adjustments (within defined guardrails).
  6. Persists the learning history to learning.json.

The UI shows each learning run's summary, insights, and whether any
parameters were auto-adjusted.
"""
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import anthropic

from config import ANTHROPIC_API_KEY, RISK_PROFILES, LEARNING_FILE

log = logging.getLogger(__name__)

# Safety guardrails – auto-apply will never push params outside these bounds
_BOUNDS = {
    "sentiment_buy_threshold": (5.0,  9.5),
    "take_profit_pct":         (5.0, 150.0),
    "stop_loss_pct":          (-25.0, -2.0),   # negative values
    "trade_amount_usd":        (50.0, 2000.0),
    "max_positions":           (1,    12),
}

# Maximum parameter drift allowed in a single auto-apply (relative)
_MAX_DRIFT = 0.20   # 20% change per learning cycle

# Aggressive learning modes for paper trading
_PAPER_LEARNING_STRATEGIES = [
    "chaos_mode",      # Make risky trades to learn failure modes
    "micro_gains",     # Focus on small consistent wins
    "momentum_chase",  # Chase trends aggressively
    "contrarian",      # Go against market sentiment
    "technical_pure",  # Rely purely on technical indicators
]


class StrategyLearner:
    """Analyses trade history with Claude and suggests strategy improvements."""

    def __init__(self):
        self._history: list[dict] = []
        self._load_history()
        self._last_hourly_check = datetime.now(timezone.utc)
        self._current_strategy_mode = "balanced"
        self._strategy_start_time = datetime.now(timezone.utc)
        self._performance_timeline: list[dict] = []

    # ── Public API ────────────────────────────────────────────────────────────

    def run_learning_cycle(
        self,
        trades: list[dict],
        current_params: dict,
        risk_level: str,
        coins_watching: list[str],
        auto_apply: bool = False,
    ) -> dict:
        """
        Analyse recent trades with Claude and generate improvement insights.

        Args:
            trades:         Recent completed trades from paper_portfolio
            current_params: Active risk-profile parameter dict
            risk_level:     Active risk level name (e.g. "medium")
            coins_watching: List of symbols currently on the watchlist
            auto_apply:     If True, apply Claude's suggestions within guardrails

        Returns:
            Insight dict (also stored in self._history and saved to disk).
        """
        # Need at least 3 completed sell trades to learn from
        sells = [t for t in trades if t.get("action") == "SELL"]
        if len(sells) < 3:
            log.info(
                f"[Learner] Only {len(sells)} completed trade(s) — "
                "need 3+ to run learning cycle"
            )
            return {"skipped": True, "reason": f"Only {len(sells)} completed trade(s) — need 3+"}

        stats        = self._calc_stats(sells)
        prompt_text  = self._build_prompt(sells, stats, current_params, risk_level, coins_watching)
        raw_response = self._call_claude(prompt_text)

        if raw_response is None:
            return {"skipped": True, "reason": "Claude API unavailable"}

        parsed = self._parse_response(raw_response)

        applied = []
        new_params = dict(current_params)
        if auto_apply and parsed.get("suggestions"):
            new_params, applied = self._apply_suggestions(
                parsed["suggestions"], current_params
            )

        insight = {
            "timestamp":   datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "risk_level":  risk_level,
            "trade_count": len(sells),
            "stats":       stats,
            "analysis":    parsed.get("analysis", ""),
            "key_insight": parsed.get("key_insight", ""),
            "suggestions": parsed.get("suggestions", []),
            "patterns":    parsed.get("patterns", []),
            "coin_notes":  parsed.get("coin_notes", {}),
            "auto_applied": applied,
            "new_params":  new_params if applied else None,
            "strategy_mode": self._current_strategy_mode,
            "performance_change": self._calculate_performance_change(stats),
            "timeline_entry": {
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "win_rate": stats.get("win_rate_pct", 0),
                "avg_pnl": stats.get("total_pnl_usd", 0) / max(len(sells), 1),
                "strategy_mode": self._current_strategy_mode
            },
        }

        self._history.append(insight)
        # Keep last 20 learning runs
        if len(self._history) > 20:
            self._history = self._history[-20:]

        # Update performance timeline
        self._performance_timeline.append(insight["timeline_entry"])
        if len(self._performance_timeline) > 50:  # Keep last 50 entries
            self._performance_timeline = self._performance_timeline[-50:]
        
        self._save_history()
        log.info(
            f"[Learner] Cycle complete — {len(parsed.get('suggestions', []))} suggestion(s), "
            f"{len(applied)} auto-applied, mode: {self._current_strategy_mode}"
        )
        return insight

    def get_insights(self) -> list[dict]:
        """Return learning history, newest first."""
        return list(reversed(self._history))

    def get_latest(self) -> dict | None:
        return self._history[-1] if self._history else None
    
    def get_performance_timeline(self) -> list[dict]:
        """Return performance timeline for sparkline visualization."""
        return self._performance_timeline
    
    def check_hourly_learning(self, trades: list[dict], current_params: dict, 
                             risk_level: str, coins_watching: list[str]) -> dict | None:
        """Check if hourly active learning should trigger strategy changes."""
        now = datetime.now(timezone.utc)
        hours_since_check = (now - self._last_hourly_check).total_seconds() / 3600
        
        if hours_since_check < 1.0:
            return None
            
        self._last_hourly_check = now
        
        # Check if current strategy is underperforming
        if self._should_change_strategy(trades):
            new_mode = self._select_new_strategy_mode()
            if new_mode != self._current_strategy_mode:
                log.info(f"[Learner] Switching from {self._current_strategy_mode} to {new_mode} mode")
                self._current_strategy_mode = new_mode
                self._strategy_start_time = now
                
                return {
                    "hourly_change": True,
                    "new_strategy": new_mode,
                    "reason": "Hourly strategy optimization triggered",
                    "timestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ")
                }
        
        return None
    
    def _should_change_strategy(self, trades: list[dict]) -> bool:
        """Determine if strategy should be changed based on recent performance."""
        # Get trades from the last hour
        one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
        recent_sells = [t for t in trades 
                       if t.get("action") == "SELL" 
                       and "timestamp" in t
                       and datetime.fromisoformat(t["timestamp"].replace("Z", "+00:00")) > one_hour_ago]
        
        if len(recent_sells) < 2:
            # Not enough recent activity, check if we've been in same strategy too long
            hours_in_strategy = (datetime.now(timezone.utc) - self._strategy_start_time).total_seconds() / 3600
            return hours_in_strategy > 4  # Change every 4 hours minimum
        
        # Check recent win rate
        wins = [t for t in recent_sells if t.get("pnl_usd", 0) > 0]
        win_rate = len(wins) / len(recent_sells) * 100 if recent_sells else 0
        
        # Aggressive paper trading: change strategy if not learning enough
        return win_rate < 40 or win_rate > 80  # Change if too good or too bad
    
    def _select_new_strategy_mode(self) -> str:
        """Select a new strategy mode for experimentation."""
        import random
        
        # In paper trading, be aggressive and try different approaches
        available_modes = [mode for mode in _PAPER_LEARNING_STRATEGIES 
                          if mode != self._current_strategy_mode]
        
        if not available_modes:
            return "balanced"
            
        return random.choice(available_modes)
    
    def _calculate_performance_change(self, current_stats: dict) -> dict:
        """Calculate performance change from last learning cycle."""
        if not self._history:
            return {"change": 0, "direction": "neutral"}
            
        last_stats = self._history[-1].get("stats", {})
        current_wr = current_stats.get("win_rate_pct", 0)
        last_wr = last_stats.get("win_rate_pct", 0)
        
        change = current_wr - last_wr
        direction = "up" if change > 2 else "down" if change < -2 else "neutral"
        
        return {"change": round(change, 1), "direction": direction}

    # ── Stats ─────────────────────────────────────────────────────────────────

    def _calc_stats(self, sells: list[dict]) -> dict:
        wins   = [t for t in sells if t.get("pnl_usd", 0) > 0]
        losses = [t for t in sells if t.get("pnl_usd", 0) <= 0]

        win_rate  = len(wins) / len(sells) * 100 if sells else 0
        avg_win   = sum(t.get("pnl_pct", 0) for t in wins)   / max(len(wins), 1)
        avg_loss  = sum(t.get("pnl_pct", 0) for t in losses) / max(len(losses), 1)
        total_pnl = sum(t.get("pnl_usd", 0) for t in sells)

        # Score-to-outcome correlation (buys with sentiment_score attached)
        scored_wins   = [t for t in wins   if "sentiment_score" in t]
        scored_losses = [t for t in losses if "sentiment_score" in t]
        avg_score_win  = (
            sum(t["sentiment_score"] for t in scored_wins)   / len(scored_wins)
            if scored_wins else None
        )
        avg_score_loss = (
            sum(t["sentiment_score"] for t in scored_losses) / len(scored_losses)
            if scored_losses else None
        )

        # Coin-level breakdown
        coin_pnl: dict[str, list[float]] = {}
        for t in sells:
            sym = t.get("symbol", "?")
            coin_pnl.setdefault(sym, []).append(t.get("pnl_pct", 0))
        coin_avg = {sym: sum(v) / len(v) for sym, v in coin_pnl.items()}

        return {
            "total_trades": len(sells),
            "wins":         len(wins),
            "losses":       len(losses),
            "win_rate_pct": round(win_rate, 1),
            "avg_win_pct":  round(avg_win,  2),
            "avg_loss_pct": round(avg_loss, 2),
            "total_pnl_usd": round(total_pnl, 2),
            "avg_score_on_wins":   round(avg_score_win,  1) if avg_score_win  else None,
            "avg_score_on_losses": round(avg_score_loss, 1) if avg_score_loss else None,
            "coin_avg_pnl_pct":    {k: round(v, 2) for k, v in coin_avg.items()},
        }

    # ── Prompt ────────────────────────────────────────────────────────────────

    def _build_prompt(
        self,
        sells: list[dict],
        stats: dict,
        params: dict,
        risk_level: str,
        coins: list[str],
    ) -> str:
        # Format trade list — include RSI extracted from reasoning when available
        trade_lines = []
        for t in sells[-25:]:   # last 25 sells
            score_str = f"  score={t['sentiment_score']}" if "sentiment_score" in t else ""
            pnl_str   = f"{t.get('pnl_pct', 0):+.1f}%"
            # Extract RSI from reasoning string if present
            reasoning = t.get("buy_reasoning") or t.get("reasoning") or ""
            rsi_str = ""
            if "RSI" in reasoning:
                m = re.search(r"RSI\s+([\d.]+)", reasoning)
                if m:
                    rsi_str = f"  rsi={m.group(1)}"
            trade_lines.append(
                f"  {t.get('timestamp','')[:10]}  {t['symbol']:6s}  "
                f"{t.get('reason','?'):12s}  {pnl_str:>8}{score_str}{rsi_str}"
            )

        trade_block = "\n".join(trade_lines)

        score_note = ""
        if stats.get("avg_score_on_wins") and stats.get("avg_score_on_losses"):
            score_note = (
                f"\nSentiment score correlation:\n"
                f"  Avg score on WINNING trades: {stats['avg_score_on_wins']}/10\n"
                f"  Avg score on LOSING trades:  {stats['avg_score_on_losses']}/10"
            )

        coin_block = "\n".join(
            f"  {sym}: avg {pct:+.1f}%"
            for sym, pct in stats.get("coin_avg_pnl_pct", {}).items()
        )

        return f"""You are reviewing the performance of a paper crypto trading bot to help it improve.

CURRENT STRATEGY ({risk_level.upper()} risk profile):
  Buy threshold:   {params['sentiment_buy_threshold']}/10
  Take profit:    +{params['take_profit_pct']}%
  Stop loss:       {params['stop_loss_pct']}%
  Trade size:      ${params['trade_amount_usd']}
  Max positions:   {params['max_positions']}
  Watching:        {', '.join(coins)}

TECHNICAL INDICATOR RULES (integrated into buy/sell decisions):
  RSI < 35  → strong oversold signal  (buy_points +2)
  RSI < 50  → mild bullish bias       (buy_points +1)
  RSI > 65  → mild overbought         (sell_points +1)
  RSI > 72  → overbought — NEW buys blocked entirely
  RSI > 80  → overbought exit — existing position force-sold
  MACD > Signal → bullish momentum    (buy_points +1)
  MACD < Signal → bearish momentum    (sell_points +1)
  BB pct_b < 0.20 → near lower band  (buy_points +1)
  BB pct_b > 0.80 → near upper band  (sell_points +1)
  Composite: BUY requires buy_points ≥ 2; SELL requires sell_points ≥ 3

PERFORMANCE SUMMARY ({stats['total_trades']} completed trades):
  Win rate:   {stats['win_rate_pct']}%  ({stats['wins']} wins / {stats['losses']} losses)
  Avg gain:  +{stats['avg_win_pct']}% on wins
  Avg loss:   {stats['avg_loss_pct']}% on losses
  Total P&L:  ${stats['total_pnl_usd']:+,.2f}
{score_note}

PER-COIN RESULTS:
{coin_block}

RECENT TRADES (date | symbol | exit_reason | pnl | sentiment_score_at_entry | rsi_at_entry):
{trade_block}

Analyse this data and respond with a JSON object in EXACTLY this format (no markdown, no explanation outside the JSON):
{{
  "analysis": "2-3 sentence overall assessment",
  "key_insight": "single most important thing to change or keep",
  "patterns": ["pattern 1", "pattern 2", "pattern 3"],
  "coin_notes": {{
    "SYMBOL": "brief note on this coin's performance"
  }},
  "suggestions": [
    {{
      "parameter": "sentiment_buy_threshold | take_profit_pct | stop_loss_pct | trade_amount_usd | max_positions",
      "current_value": <number>,
      "suggested_value": <number>,
      "reasoning": "why this change"
    }}
  ]
}}

Rules:
- Only suggest changes where the data clearly supports them
- Suggest at most 2-3 parameter changes
- Do not suggest changes outside these safe ranges:
    sentiment_buy_threshold: 5.0 – 9.5
    take_profit_pct: 5 – 150
    stop_loss_pct: -25 to -2
    trade_amount_usd: 50 – 2000
    max_positions: 1 – 12
- Consider the interaction of sentiment score AND RSI: if wins tend to have low RSI entries
  (oversold), that validates the technical filters. If losses all had RSI > 65, recommend
  tightening the sentiment_buy_threshold to compensate for less technical confirmation.
- If performance is good, say so and suggest minor fine-tuning only
"""

    # ── Claude call ───────────────────────────────────────────────────────────

    def _call_claude(self, prompt: str) -> str | None:
        if not ANTHROPIC_API_KEY:
            log.warning("[Learner] No Anthropic API key – skipping learning cycle")
            return None
        try:
            from cost_tracker import cost_tracker
            
            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            msg = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1000,
                messages=[{"role": "user", "content": prompt}],
            )
            
            # Track cost for this API call using actual token counts
            if hasattr(msg, 'usage') and msg.usage:
                input_tokens = msg.usage.input_tokens
                output_tokens = msg.usage.output_tokens
                cache_read_tokens = getattr(msg.usage, 'cache_read_tokens', 0)
                model = "claude-haiku"
            else:
                # Fallback to estimates
                input_tokens = len(prompt.split()) * 1.3
                output_tokens = len(msg.content[0].text.split()) * 1.3
                cache_read_tokens = 0
                model = "claude-haiku"
                
            cost_tracker.track_claude_usage(int(input_tokens), int(output_tokens), int(cache_read_tokens), model)
            
            return msg.content[0].text.strip()
        except Exception as e:
            log.error(f"[Learner] Claude API error: {e}")
            return None

    # ── Response parsing ──────────────────────────────────────────────────────

    def _parse_response(self, text: str) -> dict:
        # Strip any markdown code fences Claude might add
        text = re.sub(r"```(?:json)?", "", text).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Try to extract just the JSON object
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
        log.warning("[Learner] Could not parse Claude response as JSON")
        return {
            "analysis":    text[:500],
            "key_insight": "",
            "patterns":    [],
            "suggestions": [],
            "coin_notes":  {},
        }

    # ── Auto-apply ────────────────────────────────────────────────────────────

    def _apply_suggestions(
        self,
        suggestions: list[dict],
        current: dict,
    ) -> tuple[dict, list[dict]]:
        """
        Apply suggestions that fall within safety guardrails.
        Returns (new_params, list_of_applied_changes).
        """
        new_params = dict(current)
        applied    = []

        for s in suggestions:
            param   = s.get("parameter")
            current_val = s.get("current_value")
            suggested   = s.get("suggested_value")

            if param not in _BOUNDS or suggested is None:
                continue

            lo, hi = _BOUNDS[param]

            # Clamp to safe range
            suggested = max(lo, min(hi, suggested))

            # Don't drift more than MAX_DRIFT from current
            if current_val and current_val != 0:
                max_change = abs(current_val) * _MAX_DRIFT
                if abs(suggested - current_val) > max_change:
                    direction = 1 if suggested > current_val else -1
                    suggested = current_val + direction * max_change

            # Round appropriately
            if param in ("max_positions",):
                suggested = int(round(suggested))
            else:
                suggested = round(suggested, 1)

            if suggested != new_params.get(param):
                new_params[param] = suggested
                applied.append({
                    "parameter": param,
                    "from":      new_params.get(param),
                    "to":        suggested,
                    "reasoning": s.get("reasoning", ""),
                })
                log.info(
                    f"[Learner] Auto-applied: {param} "
                    f"{current.get(param)} → {suggested}"
                )

        return new_params, applied

    # ── Persistence ───────────────────────────────────────────────────────────

    def _save_history(self) -> None:
        try:
            with open(LEARNING_FILE, "w") as f:
                json.dump(self._history, f, indent=2)
        except Exception as e:
            log.warning(f"[Learner] Could not save learning history: {e}")

    def _load_history(self) -> None:
        if Path(LEARNING_FILE).exists():
            try:
                with open(LEARNING_FILE) as f:
                    self._history = json.load(f)
                log.info(f"[Learner] Loaded {len(self._history)} past learning run(s)")
            except Exception as e:
                log.warning(f"[Learner] Could not load learning history: {e}")
