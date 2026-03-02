"""
FastAPI web server for the Crypto Sentiment Bot dashboard.

Endpoints:
    GET  /            – Serves the HTML dashboard
    POST /bot/start   – Starts the trading bot
    POST /bot/stop    – Stops the trading bot
    GET  /bot/status  – Returns current state as JSON
    WS   /ws          – WebSocket: pushes state to all connected browsers every 2s

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

from bot_controller import BotController

# ── Logging ───────────────────────────────────────────────────────────────────
# Keep uvicorn access logs separate so they don't flood the bot's log buffer
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)-20s  %(message)s",
)
# Quieten uvicorn's access log – it's noisy and not useful in the bot buffer
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

log = logging.getLogger("web_server")

# ── Core objects ──────────────────────────────────────────────────────────────
bot       = BotController()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


# ── WebSocket connection manager ──────────────────────────────────────────────

class ConnectionManager:
    """Manages all live WebSocket connections to the dashboard."""

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
    """Push the full bot state to every connected browser every 2 seconds."""
    while True:
        try:
            state = bot.get_state()
            await manager.broadcast(state)
        except Exception as e:
            log.error(f"Broadcast error: {e}")
        await asyncio.sleep(2)


# ── FastAPI app with lifespan ─────────────────────────────────────────────────

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


# ── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        # Send the current state immediately on connect so the page isn't blank
        await ws.send_text(json.dumps(bot.get_state(), default=str))
        # Keep the connection open; broadcast_loop handles pushing updates
        while True:
            await ws.receive_text()   # wait for keep-alive pings from client
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception as e:
        log.debug(f"WebSocket closed: {e}")
        manager.disconnect(ws)
