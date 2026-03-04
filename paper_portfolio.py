from __future__ import annotations
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

    def __init__(
        self,
        portfolio_file: str | None = None,
        trades_file: str | None = None,
    ):
        self._portfolio_file = portfolio_file or PORTFOLIO_FILE
        self._trades_file    = trades_file    or TRADES_FILE
        self.cash: float = PAPER_STARTING_CASH
        self.positions: dict = {}
        self.trade_history: list = []
        self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self):
        """Load saved state from disk (if it exists)."""
        if Path(self._portfolio_file).exists():
            try:
                with open(self._portfolio_file) as f:
                    data = json.load(f)
                self.cash      = data.get("cash", PAPER_STARTING_CASH)
                self.positions = data.get("positions", {})
                log.info(
                    f"Portfolio loaded: ${self.cash:,.2f} cash, "
                    f"{len(self.positions)} open position(s)"
                )
            except Exception as e:
                log.error(f"Could not load portfolio file: {e}")

        if Path(self._trades_file).exists():
            try:
                with open(self._trades_file) as f:
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
        with open(self._portfolio_file, "w") as f:
            json.dump(portfolio_data, f, indent=2)

        # Keep only the most recent 200 trades on disk
        with open(self._trades_file, "w") as f:
            json.dump(self.trade_history[-200:], f, indent=2)

    # ── Trading actions ───────────────────────────────────────────────────────

    def buy(
        self,
        symbol: str,
        price: float,
        amount_usd: float,
        sentiment_score: float | None = None,
        reasoning: str | None = None,
    ) -> dict | None:
        """
        Simulate buying a coin.

        Args:
            symbol:          Coin ticker, e.g. "BTC"
            price:           Current market price
            amount_usd:      Dollar amount to spend
            sentiment_score: Claude's sentiment score at time of buy (stored for
                             the AI strategy learner to analyse later)

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
            "reasoning":   reasoning or "",
        }

        trade = {
            "action":    "BUY",
            "symbol":    symbol,
            "price":     price,
            "quantity":  quantity,
            "total_usd": amount_usd,
            "timestamp": _now_iso(),
        }
        if sentiment_score is not None:
            trade["sentiment_score"] = round(sentiment_score, 1)
        if reasoning:
            trade["reasoning"] = reasoning
        self.trade_history.append(trade)
        self._save()

        log.info(
            f"[PAPER BUY]  {symbol}: {quantity:.6f} units @ ${price:,.4f} "
            f"= ${amount_usd:,.2f}"
        )
        return trade

    def sell(
        self, 
        symbol: str, 
        price: float, 
        reason: str = "manual", 
        reason_detail: str | None = None,
        trigger_price: float | None = None,
        trigger_conditions: dict | None = None
    ) -> dict | None:
        """
        Simulate selling an entire position.

        Args:
            symbol: Coin ticker
            price:  Current market price
            reason: Why we're selling ("take_profit", "stop_loss", "manual", "overbought")
            reason_detail: Detailed explanation of the sell trigger
            trigger_price: The specific price/threshold that triggered the sell
            trigger_conditions: Additional context about market conditions at sell time

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
        
        # Calculate hold time
        try:
            entry_time = datetime.fromisoformat(pos["entry_time"].replace('Z', '+00:00'))
            sell_time = datetime.now(timezone.utc)
            hold_duration_hours = (sell_time - entry_time).total_seconds() / 3600
        except:
            hold_duration_hours = None

        self.cash += proceeds

        buy_reasoning = pos.get("reasoning", "")

        trade = {
            "id":        f"{symbol}_{_now_iso()}_{reason}",  # Unique ID for frontend tracking
            "action":    "SELL",
            "symbol":    symbol,
            "price":     price,
            "quantity":  quantity,
            "total_usd": proceeds,
            "total_cost": cost,  # Add original cost for better tracking
            "pnl_usd":   pnl_usd,
            "pnl_pct":   pnl_pct,
            "reason":    reason,
            "timestamp": _now_iso(),
            "entry_price": pos["entry_price"],  # Track entry price in sell record
            "entry_time": pos["entry_time"],    # Track entry time in sell record
        }
        
        # Enhanced sell evidence and proof
        if reason_detail:
            trade["reason_detail"] = reason_detail
        if trigger_price:
            trade["trigger_price"] = trigger_price
        if trigger_conditions:
            trade["trigger_conditions"] = trigger_conditions
        if hold_duration_hours:
            trade["hold_duration_hours"] = round(hold_duration_hours, 1)
        if buy_reasoning:
            trade["buy_reasoning"] = buy_reasoning
            
        # Add sell evidence summary for easy dashboard display
        evidence = []
        if reason == "take_profit":
            evidence.append(f"✅ Profit target reached: +{pnl_pct:.1f}%")
            if trigger_price:
                evidence.append(f"🎯 Target price: ${trigger_price:,.4f}")
        elif reason == "stop_loss":
            evidence.append(f"🛑 Stop loss triggered: {pnl_pct:.1f}%")
            if trigger_price:
                evidence.append(f"⚠️ Stop price: ${trigger_price:,.4f}")
        elif reason == "overbought":
            evidence.append("📈 Technical exit: Overbought conditions")
            if trigger_conditions and 'rsi' in trigger_conditions:
                evidence.append(f"📊 RSI: {trigger_conditions['rsi']:.0f}")
        elif reason == "proof_demonstration":
            evidence.append(f"🎯 PROOF SELL: Demonstrating sell functionality works!")
            evidence.append(f"✅ Early profit-taking: +{pnl_pct:.1f}%")
            evidence.append(f"📈 Manual trigger: One-time proof of concept")
            if trigger_conditions and trigger_conditions.get('target_profit_pct'):
                evidence.append(f"🎯 Target was >{trigger_conditions['target_profit_pct']:.1f}%, achieved {pnl_pct:.1f}%")
        
        if hold_duration_hours:
            if hold_duration_hours < 1:
                evidence.append(f"⏱️ Quick trade: {hold_duration_hours*60:.0f}min hold")
            elif hold_duration_hours < 24:
                evidence.append(f"⏱️ Hold time: {hold_duration_hours:.1f}hrs")
            else:
                evidence.append(f"⏱️ Hold time: {hold_duration_hours/24:.1f}days")
                
        trade["sell_evidence"] = evidence
        
        self.trade_history.append(trade)
        self._save()

        icon = "+" if pnl_usd >= 0 else ""
        log.info(
            f"[PAPER SELL] {symbol}: {icon}${pnl_usd:,.2f} ({icon}{pnl_pct:.1f}%) "
            f"reason={reason} • Evidence: {', '.join(evidence[:2])}"
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
    
    def get_pnl_breakdown(self, time_period: str = "24h") -> dict:
        """
        Calculate P&L breakdown for specified time period.
        
        Args:
            time_period: "1h", "24h", or "7d"
            
        Returns:
            Dict with P&L breakdown, trade counts, and details
        """
        try:
            now = datetime.now(timezone.utc)
            
            if time_period == "1h":
                cutoff = now.replace(minute=0, second=0, microsecond=0)
                cutoff = cutoff.replace(hour=cutoff.hour - 1)
            elif time_period == "24h":
                cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0)
                cutoff = cutoff.replace(day=cutoff.day - 1)
            elif time_period == "7d":
                cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0)
                cutoff = cutoff.replace(day=cutoff.day - 7)
            else:
                cutoff = datetime.min.replace(tzinfo=timezone.utc)
                
            relevant_trades = []
            total_pnl = 0
            wins = 0
            losses = 0
            trade_details = []
            
            for trade in self.trade_history:
                try:
                    trade_time = datetime.fromisoformat(trade["timestamp"].replace('Z', '+00:00'))
                    if trade_time >= cutoff:
                        relevant_trades.append(trade)
                        
                        # Only count sell trades for P&L
                        if trade["action"] == "SELL" and "pnl_usd" in trade:
                            pnl = trade["pnl_usd"]
                            total_pnl += pnl
                            
                            if pnl > 0:
                                wins += 1
                            else:
                                losses += 1
                                
                            # Add trade detail for UI display
                            detail = {
                                "symbol": trade["symbol"],
                                "timestamp": trade["timestamp"],
                                "pnl_usd": pnl,
                                "pnl_pct": trade.get("pnl_pct", 0),
                                "reason": trade.get("reason", "manual"),
                                "evidence": trade.get("sell_evidence", []),
                                "hold_time": trade.get("hold_duration_hours"),
                            }
                            trade_details.append(detail)
                            
                except (ValueError, KeyError):
                    continue
                    
            # Sort trade details by timestamp (newest first)
            trade_details.sort(key=lambda x: x["timestamp"], reverse=True)
            
            win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0
            
            return {
                "period": time_period,
                "total_pnl_usd": total_pnl,
                "wins": wins,
                "losses": losses,
                "total_trades": len(relevant_trades),
                "sell_trades": wins + losses,
                "win_rate": win_rate,
                "trade_details": trade_details,
                "period_start": cutoff.isoformat(),
                "period_end": now.isoformat(),
            }
            
        except Exception as e:
            log.error(f"Error calculating P&L breakdown for {time_period}: {e}")
            return {
                "period": time_period,
                "total_pnl_usd": 0,
                "wins": 0,
                "losses": 0,
                "total_trades": 0,
                "sell_trades": 0,
                "win_rate": 0,
                "trade_details": [],
                "error": str(e)
            }
    
    @property
    def paper_trading(self) -> bool:
        """Always return True for PaperPortfolio to indicate this is paper trading."""
        return True


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
