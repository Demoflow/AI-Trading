"""
Scalper Executor v3 - Long Options Only.
- Long calls and puts only
- Settled cash tracking: intraday proceeds settle overnight (T+1)
- Equity auto-updates with each closed trade so 2% sizing scales correctly
- Portfolio: config/paper_scalp.json
"""

import os
import json
from pathlib import Path
from datetime import datetime, date
from loguru import logger

BUY_SLIPPAGE = 0.03    # Pay 3% above mid when buying
SELL_SLIPPAGE = 0.015  # Receive 1.5% below mid when selling


class ScalperExecutor:

    def __init__(self, equity=25000):
        self.equity = equity
        self.portfolio = self._load()

    def _load(self):
        path = "config/paper_scalp.json"
        if os.path.exists(path):
            with open(path) as f:
                data = json.load(f)
            # Back-fill settled_cash field if missing (first run after upgrade)
            if "settled_cash" not in data:
                data["settled_cash"] = data.get("cash", self.equity)
                data["settlement_date"] = date.today().isoformat()
            if "equity" not in data:
                data["equity"] = self.equity
            # Back-fill monotonic position ID counter
            if "next_id" not in data:
                all_ids = [
                    p.get("id", 0)
                    for p in data.get("positions", []) + data.get("history", [])
                ]
                data["next_id"] = max(all_ids, default=0) + 1
            return data
        return {
            "equity": self.equity,
            "cash": self.equity,
            "settled_cash": self.equity,
            "settlement_date": date.today().isoformat(),
            "positions": [],
            "history": [],
            "daily_stats": {},
            "next_id": 1,
        }

    def _save(self):
        path = Path("config/paper_scalp.json")
        path.parent.mkdir(exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.portfolio, indent=2, default=str))
        tmp.replace(path)

    # ── SETTLEMENT ─────────────────────────────────────────────────────────────

    def advance_settlement(self):
        """
        Call once at the start of each trading session.
        Overnight settlement: all cash (including yesterday's proceeds) becomes
        available settled cash for today's trades.
        """
        today = date.today().isoformat()
        last = self.portfolio.get("settlement_date", "")
        if last != today:
            self.portfolio["settled_cash"] = self.portfolio["cash"]
            self.portfolio["settlement_date"] = today
            self._save()
            logger.info(
                f"Settlement advanced: ${self.portfolio['settled_cash']:,.2f} "
                f"available for today ({today})"
            )
        return self.portfolio["settled_cash"]

    def get_available_cash(self):
        """Settled cash available for new trades today."""
        return self.portfolio.get("settled_cash", self.portfolio["cash"])

    # ── OPEN ───────────────────────────────────────────────────────────────────

    def open_position(self, signal, contract, max_cost):
        """Open a directional long call or put."""
        if not contract:
            return {"status": "REJECTED", "reason": "no_contract"}

        mid = contract.get("mid", 0)
        qty = contract.get("qty", 1)
        fill = round(mid * (1 + BUY_SLIPPAGE), 2)
        cost = fill * qty * 100

        available = self.get_available_cash()
        if cost > available:
            qty = int(available / (fill * 100))
            if qty < 1:
                return {"status": "REJECTED", "reason": "no_settled_cash"}
            cost = fill * qty * 100

        # Deduct from both cash and settled_cash (reserves today's budget)
        self.portfolio["cash"] -= cost
        self.portfolio["settled_cash"] -= cost

        # Guard: another signal in the same cycle may have consumed the cash
        if self.portfolio["settled_cash"] < 0:
            self.portfolio["cash"] += cost
            self.portfolio["settled_cash"] += cost
            return {"status": "REJECTED", "reason": "no_settled_cash"}

        pos = {
            "id": self.portfolio["next_id"],
            "symbol": signal["symbol"],
            "direction": signal["direction"],
            "signal_type": signal["type"],
            "structure": "LONG_OPTION",
            "confidence": signal["confidence"],
            "contract": contract.get("symbol", ""),
            "strike": contract.get("strike", 0),
            "delta": contract.get("delta", 0),
            "gamma": contract.get("gamma", 0),
            "theta": contract.get("theta", 0),
            "entry_price": fill,
            "entry_cost": round(cost, 2),
            "qty": qty,
            "entry_time": datetime.now().isoformat(),
            "entry_underlying": signal["price"],
            "status": "OPEN",
            "peak_value": cost,
            "reason": signal.get("reason", ""),
        }
        self.portfolio["next_id"] += 1
        self.portfolio["positions"].append(pos)
        self._save()
        logger.info(
            f"SCALP OPEN: {signal['direction']} {signal['symbol']} "
            f"${contract['strike']} {signal['type']} "
            f"d={contract.get('delta', 0):.3f} g={contract.get('gamma', 0):.4f} "
            f"@ ${fill} x{qty} = ${cost:,.2f} "
            f"(settled avail: ${self.get_available_cash():,.2f})"
        )
        return {"status": "FILLED", "cost": cost, "position": pos}

    # ── CLOSE ──────────────────────────────────────────────────────────────────

    def close_position(self, pos_id, current_option_value):
        for pos in self.portfolio["positions"]:
            if pos.get("id") != pos_id or pos["status"] != "OPEN":
                continue

            qty = pos["qty"]
            fill = round(current_option_value * (1 - SELL_SLIPPAGE), 2)
            proceeds = fill * qty * 100
            pnl = proceeds - pos["entry_cost"]
            pnl_pct = pnl / max(pos["entry_cost"], 1)

            # Proceeds go to cash but NOT settled_cash (settle overnight)
            self.portfolio["cash"] += proceeds

            # Equity tracks cumulative realized performance
            self.portfolio["equity"] = self.portfolio.get("equity", self.equity) + pnl

            pos["status"] = "CLOSED"
            pos["exit_price"] = fill
            pos["exit_time"] = datetime.now().isoformat()
            pos["pnl"] = round(pnl, 2)
            pos["pnl_pct"] = round(pnl_pct * 100, 1)

            self.portfolio["history"].append(pos)
            self.portfolio["positions"] = [
                p for p in self.portfolio["positions"] if p.get("id") != pos_id
            ]

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
            self._save()

            r = "WIN" if pnl > 0 else "LOSS"
            et = datetime.fromisoformat(pos["entry_time"])
            mins = (datetime.now() - et).total_seconds() / 60
            logger.info(
                f"SCALP {r}: {pos['direction']} {pos['symbol']} "
                f"${pnl:+,.2f} ({pnl_pct:+.0%}) {mins:.1f}min "
                f"| equity=${self.portfolio['equity']:,.2f}"
            )
            return {"status": "CLOSED", "pnl": pnl}

        return {"status": "NOT_FOUND"}

    # ── STALE POSITION CLEANUP ─────────────────────────────────────────────────

    def expire_stale_positions(self):
        """Expire any 0DTE long options from previous days (expired worthless)."""
        today = date.today().isoformat()
        expired = []
        for pos in self.portfolio["positions"]:
            if pos["status"] != "OPEN":
                continue
            entry_date = pos.get("entry_time", "")[:10]
            if entry_date and entry_date < today:
                pnl = -pos["entry_cost"]
                self.portfolio["equity"] = self.portfolio.get("equity", self.equity) + pnl
                logger.warning(
                    f"EXPIRED WORTHLESS: {pos['symbol']} "
                    f"{pos.get('direction','?')} -${pos['entry_cost']:,.2f}"
                )
                pos["status"] = "CLOSED"
                pos["exit_price"] = 0
                pos["exit_time"] = f"{entry_date}T16:00:00"
                pos["pnl"] = round(pnl, 2)
                pos["pnl_pct"] = -100.0
                pos["exit_reason"] = "EXPIRED_0DTE"
                self.portfolio["history"].append(pos)
                expired.append(pos["id"])

                if entry_date not in self.portfolio["daily_stats"]:
                    self.portfolio["daily_stats"][entry_date] = {
                        "trades": 0, "wins": 0, "losses": 0, "pnl": 0
                    }
                s = self.portfolio["daily_stats"][entry_date]
                s["trades"] += 1
                s["pnl"] += pnl
                s["losses"] += 1

        if expired:
            self.portfolio["positions"] = [
                p for p in self.portfolio["positions"] if p.get("id") not in expired
            ]
            self._save()
            logger.info(f"Expired {len(expired)} stale long options from previous days")
        return len(expired)

    # ── QUERIES ────────────────────────────────────────────────────────────────

    def get_open_positions(self):
        return [p for p in self.portfolio["positions"] if p["status"] == "OPEN"]

    def get_summary(self):
        today = date.today().isoformat()
        ds = self.portfolio["daily_stats"].get(
            today, {"trades": 0, "wins": 0, "losses": 0, "pnl": 0}
        )
        h = self.portfolio["history"]
        wins = [t for t in h if t.get("pnl", 0) > 0]
        return {
            "equity": round(self.portfolio.get("equity", self.equity), 2),
            "cash": round(self.portfolio["cash"], 2),
            "settled_cash": round(self.get_available_cash(), 2),
            "open_positions": len(self.get_open_positions()),
            "today_trades": ds["trades"],
            "today_pnl": round(ds["pnl"], 2),
            "today_wins": ds["wins"],
            "today_losses": ds["losses"],
            "total_trades": len(h),
            "total_pnl": round(sum(t.get("pnl", 0) for t in h), 2),
            "win_rate": round(len(wins) / max(len(h), 1), 2),
        }
