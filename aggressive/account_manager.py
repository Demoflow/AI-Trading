"""
Account Manager v2.
Handles dynamic equity sync, portfolio heat, cash reserves,
position aging, win rate tracking, and compounding.
"""
import os
import json
from datetime import datetime, date
from loguru import logger

TRADE_LOG = "config/trade_log.json"


class AccountManager:

    MIN_CASH_RESERVE_PCT = 0.20  # Always keep 20% cash
    MAX_PORTFOLIO_HEAT = 0.70    # Max 70% of equity deployed
    DAILY_LOSS_HALT_PCT = 0.05   # Halt if down 5% in a day
    WIN_RATE_REDUCE_THRESHOLD = 0.45  # Reduce size if WR < 45%
    WIN_RATE_LOOKBACK = 10       # Last 10 trades

    def __init__(self, client):
        self.client = client
        self._load_trade_log()

    def _load_trade_log(self):
        if os.path.exists(TRADE_LOG):
            self.trade_log = json.load(open(TRADE_LOG))
        else:
            self.trade_log = {"trades": [], "daily_pnl": {}}

    def _save_trade_log(self):
        json.dump(self.trade_log, open(TRADE_LOG, "w"), indent=2)

    def get_real_equity(self):
        """Fetch real equity from Schwab."""
        try:
            r = self.client.get_account_numbers()
            h = r.json()[0]["hashValue"]
            r2 = self.client.get_account(h)
            d = r2.json()
            bal = d.get("securitiesAccount", {}).get("currentBalances", {})
            return {
                "cash": bal.get("cashBalance", 0),
                "equity": bal.get("liquidationValue", 0),
                "options_value": bal.get("longOptionMarketValue", 0),
                "buying_power": bal.get("buyingPower", 0),
                "available": bal.get("availableFundsNonMarginableTrade", 0),
            }
        except Exception as e:
            logger.error(f"Equity sync error: {e}")
            return None

    def can_enter_trade(self, trade_cost):
        """Check all account rules before entering a trade."""
        acct = self.get_real_equity()
        if not acct:
            return False, "cant_read_account"

        cash = acct["cash"]
        equity = acct["equity"]
        options = acct["options_value"]

        # Rule 1: Cash reserve
        min_cash = equity * self.MIN_CASH_RESERVE_PCT
        if cash - trade_cost < min_cash:
            return False, f"cash_reserve_${min_cash:.0f}_needed"

        # Rule 2: Portfolio heat
        if options + trade_cost > equity * self.MAX_PORTFOLIO_HEAT:
            return False, f"heat_{(options+trade_cost)/equity:.0%}_exceeds_{self.MAX_PORTFOLIO_HEAT:.0%}"

        # Rule 3: Available funds
        if trade_cost > acct["available"]:
            return False, f"insufficient_funds_${acct['available']:.0f}"

        # Rule 4: Max 10% per trade
        if trade_cost > equity * 0.10:
            return False, f"trade_too_large_{trade_cost/equity:.0%}"

        return True, "approved"

    def get_size_modifier(self):
        """
        Adjust position size based on win rate.
        Returns multiplier (0.5 to 1.0).
        """
        trades = self.trade_log.get("trades", [])
        recent = trades[-self.WIN_RATE_LOOKBACK:]
        if len(recent) < 5:
            return 1.0  # Not enough data

        wins = sum(1 for t in recent if t.get("pnl", 0) > 0)
        wr = wins / len(recent)

        if wr < self.WIN_RATE_REDUCE_THRESHOLD:
            logger.warning(f"Win rate {wr:.0%} < {self.WIN_RATE_REDUCE_THRESHOLD:.0%} - reducing size 50%")
            return 0.5
        return 1.0

    def record_trade(self, symbol, direction, strategy, entry_cost, exit_value, pnl):
        """Record a completed trade for tracking."""
        self.trade_log["trades"].append({
            "date": date.today().isoformat(),
            "symbol": symbol,
            "direction": direction,
            "strategy": strategy,
            "entry_cost": entry_cost,
            "exit_value": exit_value,
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl / entry_cost * 100, 1) if entry_cost > 0 else 0,
        })

        # Update daily P&L
        today = date.today().isoformat()
        daily = self.trade_log.get("daily_pnl", {})
        daily[today] = daily.get(today, 0) + pnl
        self.trade_log["daily_pnl"] = daily

        self._save_trade_log()

    def check_daily_halt(self):
        """Check if daily loss limit has been hit."""
        acct = self.get_real_equity()
        if not acct:
            return False

        today = date.today().isoformat()
        daily_pnl = self.trade_log.get("daily_pnl", {}).get(today, 0)
        equity = acct["equity"]

        if daily_pnl < -(equity * self.DAILY_LOSS_HALT_PCT):
            logger.warning(f"DAILY HALT: P&L ${daily_pnl:+,.0f} exceeds {self.DAILY_LOSS_HALT_PCT:.0%} of equity")
            return True
        return False

    def get_stats(self):
        """Get trading statistics."""
        trades = self.trade_log.get("trades", [])
        if not trades:
            return {"total": 0, "wr": 0, "avg_pnl": 0, "total_pnl": 0}

        wins = sum(1 for t in trades if t.get("pnl", 0) > 0)
        total_pnl = sum(t.get("pnl", 0) for t in trades)
        avg_pnl = total_pnl / len(trades)

        return {
            "total": len(trades),
            "wins": wins,
            "losses": len(trades) - wins,
            "wr": round(wins / len(trades) * 100, 1),
            "avg_pnl": round(avg_pnl, 2),
            "total_pnl": round(total_pnl, 2),
        }
