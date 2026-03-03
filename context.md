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
- `trading_engine.py` — orchestrates cycles; run_shadow_cycle() for shadow portfolios (no extra API calls)
- `bot_controller.py` — thread-safe lifecycle; shadow portfolios; get_shadow_comparison()
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

### DONE
- RSI + MACD + Bollinger Bands technical indicators
- Combined sentiment + tech score threshold for buys
- AI learner with last 25 trades — auto-adjusts strategy every 5 cycles
- Dashboard redesign (API health bar, emergency stop, trade filters, reasoning expand)
- AWS hosted — live at http://3.219.170.4:8000
- News stack: RSS feeds + Fear & Greed index (CryptoPanic dead, Reddit 403)
- All timestamps now local time — fmtTs() converts UTC ISO to browser local time (2026-03-03)
- Strategy A/B/C/D comparison — all 4 risk profiles run as shadow portfolios simultaneously (2026-03-03)
  - paper_portfolio.py: custom file paths; trading_engine.py: run_shadow_cycle() reuses analysis
  - bot_controller.py: shadow engines init on start, run after each main cycle
  - Dashboard: comparison table sorted by P&L (between Open Positions and Sentiment sections)

### IN PROGRESS / NEXT
- Learning self-redeploy — shadow data now available; next: learner writes strategy_overrides.json
  that trading_engine.py reads each cycle (safe, partitioned, reversible code-level evolution)
- Daily / On-demand Status Report — full P&L breakdown, win rate, what's been learned
- MACD / charting graphics — mini chart in open positions row
- Separate platform that utilizes the latest successful version of the engine but sets trading at dynamically scalable/lower amount for when I want to turn it on with $25 of real money. Should be similar to normal app infrastructure where the paper trading is like a sandbox but the real money is prod.

### Noticed bugs
- "Click a row to see the AI reasoning" doesn't seem functional
- API errors on frontend need to have overflow/ability to open error for view, current ex. is ETH api error 529 type "overloading" -- would like to see in open modal or other view what the full error is and what bot recommends is needed to fix along with what was already done to try and address
- IDLE / NEXT timing still in UTC
- text-xs text-slate-600 truncate mb-2 for a.reasoning should be able to enlarge or expand in such a way that I can read the entire reasoning section

### BACKLOG
- Reddit API deprioritized (EC2 IPs blocked with 403)
- Claude API cost efficiency review (using Haiku, already cheapest)
- Dynamic self-editing logic beyond parameter adjustments
- Watchlist coins aren't all showing technical indicator
- What the bot is costing to run between Claude, AWS hosting, etc. -- added to dashboard

### QUESTIONS
- Can bot change Risk level on its own?
- OPEN POSITIONS seems limited to 4 max — want max 10
