"""
LETF Exit Manager.
Trailing stops, profit targets, time stops, regime change exits.
"""
from datetime import date, datetime
from loguru import logger


class LETFExitManager:

    def __init__(self, config):
        self.config = config

    def check_exit(self, position, current_price):
        """
        Check all exit conditions.
        Returns (should_exit, reason).
        """
        entry = position["entry_price"]
        peak = position.get("peak_price", entry)
        leverage = position.get("leverage", 3)
        days_held = 0

        try:
            ed = date.fromisoformat(position["entry_date"])
            days_held = (date.today() - ed).days
        except Exception:
            pass

        pnl_pct = (current_price - entry) / entry

        # Update peak
        if current_price > peak:
            position["peak_price"] = current_price
            peak = current_price

        # 1. PROFIT TARGET
        is_single = position.get("single_stock", False)
        if is_single:
            target = 0.08  # 8% target for single-stock (more volatile)
            trail = 0.025  # Tighter trail
        else:
            target = self.config["profit_target_3x"] if leverage == 3 else self.config["profit_target_2x"]
        if pnl_pct >= target:
            return True, f"profit_target_{pnl_pct:+.1%}"

        # 2. TRAILING STOP from peak
        trail = self.config["trailing_stop_3x"] if leverage == 3 else self.config["trailing_stop_2x"]
        # Tighten trail as profit grows
        if pnl_pct > target * 0.5:
            trail *= 0.7  # Tighter stop when well in profit

        drawdown = (current_price - peak) / peak
        if drawdown <= -trail and peak > entry:
            return True, f"trailing_stop_{drawdown:+.1%}_from_peak"

        # 3. STOP LOSS (hard stop, tighter for 3x)
        hard_stop = -0.04 if is_single else (-0.05 if leverage == 3 else -0.04)
        # Tighten stop after 3 days with no progress
        if days_held >= 3 and pnl_pct < 0.01:
            hard_stop *= 0.6  # Tighter stop
        if pnl_pct <= hard_stop:
            return True, f"stop_loss_{pnl_pct:+.1%}"

        # 3b. PARTIAL PROFIT: if up 6%+ on 3x, tighten trailing to 1.5%
        if leverage == 3 and pnl_pct >= 0.06:
            tight_trail = 0.015
            tight_dd = (current_price - peak) / peak
            if tight_dd <= -tight_trail:
                return True, f"tight_trail_{pnl_pct:+.1%}_dd_{tight_dd:+.1%}"

        # 4. TIME STOP
        max_hold = self.config["max_hold_days"]
        if days_held >= max_hold:
            return True, f"time_stop_{days_held}d"

        # 5. TIME DECAY WARNING: tighten after 5 days
        if days_held >= 5 and pnl_pct < 0.02:
            return True, f"time_decay_{days_held}d_flat"

        # 6. BREAKEVEN STOP: if was up 3%+ and now back to breakeven
        if peak > entry * 1.03 and current_price <= entry * 1.005:
            return True, f"breakeven_stop_was_{(peak-entry)/entry:+.1%}"

        return False, "hold"
