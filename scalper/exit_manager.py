"""
Exit Manager v1.0 — Dedicated exit logic for VWAP stock scalping.

Exit priority:
  1. VWAP break stop
  2. Hard price stop
  3. Full profit target (SD2)
  4. Partial profit (SD1) — sell 50%
  5. Breakeven stop (lock in after 0.5% move)
  6. Trailing stop (0.25% trail after 0.4% gain)
  7. Time stop (time-of-day aware)
  8. EOD gate (3:30 PM CT hard close)

All times in CT.
"""

from datetime import datetime
from loguru import logger

try:
    from zoneinfo import ZoneInfo
    _CT_TZ = ZoneInfo("America/Chicago")
except ImportError:
    _CT_TZ = None


def _now_ct():
    return datetime.now(tz=_CT_TZ) if _CT_TZ else datetime.now()


def _hour_ct() -> float:
    n = _now_ct()
    return n.hour + n.minute / 60.0 + n.second / 3600.0


class ExitManager:
    """
    Manages exit decisions for open stock positions.
    Separated from risk manager for clarity and testability.
    """

    EOD_CUTOFF = 15.5          # 3:30 PM CT — hard close
    TRAIL_ACTIVATION = 0.004   # 0.4% gain activates trailing stop
    TRAIL_DISTANCE = 0.0025    # 0.25% trail from peak
    BREAKEVEN_TRIGGER = 0.005  # 0.5% gain triggers breakeven protection
    BREAKEVEN_FLOOR = 0.0005   # Must stay above entry + 0.05% to avoid exit

    def check_exit(self, position, current_price, vwap_engine=None):
        """
        Check if a position should be exited.

        Args:
            position: position dict from executor
            current_price: latest price
            vwap_engine: VWAPEngine instance (optional, for VWAP break detection)

        Returns:
            (should_exit: bool, reason: str, action: str or None)
            action: "FULL_EXIT", "HALF_EXIT", or None
        """
        if position.get("status") != "OPEN":
            return False, "not_open", None

        symbol = position["symbol"]
        direction = position["direction"]
        entry_price = position["entry_price"]
        stop_price = position["stop_price"]
        target_1 = position.get("target_1", 0)
        target_2 = position.get("target_2", 0)
        peak_price = position.get("peak_price", entry_price)
        shares = position.get("shares", 0)

        # Calculate gain/loss percentage
        if direction == "LONG":
            gain_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0
            peak_gain_pct = (peak_price - entry_price) / entry_price if entry_price > 0 else 0
            from_peak_pct = (current_price - peak_price) / peak_price if peak_price > 0 else 0
        else:  # SHORT
            gain_pct = (entry_price - current_price) / entry_price if entry_price > 0 else 0
            peak_gain_pct = (entry_price - peak_price) / entry_price if entry_price > 0 else 0
            from_peak_pct = (peak_price - current_price) / peak_price if peak_price > 0 else 0

        # ── 1. VWAP BREAK STOP ──
        if vwap_engine:
            vwap = vwap_engine.get_vwap(symbol)
            if vwap > 0:
                # Get stop distance from VWAP
                vwap_stop_dist = abs(entry_price - stop_price)
                if direction == "LONG" and current_price < vwap - vwap_stop_dist:
                    return True, f"vwap_break(${vwap:.2f})", "FULL_EXIT"
                elif direction == "SHORT" and current_price > vwap + vwap_stop_dist:
                    return True, f"vwap_break(${vwap:.2f})", "FULL_EXIT"

        # ── 2. HARD PRICE STOP ──
        if direction == "LONG" and current_price <= stop_price:
            return True, f"hard_stop(${stop_price:.2f})", "FULL_EXIT"
        elif direction == "SHORT" and current_price >= stop_price:
            return True, f"hard_stop(${stop_price:.2f})", "FULL_EXIT"

        # ── 3. FULL PROFIT TARGET (SD2) ──
        if target_2 > 0:
            if direction == "LONG" and current_price >= target_2:
                return True, f"target_2(${target_2:.2f})", "FULL_EXIT"
            elif direction == "SHORT" and current_price <= target_2:
                return True, f"target_2(${target_2:.2f})", "FULL_EXIT"

        # ── 4. PARTIAL PROFIT (SD1) — sell 50% ──
        has_partial = len(position.get("partial_exits", [])) > 0
        if not has_partial and target_1 > 0 and shares > 1:
            if direction == "LONG" and current_price >= target_1:
                return True, f"target_1(${target_1:.2f})", "HALF_EXIT"
            elif direction == "SHORT" and current_price <= target_1:
                return True, f"target_1(${target_1:.2f})", "HALF_EXIT"

        # ── 5. BREAKEVEN STOP ──
        # Once peak gain >= 0.5%, don't let position fall back below entry + 0.05%
        if peak_gain_pct >= self.BREAKEVEN_TRIGGER:
            if gain_pct <= self.BREAKEVEN_FLOOR:
                return True, f"breakeven(peak={peak_gain_pct:+.2%})", "FULL_EXIT"

        # ── 6. TRAILING STOP ──
        # Once gain >= 0.4%, trail with 0.25% from peak
        if gain_pct >= self.TRAIL_ACTIVATION:
            if from_peak_pct <= -self.TRAIL_DISTANCE:
                return True, f"trail({from_peak_pct:+.2%}_from_peak)", "FULL_EXIT"

        # ── 7. TIME STOP ──
        entry_time = position.get("entry_time")
        if entry_time:
            max_hold = self._get_max_hold_minutes()
            if isinstance(entry_time, str):
                entry_time = datetime.fromisoformat(entry_time)
            now = _now_ct()
            if entry_time.tzinfo is None and now.tzinfo is not None:
                entry_time = entry_time.replace(tzinfo=_CT_TZ)
            mins_held = (now - entry_time).total_seconds() / 60
            if mins_held >= max_hold:
                return True, f"time_stop({mins_held:.0f}min/{max_hold}min)", "FULL_EXIT"

        # ── 8. EOD GATE ──
        h = _hour_ct()
        if h >= self.EOD_CUTOFF:
            return True, "eod_gate(3:30PM)", "FULL_EXIT"

        return False, "hold", None

    def _get_max_hold_minutes(self):
        """Time-of-day aware maximum hold duration."""
        h = _hour_ct()
        if h < 11.5:
            return 30   # Morning: 30 min max
        elif h < 14.0:
            return 20   # Lunch/early afternoon: 20 min max
        else:
            return 15   # Late afternoon: 15 min max

    def check_eod_flatten(self):
        """Check if it's time for EOD forced flatten."""
        return _hour_ct() >= self.EOD_CUTOFF
