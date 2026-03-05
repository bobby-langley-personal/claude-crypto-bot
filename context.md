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

### AWS Deployment (Legacy)
- Dashboard: http://3.219.170.4:8000
- SSH: `ssh -i deploy/crypto-bot-key.pem ec2-user@3.219.170.4`
- Service logs: `sudo journalctl -u crypto-bot -f`

### DigitalOcean Deployment (Current)

#### Droplet Details
- **Provider:** DigitalOcean
- **Droplet Name:** crypto-tool
- **IP Address:** 167.71.253.88
- **OS:** Ubuntu 24.04 LTS
- **Size:** 1GB RAM, 1 vCPU ($6/month)
- **Region:** New York

#### Running Services
Two services run simultaneously on this server:

| Service | Port | Purpose |
|---------|------|---------|
| uvicorn (bot dashboard) | 8001 | FastAPI web server serving the trading bot UI |
| webhook listener (Flask) | 9000 | Receives GitHub push webhooks and triggers deploys |

#### Firewall Rules (DigitalOcean Firewall)
| Type | Protocol | Port | Source |
|------|----------|------|--------|
| SSH | TCP | 22 | All IPv4, All IPv6 |
| Custom | TCP | 8001 | All IPv4, All IPv6 |
| Custom | TCP | 9000 | All IPv4, All IPv6 |

#### File Structure on Server
```
/root/
├── claude-crypto-bot/          ← main app repo (cloned from GitHub)
│   ├── .env                    ← API keys (never in git)
│   ├── cdp_api_key.json        ← Coinbase API key (never in git)
│   ├── requirements.txt
│   ├── web_server.py
│   ├── bot_controller.py
│   ├── trading_engine.py
│   ├── sentiment_analyzer.py
│   ├── strategy_learner.py
│   ├── templates/
│   │   └── index.html
│   ├── portfolio.json          ← paper trading state
│   ├── trades.json             ← trade history
│   ├── learning.json           ← AI learning entries
│   └── costs.json              ← API cost tracking
├── deploy.sh                   ← deployment script
├── deploy.log                  ← deployment log
└── webhook.py                  ← GitHub webhook listener
```

#### Environment Variables (.env)
```
ANTHROPIC_API_KEY=
CRYPTOPANIC_API_KEY=
PAPER_TRADING=True
GITHUB_TOKEN=
GITHUB_REPO=bobby-langley-personal/claude-crypto-bot
```

#### Systemd Services
Two services managed by systemd:

**cryptobot.service** — runs the trading bot (enhanced for stability)
```ini
[Unit]
Description=Crypto Bot
After=network.target
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
WorkingDirectory=/root/claude-crypto-bot
ExecStart=/usr/local/bin/uvicorn web_server:app --host 0.0.0.0 --port 8001
Restart=always
RestartSec=10
User=root
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

**IMPORTANT**: Service updated with auto-restart policies, start limiting to prevent boot loops, and proper logging.

**webhook.service** — listens for GitHub pushes
```ini
[Unit]
Description=GitHub Webhook Listener
After=network.target

[Service]
ExecStart=/usr/bin/python3 /root/webhook.py
Restart=always
User=root
Environment=WEBHOOK_SECRET=mysecretkey123

[Install]
WantedBy=multi-user.target
```

#### Deploy Script (/root/deploy.sh)
Triggered automatically by webhook on every push to main:
```bash
#!/bin/bash
echo "=== Deploy started at $(date) ===" >> /root/deploy.log
cd /root/claude-crypto-bot
git pull origin main >> /root/deploy.log 2>&1
pip3 install -r requirements.txt --break-system-packages --ignore-installed >> /root/deploy.log 2>&1
systemctl restart cryptobot
echo "=== Deploy complete at $(date) ===" >> /root/deploy.log
systemctl status cryptobot >> /root/deploy.log
```

**IMPORTANT**: Updated deploy script now uses systemctl instead of manual process management to prevent conflicts and zombie processes.

#### Webhook Configuration
- **Payload URL:** http://167.71.253.88:9000/deploy
- **Content Type:** application/json
- **Secret:** mysecretkey123
- **Trigger:** Push to main branch only
- **SSL Verification:** Disabled (no SSL cert on server)

#### Auto-Deploy Pipeline
```
PR merged to main
      ↓
GitHub fires webhook → http://167.71.253.88:9000/deploy
      ↓
webhook.py receives POST, verifies ref is main
      ↓
Calls /root/deploy.sh
      ↓
deploy.sh: git pull → pkill uvicorn → restart uvicorn
      ↓
Bot live with new code at http://167.71.253.88:8001
      ↓
Logged to /root/deploy.log
```

#### Useful Server Commands

**Check what's running:**
```bash
ps aux | grep uvicorn
systemctl status webhook
systemctl status cryptobot
```

**View live logs:**
```bash
tail -f /root/deploy.log
journalctl -u webhook -f
journalctl -u cryptobot -f
```

**Full restart from scratch:**
```bash
pkill -f uvicorn
pkill -f screen
sleep 2
cd /root/claude-crypto-bot
git fetch origin
git checkout main
git reset --hard origin/main
pip3 install -r requirements.txt --break-system-packages --ignore-installed
screen -S bot
uvicorn web_server:app --host 0.0.0.0 --port 8001
# Detach: Ctrl+A then D
```

**Check current deployed commit:**
```bash
cd /root/claude-crypto-bot && git log --oneline -5
```

**View deploy history:**
```bash
cat /root/deploy.log
```

**Manually trigger deploy:**
```bash
/root/deploy.sh
```

#### Python Environment
- Python 3.12
- pip packages installed globally with --break-system-packages
- No virtual environment (intentional for simplicity)
- Key packages: uvicorn, fastapi, anthropic, coinbase-advanced-py,
  python-dotenv, pandas, pandas-ta, requests, schedule, rich,
  flask, websockets

#### App Stability and Self-Healing (March 2026)

**Health Monitoring**
- Enhanced `/health` endpoint with bot status, uptime, and version info
- Self-healing script (`/root/healthcheck.sh`) runs every 5 minutes via cron
- Automatic restart if health check fails, with success verification
- Dashboard shows uptime in header and git commit hash in footer

**Process Management Improvements**  
- Fixed systemd vs screen session conflicts
- Deploy script now uses `systemctl restart cryptobot` instead of manual process killing
- Enhanced `cryptobot.service` with auto-restart policies and start limiting
- Proper logging to systemd journal for better troubleshooting

**Deployment Verification**
- Deploy log now includes `systemctl status` output for verification
- Git commit hash displayed in dashboard footer to verify deployed version
- Health status link in footer for quick system check access

**Crontab Entry (needs manual setup on server):**
```bash
# Add this to root crontab with: crontab -e
*/5 * * * * /bin/bash /root/healthcheck.sh
```

#### Known Issues / Quirks
- typing-extensions conflict with system packages — always use
  --ignore-installed when pip installing
- Bot runs on port 8001 (not 8000) because 8000 was already 
  bound during initial setup
- webhook.py has no HMAC verification currently — security 
  improvement needed before going to live trading
- Server does not have SSL certificate — all traffic is HTTP
  Dashboard URL is http not https

#### Important Rules for Claude Code
- NEVER commit .env or cdp_api_key.json
- NEVER change the port from 8001 without updating firewall rules
- NEVER modify /root/deploy.sh or /root/webhook.py directly —
  these are outside the repo. Request manual update if needed.
- ALWAYS check git log after deploy to confirm new code is live
- If adding new pip dependencies, add to requirements.txt so
  deploy.sh picks them up automatically
- The bot is in PAPER_TRADING=True mode — never change this
  without explicit user instruction

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
