"""
Scalper Dashboard Server — VWAP Stock Scalping Edition.
Serves real-time view of stock scalper state via WebSocket.
Reads config/paper_scalp.json + latest log + live Schwab quotes.
"""
import sys
import os
import json
import asyncio
from datetime import datetime, date
from pathlib import Path
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from loguru import logger

from data.broker.schwab_auth import get_schwab_client

BASE_DIR = Path(__file__).parent.parent
SCALP_FILE = BASE_DIR / "config" / "paper_scalp.json"
LOG_DIR = BASE_DIR / "logs"

app = FastAPI(title="VWAP Scalper Dashboard")
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")


# ── Schwab client (lazy init) ──
_schwab_client = None
def get_client():
    global _schwab_client
    if _schwab_client is None:
        try:
            _schwab_client = get_schwab_client()
            logger.info("Dashboard: Schwab client connected")
        except Exception as e:
            logger.warning(f"Dashboard: Schwab connect failed: {e}")
    return _schwab_client


# ── Connection manager ──
class ConnectionManager:
    def __init__(self):
        self.active = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)
        logger.info(f"Dashboard: Client connected ({len(self.active)} total)")

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)
        logger.info(f"Dashboard: Client disconnected ({len(self.active)} total)")

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


# ── State readers ──
def read_scalp_state():
    try:
        with open(SCALP_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return None


def read_market_context():
    """Fetch live SPY, VIX, etc. from Schwab."""
    client = get_client()
    if not client:
        return {}
    ctx = {}
    try:
        import httpx
        r = client.get_quote("SPY")
        if r.status_code == httpx.codes.OK:
            q = r.json().get("SPY", {}).get("quote", {})
            ctx["spy_price"] = q.get("lastPrice", 0)
            ctx["spy_change_pct"] = q.get("netPercentChangeInDouble", 0)
        r = client.get_quote("$VIX")
        if r.status_code == httpx.codes.OK:
            q = r.json().get("$VIX", {}).get("quote", {})
            ctx["vix"] = q.get("lastPrice", 0)
    except Exception as e:
        logger.debug(f"Market context error: {e}")
    return ctx


def fetch_stock_price(symbol):
    """Get live stock price."""
    client = get_client()
    if not client:
        return None
    try:
        import httpx
        r = client.get_quote(symbol)
        if r.status_code == httpx.codes.OK:
            q = r.json().get(symbol, {}).get("quote", {})
            return q.get("lastPrice", 0)
    except Exception:
        pass
    return None


def enrich_positions(positions):
    """Add live values and P&L to open stock positions."""
    enriched = []
    for pos in positions:
        symbol = pos.get("symbol", "")
        shares = pos.get("shares", 0)
        entry_price = pos.get("entry_price", 0)
        direction = pos.get("direction", "LONG")
        cost_basis = pos.get("cost_basis", 0)

        current = fetch_stock_price(symbol) if symbol else None

        if current is not None and current > 0:
            current_value = current * shares
            if direction == "LONG":
                pnl = (current - entry_price) * shares
            else:
                pnl = (entry_price - current) * shares
            # Include partial exit P&L
            partial_pnl = sum(pe.get("pnl", 0) for pe in pos.get("partial_exits", []))
            pnl += partial_pnl
            pnl_pct = pnl / cost_basis if cost_basis > 0 else 0
        else:
            current = entry_price
            current_value = entry_price * shares
            pnl = 0
            pnl_pct = 0

        # Hold time
        held_min = 0
        try:
            et = datetime.fromisoformat(pos.get("entry_time", ""))
            now = datetime.now()
            # Handle tz-aware entry_time vs naive now
            if et.tzinfo is not None and now.tzinfo is None:
                now = datetime.now(et.tzinfo)
            elif et.tzinfo is None and now.tzinfo is not None:
                et = et.replace(tzinfo=now.tzinfo)
            held_min = (now - et).total_seconds() / 60
        except Exception:
            pass

        # Distance from VWAP
        vwap = pos.get("vwap_at_entry", 0)
        vwap_dist_pct = ((current - vwap) / vwap * 100) if vwap > 0 else 0

        enriched.append({
            **pos,
            "current_price": round(current, 2) if current else 0,
            "current_value": round(current_value, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct * 100, 1),
            "held_minutes": round(held_min, 1),
            "vwap_distance_pct": round(vwap_dist_pct, 2),
        })
    return enriched


def get_latest_log_lines(n=25):
    """Tail the most recent log file."""
    try:
        today_log = LOG_DIR / f"trading_{date.today().isoformat()}.log"
        if not today_log.exists():
            logs = sorted(LOG_DIR.glob("trading_*.log"))
            if not logs:
                return []
            today_log = logs[-1]
        with open(today_log, "r", encoding="utf-8", errors="ignore") as f:
            lines = deque(f, maxlen=n)
        return [line.strip() for line in lines]
    except Exception:
        return []


def build_snapshot():
    """Assemble the full state snapshot for the frontend."""
    scalp = read_scalp_state()
    if not scalp:
        return {"error": "Could not read scalper state", "timestamp": datetime.now().isoformat()}

    positions_raw = [p for p in scalp.get("positions", []) if p.get("status") == "OPEN"]
    positions = enrich_positions(positions_raw)

    # Today's history
    today = date.today().isoformat()
    history = scalp.get("history", [])
    today_history = [h for h in history if h.get("entry_time", "")[:10] == today]
    today_history.sort(key=lambda x: x.get("exit_time", ""), reverse=True)

    # Today's stats
    daily = scalp.get("daily_stats", {}).get(today, {"trades": 0, "wins": 0, "losses": 0, "pnl": 0})
    total_pnl_open = sum(p["pnl"] for p in positions)

    # Cash & equity
    cash = scalp.get("cash", 0)
    equity = scalp.get("equity", 25000)
    deployed = sum(p.get("current_value", 0) for p in positions)

    market = read_market_context()
    log_lines = get_latest_log_lines(25)

    return {
        "timestamp": datetime.now().isoformat(),
        "summary": {
            "equity": round(equity, 2),
            "cash": round(cash, 2),
            "deployed": round(deployed, 2),
            "buying_power": round(equity * 4 - deployed, 2),
            "today_pnl": round(daily.get("pnl", 0), 2),
            "open_pnl": round(total_pnl_open, 2),
            "trades": daily.get("trades", 0),
            "wins": daily.get("wins", 0),
            "losses": daily.get("losses", 0),
            "open_count": len(positions),
            "win_rate": round(daily.get("wins", 0) / max(daily.get("trades", 1), 1) * 100, 0),
        },
        "positions": positions,
        "closed_today": today_history[:10],
        "market": market,
        "log": log_lines,
        "status": "LIVE" if positions or daily.get("trades", 0) > 0 else "IDLE",
    }


# ── Background broadcaster ──
async def broadcast_loop():
    """Push snapshot updates to all connected clients."""
    while True:
        try:
            if manager.active:
                snapshot = build_snapshot()
                await manager.broadcast(snapshot)
        except Exception as e:
            logger.error(f"Broadcast error: {e}")
        await asyncio.sleep(2)


@app.on_event("startup")
async def startup():
    asyncio.create_task(broadcast_loop())
    logger.info("Dashboard: Broadcast loop started")


# ── Routes ──
@app.get("/")
async def root():
    return FileResponse(str(Path(__file__).parent / "static" / "index.html"))


@app.get("/api/snapshot")
async def snapshot():
    return build_snapshot()


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        initial = build_snapshot()
        await ws.send_json(initial)
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)


if __name__ == "__main__":
    import uvicorn
    logger.info("=" * 60)
    logger.info("  VWAP SCALPER DASHBOARD")
    logger.info("=" * 60)
    logger.info("  Open: http://localhost:8888")
    logger.info("  Updates every 2 seconds via WebSocket")
    logger.info("=" * 60)
    uvicorn.run(app, host="127.0.0.1", port=8888, log_level="warning")
