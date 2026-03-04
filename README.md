# Crypto Sentiment Bot


> A paper-trading bot powered by Claude AI. Watches crypto prices, reads news, scores sentiment 1–10, and automatically simulates trades — all without touching real money.

---

## What it does

Every 30 minutes the bot runs a full analysis cycle:

```
Fetch prices  →  Validate (CoinGecko cross-check)  →  Check exits  →  Fetch news
     →  Claude AI scores sentiment 1–10  →  Validate score  →  Buy/skip
```

Results stream live to a **web dashboard** — start/stop the bot, switch risk levels, add meme coins, and let the AI review its own performance, all from the browser.

---

## Features

| Feature | Details |
|---|---|
| **Live prices** | Coinbase public API, updated every 60 s |
| **Dual-source validation** | Prices cross-checked against CoinGecko; >2% divergence flagged |
| **AI sentiment** | Claude Haiku scores each coin's news 1–10 in seconds |
| **4 risk profiles** | Low / Medium / High / Degen — switchable live from the dashboard |
| **Dynamic watchlist** | Add any coin (BTC, PEPE, WIF…) or one-click add from trending |
| **AI self-learning** | After every 5 cycles, Claude analyses trade history and suggests improvements |
| **Web dashboard** | FastAPI + WebSockets; dark terminal aesthetic; auto-reconnects |
| **AWS deploy** | One command provisions EC2, uploads code, starts as a systemd service |
| **Paper or live trading** | Simulated by default; flip one flag to place real Coinbase orders |

---

## Quick start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Add API keys to `.env`

```env
ANTHROPIC_API_KEY=sk-ant-...
CRYPTOPANIC_API_KEY=...        # optional — Reddit used as fallback if blank
```

| Key | Where to get it | Required? |
|---|---|---|
| Anthropic | [console.anthropic.com](https://console.anthropic.com/settings/keys) | **Yes** |
| CryptoPanic | [cryptopanic.com/developers/api](https://cryptopanic.com/developers/api/) — free tier | No (Reddit fallback) |

> Live prices use Coinbase's **public** API — no key needed for that.

**For real trading only** — add `cdp_api_key.json` to the project root (never commit it):

```json
{"name": "organizations/.../apiKeys/...", "privateKey": "-----BEGIN EC PRIVATE KEY-----\n..."}
```

Get this from [developer.coinbase.com](https://developer.coinbase.com) → API Keys → Create.
Scope it to your isolated portfolio with **Trade** permission only — no Withdrawal.

### 3. Run locally

**Terminal dashboard:**
```bash
python main.py
```

**Web dashboard** (recommended):
```bash
uvicorn web_server:app --host 0.0.0.0 --port 8000 --reload
```
Then open [http://localhost:8000](http://localhost:8000)

---

## Web dashboard

The dashboard updates every 2 seconds via WebSocket.

### Risk selector
Four profiles, switchable with one click — no restart needed:

| Profile | Buy signal | Take profit | Stop loss | Trade size | Best for |
|---|---|---|---|---|---|
| **Low** | Score ≥ 8/10 | +10% | −4% | $250 · max 2 | BTC, ETH — capital preservation |
| **Medium** | Score ≥ 7/10 | +20% | −6% | $500 · max 4 | Large-caps + established alts |
| **High** | Score ≥ 6/10 | +40% | −10% | $750 · max 6 | Mid-cap alts, momentum plays |
| **Degen** | Score ≥ 5/10 | +100% | −20% | $150 · max 10 | Meme coins, micro-caps |

> Crypto is volatile — the wider stops on High/Degen are intentional. A coin can drop 15% in a
> day and recover. Tight stops on meme coins get triggered constantly.

### Watchlist management
- **+ Add Coin** — type any ticker (PEPE, SHIB, BONK, WIF…). CoinGecko ID is auto-detected.
- **🔥 Trending** — pulls CoinGecko's real-time top-7 trending coins with one-click add.
- **✕** on any coin to remove it from the watchlist.

### AI strategy learner
After every 5 trading cycles the bot sends its trade history to Claude for review:

- Win rate, average P&L, and patterns across coins
- Parameter suggestions with reasoning ("raise threshold from 7 → 7.5 — most losses came from borderline 7.0–7.5 scores")
- **Run Analysis Now** — trigger manually any time
- **Auto-Apply** — applies Claude's suggestions within safety guardrails (max 20% parameter drift per cycle)

---

## Data validation

Before any trade, two independent checks run:

**1. Price cross-validation**
Coinbase prices are verified against CoinGecko. If they disagree by >2%, a `⚠` badge appears and a warning is logged.

**2. Sentiment sanity**
| Badge | Meaning |
|---|---|
| `✓ HIGH` | Enough articles, score varying normally |
| `⚠ MEDIUM` | Few articles or mild concern — trade allowed with warning |
| `⚠ LOW` | Zero articles found — **trade is blocked** |
| `⚠` score stuck | Same score returned 5 cycles in a row — possible hallucination |

---

## AWS deployment

One command provisions a free-tier EC2 instance and deploys the bot:

```bash
python deploy/deploy_aws.py
```

This script:
1. Finds the latest Amazon Linux 2023 AMI automatically
2. Creates a key pair (`deploy/crypto-bot-key.pem`) and security group
3. Launches a `t2.micro` instance (free tier eligible)
4. Uploads all bot code via SFTP (`.env` is **never** uploaded)
5. Installs dependencies and starts the bot as a `systemd` service

After deployment, SSH in to add your real API keys:

```bash
ssh -i deploy/crypto-bot-key.pem ec2-user@<public-ip>
nano /home/ec2-user/crypto-bot/.env
sudo systemctl restart crypto-bot
```

Then visit `http://<public-ip>:8000` for your live dashboard.

---

## Keeping your real Coinbase money safe

**Your existing Coinbase balance is completely safe.** Here's why, and what to do if you ever want to trade real money:

### Right now: zero risk to your funds

The bot currently has **no ability to touch your money**, for two reasons:

1. **Paper trading is on by default** (`PAPER_TRADING = True` in `config.py`). All buys and sells happen in software only — nothing is sent to Coinbase.

2. **The `cdp_api_key.json` file is not present.** Even if paper trading were disabled, the bot cannot connect to Coinbase without this file — it will refuse to start and log an error instead of placing any order.

### When you're ready for real trading

The recommended approach is **isolated funds with a scoped API key**:

**Option A — Coinbase separate portfolio (simplest)**
1. In your Coinbase account, create a new **Portfolio** (Settings → Portfolios → New Portfolio)
2. Transfer only the amount you're willing to risk into that portfolio (e.g. $500)
3. Create a new API key scoped to **that portfolio only**, with **Trade** permission but **no Withdrawal** permission
4. Paste those keys into `.env`

**Option B — Separate Coinbase account**
Create a fresh Coinbase account with a different email. Fund it with a small dedicated amount. This gives the cleanest separation.

**Option C — Coinbase Advanced Trade sandbox**
Coinbase offers a sandbox/testnet environment for the Advanced Trade API. You can run real API calls against fake money before committing real funds.

**Key rules regardless of which option:**
- API key should have **Trade** permission only — **never Withdrawal**
- Only fund the trading account with money you're comfortable losing entirely
- Start with the **Low** risk profile and small `TRADE_AMOUNT_USD`
- Run paper trading for at least a few weeks first to verify the strategy works

> This bot and its AI signal source are experimental. Crypto is highly volatile. Past paper
> performance does not predict real performance. Never risk money you can't afford to lose.

---

## API endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Web dashboard |
| `POST` | `/bot/start` | Start trading loop |
| `POST` | `/bot/stop` | Stop trading loop |
| `GET` | `/bot/status` | Full state as JSON |
| `POST` | `/bot/risk` | `{"level": "high"}` — change risk profile |
| `POST` | `/bot/coins/add` | `{"symbol": "PEPE", "name": "Pepe"}` — add coin |
| `DELETE` | `/bot/coins/{symbol}` | Remove coin from watchlist |
| `GET` | `/coins/trending` | Top-7 trending coins from CoinGecko |
| `POST` | `/bot/learn` | `{"auto_apply": false}` — trigger AI analysis |
| `GET` | `/bot/learning` | Full learning history |
| `WS` | `/ws` | State stream (2 s interval) |

---

## File overview

```
crypto-bot/
├── config.py               All settings, risk profiles, .env loader
├── main.py                 Terminal entry point (Rich Live dashboard)
├── web_server.py           FastAPI web server + WebSocket broadcast
├── bot_controller.py       Thread-safe bot lifecycle, risk/coin management
├── trading_engine.py       Core cycle: prices → news → sentiment → trade
├── paper_portfolio.py      Virtual portfolio (portfolio.json / trades.json)
├── live_portfolio.py       Real-money portfolio (live_positions.json / live_trades.json)
├── coinbase_trader.py      Coinbase CDP API wrapper — real order execution
├── strategy_learner.py     AI self-learning (learning.json)
├── coinbase_client.py      Live prices from Coinbase public API
├── news_client.py          CryptoPanic API + Reddit fallback
├── sentiment_analyzer.py   Claude AI sentiment scoring (1–10)
├── data_validator.py       Price cross-check + sentiment sanity
├── log_buffer.py           Thread-safe in-memory log ring buffer
├── dashboard.py            Rich Live terminal dashboard panels
├── templates/
│   └── index.html          Web dashboard (Tailwind + Alpine.js)
├── deploy/
│   └── deploy_aws.py       AWS EC2 provisioning + deployment script
└── requirements.txt
```

---

## Log files

| File | Contents |
|---|---|
| `bot.log` | All INFO / WARNING / ERROR messages with timestamps |
| `portfolio.json` | Current cash, open positions |
| `trades.json` | Last 200 completed trades (with sentiment score at entry) |
| `learning.json` | AI strategy review history |

---

## Requirements

- Python 3.12+
- Anthropic API key with available credits
- CryptoPanic key (optional) or Reddit access (no key needed)
