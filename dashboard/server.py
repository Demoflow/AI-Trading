"""
Scalper Dashboard Server
Serves real-time view of scalper state via WebSocket.
Reads config/paper_scalp.json + latest log + live Schwab quotes.
"""
import sys
import os
import json
import asyncio
import glob
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

app = FastAPI(title="Scalper Dashboard")
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


def fetch_option_value(symbol):
    """Get live mid price for an option symbol."""
    client = get_client()
    if not client:
        return None
    try:
        import httpx
        r = client.get_quote(symbol)
        if r.status_code == httpx.codes.OK:
            q = r.json().get(symbol, {}).get("quote", {})
            bid = q.get("bidPrice", 0)
            ask = q.get("askPrice", 0)
            if bid > 0 and ask > 0:
                return (bid + ask) / 2
            return q.get("lastPrice", 0)
    except Exception:
        pass
    return None


def enrich_positions(positions):
    """Add live values and P&L to open positions."""
    enriched = []
    for pos in positions:
        csym = pos.get("contract", "")
        qty = pos.get("qty", 0)
        entry_cost = pos.get("entry_cost", 0)
        structure = pos.get("structure", "LONG_OPTION")

        current_opt = fetch_option_value(csym) if csym else None

        if current_opt is not None:
            if structure in ("NAKED_PUT", "NAKED_CALL", "CREDIT_SPREAD", "STRADDLE", "STRANGLE", "IRON_CONDOR"):
                # Premium sells: profit = credit - buyback cost
                credit = pos.get("credit_received", 0)
                buyback = current_opt * qty * 100
                pnl = credit - buyback
                pnl_pct = pnl / credit if credit > 0 else 0
            else:
                # Long options: profit = current - entry
                current_val = current_opt * qty * 100
                pnl = current_val - entry_cost
                pnl_pct = pnl / entry_cost if entry_cost > 0 else 0
        else:
            pnl = 0
            pnl_pct = 0

        # Calculate hold time
        held_min = 0
        try:
            et = datetime.fromisoformat(pos.get("entry_time", ""))
            held_min = (datetime.now() - et).total_seconds() / 60
        except Exception:
            pass

        enriched.append({
            **pos,
            "current_price": round(current_opt, 2) if current_opt else 0,
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct * 100, 1),
            "held_minutes": round(held_min, 1),
        })
    return enriched


def get_latest_log_lines(n=20):
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
    deployed = sum(p.get("entry_cost", 0) for p in positions_raw)

    market = read_market_context()
    log_lines = get_latest_log_lines(25)

    return {
        "timestamp": datetime.now().isoformat(),
        "summary": {
            "equity": round(equity, 2),
            "cash": round(cash, 2),
            "deployed": round(deployed, 2),
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
        await asyncio.sleep(2)  # Push every 2 seconds


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
        # Send initial snapshot immediately
        initial = build_snapshot()
        await ws.send_json(initial)
        while True:
            await ws.receive_text()  # Keep alive
    except WebSocketDisconnect:
        manager.disconnect(ws)


if __name__ == "__main__":
    import uvicorn
    print("=" * 60)
    print("  SCALPER DASHBOARD")
    print("=" * 60)
    print("  Open: http://localhost:8888")
    print("  Updates every 2 seconds via WebSocket")
    print("  Reads: config/paper_scalp.json + live Schwab quotes")
    print("=" * 60)
    uvicorn.run(app, host="127.0.0.1", port=8888, log_level="warning")
