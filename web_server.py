from __future__ import annotations
"""
FastAPI web server for the Crypto Sentiment Bot dashboard.

Endpoints:
    GET  /                     – Serves the HTML dashboard
    POST /bot/start            – Starts the trading bot
    POST /bot/stop             – Stops the trading bot
    GET  /bot/status           – Returns current state as JSON
    POST /bot/risk             – Change risk level  {"level": "high"}
    POST /bot/coins/add        – Add a coin         {"symbol": "PEPE", "name": "Pepe"}
    DELETE /bot/coins/{symbol} – Remove a coin
    GET  /coins/trending       – Top trending coins from CoinGecko
    GET  /coins/search         – Search coins by name/symbol  ?q=pepe
    POST /bot/learn            – Trigger AI learning cycle now  {"auto_apply": false}
    GET  /bot/learning         – Full learning history
    POST /bot/cycle/run        – Trigger an immediate analysis cycle (manual refresh)
    POST /bot/emergency_stop   – Emergency stop: sell all positions + halt bot
    POST /bot/emergency_clear  – Clear emergency mode so bot can restart
    GET  /bot/api_status       – Check connectivity to all external APIs
    GET  /bot/highlights       – Trade highlights (big winners and losers)
    WS   /ws                   – WebSocket: pushes state to all connected browsers every 2s

Run locally:
    uvicorn web_server:app --host 0.0.0.0 --port 8000 --reload

On AWS (systemd starts this automatically after deployment):
    uvicorn web_server:app --host 0.0.0.0 --port 8000
"""
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from bot_controller import BotController

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)-20s  %(message)s",
)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
log = logging.getLogger("web_server")

# ── Core objects ──────────────────────────────────────────────────────────────
bot       = BotController()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


# ── Request models ────────────────────────────────────────────────────────────

class RiskRequest(BaseModel):
    level: str

class AddCoinRequest(BaseModel):
    symbol:       str
    name:         str = ""
    coingecko_id: str = ""

class LearnRequest(BaseModel):
    auto_apply: bool = False

class AlwaysOnRequest(BaseModel):
    enabled: bool


# ── WebSocket connection manager ──────────────────────────────────────────────

class ConnectionManager:
    def __init__(self):
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.append(ws)
        log.info(f"Dashboard connected  ({len(self._connections)} live)")

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self._connections:
            self._connections.remove(ws)
        log.info(f"Dashboard disconnected  ({len(self._connections)} live)")

    async def broadcast(self, payload: dict) -> None:
        if not self._connections:
            return
        msg  = json.dumps(payload, default=str)
        dead: list[WebSocket] = []
        for ws in self._connections:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            if ws in self._connections:
                self._connections.remove(ws)


manager = ConnectionManager()


# ── Background broadcast task ─────────────────────────────────────────────────

async def _broadcast_loop() -> None:
    while True:
        try:
            state = bot.get_state()
            await manager.broadcast(state)
        except Exception as e:
            log.error(f"Broadcast error: {e}")
        await asyncio.sleep(2)


# ── FastAPI app ───────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_broadcast_loop())
    log.info("Crypto Bot web server started")
    yield
    task.cancel()
    log.info("Web server shutting down")


app = FastAPI(title="Crypto Sentiment Bot", lifespan=lifespan)


# ── HTTP routes ───────────────────────────────────────────────────────────────

@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/bot/start")
async def start_bot():
    started = bot.start()
    return JSONResponse({
        "ok":      started,
        "message": "Bot started" if started else "Bot is already running",
        "status":  bot._status,
    })


@app.post("/bot/stop")
async def stop_bot():
    stopped = bot.stop()
    return JSONResponse({
        "ok":      stopped,
        "message": "Bot stopped" if stopped else "Bot is already stopped",
        "status":  bot._status,
    })


@app.get("/bot/status")
async def bot_status():
    return JSONResponse(bot.get_state())


@app.post("/bot/risk")
async def set_risk(req: RiskRequest):
    ok = bot.set_risk(req.level)
    return JSONResponse({
        "ok":      ok,
        "message": f"Risk level set to '{req.level}'" if ok else f"Unknown risk level '{req.level}'",
        "config":  bot.get_state()["config"],
    })


@app.post("/bot/coins/add")
async def add_coin(req: AddCoinRequest):
    result = bot.add_coin(
        symbol=req.symbol,
        name=req.name,
        coingecko_id=req.coingecko_id or None,
    )
    return JSONResponse(result)


@app.delete("/bot/coins/{symbol}")
async def remove_coin(symbol: str):
    result = bot.remove_coin(symbol)
    return JSONResponse(result)


@app.get("/coins/trending")
async def trending_coins():
    coins = bot.get_trending_coins()
    return JSONResponse({"coins": coins})


@app.post("/bot/learn")
async def trigger_learning(req: LearnRequest):
    if not bot.portfolio:
        return JSONResponse({
            "ok": False,
            "message": "Start the bot first so it can build a trade history to learn from",
        })
    result = bot.trigger_learning(auto_apply=req.auto_apply)
    return JSONResponse(result)


@app.get("/bot/learning")
async def get_learning():
    return JSONResponse({"insights": bot.learner.get_insights()})


@app.post("/bot/cycle/run")
async def run_cycle_now():
    result = bot.run_cycle_now()
    return JSONResponse(result)


@app.post("/bot/emergency_stop")
async def emergency_stop():
    result = bot.emergency_stop(reason="manual")
    return JSONResponse(result)


@app.post("/bot/emergency_clear")
async def emergency_clear():
    bot.clear_emergency()
    return JSONResponse({"ok": True, "message": "Emergency mode cleared"})


@app.post("/bot/always_on")
async def set_always_on(req: AlwaysOnRequest):
    result = bot.set_always_on(req.enabled)
    return JSONResponse(result)


@app.get("/bot/api_status")
async def api_status():
    return JSONResponse(bot.get_api_status())


@app.get("/bot/highlights")
async def highlights():
    return JSONResponse(bot.get_highlights())


@app.get("/coins/search")
async def search_coins(q: str = ""):
    if not q or len(q) < 2:
        return JSONResponse({"coins": []})
    try:
        import requests as _req
        r = _req.get(
            "https://api.coingecko.com/api/v3/search",
            params={"query": q},
            headers={"Accept": "application/json"},
            timeout=5,
        )
        r.raise_for_status()
        results = []
        for item in r.json().get("coins", [])[:6]:
            sym = item.get("symbol", "").upper()
            results.append({
                "symbol":          sym,
                "name":            item.get("name", ""),
                "coingecko_id":    item.get("id", ""),
                "market_cap_rank": item.get("market_cap_rank"),
                "thumb":           item.get("thumb", ""),
                "already_watching": sym in bot._coins,
            })
        return JSONResponse({"coins": results})
    except Exception as e:
        log.warning(f"Coin search error: {e}")
        return JSONResponse({"coins": []})


# ── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        await ws.send_text(json.dumps(bot.get_state(), default=str))
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception as e:
        log.debug(f"WebSocket closed: {e}")
        manager.disconnect(ws)
