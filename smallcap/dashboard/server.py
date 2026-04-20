"""
Small Cap Dashboard Server v2
Real-time view of the dual-strategy small cap trader (Ross + Dux).

Data sources:
  config/smallcap_portfolio.json  — Ross Cameron strategy state
  config/dux_portfolio.json       — Steven Dux strategy state
  config/smallcap_candidates.json — pre-market gap candidates
  logs/trading_YYYY-MM-DD.log     — live log tail + market character + catalysts
  Schwab API                      — live quotes for open positions

Port: 8889  (scalper dashboard uses 8888)
"""

import sys
import os
import re
import json
import asyncio
from datetime import datetime, date
from pathlib import Path
from collections import deque

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv
load_dotenv()

import secrets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from loguru import logger

BASE_DIR      = Path(__file__).parent.parent.parent
PORTFOLIO     = BASE_DIR / "config" / "smallcap_portfolio.json"
DUX_PORTFOLIO = BASE_DIR / "config" / "dux_portfolio.json"
CANDIDATES    = BASE_DIR / "config" / "smallcap_candidates.json"
LOG_DIR       = BASE_DIR / "logs"

MAX_DAILY_LOSS     = 500.0
DUX_DAILY_LOSS     = 750.0
MAX_RISK_PER_TRADE = 250.0
MAX_CONSECUTIVE    = 3

app = FastAPI(title="Small Cap Dashboard")
app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).parent / "static")),
    name="static",
)

# ── HTTP Basic Auth ──────────────────────────────────────────────────────────
# Set DASHBOARD_PASSWORD in your .env file to protect the dashboard.
# Username is always "admin". If DASHBOARD_PASSWORD is not set, auth is disabled
# (backward compatible for local development).
_DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")
_security = HTTPBasic()


def _check_auth(credentials: HTTPBasicCredentials = Depends(_security)):
    """Verify HTTP Basic credentials against the env-configured password."""
    correct_user = secrets.compare_digest(credentials.username, "admin")
    correct_pass = secrets.compare_digest(credentials.password, _DASHBOARD_PASSWORD)
    if not (correct_user and correct_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials


# Build a dependency list: if password is set, require auth; otherwise no-op.
_auth_deps = [Depends(_check_auth)] if _DASHBOARD_PASSWORD else []


# ── Schwab client (lazy, shared) ─────────────────────────────────────────────
_schwab_client = None

def _get_client():
    global _schwab_client
    if _schwab_client is None:
        try:
            from data.broker.schwab_auth import get_schwab_client
            _schwab_client = get_schwab_client()
        except Exception as e:
            logger.debug(f"Dashboard Schwab: {e}")
    return _schwab_client


# ── WebSocket manager ─────────────────────────────────────────────────────────
class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
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


# ── File readers ──────────────────────────────────────────────────────────────

def _read_json(path: Path) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _read_candidates() -> list:
    try:
        with open(CANDIDATES) as f:
            data = json.load(f)
        return sorted(
            data.get("candidates", []),
            key=lambda c: c.get("gap_pct", 0),
            reverse=True,
        )
    except Exception:
        return []


# ── Live Schwab quotes ────────────────────────────────────────────────────────

def _live_quotes(symbols: list) -> dict:
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
            q = raw.get(sym, {}).get("quote", {})
            last = q.get("lastPrice") or q.get("mark") or 0
            bid  = q.get("bidPrice", 0)
            ask  = q.get("askPrice", 0)
            result[sym] = {"last": float(last), "bid": float(bid), "ask": float(ask)}
        return result
    except Exception as e:
        logger.debug(f"Dashboard quotes: {e}")
        return {}


# ── Market context from Schwab ────────────────────────────────────────────────
_market_cache: dict = {}
_market_last_fetch: float = 0.0

def _market_context() -> dict:
    global _market_cache, _market_last_fetch
    import time
    # Refresh every 10 seconds
    if time.monotonic() - _market_last_fetch < 10 and _market_cache:
        return _market_cache
    client = _get_client()
    if not client:
        return _market_cache
    ctx = dict(_market_cache)
    try:
        import httpx
        r = client.get_quotes(["SPY", "$VIX"])
        if r.status_code == httpx.codes.OK:
            raw = r.json()
            spy = raw.get("SPY", {}).get("quote", {})
            vix = raw.get("$VIX", {}).get("quote", {})
            ctx["spy_price"]      = round(float(spy.get("lastPrice", 0)), 2)
            ctx["spy_change_pct"] = round(float(spy.get("netPercentChangeInDouble", 0)), 2)
            ctx["vix"]            = round(float(vix.get("lastPrice", 0)), 2)
    except Exception as e:
        logger.debug(f"Market context: {e}")
    _market_cache = ctx
    _market_last_fetch = time.monotonic()
    return ctx


# ── Log parsing ───────────────────────────────────────────────────────────────

def _today_log() -> Path | None:
    p = LOG_DIR / f"trading_{date.today().isoformat()}.log"
    if p.exists():
        return p
    logs = sorted(LOG_DIR.glob("trading_*.log"))
    return logs[-1] if logs else None


def _tail_log(n: int = 35) -> list:
    lp = _today_log()
    if not lp:
        return []
    try:
        with open(lp, encoding="utf-8", errors="ignore") as f:
            return [line.strip() for line in deque(f, maxlen=n)]
    except Exception:
        return []


# Regex patterns for log parsing
_RE_REGIME  = re.compile(r"Market character: \[(\w+)\] OFE[≥>=]+(\d+)\s*\|.*?\|(.+?)$", re.IGNORECASE)
_RE_CATALYST = re.compile(
    r"LLM catalyst \[(\w+)\]: score [+\-\d]+[→>]+([+\-\d]+) \((\w+)\) — (.+?) \|"
)
_RE_CATALYST2 = re.compile(
    r"Universe expanded: added (\w+) \(catalyst score=([+\-\d]+)\) \| (.+?)$"
)


def _parse_market_character(lines: list) -> dict:
    """Scan the day's log for the most recent market character line."""
    for line in reversed(lines):
        m = _RE_REGIME.search(line)
        if m:
            return {
                "regime":        m.group(1).upper(),
                "ofe_threshold": int(m.group(2)),
                "regime_note":   m.group(3).strip(),
            }
    return {}


def _parse_catalysts(n: int = 8) -> list:
    """Return the last N catalyst discoveries from today's log."""
    lp = _today_log()
    if not lp:
        return []
    hits = []
    try:
        with open(lp, encoding="utf-8", errors="ignore") as f:
            for line in f:
                m = _RE_CATALYST.search(line)
                if m:
                    ts_match = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
                    hits.append({
                        "symbol":    m.group(1),
                        "score":     int(m.group(2)),
                        "sentiment": m.group(3),
                        "headline":  m.group(4).strip(),
                        "time":      ts_match.group(1) if ts_match else "",
                    })
    except Exception:
        pass
    # Deduplicate by symbol (keep highest |score|)
    seen: dict = {}
    for h in hits:
        sym = h["symbol"]
        if sym not in seen or abs(h["score"]) > abs(seen[sym]["score"]):
            seen[sym] = h
    return sorted(seen.values(), key=lambda x: x["time"], reverse=True)[:n]


# ── Session status ─────────────────────────────────────────────────────────────

def _session_status(ross: dict, dux: dict, open_count: int) -> str:
    if ross.get("daily_halted") and dux.get("daily_halted"):
        return "HALTED"
    h = datetime.now().hour + datetime.now().minute / 60.0
    if not ross and not dux:
        return "OFFLINE"
    if 6.0 <= h < 8.5:
        return "PRE-MARKET"
    if 8.5 <= h < 14.5:
        return "LIVE" if open_count else "WATCHING"
    if h >= 14.5:
        return "CLOSED"
    return "IDLE"


# ── Main snapshot builder ─────────────────────────────────────────────────────

def build_snapshot() -> dict:
    ross = _read_json(PORTFOLIO)
    dux  = _read_json(DUX_PORTFOLIO)
    candidates = _read_candidates()
    log_lines  = _tail_log(35)
    mkt_char   = _parse_market_character(log_lines)
    catalysts  = _parse_catalysts(8)
    today      = date.today().isoformat()

    # ── Ross positions ────────────────────────────────────────────────────────
    ross_positions = ross.get("positions", {}) if ross.get("date") == today else {}
    # ── Dux positions ─────────────────────────────────────────────────────────
    dux_positions  = dux.get("positions", {})  if dux.get("date")  == today else {}

    # Fetch live quotes for all open symbols
    all_syms = list(set(list(ross_positions.keys()) + list(dux_positions.keys())))
    quotes   = _live_quotes(all_syms)

    # ── Build merged positions list ───────────────────────────────────────────
    open_pnl  = 0.0
    positions_out = []

    for sym, pos in ross_positions.items():
        shares    = pos.get("shares", 0)
        avg_price = pos.get("avg_price", 0.0)
        q         = quotes.get(sym, {})
        current   = q.get("last") or avg_price
        pnl       = round((current - avg_price) * shares, 2)
        pnl_pct   = round((current - avg_price) / avg_price * 100, 2) if avg_price else 0
        open_pnl += pnl
        positions_out.append({
            "symbol":    sym,
            "strategy":  "ROSS",
            "direction": "LONG",
            "shares":    shares,
            "entry_price": round(avg_price, 4),
            "current":   round(current, 4),
            "bid":       round(q.get("bid", 0), 4),
            "ask":       round(q.get("ask", 0), 4),
            "pnl":       pnl,
            "pnl_pct":   pnl_pct,
        })

    for sym, pos in dux_positions.items():
        shares    = pos.get("shares", 0)
        avg_price = pos.get("avg_price", 0.0)
        direction = pos.get("direction", "LONG").upper()
        q         = quotes.get(sym, {})
        current   = q.get("last") or avg_price
        # Short P&L: profit when price falls
        if direction == "SHORT":
            pnl     = round((avg_price - current) * shares, 2)
            pnl_pct = round((avg_price - current) / avg_price * 100, 2) if avg_price else 0
        else:
            pnl     = round((current - avg_price) * shares, 2)
            pnl_pct = round((current - avg_price) / avg_price * 100, 2) if avg_price else 0
        open_pnl += pnl
        positions_out.append({
            "symbol":    sym,
            "strategy":  "DUX",
            "direction": direction,
            "shares":    shares,
            "entry_price": round(avg_price, 4),
            "current":   round(current, 4),
            "bid":       round(q.get("bid", 0), 4),
            "ask":       round(q.get("ask", 0), 4),
            "pnl":       pnl,
            "pnl_pct":   pnl_pct,
        })

    # Sort by abs(pnl) descending so biggest movers are at top
    positions_out.sort(key=lambda p: abs(p["pnl"]), reverse=True)

    # ── Closed trades today ────────────────────────────────────────────────────
    ross_closed = [
        {**t, "strategy": "ROSS", "direction": "LONG"}
        for t in ross.get("closed_trades", [])
        if t.get("time", "")[:10] == today
    ]
    dux_closed = [
        {**t, "strategy": "DUX", "direction": t.get("direction", "LONG").upper()}
        for t in dux.get("closed_trades", [])
        if t.get("time", "")[:10] == today
    ]
    closed_today = sorted(
        ross_closed + dux_closed,
        key=lambda t: t.get("time", ""),
        reverse=True,
    )

    # ── Ross risk summary ──────────────────────────────────────────────────────
    ross_pnl    = ross.get("daily_pnl", 0.0) if ross.get("date") == today else 0.0
    ross_trades = ross.get("trades_today", 0) if ross.get("date") == today else 0
    ross_streak = ross.get("consecutive_loss", 0) if ross.get("date") == today else 0
    ross_halted = ross.get("daily_halted", False)
    ross_wins   = sum(1 for t in ross_closed if t.get("pnl", 0) > 0)
    ross_losses = sum(1 for t in ross_closed if t.get("pnl", 0) < 0)

    # ── Dux risk summary ───────────────────────────────────────────────────────
    dux_pnl    = dux.get("daily_pnl", 0.0) if dux.get("date") == today else 0.0
    dux_trades = dux.get("trades_today", 0) if dux.get("date") == today else 0
    dux_streak = dux.get("consecutive_loss", 0) if dux.get("date") == today else 0
    dux_halted = dux.get("daily_halted", False)
    dux_wins   = dux.get("wins_today", 0) if dux.get("date") == today else 0
    dux_losses = dux_trades - dux_wins
    dux_wr     = dux.get("win_rate", 0.0) if dux.get("date") == today else 0.0
    dux_errmde = dux.get("error_mode", 0) if dux.get("date") == today else 0

    # ── Combined summary ───────────────────────────────────────────────────────
    total_pnl_realized = ross_pnl + dux_pnl
    total_trades       = ross_trades + dux_trades
    total_wins         = ross_wins + dux_wins
    total_losses       = ross_losses + dux_losses
    total_wr           = round(total_wins / total_trades * 100, 1) if total_trades else 0.0
    worst_streak       = max(ross_streak, dux_streak)
    either_halted      = ross_halted or dux_halted

    limit_used = abs(min(total_pnl_realized, 0)) / MAX_DAILY_LOSS * 100

    # ── Market context ────────────────────────────────────────────────────────
    market = _market_context()
    market.update(mkt_char)   # Overlay regime/ofe from log

    status = _session_status(ross, dux, len(positions_out))

    return {
        "timestamp": datetime.now().isoformat(),
        "status":    status,

        "market": market,

        "summary": {
            "daily_pnl":    round(total_pnl_realized, 2),
            "open_pnl":     round(open_pnl, 2),
            "total_pnl":    round(total_pnl_realized + open_pnl, 2),
            "trades_today": total_trades,
            "wins":         total_wins,
            "losses":       total_losses,
            "win_rate":     total_wr,
            "open_count":   len(positions_out),
        },

        "ross": {
            "daily_pnl":        round(ross_pnl, 2),
            "trades_today":     ross_trades,
            "wins":             ross_wins,
            "losses":           ross_losses,
            "consecutive_loss": ross_streak,
            "halted":           ross_halted,
        },

        "dux": {
            "daily_pnl":        round(dux_pnl, 2),
            "trades_today":     dux_trades,
            "wins":             dux_wins,
            "losses":           dux_losses,
            "win_rate":         round(dux_wr * 100, 1),
            "consecutive_loss": dux_streak,
            "error_mode":       dux_errmde,
            "halted":           dux_halted,
        },

        "risk": {
            "daily_pnl":        round(total_pnl_realized, 2),
            "daily_limit":      MAX_DAILY_LOSS,
            "limit_used_pct":   round(limit_used, 1),
            "consecutive_loss": worst_streak,
            "max_consecutive":  MAX_CONSECUTIVE,
            "halted":           either_halted,
        },

        "positions":    positions_out,
        "candidates":   candidates[:10],
        "closed_today": closed_today[:20],
        "catalysts":    catalysts,
        "log":          log_lines,
    }


# ── Background broadcaster ────────────────────────────────────────────────────

async def _broadcast_loop():
    while True:
        try:
            if manager.active:
                snap = build_snapshot()
                await manager.broadcast(snap)
        except Exception as e:
            logger.debug(f"Broadcast error: {e}")
        await asyncio.sleep(2)


@app.on_event("startup")
async def _startup():
    asyncio.create_task(_broadcast_loop())


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", dependencies=_auth_deps)
async def root():
    return FileResponse(str(Path(__file__).parent / "static" / "index.html"))


@app.get("/api/snapshot", dependencies=_auth_deps)
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
    except Exception:
        manager.disconnect(ws)
