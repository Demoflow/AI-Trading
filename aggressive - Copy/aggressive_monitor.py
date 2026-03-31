"""
Aggressive Mode Market Monitor.
Executes options trades from aggressive_trades.json.
Manages positions with options-specific exit logic.
"""

import os
import sys
import json
import time
from datetime import datetime, date
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class AggressiveMonitor:

    ENTRY_START = 10.0
    ENTRY_END = 14.5
    HARD_CUTOFF = 15.0

    def __init__(self, schwab_client, executor, equity):
        self.client = schwab_client
        self.executor = executor
        self.equity = equity
        self.positions = self._load_positions()
        self.trades_today = self._load_trades()

    def _load_trades(self):
        p = "config/aggressive_trades.json"
        if os.path.exists(p):
            with open(p) as f:
                data = json.load(f)
            return data.get("trades", [])
        return []

    def _load_positions(self):
        p = "config/aggressive_positions.json"
        if os.path.exists(p):
            with open(p) as f:
                return json.load(f)
        return []

    def _save_positions(self):
        with open("config/aggressive_positions.json", "w") as f:
            json.dump(self.positions, f, indent=2, default=str)

    def run_cycle(self, prices):
        now = datetime.now()
        hour = now.hour + now.minute / 60.0

        # Check exits first
        self._check_exits(prices)

        # Check entries during window
        if self.ENTRY_START <= hour <= self.ENTRY_END:
            self._check_entries(prices)

    def _check_entries(self, prices):
        for trade in list(self.trades_today):
            sym = trade["symbol"]
            if any(p["symbol"] == sym for p in self.positions):
                continue

            price = prices.get(sym)
            if not price:
                continue

            contract = trade["contract"]
            direction = trade["direction"]
            qty = contract["qty"]
            limit = round(contract["ask"] * 0.98, 2)

            logger.info(
                f"ENTRY: {direction} {sym} "
                f"${contract['strike']} "
                f"{contract['dte']}DTE x{qty} "
                f"@ ${limit}"
            )

            pos = {
                "symbol": sym,
                "direction": direction,
                "contract_symbol": contract.get("symbol", ""),
                "strike": contract["strike"],
                "dte": contract["dte"],
                "qty": qty,
                "entry_price": contract["mid"],
                "entry_date": date.today().isoformat(),
                "entry_cost": contract["total_cost"],
                "stop_pct": trade.get("stop_pct", 0.35),
                "target_1_pct": trade.get("target_1_pct", 0.50),
                "target_2_pct": trade.get("target_2_pct", 1.00),
                "max_hold_days": trade.get("max_hold_days", 30),
                "status": "OPEN",
                "scale_stage": 0,
                "highest_value": contract["mid"],
                "conviction": trade["conviction"],
                "composite": trade["composite"],
            }
            self.positions.append(pos)
            self._save_positions()
            self.trades_today.remove(trade)

            logger.info(f"POSITION OPENED: {direction} {sym}")

    def _check_exits(self, prices):
        for pos in list(self.positions):
            if pos["status"] != "OPEN":
                continue

            sym = pos["symbol"]
            price = prices.get(sym)
            if not price:
                continue

            entry = pos["entry_price"]
            if entry <= 0:
                continue

            # Estimate current option value
            # Rough approximation based on underlying move
            pct_move = (price - pos["strike"]) / pos["strike"]
            if pos["direction"] == "CALL":
                est_value = max(0.01, entry * (1 + pct_move * 3))
            else:
                est_value = max(0.01, entry * (1 - pct_move * 3))

            pos["highest_value"] = max(pos["highest_value"], est_value)
            pnl_pct = (est_value - entry) / entry

            # Stop loss
            if pnl_pct <= -pos["stop_pct"]:
                self._close(pos, est_value, "stop_loss")
                continue

            # Target 1: take half
            if pos["scale_stage"] == 0 and pnl_pct >= pos["target_1_pct"]:
                pos["scale_stage"] = 1
                pos["qty"] = max(1, pos["qty"] // 2)
                logger.info(f"T1 HIT: {sym} +{pnl_pct:.0%} - scaled to {pos['qty']}")
                self._save_positions()
                continue

            # Target 2: close remaining
            if pos["scale_stage"] >= 1 and pnl_pct >= pos["target_2_pct"]:
                self._close(pos, est_value, "target_2")
                continue

            # Trailing stop after T1
            if pos["scale_stage"] >= 1:
                trail_pnl = (est_value - pos["highest_value"]) / pos["highest_value"]
                if trail_pnl <= -0.25:
                    self._close(pos, est_value, "trailing_stop")
                    continue

            # Time stop
            entry_date = date.fromisoformat(pos["entry_date"])
            days = (date.today() - entry_date).days
            if days >= pos["max_hold_days"]:
                if pnl_pct > 0:
                    self._close(pos, est_value, "time_stop_profit")
                else:
                    self._close(pos, est_value, "time_stop_loss")

    def _close(self, pos, exit_value, reason):
        entry = pos["entry_price"]
        pnl_pct = (exit_value - entry) / entry
        pnl_dollar = (exit_value - entry) * pos["qty"] * 100

        pos["status"] = "CLOSED"
        pos["exit_date"] = date.today().isoformat()
        pos["exit_price"] = round(exit_value, 2)
        pos["exit_reason"] = reason
        pos["pnl_pct"] = round(pnl_pct, 4)
        pos["pnl_dollar"] = round(pnl_dollar, 2)

        self._save_positions()

        result = "WIN" if pnl_dollar > 0 else "LOSS"
        logger.info(
            f"CLOSED {result}: {pos['direction']} {pos['symbol']} "
            f"${pnl_dollar:+,.2f} ({pnl_pct:+.0%}) "
            f"reason={reason}"
        )

    def get_summary(self):
        open_pos = [p for p in self.positions if p["status"] == "OPEN"]
        closed = [p for p in self.positions if p["status"] == "CLOSED"]
        total_pnl = sum(p.get("pnl_dollar", 0) for p in closed)
        wins = len([p for p in closed if p.get("pnl_dollar", 0) > 0])
        losses = len(closed) - wins
        return {
            "open": len(open_pos),
            "closed": len(closed),
            "wins": wins,
            "losses": losses,
            "win_rate": wins / max(len(closed), 1),
            "total_pnl": round(total_pnl, 2),
            "positions": open_pos,
        }
