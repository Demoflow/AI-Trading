"""
Exit Manager v2.0 — Dedicated exit logic for VWAP stock scalping.

Exit priority:
  1. VWAP break stop
  2. Hard price stop
  3. Second partial profit (SD2) — sell 50% of remaining (requires SD1 already taken)
  4. Partial profit (SD1) — sell 50%
  5. Breakeven stop (lock in after volatility-scaled threshold)
  6. Trailing stop (volatility-scaled trail, activation >= 1.5x trail distance)
  7. Time stop (time-of-day aware)
  8. EOD gate (3:30 PM CT hard close)

After SD1 + SD2 partials, the remaining 25% of the position rides with the
trailing stop only — allowing outsized gains on the best trades.

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
    TRAIL_DISTANCE = 0.0025    # 0.25% trail from peak (minimum — scaled up for volatile names)
    TRAIL_DISTANCE_MAX = 0.008 # 0.80% max trail (NVDA/TSLA range)
    BREAKEVEN_TRIGGER = 0.005  # 0.5% gain triggers breakeven protection
    BREAKEVEN_FLOOR = 0.0005   # Must stay above entry + 0.05% to avoid exit

    # Implied move per symbol — used to scale trailing stop with volatility.
    # Higher implied move = wider trail needed to avoid premature stops.
    _IMPLIED_MOVE = {
        "SPY": 0.0085, "QQQ": 0.011, "AAPL": 0.014, "MSFT": 0.012,
        "NVDA": 0.035, "META": 0.022, "AMZN": 0.018, "GOOGL": 0.015,
        "TSLA": 0.045, "TQQQ": 0.033, "SOXL": 0.050,
        "AMD": 0.030, "IWM": 0.015, "XLF": 0.012, "XLE": 0.015, "AVGO": 0.025,
    }

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

        # Volatility-scaled trailing stop: wider trail for volatile names
        implied_move = self._IMPLIED_MOVE.get(symbol, self.TRAIL_DISTANCE * 2)
        trail_distance = min(
            max(implied_move * 0.12, self.TRAIL_DISTANCE),
            self.TRAIL_DISTANCE_MAX,
        )
        # Trail activation must be at least 1.5× the trail distance so the stop
        # can never trigger at a net loss after first reaching the activation point.
        trail_activation = max(self.TRAIL_ACTIVATION, trail_distance * 1.5)

        # Breakeven trigger scales with volatility: tight names (SPY) stay at 0.5%,
        # volatile names (NVDA, TSLA) require a larger gain before locking breakeven.
        breakeven_trigger = max(self.BREAKEVEN_TRIGGER, implied_move * 0.25)

        # Calculate gain/loss percentage
        if direction == "LONG":
            gain_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0
            peak_gain_pct = (peak_price - entry_price) / entry_price if entry_price > 0 else 0
            from_peak_pct = (current_price - peak_price) / peak_price if peak_price > 0 else 0
        else:  # SHORT
            gain_pct = (entry_price - current_price) / entry_price if entry_price > 0 else 0
            peak_gain_pct = (entry_price - peak_price) / entry_price if entry_price > 0 else 0
            from_peak_pct = (peak_price - current_price) / peak_price if peak_price > 0 else 0

        partial_count = len(position.get("partial_exits", []))

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

        # ── 3. SECOND PARTIAL PROFIT (SD2) — sell 50% of remaining, trail the rest ──
        # Requires SD1 already taken (partial_count == 1). The final ~25% of the
        # original position then rides with the trailing stop for maximum upside.
        if partial_count == 1 and target_2 > 0 and shares > 1:
            if direction == "LONG" and current_price >= target_2:
                return True, f"target_2_partial(${target_2:.2f})", "HALF_EXIT"
            elif direction == "SHORT" and current_price <= target_2:
                return True, f"target_2_partial(${target_2:.2f})", "HALF_EXIT"

        # ── 4. PARTIAL PROFIT (SD1) — sell 50% ──
        if partial_count == 0 and target_1 > 0 and shares > 1:
            if direction == "LONG" and current_price >= target_1:
                return True, f"target_1(${target_1:.2f})", "HALF_EXIT"
            elif direction == "SHORT" and current_price <= target_1:
                return True, f"target_1(${target_1:.2f})", "HALF_EXIT"

        # ── 5. BREAKEVEN STOP ──
        # Trigger threshold scales with volatility: NVDA needs ~0.875% gain before
        # locking breakeven so normal noise doesn't knock it out too early.
        if peak_gain_pct >= breakeven_trigger:
            if gain_pct <= self.BREAKEVEN_FLOOR:
                return True, f"breakeven(peak={peak_gain_pct:+.2%})", "FULL_EXIT"

        # ── 6. TRAILING STOP ──
        # Activation is max(0.4%, trail_distance × 1.5) — guarantees the trail
        # can never trigger at a net loss after reaching the activation point.
        if gain_pct >= trail_activation:
            if from_peak_pct <= -trail_distance:
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
