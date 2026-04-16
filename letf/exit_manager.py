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
        else:
            target = self.config["profit_target_3x"] if leverage == 3 else self.config["profit_target_2x"]
        if pnl_pct >= target:
            return True, f"profit_target_{pnl_pct:+.1%}"

        # 2. TRAILING STOP from peak
        if is_single:
            trail = 0.025  # Tighter trail for single-stock LETFs
        else:
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
        max_hold = position.get("max_hold_days") or self.config["max_hold_days"]
        if days_held >= max_hold:
            return True, f"time_stop_{days_held}d"

        # 5. TIME DECAY WARNING: tighten after 5 days
        if days_held >= 5 and pnl_pct < 0.02:
            return True, f"time_decay_{days_held}d_flat"

        # 6. BREAKEVEN STOP: if was up 3%+ and now back to breakeven
        if peak > entry * 1.03 and current_price <= entry * 1.005:
            return True, f"breakeven_stop_was_{(peak-entry)/entry:+.1%}"

        return False, "hold"

    def check_exit_with_timing(self, position, current_price, hour_ct=None):
        """
        check_exit() with time-of-day overlays.

        hour_ct: current Central Time as float (e.g. 14.5 = 2:30 PM CT).
                 Computed from wall clock if not supplied.

        Extra layers:
          1. Open protection  (8:30–8:45 CT) — suppress normal stops; only exit on catastrophic loss
          2. EOD tightening   (≥2:15 PM CT)  — take any profit ≥0.5%; cut losers ≥−1%
          3. Pre-EOD capture  (1:30–2:15 CT) — take ≥80% of profit target rather than risk reversal
        """
        if hour_ct is None:
            now = datetime.now()
            hour_ct = now.hour + now.minute / 60.0

        entry    = position["entry_price"]
        pnl_pct  = (current_price - entry) / entry
        leverage = position.get("leverage", 3)

        # 1. OPEN PROTECTION — first 15 min (8:30–8:45 CT)
        #    Opening noise triggers normal stops often; only exit on truly catastrophic loss.
        if hour_ct < 8.75:
            if pnl_pct > -0.08:
                return False, "open_protection"
            # Fall through: ≥8% loss at open is real; standard checks will fire.

        # 2. EOD TIGHTENING — last 45 min (≥2:15 PM CT)
        if hour_ct >= 14.25:
            if pnl_pct >= 0.005:
                return True, f"eod_take_profit_{pnl_pct:+.1%}"
            if pnl_pct <= -0.010:
                return True, f"eod_cut_loser_{pnl_pct:+.1%}"

        # 3. PRE-EOD PROFIT CAPTURE — 1:30–2:15 PM CT
        if 13.5 <= hour_ct < 14.25:
            is_single = position.get("single_stock", False)
            if is_single:
                target = 0.08
            else:
                target = (
                    self.config["profit_target_3x"] if leverage == 3
                    else self.config["profit_target_2x"]
                )
            if pnl_pct >= target * 0.80:
                return True, f"pre_eod_near_target_{pnl_pct:+.1%}"

        # 4. Standard price-based checks
        return self.check_exit(position, current_price)
