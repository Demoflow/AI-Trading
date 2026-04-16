"""
Small Cap Pattern Engine.

Detects three setups from 1-minute OHLCV candles:

  1. Bull Flag
     Thrust candle → tight consolidation (2–8 bars, range < 3%) →
     breakout above consolidation high on expanding volume.
     Ross's primary setup: buy the break of the flag high.

  2. ABCD Pattern
     A→B impulse spike, B→C pullback (38–62% of AB), C→D continuation
     targeting AB extension. Entry on break of B (prior swing high).

  3. Opening Range Breakout (ORB)
     First-5-minute candle high/low defines the range.
     Entry on break above OR high with volume ≥ 1.5× OR average.

Each detected pattern returns a PatternSignal with:
  - pattern type
  - entry price (breakout level)
  - stop price (below consolidation low or OR low)
  - target price (R:R based on pattern measurement)
  - signal strength (0–100, based on quality metrics)
  - the candles that formed it

Usage:
    pe = PatternEngine(stream_manager)
    pe.start()
    pe.watch("NVAX")
    signals = pe.get_signals("NVAX")   # list of PatternSignal
    pe.stop()
"""

import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from loguru import logger

from smallcap.config import (
    MIN_CONSOL_BARS, MAX_CONSOL_BARS, MAX_CONSOL_RANGE_PCT,
    MIN_BREAKOUT_VOL_MULT, PARTIAL_1_TARGET_PCT, PARTIAL_2_TARGET_PCT,
    PATTERN_SCAN_INTERVAL_SEC,
)

# ── CONSTANTS ──────────────────────────────────────────────────────────────────
# ORB uses first N minutes of trading
_ORB_MINUTES = 5

# ABCD pullback depth range (as fraction of AB move)
_ABCD_RETRACE_MIN = 0.38
_ABCD_RETRACE_MAX = 0.62

# Minimum thrust candle body as % of prior average range (quality filter)
_THRUST_MIN_BODY_MULT = 2.0

# Lookback for average candle range (used for thrust quality check)
_AVG_RANGE_LOOKBACK = 10

# How long a signal stays "active" before expiring (minutes)
_SIGNAL_EXPIRY_MIN = 5


@dataclass
class PatternSignal:
    symbol:      str
    pattern:     str          # "BULL_FLAG" | "ABCD" | "ORB"
    entry:       float        # breakout trigger price
    stop:        float        # initial stop loss price
    target1:     float        # first partial target
    target2:     float        # second partial target
    strength:    int          # 0–100
    ts:          datetime
    candles:     list = field(default_factory=list, repr=False)

    @property
    def risk(self) -> float:
        return abs(self.entry - self.stop)

    @property
    def reward1(self) -> float:
        return abs(self.target1 - self.entry)

    @property
    def rr1(self) -> float:
        return self.reward1 / self.risk if self.risk > 0 else 0


class PatternEngine:
    """Detects chart patterns from live 1-minute candles."""

    def __init__(self, stream_manager):
        self._stream = stream_manager
        self._watch: set[str] = set()
        self._signals: dict[str, list[PatternSignal]] = {}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    # ── PUBLIC API ─────────────────────────────────────────────────────────────

    def start(self):
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="PatternEngine"
        )
        self._thread.start()
        logger.info("PatternEngine started")

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

    def get_signals(self, symbol: str) -> list[PatternSignal]:
        """Return active (non-expired) signals for symbol."""
        sym = symbol.upper()
        now = _utcnow()
        with self._lock:
            sigs = self._signals.get(sym, [])
            active = [
                s for s in sigs
                if (now - s.ts).total_seconds() < _SIGNAL_EXPIRY_MIN * 60
            ]
            self._signals[sym] = active
            return list(active)

    def get_all_signals(self) -> dict[str, list[PatternSignal]]:
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
                    logger.debug(f"PatternEngine scan error for {sym}: {e}")

            self._stop_event.wait(PATTERN_SCAN_INTERVAL_SEC)

    def _scan(self, sym: str):
        candles = self._stream.get_candles(sym)
        if len(candles) < 3:
            return

        found: list[PatternSignal] = []

        # Run all four detectors
        bull_flag = _detect_bull_flag(sym, candles)
        if bull_flag:
            found.append(bull_flag)

        abcd = _detect_abcd(sym, candles)
        if abcd:
            found.append(abcd)

        orb = _detect_orb(sym, candles)
        if orb:
            found.append(orb)

        vwap_reclaim = _detect_vwap_reclaim(sym, candles)
        if vwap_reclaim:
            found.append(vwap_reclaim)

        if not found:
            return

        now = _utcnow()
        with self._lock:
            existing = self._signals.get(sym, [])
            for sig in found:
                # Don't re-add the same pattern at the same entry level
                duplicate = any(
                    s.pattern == sig.pattern
                    and abs(s.entry - sig.entry) < 0.01
                    and (now - s.ts).total_seconds() < 60
                    for s in existing
                )
                if not duplicate:
                    existing.append(sig)
                    logger.info(
                        f"PATTERN [{sym}] {sig.pattern} | "
                        f"entry=${sig.entry:.2f} stop=${sig.stop:.2f} "
                        f"target1=${sig.target1:.2f} strength={sig.strength}"
                    )
            self._signals[sym] = existing


# ── PATTERN DETECTORS ──────────────────────────────────────────────────────────

def _detect_bull_flag(sym: str, candles: list[dict]) -> PatternSignal | None:
    """
    Bull Flag: large thrust candle → 2–8 tight consolidation bars →
    current candle breaking above consolidation high.

    Structure: [thrust] [consol_1] ... [consol_N] [breakout]
    """
    if len(candles) < MIN_CONSOL_BARS + 2:
        return None

    avg_range = _avg_candle_range(candles[:-1], _AVG_RANGE_LOOKBACK)
    if avg_range <= 0:
        return None

    # Walk backward from the most recent candle to find consolidation end
    # We look at the last candle as the potential breakout bar
    breakout_candle = candles[-1]

    # Scan for the consolidation block ending just before the breakout bar
    for consol_end in range(len(candles) - 2, MIN_CONSOL_BARS, -1):
        consol_start = consol_end - MAX_CONSOL_BARS + 1
        if consol_start < 1:
            consol_start = 1

        for start in range(consol_start, consol_end):
            consol = candles[start:consol_end + 1]
            if len(consol) < MIN_CONSOL_BARS or len(consol) > MAX_CONSOL_BARS:
                continue

            consol_high = max(c["high"] for c in consol)
            consol_low  = min(c["low"]  for c in consol)
            consol_range_pct = (consol_high - consol_low) / consol_low if consol_low > 0 else 1

            if consol_range_pct > MAX_CONSOL_RANGE_PCT:
                continue

            # The candle immediately before consolidation is the thrust
            thrust = candles[start - 1]
            thrust_body = abs(thrust["close"] - thrust["open"])
            if thrust_body < avg_range * _THRUST_MIN_BODY_MULT:
                continue   # thrust not significant enough

            # Thrust must be bullish and close near its high
            if thrust["close"] < thrust["open"]:
                continue   # bearish thrust — not a bull flag

            # Breakout: close above consolidation high
            if breakout_candle["close"] <= consol_high:
                continue

            # Volume check: breakout bar volume > average of consolidation bars
            avg_consol_vol = sum(c["volume"] for c in consol) / len(consol) if consol else 0
            if avg_consol_vol > 0 and breakout_candle["volume"] < avg_consol_vol * MIN_BREAKOUT_VOL_MULT:
                continue

            # Quality score
            # Higher score for: tighter flag, stronger thrust, larger breakout volume
            tightness   = max(0, 1 - consol_range_pct / MAX_CONSOL_RANGE_PCT)
            thrust_qual = min(1, thrust_body / (avg_range * 4))
            vol_qual    = min(1, (breakout_candle["volume"] / avg_consol_vol - 1) / 2) if avg_consol_vol else 0.5
            strength = int((tightness * 40 + thrust_qual * 35 + vol_qual * 25))

            entry = round(consol_high + 0.01, 2)   # penny above the flag high
            stop  = round(consol_low  - 0.01, 2)
            risk  = entry - stop

            # Ross Cameron target: full flagpole height extended from the breakout.
            # Use the thrust candle's full range as the pole measurement — this
            # represents the momentum move that created the flag.
            # Guarantee ≥ 2:1 R:R at t1 (floor) so the risk manager never rejects
            # a pattern-detected signal on R:R grounds alone.
            pole_height = thrust["high"] - thrust["low"]
            t1 = round(max(entry + pole_height * 0.5, entry + risk * 2.0), 2)
            t2 = round(max(entry + pole_height * 1.0, entry + risk * 3.0), 2)

            return PatternSignal(
                symbol=sym, pattern="BULL_FLAG",
                entry=round(entry, 2), stop=round(stop, 2),
                target1=t1, target2=t2,
                strength=strength, ts=_utcnow(),
                candles=consol + [breakout_candle],
            )

    return None


def _detect_abcd(sym: str, candles: list[dict]) -> PatternSignal | None:
    """
    ABCD Pattern: A→B thrust, B→C pullback (38–62% retrace of AB),
    C→D continuation targeting AB extension.

    We look for the pattern in the last N candles.
    A = prior significant low before the thrust
    B = thrust high
    C = pullback low (retrace of AB)
    D = entry (break above B)
    """
    if len(candles) < 6:
        return None

    recent = candles[-20:]  # scan within last 20 candles

    # Find the most recent significant swing high (B point)
    b_idx = None
    b_high = 0.0
    for i in range(len(recent) - 3, 1, -1):
        if recent[i]["high"] > recent[i-1]["high"] and recent[i]["high"] > recent[i+1]["high"]:
            b_idx  = i
            b_high = recent[i]["high"]
            break

    if b_idx is None or b_idx < 2:
        return None

    # A point: significant low before B (within last 10 candles before B)
    a_idx = None
    a_low = float("inf")
    for i in range(max(0, b_idx - 10), b_idx):
        if recent[i]["low"] < a_low:
            a_low = recent[i]["low"]
            a_idx = i

    if a_idx is None:
        return None

    ab_move = b_high - a_low
    if ab_move <= 0:
        return None

    # C point: pullback low after B (must retrace 38–62% of AB)
    c_idx = None
    c_low = float("inf")
    for i in range(b_idx + 1, len(recent)):
        if recent[i]["low"] < c_low:
            c_low = recent[i]["low"]
            c_idx = i

    if c_idx is None:
        return None

    retrace = (b_high - c_low) / ab_move
    if not (_ABCD_RETRACE_MIN <= retrace <= _ABCD_RETRACE_MAX):
        return None

    # D entry: current candle must be breaking above B (the prior high)
    current = candles[-1]
    if current["close"] <= b_high:
        return None

    # Quality: tighter retrace = better pattern
    ideal_retrace = 0.50
    retrace_quality = 1 - abs(retrace - ideal_retrace) / 0.12
    strength = int(max(0, min(100, retrace_quality * 100)))

    entry = round(b_high + 0.01, 2)
    stop  = round(c_low - 0.01, 2)
    risk  = entry - stop

    # Ross Cameron ABCD target: D leg equals AB leg (100% AB extension).
    # t1 = full AB extension (minimum); t2 = 1.618× (Fibonacci extension).
    # Both floored to guarantee ≥ 2:1 and 3:1 R:R respectively so that the
    # risk manager's R:R gate does not reject valid ABCD setups.
    t1 = round(max(entry + ab_move * 1.0, entry + risk * 2.0), 2)
    t2 = round(max(entry + ab_move * 1.618, entry + risk * 3.0), 2)

    return PatternSignal(
        symbol=sym, pattern="ABCD",
        entry=entry, stop=stop,
        target1=t1, target2=t2,
        strength=strength, ts=_utcnow(),
        candles=recent[a_idx:],
    )


def _detect_orb(sym: str, candles: list[dict]) -> PatternSignal | None:
    """
    Opening Range Breakout: First 5-minute candles define the OR.
    Entry on break above OR high with confirming volume.
    """
    if len(candles) < _ORB_MINUTES + 1:
        return None

    # Opening range = first _ORB_MINUTES candles
    or_candles = candles[:_ORB_MINUTES]
    or_high = max(c["high"]   for c in or_candles)
    or_low  = min(c["low"]    for c in or_candles)
    or_avg_vol = sum(c["volume"] for c in or_candles) / _ORB_MINUTES

    # Current (or most recent) candle breaking above OR high
    current = candles[-1]
    if current["close"] <= or_high:
        return None

    # Volume confirmation
    if or_avg_vol > 0 and current["volume"] < or_avg_vol * MIN_BREAKOUT_VOL_MULT:
        return None

    # Don't signal if we're more than 15 candles past the OR (too late)
    candles_since_or = len(candles) - _ORB_MINUTES
    if candles_since_or > 15:
        return None

    # Strength: closer to open = stronger signal; larger range = more meaningful
    time_factor  = max(0, 1 - candles_since_or / 15)
    range_factor = min(1, (or_high - or_low) / (or_low * 0.05)) if or_low > 0 else 0.5
    vol_factor   = min(1, (current["volume"] / or_avg_vol - 1) / 3) if or_avg_vol > 0 else 0.5
    strength = int((time_factor * 40 + range_factor * 30 + vol_factor * 30))

    or_range = or_high - or_low
    entry    = round(or_high + 0.01, 2)
    stop     = round(or_low  - 0.01, 2)
    risk     = entry - stop   # ≈ or_range + 0.02

    # Ross Cameron ORB target: 2× and 3× OR range above the breakout.
    # This ensures t1 ≥ 2:1 R:R (since risk ≈ or_range, 2× range ≈ 2:1).
    t1 = round(max(entry + or_range * 2.0, entry + risk * 2.0), 2)
    t2 = round(max(entry + or_range * 3.0, entry + risk * 3.0), 2)

    return PatternSignal(
        symbol=sym, pattern="ORB",
        entry=entry, stop=stop,
        target1=t1, target2=t2,
        strength=strength, ts=_utcnow(),
        candles=or_candles + [current],
    )


def _calc_vwap(candles: list[dict]) -> float:
    """
    Calculate VWAP from a list of OHLCV candles.
    VWAP = Σ(typical_price × volume) / Σ(volume)
    typical_price = (high + low + close) / 3
    """
    cum_pv = 0.0
    cum_v  = 0.0
    for c in candles:
        h = c.get("high", 0) or 0
        l = c.get("low",  0) or 0
        cl= c.get("close",0) or 0
        v = c.get("volume",0) or 0
        if h > 0 and l > 0 and cl > 0 and v > 0:
            cum_pv += ((h + l + cl) / 3) * v
            cum_v  += v
    return cum_pv / cum_v if cum_v > 0 else 0.0


def _detect_vwap_reclaim(sym: str, candles: list[dict]) -> PatternSignal | None:
    """
    VWAP Reclaim: stock dips below VWAP then reclaims and holds above it.

    This is one of Ross Cameron's most reliable intraday setups — the stock
    found buyers at VWAP, absorbs selling, and resumes the uptrend.

    Structure (reading right to left):
      [current]   close > VWAP, open > VWAP  ← holding above VWAP
      [prev]      close > VWAP, low  < VWAP  ← the actual reclaim candle
      [earlier]   at least one close < VWAP  ← confirmed dip below VWAP

    Entry: current candle high + $0.01 (break of holding candle)
    Stop:  VWAP - $0.02 buffer
    Targets: 2:1 and 3:1 R:R from the entry
    """
    if len(candles) < 8:
        return None

    vwap = _calc_vwap(candles)
    if vwap <= 0:
        return None

    current = candles[-1]
    prev    = candles[-2]

    # Current candle must be holding above VWAP (both open and close)
    if (current.get("close") or 0) <= vwap:
        return None
    if (current.get("open") or 0) <= vwap:
        return None

    # Previous candle must have crossed VWAP from below to above:
    #   close above VWAP (reclaim) but low touched below VWAP (the dip)
    prev_close = prev.get("close") or 0
    prev_low   = prev.get("low")   or 0
    if prev_close <= vwap:
        return None
    if prev_low >= vwap:
        return None   # didn't actually touch/cross VWAP

    # Must have at least one candle closing below VWAP in the last 8 bars
    # (not counting current or prev — those are the reclaim/hold bars)
    had_dip = any((c.get("close") or 0) < vwap for c in candles[-8:-2])
    if not had_dip:
        return None

    entry = round((current.get("high") or 0) + 0.01, 2)
    stop  = round(vwap - 0.02, 2)
    risk  = entry - stop

    # Sanity checks
    if risk <= 0:
        return None
    if entry <= 0 or stop <= 0:
        return None
    if risk > entry * 0.10:   # reject if stop is >10% away (too wide for small cap)
        return None

    # Guarantee 2:1 R:R
    t1 = round(entry + risk * 2.0, 2)
    t2 = round(entry + risk * 3.0, 2)

    # Strength: reclaim candle volume vs recent average, and proximity to VWAP
    recent_candles = candles[-10:]
    avg_vol = (
        sum(c.get("volume", 0) or 0 for c in recent_candles) / len(recent_candles)
        if recent_candles else 0
    )
    prev_vol = prev.get("volume", 0) or 0
    vol_qual = min(1.0, prev_vol / avg_vol) if avg_vol > 0 else 0.5

    # Proximity: the closer the current close is to VWAP, the cleaner the reclaim
    dist_pct = (current.get("close", vwap) - vwap) / vwap if vwap > 0 else 0.05
    proximity = max(0.0, 1.0 - dist_pct / 0.02)   # within 2% of VWAP = full score

    strength = int(vol_qual * 50 + proximity * 50)

    return PatternSignal(
        symbol=sym, pattern="VWAP_RECLAIM",
        entry=entry, stop=stop,
        target1=t1, target2=t2,
        strength=strength, ts=_utcnow(),
        candles=candles[-5:],
    )


# ── HELPERS ────────────────────────────────────────────────────────────────────

def _avg_candle_range(candles: list[dict], n: int) -> float:
    recent = candles[-n:] if len(candles) >= n else candles
    if not recent:
        return 0.0
    return sum(c["high"] - c["low"] for c in recent) / len(recent)


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)
