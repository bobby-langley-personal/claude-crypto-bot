"""
Terminal dashboard built with Rich.

Uses rich.live.Live so the display updates smoothly in-place
without flickering. Call make_renderable() each refresh cycle
and pass the result to live.update().

Layout (top to bottom):
  ┌─ HEADER ──────────────────────────────────────────┐
  │  Bot name | mode | risk level | time | status     │
  ├─ PRICES ──────────────────────────────────────────┤
  │  BTC $68k ✓  ETH $2k ✓  SOL $88 ✓  DOGE $0.09 ✓ │
  ├─ PORTFOLIO ─────────┬─ OPEN POSITIONS ────────────┤
  │  Cash / Total / P&L │  table of open trades       │
  ├─ SENTIMENT ──────────────────────────────────────-┤
  │  Score / signal / confidence / source / reasoning  │
  ├─ RECENT TRADES ───────────────────────────────────┤
  │  table                                             │
  ├─ DEBUG LOG ───────────────────────────────────────┤
  │  last N log lines with colour-coded levels         │
  └────────────────────────────────────────────────────┘
"""
from datetime import datetime
from rich import box
from rich.columns import Columns
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from config import PAPER_STARTING_CASH, PAPER_TRADING, RISK_LEVEL, RISK_DESCRIPTION

# ── Risk level colours ────────────────────────────────────────────────────────
_RISK_COLOUR = {
    "conservative": "green",
    "moderate":     "yellow",
    "aggressive":   "red",
}


# ── Header ────────────────────────────────────────────────────────────────────

def _header(next_check: str, status: str) -> Panel:
    mode_str   = (
        "[bold yellow]PAPER TRADING[/bold yellow]"
        if PAPER_TRADING else
        "[bold red]LIVE TRADING[/bold red]"
    )
    risk_color = _RISK_COLOUR.get(RISK_LEVEL, "white")
    risk_str   = f"[{risk_color}]Risk: {RISK_LEVEL.upper()}[/{risk_color}]"
    ts         = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")

    top_line = (
        f"[bold cyan] Crypto Sentiment Bot[/bold cyan]   "
        f"{mode_str}   {risk_str}   [dim]{ts}[/dim]"
    )
    bot_line = (
        f"[dim]  {RISK_DESCRIPTION}[/dim]   "
        f"[dim]Next analysis: [bold]{next_check}[/bold]   "
        f"Status: {status}[/dim]"
    )
    return Panel(f"{top_line}\n{bot_line}", border_style="cyan", padding=(0, 1))


# ── Live prices ───────────────────────────────────────────────────────────────

def _prices_panel(prices: dict, validation: dict) -> Panel:
    """Show each coin's price with a CoinGecko validation badge."""
    if not prices:
        return Panel("[dim]Fetching prices…[/dim]", title="Live Prices", border_style="dim")

    parts: list[str] = []
    for sym, price in prices.items():
        vinfo  = validation.get(sym, {})
        badge  = vinfo.get("badge", "?")
        badge_color = "green" if badge == "✓" else ("yellow" if badge == "⚠" else "dim")

        if price >= 1_000:
            price_str = f"${price:,.2f}"
        elif price >= 1:
            price_str = f"${price:.4f}"
        else:
            price_str = f"${price:.6f}"

        parts.append(
            f"[bold white]{sym}[/bold white] {price_str} "
            f"[{badge_color}]{badge}[/{badge_color}]"
        )

    # Show any price warnings below the prices
    warn_lines = [
        f"[yellow]  ⚠ {v['warnings'][0]}[/yellow]"
        for v in validation.values()
        if v.get("warnings")
    ]
    content = "   │   ".join(parts)
    if warn_lines:
        content += "\n" + "   ".join(warn_lines)

    return Panel(
        content,
        title="[dim]Live Prices  (✓ = agrees with CoinGecko   ⚠ = divergence   ? = CoinGecko unavailable)[/dim]",
        border_style="dim",
        padding=(0, 1),
    )


# ── Portfolio summary ─────────────────────────────────────────────────────────

def _portfolio_panel(portfolio, prices: dict) -> Panel:
    total   = portfolio.get_total_value(prices)
    pnl     = total - PAPER_STARTING_CASH
    pnl_pct = (pnl / PAPER_STARTING_CASH) * 100
    color   = "green" if pnl >= 0 else "red"
    sign    = "+" if pnl >= 0 else ""

    n_pos  = len(portfolio.positions)
    from config import MAX_POSITIONS
    slots  = f"{n_pos}/{MAX_POSITIONS} slots used"

    content = (
        f"  Cash :  [white]${portfolio.cash:>11,.2f}[/white]\n"
        f"  Total:  [white]${total:>11,.2f}[/white]\n"
        f"  P&L  :  [{color}]{sign}${pnl:>10,.2f}  ({sign}{pnl_pct:.1f}%)[/{color}]\n"
        f"  [dim]{slots}[/dim]"
    )
    return Panel(content, title="[bold]Portfolio[/bold]", border_style="cyan", padding=(0, 1))


# ── Open positions ────────────────────────────────────────────────────────────

def _positions_table(portfolio, prices: dict) -> Table:
    from config import TAKE_PROFIT_PCT, STOP_LOSS_PCT

    tbl = Table(
        "Coin", "Qty", "Entry $", "Now $", "P&L $", "P&L %", "Target", "Stop",
        box=box.SIMPLE_HEAD,
        title="[bold]Open Positions[/bold]",
        show_edge=False,
        padding=(0, 1),
    )

    if not portfolio.positions:
        tbl.add_row("[dim]No open positions[/dim]", "", "", "", "", "", "", "")
        return tbl

    for sym, pos in portfolio.positions.items():
        current = prices.get(sym, pos["entry_price"])
        pnl_pct = ((current - pos["entry_price"]) / pos["entry_price"]) * 100
        pnl_usd = pos["quantity"] * (current - pos["entry_price"])
        color   = "green" if pnl_pct >= 0 else "red"
        sign    = "+" if pnl_pct >= 0 else ""

        target_price = pos["entry_price"] * (1 + TAKE_PROFIT_PCT / 100)
        stop_price   = pos["entry_price"] * (1 + STOP_LOSS_PCT  / 100)

        tbl.add_row(
            f"[bold]{sym}[/bold]",
            f"{pos['quantity']:.6f}",
            f"${pos['entry_price']:,.4f}",
            f"${current:,.4f}",
            f"[{color}]{sign}${pnl_usd:,.2f}[/{color}]",
            f"[{color}]{sign}{pnl_pct:.1f}%[/{color}]",
            f"[dim green]+{TAKE_PROFIT_PCT:.0f}% (${target_price:,.2f})[/dim green]",
            f"[dim red]{STOP_LOSS_PCT:.0f}% (${stop_price:,.2f})[/dim red]",
        )
    return tbl


# ── Sentiment analysis ────────────────────────────────────────────────────────

def _sentiment_table(analysis: dict) -> Table:
    tbl = Table(
        "Coin", "Score", "Signal", "Confidence", "Source", "Articles", "Reasoning",
        box=box.SIMPLE_HEAD,
        title="[bold]Latest Sentiment Analysis[/bold]  [dim](Claude AI rating 1–10)[/dim]",
        show_edge=False,
        padding=(0, 1),
    )

    if not analysis:
        tbl.add_row("[dim]Waiting for first analysis cycle…[/dim]", "", "", "", "", "", "")
        return tbl

    for sym, data in analysis.items():
        score = data["score"]
        val   = data.get("validation", {})
        conf  = val.get("confidence", "—")
        badge = val.get("badge", "")

        if score >= 7:
            signal    = "[green]BULLISH[/green]"
            score_str = f"[bold green]{score:.1f}[/bold green]"
        elif score >= 4:
            signal    = "[yellow]NEUTRAL[/yellow]"
            score_str = f"[yellow]{score:.1f}[/yellow]"
        else:
            signal    = "[red]BEARISH[/red]"
            score_str = f"[red]{score:.1f}[/red]"

        conf_color = {"high": "green", "medium": "yellow", "low": "red"}.get(conf, "dim")
        conf_str   = f"[{conf_color}]{badge} {conf.upper()}[/{conf_color}]"

        source  = data.get("source", "—")
        n_arts  = data.get("articles_count", "—")
        reason  = data.get("reasoning", "—")
        # Truncate reasoning to fit terminal
        reason_short = reason[:70] + ("…" if len(reason) > 70 else "")

        # Show any validation warnings inline
        warn_lines = val.get("warnings", [])
        if warn_lines:
            reason_short += f"  [yellow][⚠ {warn_lines[0]}][/yellow]"

        tbl.add_row(
            f"[bold]{sym}[/bold]",
            score_str,
            signal,
            conf_str,
            f"[dim]{source}[/dim]",
            str(n_arts),
            f"[dim]{reason_short}[/dim]",
        )
    return tbl


# ── Recent trades ─────────────────────────────────────────────────────────────

def _trades_table(trades: list) -> Table:
    tbl = Table(
        "Time (UTC)", "Action", "Coin", "Price $", "Amount $", "P&L", "Reason",
        box=box.SIMPLE_HEAD,
        title="[bold]Recent Trades[/bold]",
        show_edge=False,
        padding=(0, 1),
    )

    if not trades:
        tbl.add_row("[dim]No trades yet[/dim]", "", "", "", "", "", "")
        return tbl

    for t in trades:
        ts     = (t.get("timestamp") or "")[:16].replace("T", " ")
        action = t["action"]
        color  = "green" if action == "BUY" else "yellow"

        pnl_str = "—"
        if "pnl_usd" in t:
            v  = t["pnl_usd"]
            pc = t.get("pnl_pct", 0)
            tc = "green" if v >= 0 else "red"
            s  = "+" if v >= 0 else ""
            pnl_str = f"[{tc}]{s}${v:,.2f} ({s}{pc:.1f}%)[/{tc}]"

        reason = t.get("reason", "—").replace("_", " ")

        tbl.add_row(
            f"[dim]{ts}[/dim]",
            f"[{color}]{action}[/{color}]",
            f"[bold]{t['symbol']}[/bold]",
            f"${t['price']:,.4f}",
            f"${t['total_usd']:,.2f}",
            pnl_str,
            f"[dim]{reason}[/dim]",
        )
    return tbl


# ── Debug log panel ───────────────────────────────────────────────────────────

def _log_panel(log_buffer) -> Panel:
    """Show the last N log lines, colour-coded by level."""
    entries = log_buffer.get_recent(14)

    if not entries:
        content = "[dim]No log entries yet…[/dim]"
    else:
        lines = []
        for e in entries:
            style   = e["style"]
            level   = e["level"][:4]          # DEBUG→DEBU, WARNING→WARN etc.
            name    = e["name"][:18]
            message = e["message"]

            # Truncate very long messages
            if len(message) > 100:
                message = message[:97] + "…"

            lines.append(
                f"[dim]{e['ts']}[/dim]  "
                f"[{style}]{level:<4}[/{style}]  "
                f"[dim cyan]{name:<18}[/dim cyan]  "
                f"[{style}]{message}[/{style}]"
            )
        content = "\n".join(lines)

    return Panel(
        content,
        title="[bold]Debug Log[/bold]  [dim](all API calls, decisions, and errors appear here)[/dim]",
        border_style="dim",
        padding=(0, 1),
    )


# ── Main render function ──────────────────────────────────────────────────────

def make_renderable(
    portfolio,
    prices: dict,
    analysis: dict,
    validation: dict,
    log_buffer,
    next_check: str = "—",
    status: str    = "Idle",
):
    """
    Build and return the complete dashboard as a Rich renderable.

    Pass the result to live.update() in main.py each refresh cycle.

    Args:
        portfolio:  PaperPortfolio instance
        prices:     {symbol: float}  latest prices
        analysis:   engine.last_analysis dict
        validation: data_validator.validate_prices() result
        log_buffer: LogBuffer instance
        next_check: time string for next analysis cycle
        status:     short status string
    """
    portfolio_panel  = _portfolio_panel(portfolio, prices)
    positions_table  = _positions_table(portfolio, prices)

    return Group(
        _header(next_check, status),
        _prices_panel(prices, validation),
        # Portfolio and positions side by side
        Columns(
            [portfolio_panel, positions_table],
            equal=False,
            expand=True,
            padding=(0, 0),
        ),
        _sentiment_table(analysis),
        _trades_table(portfolio.get_recent_trades(7)),
        _log_panel(log_buffer),
        Text("  Ctrl+C to stop", style="dim"),
    )
