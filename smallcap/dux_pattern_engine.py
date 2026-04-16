"""
Dux Pattern Engine.

Detects four setups from 1-minute OHLCV candles, all derived from
Steven Dux's methodology (pure price action, no indicators):

  1. FRD  — First Red Day
     Prerequisite: stock ran 100%+ the prior session (latecomers trapped long).
     Signal: first candle that closes RED below the prior candle's open, with
     declining momentum.  Short below the red candle's low.
     Best window: 8:30–9:45 AM CT.

  2. SPIKE_SHORT — Intraday Parabolic Fade
     Stock spikes 15%+ from its open within the last 20 candles, then prints
     a large reversal bar at a resistance level (round number / prior high).
     Short below the reversal bar's low.
     Window: any time 8:30–10:30 AM CT.

  3. H_AND_S — Head & Shoulders Topping Pattern
     Classic H&S on the 1-min chart after a spike or FRD run.
     Short on break below the neckline.
     Window: 8:30–10:00 AM CT (needs time to play out before lunch).

  4. DIP_PANIC — Dip Panic Buy (LONG)
     After a 20%+ crash from HOD, a flush candle with massive range prints
     at/below VWAP, then the next candle recovers.  Buy the recovery.
     Window: 8:45–10:00 AM CT.

Each detected pattern returns a DuxPatternSignal.

Usage:
    pe = DuxPatternEngine(stream_manager)
    pe.start()
    pe.watch("NVAX")
    pe.set_candidate_meta("NVAX", {
        "prev_day_change_pct": 215.0,
        "prior_close": 8.50,
        "premarket_vol": 1_200_000,
        "float": 15_000_000,
    })
    signals = pe.get_signals("NVAX")   # list of DuxPatternSignal
    pe.stop()
"""

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from loguru import logger

from smallcap.dux_config import (
    FRD_MIN_PREV_DAY_MOVE,
    FRD_PRIME_WINDOW_END_CT,
    FRD_MIN_RED_BODY_RATIO,
    FRD_MIN_VOL_RATIO,
    SPIKE_MIN_INTRADAY_MOVE,
    SPIKE_MAX_CANDLES,
    SPIKE_RESISTANCE_WINDOW,
    SPIKE_MIN_REVERSAL_MULT,
    SPIKE_MIN_VOL_RATIO,
    HS_LOOKBACK_CANDLES,
    HS_SHOULDER_SYMMETRY,
    HS_MAX_NECKLINE_SLOPE,
    HS_PRIME_WINDOW_END_CT,
    DIP_MIN_DRAWDOWN_FROM_HOD,
    DIP_FLUSH_RANGE_MULT,
    DIP_FLUSH_CLOSE_RATIO,
    DIP_MIN_BELOW_VWAP,
    DIP_RECOVERY_VOL_RATIO,
    DUX_SIGNAL_EXPIRY_MIN,
    DUX_MIN_PREV_DAY_MOVE,
    DUX_MIN_PREMARKET_VOL,
    DUX_MIN_PRICE,
)

# Scan interval for the background thread (seconds)
_SCAN_INTERVAL_SEC = 5

# Minimum candles required before any pattern scan runs
_MIN_CANDLES = 3

# Lookback for average candle range calculations
_AVG_RANGE_LOOKBACK = 10


@dataclass
class DuxPatternSignal:
    symbol:    str
    pattern:   str          # "FRD" | "SPIKE_SHORT" | "H_AND_S" | "DIP_PANIC"
    direction: str          # "SHORT" | "LONG"
    entry:     float        # limit price for entry
    stop:      float        # initial stop price
    target1:   float        # first exit target (VWAP / 50% retrace / etc.)
    target2:   float        # second exit target
    strength:  int          # 0–100 quality score
    ts:        datetime
    metadata:  dict = field(default_factory=dict, repr=False)

    @property
    def risk(self) -> float:
        return abs(self.entry - self.stop)

    @property
    def rr1(self) -> float:
        reward = abs(self.target1 - self.entry)
        return reward / self.risk if self.risk > 0 else 0.0


class DuxPatternEngine:
    """
    Detects Dux setups from live 1-minute candles via StreamManager.
    Runs in a background daemon thread; all state is thread-safe.
    """

    def __init__(self, stream_manager):
        self._stream     = stream_manager
        self._watch:     set[str]              = set()
        self._signals:   dict[str, list[DuxPatternSignal]] = {}
        # Per-symbol metadata: prev_day_change_pct, prior_close, premarket_vol, float
        self._meta:      dict[str, dict]       = {}
        self._lock       = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None  = None

    # ── PUBLIC API ─────────────────────────────────────────────────────────────

    def start(self):
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="DuxPatternEngine"
        )
        self._thread.start()
        logger.info("DuxPatternEngine started")

    def stop(self):
        self._stop_event.set()

    def watch(self, symbol: str):
        sym = symbol.upper()
        with self._lock:
            self._watch.add(sym)
            if sym not in self._signals:
                self._signals[sym] = []

    def unwatch(self, symbol: str):
        sym = symbol.upper()
        with self._lock:
            self._watch.discard(sym)

    def set_candidate_meta(self, symbol: str, meta: dict):
        """
        Provide per-symbol data needed for pattern filtering.

        Expected keys:
          prev_day_change_pct  (float) — prior full-day % change (for FRD gate)
          prior_close          (float) — prior session close (FRD T2 target)
          premarket_vol        (int)   — pre-market share volume (Dux universe filter)
          float                (int)   — share float (Dux universe filter)
        """
        sym = symbol.upper()
        with self._lock:
            self._meta[sym] = meta

    def get_signals(self, symbol: str) -> list["DuxPatternSignal"]:
        """Return active (non-expired) signals for symbol."""
        sym  = symbol.upper()
        now  = _utcnow()
        expiry_sec = DUX_SIGNAL_EXPIRY_MIN * 60
        with self._lock:
            sigs   = self._signals.get(sym, [])
            active = [s for s in sigs
                      if (now - s.ts).total_seconds() < expiry_sec]
            self._signals[sym] = active
            return list(active)

    def get_all_signals(self) -> dict[str, list["DuxPatternSignal"]]:
        result = {}
        with self._lock:
            syms = list(self._watch)
        for sym in syms:
            sigs = self.get_signals(sym)
            if sigs:
                result[sym] = sigs
        return result

    # ── BACKGROUND LOOP ────────────────────────────────────────────────────────

    def _run(self):
        while not self._stop_event.is_set():
            with self._lock:
                symbols = list(self._watch)
            for sym in symbols:
                try:
                    self._scan(sym)
                except Exception as e:
                    logger.debug(f"DuxPatternEngine scan error for {sym}: {e}")
            self._stop_event.wait(_SCAN_INTERVAL_SEC)

    def _scan(self, sym: str):
        candles = self._stream.get_candles(sym)
        if len(candles) < _MIN_CANDLES:
            return

        with self._lock:
            meta = self._meta.get(sym, {})

        # ── Dux universe gate ──────────────────────────────────────────────────
        # Skip symbols that don't meet Dux's basic criteria.
        # (These are secondary checks — gap scanner already filtered most of these.)
        price  = candles[-1]["close"]
        pm_vol = meta.get("premarket_vol", 0)

        if price < DUX_MIN_PRICE:
            return
        if pm_vol > 0 and pm_vol < DUX_MIN_PREMARKET_VOL:
            return

        # ── Time of day (CT) ──────────────────────────────────────────────────
        now_ct = _hour_ct()

        # Compute shared analytics used by multiple detectors
        vwap       = _calc_vwap(candles)
        hod        = _calc_hod(candles)
        avg_range  = _avg_candle_range(candles, _AVG_RANGE_LOOKBACK)

        found: list[DuxPatternSignal] = []

        # ── FRD ───────────────────────────────────────────────────────────────
        if now_ct <= FRD_PRIME_WINDOW_END_CT:
            prev_day_pct = meta.get("prev_day_change_pct", 0)
            prior_close  = meta.get("prior_close", 0)
            if prev_day_pct >= FRD_MIN_PREV_DAY_MOVE:
                sig = _detect_frd(sym, candles, vwap, hod, avg_range,
                                  prior_close, prev_day_pct)
                if sig:
                    found.append(sig)

        # ── Spike Short ───────────────────────────────────────────────────────
        prior_close_spike = meta.get("prior_close", 0)
        sig = _detect_spike_short(sym, candles, avg_range, prior_close_spike)
        if sig:
            found.append(sig)

        # ── Head & Shoulders ──────────────────────────────────────────────────
        if now_ct <= HS_PRIME_WINDOW_END_CT:
            sig = _detect_h_and_s(sym, candles)
            if sig:
                found.append(sig)

        # ── Dip Panic Buy ────────────────────────────────────────────────────
        sig = _detect_dip_panic(sym, candles, vwap, hod, avg_range)
        if sig:
            found.append(sig)

        if not found:
            return

        now = _utcnow()
        # Deduplication window matches signal expiry: a pattern at a given entry
        # level fires at most once per expiry period.  Using 60s previously caused
        # the same candle structure to emit 3 signals over a 3-minute window,
        # flooding the log with redundant "entry denied" messages.
        _dedup_sec = DUX_SIGNAL_EXPIRY_MIN * 60
        with self._lock:
            existing = self._signals.get(sym, [])
            for sig in found:
                # Deduplicate: same pattern + same entry level within expiry window
                duplicate = any(
                    s.pattern == sig.pattern
                    and abs(s.entry - sig.entry) < 0.02
                    and (now - s.ts).total_seconds() < _dedup_sec
                    for s in existing
                )
                if not duplicate:
                    existing.append(sig)
                    logger.info(
                        f"[Dux] PATTERN [{sym}] {sig.pattern} {sig.direction} | "
                        f"entry=${sig.entry:.2f} stop=${sig.stop:.2f} "
                        f"t1=${sig.target1:.2f} strength={sig.strength}"
                    )
            self._signals[sym] = existing


# ── PATTERN DETECTORS ─────────────────────────────────────────────────────────

def _detect_frd(
    sym:          str,
    candles:      list[dict],
    vwap:         float,
    hod:          float,
    avg_range:    float,
    prior_close:  float,
    prev_day_pct: float,
) -> DuxPatternSignal | None:
    """
    First Red Day: prior session ran hard → today's first candle to close
    below the prior candle's open = "the run is reversing".

    Structure: [...GREEN candles...][RED candle ← signal bar]
    Entry: SHORT below the red candle's low.
    Stop:  Above HOD (capped at entry × 1.03 if HOD is distant).
    T1:    VWAP  (first magnet for fading longs)
    T2:    prior_close  (where the trapped longs bought in)
    """
    if len(candles) < 2:
        return None

    red  = candles[-1]   # potential signal bar
    prev = candles[-2]   # prior candle

    # Signal bar must close RED
    if red["close"] >= red["open"]:
        return None

    # Prior bar must have been GREEN (momentum was still up)
    if prev["close"] <= prev["open"]:
        return None

    # Red candle's close must be below the prior candle's open
    # (price gave back the prior bar's entire body — genuine reversal, not noise)
    if red["close"] >= prev["open"]:
        return None

    # Red body must be significant relative to the prior bar's range
    red_body      = abs(red["open"] - red["close"])
    prior_range   = prev["high"] - prev["low"]
    if prior_range > 0 and red_body < prior_range * FRD_MIN_RED_BODY_RATIO:
        return None

    # Volume confirmation: red candle has enough participation
    recent_vols = [c["volume"] for c in candles[-6:-1] if c["volume"] > 0]
    if recent_vols:
        avg_vol = sum(recent_vols) / len(recent_vols)
        if avg_vol > 0 and red["volume"] < avg_vol * FRD_MIN_VOL_RATIO:
            return None

    entry = round(red["low"] - 0.02, 2)
    if entry <= 0:
        return None

    # Stop: above HOD, capped at 3% above entry
    raw_stop = round(hod + 0.02, 2)
    cap_stop = round(entry * 1.03, 2)
    stop     = min(raw_stop, cap_stop)

    # Targets: VWAP then prior_close (if below current price)
    if vwap > 0 and vwap < entry:
        t1 = round(vwap, 2)
    else:
        # VWAP unavailable or above entry — use 50% retrace from entry to HOD
        t1 = round(entry - (hod - entry) * 0.50, 2) if hod > entry else round(entry * 0.95, 2)

    if prior_close > 0 and prior_close < entry:
        t2 = round(prior_close, 2)
    else:
        t2 = round(entry - (entry - t1) * 2.0, 2)

    t1 = max(t1, 0.01)
    t2 = max(t2, 0.01)

    # R:R gate: T1 must provide at least 1.5:1 (real target, not forced)
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    rr1 = abs(entry - t1) / risk
    if rr1 < 2.0:
        return None

    # ── Strength score ─────────────────────────────────────────────────────
    # Prior day move magnitude (0-30)
    if prev_day_pct >= 200:
        s_move = 30
    elif prev_day_pct >= 150:
        s_move = 20
    elif prev_day_pct >= 100:
        s_move = 10
    else:
        s_move = 5

    # Red candle body vs prior range (0-25)
    s_body = min(25, int(red_body / max(prior_range, 0.01) * 50)) if prior_range > 0 else 0

    # Volume (0-20): red vol vs 5-bar avg
    if recent_vols:
        avg_vol = sum(recent_vols) / len(recent_vols)
        vol_ratio = red["volume"] / avg_vol if avg_vol > 0 else 1.0
        s_vol = min(20, int((vol_ratio - 0.8) / 1.2 * 20))
    else:
        s_vol = 5

    # Extension above VWAP (0-15): more extended = more to fall
    if vwap > 0 and entry > vwap:
        ext_pct = (entry - vwap) / vwap
        s_ext = min(15, int(ext_pct * 150))
    else:
        s_ext = 0

    # Clean structure (0-10): no whipsaw in prior 3 bars
    last_3 = candles[-4:-1] if len(candles) >= 4 else candles[:-1]
    highs_close = all(c["high"] < hod * 1.01 for c in last_3)
    s_struct = 10 if highs_close else 0

    strength = max(0, min(100, s_move + s_body + s_vol + s_ext + s_struct))

    return DuxPatternSignal(
        symbol=sym, pattern="FRD", direction="SHORT",
        entry=entry, stop=stop, target1=t1, target2=t2,
        strength=strength, ts=_utcnow(),
        metadata={
            "prev_day_pct": prev_day_pct,
            "hod":          round(hod, 2),
            "vwap":         round(vwap, 2) if vwap else 0,
            "prior_close":  round(prior_close, 2),
        },
    )


def _detect_spike_short(
    sym:         str,
    candles:     list[dict],
    avg_range:   float,
    prior_close: float,
) -> DuxPatternSignal | None:
    """
    Spike Short: intraday move of 15%+ from the session open within the last
    20 candles, then a large reversal bar at a resistance level.

    Entry: SHORT below the reversal bar's low.
    Stop:  Above the reversal bar's high.
    T1:    50% retrace of the spike from entry.
    T2:    Base of the spike (where the parabolic move started).
    """
    if len(candles) < 6:
        return None

    # Session open = first candle's open
    session_open = candles[0]["open"]
    if session_open <= 0:
        return None

    current = candles[-1]
    prev    = candles[-2]

    # Current must be a reversal: closes RED, close < prior bar's open
    if current["close"] >= current["open"]:
        return None
    if current["close"] >= prev["open"]:
        return None

    # Find the spike high (highest close in the last SPIKE_MAX_CANDLES)
    spike_window = candles[-SPIKE_MAX_CANDLES:] if len(candles) >= SPIKE_MAX_CANDLES else candles
    spike_high = max(c["high"] for c in spike_window)
    # Spike base: lowest low in the first third of the spike window, representing
    # where the parabolic move originated before momentum accelerated.
    first_third  = spike_window[:max(1, len(spike_window) // 3)]
    spike_base   = min(c["low"] for c in first_third)

    # Intraday move from session open to spike high must be >= threshold
    intraday_move = (spike_high - session_open) / session_open
    if intraday_move < SPIKE_MIN_INTRADAY_MOVE:
        return None

    # Reversal bar must be large (>= 1.5× avg range)
    reversal_range = current["high"] - current["low"]
    if avg_range > 0 and reversal_range < avg_range * SPIKE_MIN_REVERSAL_MULT:
        return None

    # Volume on reversal bar must be elevated
    recent_vols = [c["volume"] for c in candles[-6:-1] if c["volume"] > 0]
    if recent_vols:
        avg_vol = sum(recent_vols) / len(recent_vols)
        if avg_vol > 0 and current["volume"] < avg_vol * SPIKE_MIN_VOL_RATIO:
            return None

    # Resistance confluence: reversal bar high near a key resistance level
    price           = current["high"]
    round_5         = round(price / 5) * 5
    round_1         = round(price)
    resistance_levels = [r for r in (round_5, round_1, prior_close, spike_high)
                         if r > 0]
    at_resistance = any(
        abs(price - r) / r <= SPIKE_RESISTANCE_WINDOW
        for r in resistance_levels
    )
    # Award resistance confluence in scoring but don't hard-block on it
    # (the reversal bar itself is the primary signal)

    entry = round(current["low"] - 0.02, 2)
    if entry <= 0:
        return None

    stop = round(current["high"] + 0.02, 2)

    # T1: 50% retrace of the spike from entry toward the base
    t1 = round(entry - (spike_high - spike_base) * 0.50, 2)
    t1 = max(t1, 0.01)

    # T2: base of the spike
    t2 = round(spike_base - 0.02, 2)
    t2 = max(t2, 0.01)

    # R:R gate
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    rr1 = abs(entry - t1) / risk
    if rr1 < 2.0:
        return None

    # ── Strength score ─────────────────────────────────────────────────────
    # Speed of spike (faster = more overextended) (0-30)
    speed = len(spike_window)  # fewer candles = faster move
    s_speed = max(0, min(30, int((SPIKE_MAX_CANDLES - speed) / SPIKE_MAX_CANDLES * 30)))

    # Resistance confluence (0-25)
    s_res = 25 if at_resistance else 0

    # Reversal candle size (0-25)
    if avg_range > 0:
        s_range = min(25, int((reversal_range / avg_range - 1) * 15))
    else:
        s_range = 10

    # Volume (0-20)
    if recent_vols:
        avg_vol = sum(recent_vols) / len(recent_vols)
        vol_ratio = current["volume"] / avg_vol if avg_vol > 0 else 1.0
        s_vol = min(20, int((vol_ratio - 1) / 2 * 20))
    else:
        s_vol = 5

    strength = max(0, min(100, s_speed + s_res + s_range + s_vol))

    return DuxPatternSignal(
        symbol=sym, pattern="SPIKE_SHORT", direction="SHORT",
        entry=entry, stop=stop, target1=t1, target2=t2,
        strength=strength, ts=_utcnow(),
        metadata={
            "intraday_move_pct": round(intraday_move * 100, 1),
            "spike_high":        round(spike_high, 2),
            "spike_base":        round(spike_base, 2),
            "at_resistance":     at_resistance,
        },
    )


def _detect_h_and_s(
    sym:     str,
    candles: list[dict],
) -> DuxPatternSignal | None:
    """
    Head & Shoulders: topping pattern on the 1-min chart.

    Algorithm:
      1. Find the highest local high in the lookback window = HEAD.
      2. Find the last local high before the head = LEFT SHOULDER.
      3. Find the first local high after the head = RIGHT SHOULDER.
      4. Shoulders must both be below the head, and within 15% of each other.
      5. Neckline connects the trough between LS-Head and the trough between Head-RS.
      6. Signal fires when the most recent candle closes below the neckline.

    Entry: SHORT at neckline - 0.02.
    Stop:  Above right shoulder high.
    T1:    Neckline − (head − neckline)  [measured move projection]
    T2:    T1 × 1.5
    """
    window = candles[-HS_LOOKBACK_CANDLES:] if len(candles) >= HS_LOOKBACK_CANDLES else candles
    n = len(window)
    if n < 10:
        return None

    # Find all local highs (higher than both neighbours)
    local_highs = []  # (index_in_window, high_price)
    for i in range(1, n - 1):
        if window[i]["high"] > window[i-1]["high"] and window[i]["high"] > window[i+1]["high"]:
            local_highs.append((i, window[i]["high"]))

    if len(local_highs) < 3:
        return None

    # Head: highest of all local highs
    head_idx, head_high = max(local_highs, key=lambda x: x[1])

    # Left shoulder: last local high before head with lower high
    ls_candidates = [(i, h) for i, h in local_highs if i < head_idx and h < head_high]
    if not ls_candidates:
        return None
    ls_idx, ls_high = max(ls_candidates, key=lambda x: x[0])  # most recent before head

    # Right shoulder: first local high after head with lower high
    rs_candidates = [(i, h) for i, h in local_highs if i > head_idx and h < head_high]
    if not rs_candidates:
        return None
    rs_idx, rs_high = min(rs_candidates, key=lambda x: x[0])  # earliest after head

    # Shoulders must be within SYMMETRY range of each other
    symmetry = abs(ls_high - rs_high) / ls_high if ls_high > 0 else 1.0
    if symmetry > HS_SHOULDER_SYMMETRY:
        return None

    # Neckline: trough between LS and Head (trough_l) and between Head and RS (trough_r)
    # These are the lows of the candles in those spans.
    trough_l = min(window[ls_idx:head_idx+1], key=lambda c: c["low"])["low"]
    trough_r = min(window[head_idx:rs_idx+1],  key=lambda c: c["low"])["low"]

    neckline = (trough_l + trough_r) / 2

    # Neckline slope check: the two trough prices must be close to each other.
    # Slope = relative difference between the two neckline anchor points.
    neckline_mid = (trough_l + trough_r) / 2
    if neckline_mid <= 0:
        return None
    slope_pct = abs(trough_r - trough_l) / neckline_mid
    if slope_pct > HS_MAX_NECKLINE_SLOPE:
        return None

    # Signal: current candle closes below neckline (break confirmation)
    current = candles[-1]
    if current["close"] >= neckline:
        return None

    # Check right shoulder is still "recent" (RS must be in the last 15 candles)
    # rs_idx is relative to window; window is the last HS_LOOKBACK_CANDLES candles
    rs_age = (n - 1) - rs_idx
    if rs_age > 15:
        return None

    entry = round(neckline - 0.02, 2)
    if entry <= 0:
        return None

    stop = round(rs_high + 0.02, 2)

    # Measured move: project head-to-neckline distance downward from neckline
    head_to_neckline = head_high - neckline
    t1 = round(neckline - head_to_neckline, 2)
    t2 = round(neckline - head_to_neckline * 1.5, 2)
    t1 = max(t1, 0.01)
    t2 = max(t2, 0.01)

    # R:R gate
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    rr1 = abs(entry - t1) / risk
    if rr1 < 2.0:
        return None

    # ── Strength score ─────────────────────────────────────────────────────
    # Shoulder symmetry (0-30): more symmetric = higher score
    s_sym = max(0, min(30, int((1 - symmetry / HS_SHOULDER_SYMMETRY) * 30)))

    # Head height vs shoulders (0-25): taller head = cleaner pattern
    head_vs_ls = (head_high - ls_high) / ls_high if ls_high > 0 else 0
    s_head = min(25, int(head_vs_ls * 100))

    # Neckline flatness (0-25): slope_pct now 0→HS_MAX_NECKLINE_SLOPE → 1→0 score
    s_flat = max(0, min(25, int((1 - slope_pct / HS_MAX_NECKLINE_SLOPE) * 25)))

    # Neckline break candle volume vs avg (0-20)
    recent_vols = [c["volume"] for c in candles[-6:-1] if c["volume"] > 0]
    if recent_vols:
        avg_vol = sum(recent_vols) / len(recent_vols)
        vol_ratio = current["volume"] / avg_vol if avg_vol > 0 else 1.0
        s_vol = min(20, int((vol_ratio - 1.0) / 1.2 * 20))
    else:
        s_vol = 5

    strength = max(0, min(100, s_sym + s_head + s_flat + s_vol))

    return DuxPatternSignal(
        symbol=sym, pattern="H_AND_S", direction="SHORT",
        entry=entry, stop=stop, target1=t1, target2=t2,
        strength=strength, ts=_utcnow(),
        metadata={
            "head_high":   round(head_high, 2),
            "ls_high":     round(ls_high, 2),
            "rs_high":     round(rs_high, 2),
            "neckline":    round(neckline, 2),
            "symmetry":    round(symmetry, 3),
        },
    )


def _detect_dip_panic(
    sym:       str,
    candles:   list[dict],
    vwap:      float,
    hod:       float,
    avg_range: float,
) -> DuxPatternSignal | None:
    """
    Dip Panic Buy: stock crashes 20%+ from HOD into a flush candle
    (huge bearish bar with close at the low), then the next candle
    recovers → buy the bounce to VWAP.

    Entry: LONG above the recovery candle's close.
    Stop:  Below the flush candle's low.
    T1:    VWAP
    T2:    50% retrace of the flush move
    """
    if len(candles) < 4:
        return None

    current = candles[-1]   # recovery bar
    flush   = candles[-2]   # potential flush bar

    # Current price must be down significantly from HOD
    if hod <= 0:
        return None
    drawdown = (hod - current["close"]) / hod
    if drawdown < DIP_MIN_DRAWDOWN_FROM_HOD:
        return None

    # Flush bar: large range, closes near its low
    flush_range = flush["high"] - flush["low"]
    if avg_range <= 0 or flush_range < avg_range * DIP_FLUSH_RANGE_MULT:
        return None
    # Flush closes in the bottom DIP_FLUSH_CLOSE_RATIO of its range
    if flush_range > 0:
        close_position = (flush["close"] - flush["low"]) / flush_range
        if close_position > DIP_FLUSH_CLOSE_RATIO:
            return None

    # Price must be significantly below VWAP (oversold relative to session value)
    if vwap <= 0 or current["close"] >= vwap * (1 - DIP_MIN_BELOW_VWAP):
        return None

    # Recovery candle: closes ABOVE the flush candle's close (first sign of buyers)
    if current["close"] <= flush["close"]:
        return None

    # Recovery volume confirmation
    if flush["volume"] > 0 and current["volume"] < flush["volume"] * DIP_RECOVERY_VOL_RATIO:
        return None

    entry = round(current["close"] + 0.02, 2)
    stop  = round(flush["low"] - 0.02, 2)
    if stop <= 0 or entry <= stop:
        return None

    # Targets: VWAP, then 50% retrace of the flush
    if vwap > 0 and vwap > entry:
        t1 = round(vwap, 2)
    else:
        t1 = round(entry + (entry - stop) * 2.0, 2)   # fallback: 2:1 R:R

    flush_mid = (flush["high"] + flush["low"]) / 2
    t2 = round(flush_mid, 2) if flush_mid > t1 else round(t1 + (t1 - entry), 2)
    t2 = max(t2, t1 + 0.01)

    # R:R gate
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    rr1 = abs(t1 - entry) / risk
    if rr1 < 2.0:
        return None

    # ── Strength score ─────────────────────────────────────────────────────
    # Drawdown depth (0-35): deeper = more oversold
    s_draw = min(35, int(drawdown * 120))

    # Flush candle size vs avg (0-30)
    if avg_range > 0:
        s_flush = min(30, int((flush_range / avg_range - DIP_FLUSH_RANGE_MULT) / 3 * 30))
    else:
        s_flush = 10

    # Distance below VWAP (0-20)
    if vwap > 0:
        vwap_gap = (vwap - current["close"]) / vwap
        s_vwap = min(20, int(vwap_gap * 200))
    else:
        s_vwap = 0

    # Recovery volume vs flush (0-15)
    if flush["volume"] > 0:
        rec_ratio = current["volume"] / flush["volume"]
        s_recvol = min(15, int(rec_ratio * 10))
    else:
        s_recvol = 5

    strength = max(0, min(100, s_draw + s_flush + s_vwap + s_recvol))

    return DuxPatternSignal(
        symbol=sym, pattern="DIP_PANIC", direction="LONG",
        entry=entry, stop=stop, target1=t1, target2=t2,
        strength=strength, ts=_utcnow(),
        metadata={
            "drawdown_pct": round(drawdown * 100, 1),
            "hod":          round(hod, 2),
            "flush_low":    round(flush["low"], 2),
            "vwap":         round(vwap, 2) if vwap else 0,
        },
    )


# ── ANALYTICS HELPERS ──────────────────────────────────────────────────────────

def _calc_vwap(candles: list[dict]) -> float:
    """
    Compute VWAP from a list of 1-minute candles.
    VWAP = Σ(typical_price × volume) / Σ(volume)
    typical_price = (high + low + close) / 3
    Returns 0.0 if no volume data available.
    """
    total_tpv = 0.0
    total_vol = 0.0
    for c in candles:
        vol = c.get("volume") or 0
        if vol <= 0:
            continue
        tp = (c["high"] + c["low"] + c["close"]) / 3.0
        total_tpv += tp * vol
        total_vol  += vol
    return total_tpv / total_vol if total_vol > 0 else 0.0


def _calc_hod(candles: list[dict]) -> float:
    """High of day across all available candles."""
    if not candles:
        return 0.0
    return max(c["high"] for c in candles)


def _avg_candle_range(candles: list[dict], n: int) -> float:
    """Average (high - low) range over the last n candles."""
    recent = candles[-n:] if len(candles) >= n else candles
    if not recent:
        return 0.0
    return sum(c["high"] - c["low"] for c in recent) / len(recent)


def _hour_ct() -> float:
    """Current time as decimal CT hours (relies on system clock being in CT)."""
    now = datetime.now()
    return now.hour + now.minute / 60.0 + now.second / 3600.0


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)
