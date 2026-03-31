"""
Exit Manager v3 - Strategy-Specific Exits.
Each strategy type has its own optimal exit logic.
Time-decay scaling tightens targets near expiration.
"""

from datetime import datetime, date
from loguru import logger


class ExitManager:

    def __init__(self):
        pass

    def check_exit(self, position, current_net_value, current_price=0):
        """
        Strategy-specific exit logic.
        Returns (should_exit, reason)
        """
        stype = position.get("strategy_type", "NAKED_LONG")
        entry_cost = position.get("entry_cost", 0)
        entry_date = position.get("entry_date", "")

        if entry_cost <= 0:
            return False, "no_data"

        # Minimum hold: 2 minutes before checking exits
        entry_time = position.get("entry_time", "")
        if entry_time:
            from datetime import datetime
            try:
                et = datetime.fromisoformat(entry_time) if isinstance(entry_time, str) else entry_time
                mins = (datetime.now() - et).total_seconds() / 60
                if mins < 2:
                    return False, "min_hold"
            except Exception:
                pass

        # Days held
        days_held = 0
        if entry_date:
            try:
                ed = date.fromisoformat(entry_date) if isinstance(entry_date, str) else entry_date
                days_held = (date.today() - ed).days
            except Exception:
                pass

        # DTE remaining (approximate from entry)
        max_hold = position.get("max_hold_days", 30)
        days_remaining = max(0, max_hold - days_held)

        # Time decay multiplier: tighten targets as expiration approaches
        if days_remaining <= 3:
            time_mult = 0.50  # Very tight near expiry
        elif days_remaining <= 7:
            time_mult = 0.70
        elif days_remaining <= 14:
            time_mult = 0.85
        else:
            time_mult = 1.00

        # Route to strategy-specific exit
        if stype == "NAKED_LONG":
            return self._exit_naked_long(position, current_net_value, time_mult, days_held)
        elif stype == "DEBIT_SPREAD":
            return self._exit_debit_spread(position, current_net_value, time_mult, days_held)
        elif stype == "CREDIT_SPREAD":
            return self._exit_credit_spread(position, current_net_value, time_mult)
        elif stype == "NAKED_PUT":
            return self._exit_naked_put(position, current_net_value, time_mult, current_price)
        elif stype == "NAKED_CALL":
            return self._exit_naked_call(position, current_net_value, time_mult, current_price)
        elif stype == "SHORT_STRANGLE":
            return self._exit_strangle(position, current_net_value, time_mult)
        elif stype == "BROKEN_WING_BUTTERFLY":
            return self._exit_bwb(position, current_net_value, time_mult, days_held)
        elif stype == "CALENDAR_SPREAD":
            return self._exit_calendar(position, current_net_value, time_mult, days_held)
        else:
            return self._exit_generic(position, current_net_value, time_mult, days_held)

    def _exit_naked_long(self, pos, current_val, tm, days):
        """
        Naked long call/put.
        T1: +50% (scale out half)
        T2: +100%
        Stop: -40% (tightens with time)
        Trail: -15% from peak after +30%
        Max hold: 21 days
        """
        cost = pos["entry_cost"]
        pnl_pct = (current_val - cost) / cost

        profit_t1 = 0.50 * tm
        profit_t2 = 1.00 * tm
        stop = -0.40 * tm
        trail_trigger = 0.30

        if pnl_pct >= profit_t2:
            return True, f"T2_profit_{pnl_pct:+.0%}"
        if pnl_pct <= stop:
            return True, f"stop_{pnl_pct:+.0%}"
        if days >= 21:
            return True, f"max_hold_{days}d"

        # Trailing stop after +30%
        peak = pos.get("peak_pnl_pct", pnl_pct)
        if peak >= trail_trigger and pnl_pct < peak - 0.15:
            return True, f"trail_{pnl_pct:+.0%}_from_{peak:+.0%}"

        # Partial exit signal at T1
        if pnl_pct >= profit_t1 and not pos.get("t1_hit"):
            pos["t1_hit"] = True
            if not pos.get("_t1_alerted"):
                logger.info(f"  T1 HIT: {pos.get('underlying','?')} +{pnl_pct:.0%} - consider scaling out")
                pos["_t1_alerted"] = True

        return False, "hold"

    def _exit_debit_spread(self, pos, current_val, tm, days):
        """
        Debit spread.
        Target: 50-60% of max profit (not 100% - rarely reaches max)
        Stop: 50% of cost
        Max hold: 14 days (theta kills spreads)
        """
        cost = pos["entry_cost"]
        max_profit = pos.get("max_profit_dollar", cost)
        pnl = current_val - cost
        pnl_pct = pnl / cost

        # Target 50-60% of max profit
        target_pnl = max_profit * 0.55 * tm
        target_pct = target_pnl / cost if cost > 0 else 0.50

        if pnl >= target_pnl:
            return True, f"spread_target_{pnl_pct:+.0%}"
        if pnl_pct <= -0.50 * tm:
            return True, f"spread_stop_{pnl_pct:+.0%}"
        if days >= 14:
            return True, f"spread_max_hold_{days}d"
        if days >= 10 and pnl_pct < 0.10:
            return True, f"spread_time_decay_{days}d"

        return False, "hold"

    def _exit_credit_spread(self, pos, current_val, tm):
        """
        Credit spread (sold premium).
        Take profit: spread worth < 50% of credit (keep 50%+)
        Stop: spread worth > 2x credit received
        Time: close at 7 DTE if still open
        """
        credit = pos.get("credit_received", pos.get("premium", 0))
        if credit <= 0:
            return self._exit_generic(pos, current_val, tm, 0)

        # current_val = current cost to buy back the spread
        if current_val <= credit * 0.50:
            return True, "credit_profit_50%"
        if current_val >= credit * 2.0:
            return True, "credit_stop_2x"

        return False, "hold"

    def _exit_naked_put(self, pos, current_val, tm, stock_price):
        """
        Naked put (sold premium).
        Take profit: option worth < 50% of premium collected
        Stop: stock drops below strike - premium (breakeven)
        Or: option worth > 2x premium collected
        """
        premium = pos.get("premium", 0)
        strike = 0
        if pos.get("contracts"):
            strike = pos["contracts"][0].get("strike", 0)

        if premium <= 0:
            return self._exit_generic(pos, current_val, tm, 0)

        # current_val = current cost to buy back the put
        if current_val <= premium * 0.50:
            return True, "naked_put_profit_50%"
        if current_val >= premium * 2.0:
            return True, "naked_put_stop_2x"
        # Breakeven stop
        if stock_price > 0 and strike > 0:
            breakeven = strike - (premium / 100)
            if stock_price < breakeven:
                return True, f"naked_put_breakeven_${stock_price:.2f}"

        return False, "hold"

    def _exit_naked_call(self, pos, current_val, tm, stock_price):
        """
        Naked call (sold premium).
        Take profit: option worth < 50% of premium collected
        Stop: stock rises above strike + premium (breakeven)
        Or: option worth > 2x premium collected
        """
        premium = pos.get("premium", 0)
        strike = 0
        if pos.get("contracts"):
            strike = pos["contracts"][0].get("strike", 0)

        if premium <= 0:
            return self._exit_generic(pos, current_val, tm, 0)

        if current_val <= premium * 0.50:
            return True, "naked_call_profit_50%"
        if current_val >= premium * 2.0:
            return True, "naked_call_stop_2x"
        if stock_price > 0 and strike > 0:
            breakeven = strike + (premium / 100)
            if stock_price > breakeven:
                return True, f"naked_call_breakeven_${stock_price:.2f}"

        return False, "hold"

    def _exit_strangle(self, pos, current_val, tm):
        """
        Short strangle (sold put + call).
        Take profit: total value < 50% of premium collected
        Stop: total value > 2x premium
        One leg deep ITM: close whole position
        """
        premium = pos.get("premium", 0)
        if premium <= 0:
            return self._exit_generic(pos, current_val, tm, 0)

        if current_val <= premium * 0.50:
            return True, "strangle_profit_50%"
        if current_val >= premium * 2.0:
            return True, "strangle_stop_2x"

        return False, "hold"

    def _exit_bwb(self, pos, current_val, tm, days):
        """Broken wing butterfly."""
        cost = pos["entry_cost"]
        pnl_pct = (current_val - cost) / cost
        if pnl_pct >= 0.40 * tm:
            return True, f"bwb_profit_{pnl_pct:+.0%}"
        if pnl_pct <= -0.50 * tm:
            return True, f"bwb_stop_{pnl_pct:+.0%}"
        if days >= 21:
            return True, f"bwb_max_hold"
        return False, "hold"

    def _exit_calendar(self, pos, current_val, tm, days):
        """Calendar spread."""
        cost = pos["entry_cost"]
        pnl_pct = (current_val - cost) / cost
        if pnl_pct >= 0.30 * tm:
            return True, f"cal_profit_{pnl_pct:+.0%}"
        if pnl_pct <= -0.40 * tm:
            return True, f"cal_stop_{pnl_pct:+.0%}"
        if days >= 10:
            return True, f"cal_max_hold"
        return False, "hold"

    def _exit_generic(self, pos, current_val, tm, days):
        """Fallback for unknown strategy types."""
        cost = pos["entry_cost"]
        pnl_pct = (current_val - cost) / cost
        if pnl_pct >= 0.50 * tm:
            return True, f"generic_profit_{pnl_pct:+.0%}"
        if pnl_pct <= -0.40 * tm:
            return True, f"generic_stop_{pnl_pct:+.0%}"
        if days >= 25:
            return True, f"generic_max_hold"
        return False, "hold"