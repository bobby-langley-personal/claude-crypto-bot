# Crypto Trading Bot — Project Context

## What This Is
AI-powered crypto trading bot using Coinbase Advanced Trade API.
Goal: grow $1,000 toward $10,000 through automated sentiment + technical analysis trading.

## Current Stack (as-built)
- `config.py` — settings, risk profiles (low/medium/high/degen), loads .env
- `coinbase_client.py` — Coinbase public price API
- `news_client.py` — RSS feeds (CoinTelegraph, Decrypt, Bitcoinist) + Fear & Greed index
- `sentiment_analyzer.py` — Claude Haiku scores news 1–10
- `technical_indicators.py` — RSI, MACD, Bollinger Bands from CoinGecko
- `trading_engine.py` — orchestrates cycles, applies strategy
- `bot_controller.py` — thread-safe lifecycle management
- `web_server.py` — FastAPI dashboard (15 endpoints + WebSocket)
- `strategy_learner.py` — AI self-learning every 5 cycles
- `data_validator.py` — cross-checks prices (Coinbase vs CoinGecko)
- `templates/index.html` — web dashboard
- `deploy/deploy_aws.py` — one-command AWS EC2 deploy

## Live Deployment
- Dashboard: http://3.219.170.4:8000
- SSH: `ssh -i deploy/crypto-bot-key.pem ec2-user@3.219.170.4`
- Service logs: `sudo journalctl -u crypto-bot -f`

## API Keys
- `.env` — never commit (Coinbase, Anthropic, CryptoPanic keys)
- `cdp_api_key.json` — never commit (real-money trading, only needed if PAPER_TRADING=False)

---

## Features / Issues Tracker

### ✅ DONE
- RSI + MACD + Bollinger Bands technical indicators (confirmation layer)
- Combined sentiment + tech score threshold for buys
- AI learner with last 25 trades — auto-adjusts strategy every 5 cycles
- Dashboard redesign (API health bar, emergency stop, trade filters, reasoning expand)
- Trade history with count and expandable "why it bought/sold" reasoning
- Autocomplete watchlist search (CoinGecko powered)
- Any coin tradeable if on watchlist (no hard limit on number)
- API health bar — shows green/red for each service
- Validate prices are real (Coinbase cross-check vs CoinGecko)
- Emergency stop — sells all positions and halts bot
- Trade highlights (winners/losers filter)
- Manual refresh button (triggers immediate analysis cycle)
- **AWS hosted** — live at http://3.219.170.4:8000
- Sentiment reasoning is expandable per trade
- Technical signals renamed: "▲ Bullish / → Neutral / ▼ Bearish" (was confusing BUY/HOLD/SELL buttons)
- TP/SL acronyms removed — now "Profit Target" / "Stop Loss" everywhere
- Watchlist coin limit removed — now unlimited
- Positions card clarified — "X of N max" with tooltip
- Footer bar has hover tooltips on every stat
- AI Learner section explains schedule and what it does
- "How does this bot work?" collapsible explainer added to dashboard
- News stack fixed — CryptoPanic (dead 404) and Reddit (403, policy change) replaced with RSS feeds + Fear & Greed index
- "0 price points" error fixed — CoinGecko `interval=hourly` moved to Enterprise, free tier now used correctly
- Desktop text too small — bumped base font size + max-width container

### 🔄 IN PROGRESS / NEXT
- **Multi-risk A/B testing** — run low/medium/high strategies in parallel, compare P&L
  - Option A: split portfolio (33% each risk level, separate positions)
  - Option B: simulation-only comparison shown on dashboard
  - Deferred: requires significant engine changes
- **Reddit API** — requires OAuth since June 2023; RSS feeds are better quality anyway
  (Reddit blocks EC2 IPs with 403 even with correct user-agent)
- **Learning self-redeploy** — bot already auto-adjusts parameters every 5 cycles;
  true code-level redeployment would be risky, parameters adjust within safety bounds

### 📋 BACKLOG (lower priority)
- Research other common finance bot platforms for comparison/inspiration
- Claude API cost efficiency review (currently uses Haiku which is cheapest)
- Watchlist: does adding a coin automatically trade it? (Yes — if bot is running and score meets threshold)
- A/B/C strategy comparison view (show what each risk level would have earned historically)
- Dynamic self-editing logic beyond parameter adjustments (complex, risk of instability)
