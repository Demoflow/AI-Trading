"""
Trade Logger - Records every completed trade for ML training.
Appends to config/trade_log.json whenever a position closes.
"""

import json
from datetime import date, datetime
from pathlib import Path
from loguru import logger


class TradeLogger:

    LOG_PATH = "config/trade_log.json"

    def __init__(self):
        self.trades = self._load()

    def _load(self):
        if Path(self.LOG_PATH).exists():
            with open(self.LOG_PATH) as f:
                return json.load(f)
        return []

    def _save(self):
        Path(self.LOG_PATH).parent.mkdir(
            parents=True, exist_ok=True
        )
        with open(self.LOG_PATH, "w") as f:
            json.dump(
                self.trades, f,
                indent=2, default=str
            )

    def log_trade(self, position_dict):
        """Call this when a position is fully closed."""
        p = position_dict
        entry_price = p.get("entry_price", 0)
        exits = p.get("exit_details", [])
        if not exits:
            return

        total_pnl = p.get("realized_pnl", 0)
        avg_exit = 0
        total_qty = 0
        for ex in exits:
            avg_exit += ex.get("price", 0) * ex.get("quantity", 0)
            total_qty += ex.get("quantity", 0)
        if total_qty > 0:
            avg_exit = avg_exit / total_qty

        entry_cost = p.get("entry_cost", 0)
        if entry_cost > 0:
            pnl_pct = total_pnl / entry_cost
        else:
            pnl_pct = 0

        trade = {
            "symbol": p.get("symbol", ""),
            "instrument": p.get("instrument", ""),
            "direction": p.get("direction", ""),
            "entry_date": p.get("entry_date", ""),
            "exit_date": date.today().isoformat(),
            "entry_price": round(entry_price, 2),
            "avg_exit_price": round(avg_exit, 2),
            "quantity": p.get("original_quantity", 0),
            "pnl": round(total_pnl, 2),
            "pnl_pct": round(pnl_pct, 4),
            "profitable": total_pnl > 0,
            "signal_score": p.get("signal_score", 0),
            "sector": p.get("sector", ""),
            "hold_days": p.get("days_held", 0),
            "exit_reasons": [
                ex.get("reason", "") for ex in exits
            ],
            "stop_loss": p.get("stop_loss", 0),
            "target_1": p.get("target_1", 0),
            "scale_stage": p.get("scale_stage", 0),
            "highest_price": p.get("highest_price", 0),
            "lowest_price": p.get("lowest_price", 0),
            "logged_at": datetime.utcnow().isoformat(),
        }

        self.trades.append(trade)
        self._save()

        result = "WIN" if total_pnl > 0 else "LOSS"
        logger.info(
            f"TRADE LOGGED: {result} {trade['symbol']} "
            f"${total_pnl:+.2f} ({pnl_pct:+.1%}) "
            f"held {trade['hold_days']}d"
        )

    def get_stats(self):
        if not self.trades:
            return {"total": 0}
        wins = [t for t in self.trades if t["profitable"]]
        losses = [t for t in self.trades if not t["profitable"]]
        total_pnl = sum(t["pnl"] for t in self.trades)
        avg_win = 0
        avg_loss = 0
        if wins:
            avg_win = sum(t["pnl"] for t in wins) / len(wins)
        if losses:
            avg_loss = sum(t["pnl"] for t in losses) / len(losses)
        return {
            "total": len(self.trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / len(self.trades),
            "total_pnl": round(total_pnl, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "profit_factor": (
                abs(avg_win * len(wins))
                / max(abs(avg_loss * len(losses)), 1)
            ),
            "best_trade": max(
                t["pnl"] for t in self.trades
            ),
            "worst_trade": min(
                t["pnl"] for t in self.trades
            ),
        }
