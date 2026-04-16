"""
Small Cap Dashboard Server

Serves a real-time view of the small cap momentum trader via WebSocket.
Reads config/smallcap_portfolio.json + config/smallcap_candidates.json
+ live Schwab equity quotes for open positions.

Port: 8889  (scalper dashboard uses 8888, so they can run side by side)
"""

import sys
import os
import json
import asyncio
from datetime import datetime, date
from pathlib import Path
from collections import deque

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from loguru import logger

BASE_DIR   = Path(__file__).parent.parent.parent
PORTFOLIO  = BASE_DIR / "config" / "smallcap_portfolio.json"
CANDIDATES = BASE_DIR / "config" / "smallcap_candidates.json"
LOG_DIR    = BASE_DIR / "logs"

# Risk constants (mirrors config.py — read directly to avoid import side-effects)
MAX_DAILY_LOSS      = 500.0
MAX_RISK_PER_TRADE  = 250.0
MAX_CONSECUTIVE     = 3
STARTING_EQUITY     = 25_000.0

app = FastAPI(title="Small Cap Dashboard")
app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).parent / "static")),
    name="static",
)


# ── Schwab client (lazy, shared) ─────────────────────────────────────────────
_schwab_client = None

def _get_client():
    global _schwab_client
    if _schwab_client is None:
        try:
            from data.broker.schwab_auth import get_schwab_client
            _schwab_client = get_schwab_client()
        except Exception as e:
            logger.debug(f"Dashboard: Schwab connect: {e}")
    return _schwab_client


# ── WebSocket connection manager ─────────────────────────────────────────────
class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        self.active.discard(ws) if hasattr(self.active, "discard") else None
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, data: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

manager = ConnectionManager()


# ── Data readers ─────────────────────────────────────────────────────────────

def _read_portfolio() -> dict:
    try:
        with open(PORTFOLIO) as f:
            return json.load(f)
    except Exception:
        return {}


def _read_candidates() -> list:
    try:
        with open(CANDIDATES) as f:
            data = json.load(f)
        return data.get("candidates", [])
    except Exception:
        return []


def _live_quotes(symbols: list[str]) -> dict[str, dict]:
    """Fetch batch equity quotes from Schwab for open position symbols."""
    if not symbols:
        return {}
    client = _get_client()
    if not client:
        return {}
    try:
        import httpx
        r = client.get_quotes(symbols)
        if r.status_code != httpx.codes.OK:
            return {}
        raw = r.json()
        result = {}
        for sym in symbols:
            entry = raw.get(sym, {})
            q = entry.get("quote", entry)
            last = (
                q.get("lastPrice") or
                q.get("mark") or
                q.get("regularMarketLastPrice") or 0
            )
            result[sym] = {"last": last}
        return result
    except Exception as e:
        logger.debug(f"Dashboard quotes: {e}")
        return {}


def _market_context() -> dict:
    client = _get_client()
    if not client:
        return {}
    ctx = {}
    try:
        import httpx
        r = client.get_quote("SPY")
        if r.status_code == httpx.codes.OK:
            q = r.json().get("SPY", {}).get("quote", {})
            ctx["spy_price"]      = q.get("lastPrice", 0)
            ctx["spy_change_pct"] = q.get("netPercentChangeInDouble", 0)
        r = client.get_quote("$VIX")
        if r.status_code == httpx.codes.OK:
            q = r.json().get("$VIX", {}).get("quote", {})
            ctx["vix"] = q.get("lastPrice", 0)
    except Exception as e:
        logger.debug(f"Market context: {e}")
    return ctx


def _tail_log(n: int = 30) -> list[str]:
    try:
        today_log = LOG_DIR / f"trading_{date.today().isoformat()}.log"
        if not today_log.exists():
            logs = sorted(LOG_DIR.glob("trading_*.log"))
            if not logs:
                return []
            today_log = logs[-1]
        with open(today_log, encoding="utf-8", errors="ignore") as f:
            lines = deque(f, maxlen=n)
        return [l.strip() for l in lines]
    except Exception:
        return []


def _session_status(portfolio: dict, positions: list) -> str:
    """Infer session status from portfolio + time of day."""
    now_h = datetime.now().hour + datetime.now().minute / 60.0
    if not portfolio:
        return "OFFLINE"
    if portfolio.get("daily_halted"):
        return "HALTED"
    # 7:00–8:30 CT (6–7.5 ET offset) = pre-market
    if 6.0 <= now_h < 8.5:
        return "PRE-MARKET"
    if 8.5 <= now_h < 14.5:
        return "LIVE" if positions else "WATCHING"
    if now_h >= 14.5:
        return "CLOSED"
    return "IDLE"


# ── Snapshot builder ─────────────────────────────────────────────────────────

def build_snapshot() -> dict:
    portfolio  = _read_portfolio()
    candidates = _read_candidates()

    # ── Risk state ────────────────────────────────────────────────────────────
    daily_pnl        = portfolio.get("daily_pnl", 0.0)
    consecutive_loss = portfolio.get("consecutive_loss", 0)
    trades_today     = portfolio.get("trades_today", 0)
    daily_halted     = portfolio.get("daily_halted", False)
    raw_positions    = portfolio.get("positions", {})   # {sym: {shares, avg_price, entry}}
    closed_trades    = portfolio.get("closed_trades", [])

    # ── Live quotes for open positions ────────────────────────────────────────
    syms   = list(raw_positions.keys())
    quotes = _live_quotes(syms)

    open_pnl = 0.0
    positions_out = []
    for sym, pos in raw_positions.items():
        shares    = pos.get("shares", 0)
        avg_price = pos.get("avg_price", 0.0)
        entry     = pos.get("entry", avg_price)
        current   = quotes.get(sym, {}).get("last", 0.0) or avg_price
        pnl       = (current - avg_price) * shares
        pnl_pct   = (current - avg_price) / avg_price * 100 if avg_price else 0
        open_pnl += pnl
        positions_out.append({
            "symbol":      sym,
            "shares":      shares,
            "entry_price": round(entry, 4),
            "avg_price":   round(avg_price, 4),
            "current":     round(current, 4),
            "pnl":         round(pnl, 2),
            "pnl_pct":     round(pnl_pct, 2),
        })

    # ── Today's closed trades ─────────────────────────────────────────────────
    today = date.today().isoformat()
    closed_today = [t for t in closed_trades if t.get("time", "")[:10] == today]
    closed_today.sort(key=lambda t: t.get("time", ""), reverse=True)

    # ── Realized P&L today (from risk manager) ────────────────────────────────
    wins   = sum(1 for t in closed_today if t["pnl"] > 0)
    losses = sum(1 for t in closed_today if t["pnl"] < 0)

    # ── Summary block ────────────────────────────────────────────────────────
    limit_used_pct = abs(min(daily_pnl, 0)) / MAX_DAILY_LOSS * 100

    market = _market_context()
    log    = _tail_log(30)
    status = _session_status(portfolio, positions_out)

    return {
        "timestamp": datetime.now().isoformat(),
        "status":    status,
        "summary": {
            "daily_pnl":        round(daily_pnl, 2),
            "open_pnl":         round(open_pnl, 2),
            "total_pnl":        round(daily_pnl + open_pnl, 2),
            "trades_today":     trades_today,
            "wins":             wins,
            "losses":           losses,
            "open_count":       len(positions_out),
        },
        "risk": {
            "daily_pnl":        round(daily_pnl, 2),
            "daily_limit":      MAX_DAILY_LOSS,
            "limit_used_pct":   round(limit_used_pct, 1),
            "consecutive_loss": consecutive_loss,
            "max_consecutive":  MAX_CONSECUTIVE,
            "halted":           daily_halted,
            "max_risk_trade":   MAX_RISK_PER_TRADE,
        },
        "positions":    positions_out,
        "candidates":   candidates[:5],
        "closed_today": closed_today[:15],
        "market":       market,
        "log":          log,
    }


# ── Background broadcaster ───────────────────────────────────────────────────

async def _broadcast_loop():
    while True:
        try:
            if manager.active:
                snap = build_snapshot()
                await manager.broadcast(snap)
        except Exception as e:
            logger.debug(f"Broadcast: {e}")
        await asyncio.sleep(2)


@app.on_event("startup")
async def _startup():
    asyncio.create_task(_broadcast_loop())


# ── Routes ───────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return FileResponse(str(Path(__file__).parent / "static" / "index.html"))


@app.get("/api/snapshot")
async def snapshot():
    return build_snapshot()


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        await ws.send_json(build_snapshot())
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)


if __name__ == "__main__":
    import uvicorn
    print("=" * 60)
    print("  SMALL CAP DASHBOARD")
    print("=" * 60)
    print("  Open: http://localhost:8889")
    print("  Updates every 2 seconds via WebSocket")
    print("  Reads: config/smallcap_portfolio.json + Schwab quotes")
    print("=" * 60)
    uvicorn.run(app, host="127.0.0.1", port=8889, log_level="warning")
