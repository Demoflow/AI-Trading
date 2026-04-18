"""
Scalper Executor v4 — Stock VWAP Scalping.
Complete rewrite for stock positions (no options/contracts/premium).

- Positions tracked as shares with dollar notional
- Margin-aware: 4x buying power
- Partial exits (50% at target_1)
- Atomic file writes
- History capped at 100 entries
- Portfolio: config/paper_scalp.json
"""

import os
import json
from pathlib import Path
from datetime import datetime, date
from loguru import logger

try:
    from zoneinfo import ZoneInfo
    _CT_TZ = ZoneInfo("America/Chicago")
except ImportError:
    _CT_TZ = None


def _now_ct():
    return datetime.now(tz=_CT_TZ) if _CT_TZ else datetime.now()


MAX_HISTORY = 100  # Cap trade history to prevent unbounded growth


class ScalperExecutor:
    """Stock position executor with paper trading portfolio management."""

    def __init__(self, equity=25000):
        self.equity = equity
        self.portfolio = self._load()

    def _load(self):
        path = "config/paper_scalp.json"
        if os.path.exists(path):
            with open(path) as f:
                data = json.load(f)
            # Migration: ensure new schema fields exist
            if "buying_power" not in data:
                data["buying_power"] = data.get("equity", self.equity) * 4
            if "equity" not in data:
                data["equity"] = self.equity
            if "cash" not in data:
                data["cash"] = self.equity
            if "next_id" not in data:
                all_ids = [
                    p.get("id", 0)
                    for p in data.get("positions", []) + data.get("history", [])
                ]
                data["next_id"] = max(all_ids, default=0) + 1
            # Migrate old options positions to closed (they're incompatible)
            migrated = 0
            remaining = []
            for p in data.get("positions", []):
                if p.get("structure") == "LONG_OPTION" or p.get("contract"):
                    p["status"] = "CLOSED"
                    p["exit_reason"] = "SYSTEM_MIGRATION"
                    p["pnl"] = 0
                    data.get("history", []).append(p)
                    migrated += 1
                else:
                    remaining.append(p)
            if migrated:
                data["positions"] = remaining
                logger.info(f"Migrated {migrated} old option positions to history")
            return data
        return {
            "equity": self.equity,
            "buying_power": self.equity * 4,
            "cash": self.equity,
            "positions": [],
            "history": [],
            "daily_stats": {},
            "next_id": 1,
        }

    def _save(self):
        path = Path("config/paper_scalp.json")
        path.parent.mkdir(exist_ok=True)
        # Trim history to MAX_HISTORY
        if len(self.portfolio.get("history", [])) > MAX_HISTORY:
            self.portfolio["history"] = self.portfolio["history"][-MAX_HISTORY:]
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.portfolio, indent=2, default=str))
        tmp.replace(path)

    # ── OPEN POSITION ────────────────────────────────────────────────────────

    def open_position(self, signal, share_count, equity=None):
        """
        Open a new stock position.

        Args:
            signal: signal dict from SignalEngine
            share_count: number of shares to buy/short
            equity: current equity for buying power validation

        Returns:
            dict with status: "FILLED" or "REJECTED"
        """
        if share_count < 1:
            return {"status": "REJECTED", "reason": "zero_shares"}

        price = signal["entry_price"]
        cost_basis = round(share_count * price, 2)
        eq = equity or self.portfolio.get("equity", self.equity)

        # Validate buying power (4x margin)
        max_buying_power = eq * 4
        current_deployed = sum(
            p.get("cost_basis", 0) for p in self.portfolio["positions"]
            if p.get("status") == "OPEN"
        )
        available_bp = max_buying_power - current_deployed
        if cost_basis > available_bp:
            # Reduce share count to fit
            share_count = int(available_bp / price)
            if share_count < 1:
                return {"status": "REJECTED", "reason": "insufficient_buying_power"}
            cost_basis = round(share_count * price, 2)

        pos = {
            "id": self.portfolio["next_id"],
            "symbol": signal["symbol"],
            "direction": signal["direction"],
            "signal_type": signal["type"],
            "shares": share_count,
            "entry_price": round(price, 2),
            "entry_time": _now_ct().isoformat(),
            "stop_price": signal.get("stop_price", 0),
            "target_1": signal.get("target_1", 0),
            "target_2": signal.get("target_2", 0),
            "vwap_at_entry": signal.get("vwap", 0),
            "current_price": round(price, 2),
            "current_value": cost_basis,
            "cost_basis": cost_basis,
            "pnl": 0.0,
            "pnl_pct": 0.0,
            "peak_price": round(price, 2),
            "status": "OPEN",
            "partial_exits": [],
            "type": "STOCK",
            "confidence": signal.get("confidence", 0),
            "touch_count": signal.get("touch_count", 0),
            "volume_ratio": signal.get("volume_ratio", 0),
        }
        self.portfolio["next_id"] += 1
        self.portfolio["positions"].append(pos)
        self._save()

        logger.info(
            f"Opened {signal['symbol']} {signal['direction']} "
            f"{share_count} shares @ ${price:.2f} "
            f"| VWAP ${signal.get('vwap', 0):.2f} "
            f"| stop ${signal.get('stop_price', 0):.2f} "
            f"| target ${signal.get('target_1', 0):.2f}"
        )

        return {"status": "FILLED", "cost": cost_basis, "position": pos}

    # ── CLOSE POSITION ───────────────────────────────────────────────────────

    def close_position(self, pos_id, exit_price, reason=""):
        """
        Close an entire position.

        Returns:
            dict with status and pnl
        """
        for pos in self.portfolio["positions"]:
            if pos.get("id") != pos_id or pos["status"] != "OPEN":
                continue

            shares = pos["shares"]
            direction = pos["direction"]

            # P&L calculation
            if direction == "LONG":
                pnl = round((exit_price - pos["entry_price"]) * shares, 2)
            else:  # SHORT
                pnl = round((pos["entry_price"] - exit_price) * shares, 2)

            # Account for partial exits already taken
            partial_pnl = sum(pe.get("pnl", 0) for pe in pos.get("partial_exits", []))
            total_pnl = pnl + partial_pnl
            pnl_pct = total_pnl / pos["cost_basis"] if pos["cost_basis"] > 0 else 0

            # Update equity
            self.portfolio["equity"] = self.portfolio.get("equity", self.equity) + pnl

            pos["status"] = "CLOSED"
            pos["exit_price"] = round(exit_price, 2)
            pos["exit_time"] = _now_ct().isoformat()
            pos["exit_reason"] = reason
            pos["pnl"] = round(total_pnl, 2)
            pos["pnl_pct"] = round(pnl_pct * 100, 1)

            self.portfolio["history"].append(pos)
            self.portfolio["positions"] = [
                p for p in self.portfolio["positions"] if p.get("id") != pos_id
            ]

            # Update daily stats
            self._update_daily_stats(total_pnl)
            self._save()

            result = "WIN" if total_pnl > 0 else "LOSS"
            et = datetime.fromisoformat(pos["entry_time"])
            now = _now_ct()
            if et.tzinfo is None and now.tzinfo is not None:
                et = et.replace(tzinfo=_CT_TZ)
            mins = (now - et).total_seconds() / 60

            logger.info(
                f"SCALP {result}: {pos['direction']} {pos['symbol']} "
                f"{shares} shares ${total_pnl:+,.2f} ({pnl_pct:+.1%}) "
                f"{mins:.1f}min [{reason}] "
                f"| equity=${self.portfolio['equity']:,.2f}"
            )
            return {"status": "CLOSED", "pnl": total_pnl}

        return {"status": "NOT_FOUND"}

    # ── PARTIAL EXIT ─────────────────────────────────────────────────────────

    def partial_exit(self, pos_id, shares_to_sell, exit_price, reason=""):
        """
        Sell a portion of the position (e.g., 50% at target_1).

        Returns:
            Updated position dict or None.
        """
        for pos in self.portfolio["positions"]:
            if pos.get("id") != pos_id or pos["status"] != "OPEN":
                continue

            if shares_to_sell >= pos["shares"]:
                # Full exit
                return self.close_position(pos_id, exit_price, reason)

            direction = pos["direction"]

            # P&L on the partial
            if direction == "LONG":
                partial_pnl = round((exit_price - pos["entry_price"]) * shares_to_sell, 2)
            else:
                partial_pnl = round((pos["entry_price"] - exit_price) * shares_to_sell, 2)

            # Record partial exit
            pos["partial_exits"].append({
                "shares": shares_to_sell,
                "price": round(exit_price, 2),
                "pnl": partial_pnl,
                "time": _now_ct().isoformat(),
                "reason": reason,
            })

            # Update remaining shares
            pos["shares"] -= shares_to_sell
            # Cost basis adjusted proportionally
            original_shares = pos["shares"] + shares_to_sell
            pos["cost_basis"] = round(
                pos["cost_basis"] * (pos["shares"] / original_shares), 2
            )

            # Update equity with partial profit
            self.portfolio["equity"] = self.portfolio.get("equity", self.equity) + partial_pnl

            self._save()

            logger.info(
                f"PARTIAL EXIT: {pos['symbol']} sold {shares_to_sell} shares "
                f"@ ${exit_price:.2f} P&L=${partial_pnl:+,.2f} [{reason}] "
                f"| {pos['shares']} shares remaining"
            )
            return {"status": "PARTIAL", "pnl": partial_pnl, "position": pos}

        return {"status": "NOT_FOUND"}

    # ── UPDATE POSITION ──────────────────────────────────────────────────────

    def update_position(self, pos_id, current_price):
        """
        Update a position's current price and P&L.
        Returns the updated position or None.
        """
        for pos in self.portfolio["positions"]:
            if pos.get("id") != pos_id or pos["status"] != "OPEN":
                continue

            pos["current_price"] = round(current_price, 2)
            pos["current_value"] = round(current_price * pos["shares"], 2)

            direction = pos["direction"]
            if direction == "LONG":
                unrealized = (current_price - pos["entry_price"]) * pos["shares"]
            else:
                unrealized = (pos["entry_price"] - current_price) * pos["shares"]

            partial_pnl = sum(pe.get("pnl", 0) for pe in pos.get("partial_exits", []))
            pos["pnl"] = round(unrealized + partial_pnl, 2)
            pos["pnl_pct"] = round(
                pos["pnl"] / pos["cost_basis"] * 100 if pos["cost_basis"] > 0 else 0, 1
            )

            # Track peak price
            if direction == "LONG":
                if current_price > pos.get("peak_price", 0):
                    pos["peak_price"] = round(current_price, 2)
            else:
                if current_price < pos.get("peak_price", float("inf")):
                    pos["peak_price"] = round(current_price, 2)

            return pos
        return None

    # ── DAILY STATS ──────────────────────────────────────────────────────────

    def _update_daily_stats(self, pnl):
        today = date.today().isoformat()
        if today not in self.portfolio["daily_stats"]:
            self.portfolio["daily_stats"][today] = {
                "trades": 0, "wins": 0, "losses": 0, "pnl": 0,
            }
        s = self.portfolio["daily_stats"][today]
        s["trades"] += 1
        s["pnl"] += pnl
        if pnl > 0:
            s["wins"] += 1
        else:
            s["losses"] += 1

    # ── QUERIES ──────────────────────────────────────────────────────────────

    def get_open_positions(self):
        return [p for p in self.portfolio["positions"] if p["status"] == "OPEN"]

    def get_summary(self):
        today = date.today().isoformat()
        ds = self.portfolio["daily_stats"].get(
            today, {"trades": 0, "wins": 0, "losses": 0, "pnl": 0}
        )
        h = self.portfolio["history"]
        wins = [t for t in h if t.get("pnl", 0) > 0]
        open_pnl = sum(p.get("pnl", 0) for p in self.get_open_positions())
        deployed = sum(p.get("current_value", 0) for p in self.get_open_positions())
        return {
            "equity": round(self.portfolio.get("equity", self.equity), 2),
            "cash": round(self.portfolio.get("cash", self.equity), 2),
            "buying_power": round(
                self.portfolio.get("equity", self.equity) * 4 - deployed, 2
            ),
            "deployed": round(deployed, 2),
            "open_positions": len(self.get_open_positions()),
            "open_pnl": round(open_pnl, 2),
            "today_trades": ds["trades"],
            "today_pnl": round(ds["pnl"], 2),
            "today_wins": ds["wins"],
            "today_losses": ds["losses"],
            "total_trades": len(h),
            "total_pnl": round(sum(t.get("pnl", 0) for t in h), 2),
            "win_rate": round(len(wins) / max(len(h), 1), 2),
        }

    def save_state(self):
        """Public method to force a state save (called by main loop)."""
        self._save()
