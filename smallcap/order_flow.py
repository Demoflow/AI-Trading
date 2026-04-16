"""
Small Cap Order Flow Engine.

Computes a Composite Breakout Readiness Score (0–100) per symbol from five
real-time signals derived from Level 1 quotes, Level 2 book, and the tape:

  Signal 1 — Order Flow Imbalance (OFI)
    Tracks changes in bid and ask size at the inside quote.
    Buy pressure = bid size increasing or ask size decreasing.
    Range: 0–1 (ratio of buy events to total events).

  Signal 2 — Tape Velocity
    Prints-per-second relative to the rolling 60s baseline.
    Measures urgency / acceleration on the tape.
    Range: 0–∞ (1.0 = baseline, 2.0 = 2× baseline)

  Signal 3 — Aggressor Ratio (Lee-Ready classification)
    Each print is classified: last > midpoint → buyer aggressed,
    last < midpoint → seller aggressed, at midpoint → neutral.
    Ratio = buyer_volume / total_volume over rolling 60s window.
    Range: 0–1.

  Signal 4 — Ask Wall Status
    Tracks the ask size at the key resistance level (offer above current price).
    Score = 1 − (current_size / original_size) i.e. high score = wall absorbed.
    Range: 0–1.

  Signal 5 — Bid Depth Ratio
    Bid side total depth at current levels vs peak since watch start.
    Falling bid depth = sellers winning under the tape.
    Range: 0–1.

Composite Score = weighted sum of component scores × 100.
Weights are configured in smallcap/config.py (must sum to 100).

Usage:
    ofe = OrderFlowEngine(stream_manager)
    ofe.start_watching("NVAX", resistance=4.75)  # key ask level to track
    score_dict = ofe.get_score("NVAX")            # called by pattern engine
    ofe.stop_watching("NVAX")
"""

import threading
import time
from collections import deque
from datetime import datetime, timezone, timedelta
from loguru import logger

from smallcap.config import (
    SCORE_WEIGHT_OFI,
    SCORE_WEIGHT_TAPE_VEL,
    SCORE_WEIGHT_AGGRESSOR,
    SCORE_WEIGHT_ASK_WALL,
    SCORE_WEIGHT_BID_DEPTH,
    MIN_OFI_RATIO,
    MIN_AGGRESSOR_BUY_PCT,
    MIN_TAPE_VELOCITY,
    MAX_ASK_WALL_REMAIN,
    MIN_BID_DEPTH_RATIO,
    MIN_BREAKOUT_SCORE,
    OFI_UPDATE_INTERVAL_SEC,
)

# Rolling window for tape / aggressor calculations (seconds)
_TAPE_WINDOW_SEC   = 60
_OFI_WINDOW_SEC    = 30

# Minimum prints in window before aggressor ratio is trusted
_MIN_PRINTS_FOR_AGGRESSOR = 10


class OrderFlowEngine:
    """
    Reads live state from StreamManager and scores each watched symbol.
    Runs its own refresh loop in a background thread.
    """

    def __init__(self, stream_manager):
        self._stream = stream_manager
        # Per-symbol state
        self._watch: dict[str, _SymbolState] = {}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    # ── PUBLIC API ─────────────────────────────────────────────────────────────

    def start(self):
        """Launch background scoring loop."""
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="OrderFlowEngine"
        )
        self._thread.start()
        logger.info("OrderFlowEngine started")

    def stop(self):
        self._stop_event.set()

    def start_watching(self, symbol: str, resistance: float | None = None):
        """
        Begin tracking a symbol. Call after gap scanner identifies it as a
        candidate so we can establish baseline OFI / tape velocity.

        Args:
            resistance: Price level of the key ask wall to track (e.g. the
                        round number or prior high just above current price).
                        If None, uses the current best ask.
        """
        sym = symbol.upper()
        with self._lock:
            if sym not in self._watch:
                quote = self._stream.get_quote(sym) or {}
                if resistance is None:
                    resistance = quote.get("ask") or 0.0
                self._watch[sym] = _SymbolState(sym, resistance)
                logger.info(f"OFE: watching {sym} | resistance=${resistance:.2f}")

    def stop_watching(self, symbol: str):
        sym = symbol.upper()
        with self._lock:
            self._watch.pop(sym, None)

    def get_score(self, symbol: str) -> dict | None:
        """
        Return the latest score dict for symbol, or None if not being watched.

        Returns dict with keys:
          composite (int 0–100), ofi, tape_vel, aggressor, ask_wall, bid_depth,
          component_scores (dict), breakout_ready (bool), ts (datetime)
        """
        sym = symbol.upper()
        with self._lock:
            state = self._watch.get(sym)
        if state is None:
            return None
        return state.last_score

    def get_all_scores(self) -> dict[str, dict]:
        """Return snapshot of all current scores."""
        with self._lock:
            return {
                sym: state.last_score
                for sym, state in self._watch.items()
                if state.last_score
            }

    # ── BACKGROUND LOOP ────────────────────────────────────────────────────────

    def _run(self):
        while not self._stop_event.is_set():
            with self._lock:
                symbols = list(self._watch.keys())

            for sym in symbols:
                try:
                    self._refresh(sym)
                except Exception as e:
                    logger.debug(f"OFE refresh error for {sym}: {e}")

            self._stop_event.wait(OFI_UPDATE_INTERVAL_SEC)

    def _refresh(self, sym: str):
        """Recompute score for one symbol from latest stream state."""
        quote = self._stream.get_quote(sym)
        book  = self._stream.get_book(sym)

        if not quote:
            return

        with self._lock:
            state = self._watch.get(sym)
        if state is None:
            return

        now = _utcnow()
        bid   = quote.get("bid", 0)
        ask   = quote.get("ask", 0)
        last  = quote.get("last", 0)
        last_size = quote.get("last_size", 0)
        bid_size  = quote.get("bid_size", 0)
        ask_size  = quote.get("ask_size", 0)
        volume    = quote.get("volume", 0)

        midpoint = (bid + ask) / 2 if bid > 0 and ask > 0 else last

        # ── Signal 1: OFI ────────────────────────────────────────────────────
        ofi_ratio = state.update_ofi(bid, ask, bid_size, ask_size, now)

        # ── Signal 2: Tape Velocity ───────────────────────────────────────────
        tape_vel = state.update_tape(last, last_size, volume, now)

        # ── Signal 3: Aggressor Ratio ─────────────────────────────────────────
        aggressor = state.update_aggressor(last, last_size, midpoint, now)

        # ── Signal 4: Ask Wall ────────────────────────────────────────────────
        ask_wall = state.update_ask_wall(book, ask)

        # ── Signal 5: Bid Depth ───────────────────────────────────────────────
        bid_depth = state.update_bid_depth(book)

        # ── Composite Score ───────────────────────────────────────────────────
        ofi_score       = _clamp(ofi_ratio / max(MIN_OFI_RATIO, 0.01))
        vel_score       = _clamp(tape_vel  / max(MIN_TAPE_VELOCITY, 0.01))
        agg_score       = _clamp(aggressor / max(MIN_AGGRESSOR_BUY_PCT, 0.01))
        wall_score      = _clamp((1.0 - ask_wall) / max(1.0 - MAX_ASK_WALL_REMAIN, 0.01))
        depth_score     = _clamp(bid_depth / max(MIN_BID_DEPTH_RATIO, 0.01))

        composite = int(
            ofi_score   * SCORE_WEIGHT_OFI
            + vel_score   * SCORE_WEIGHT_TAPE_VEL
            + agg_score   * SCORE_WEIGHT_AGGRESSOR
            + wall_score  * SCORE_WEIGHT_ASK_WALL
            + depth_score * SCORE_WEIGHT_BID_DEPTH
        )
        composite = max(0, min(100, composite))

        score_dict = {
            "composite":      composite,
            "ofi":            round(ofi_ratio, 3),
            "tape_vel":       round(tape_vel, 2),
            "aggressor":      round(aggressor, 3),
            "ask_wall":       round(ask_wall, 3),
            "bid_depth":      round(bid_depth, 3),
            "component_scores": {
                "ofi":       round(ofi_score * SCORE_WEIGHT_OFI, 1),
                "tape_vel":  round(vel_score * SCORE_WEIGHT_TAPE_VEL, 1),
                "aggressor": round(agg_score * SCORE_WEIGHT_AGGRESSOR, 1),
                "ask_wall":  round(wall_score * SCORE_WEIGHT_ASK_WALL, 1),
                "bid_depth": round(depth_score * SCORE_WEIGHT_BID_DEPTH, 1),
            },
            "breakout_ready": composite >= MIN_BREAKOUT_SCORE,
            "ts": now,
        }

        with self._lock:
            if sym in self._watch:
                self._watch[sym].last_score = score_dict

        if composite >= MIN_BREAKOUT_SCORE:
            logger.info(
                f"OFE [{sym}] BREAKOUT READY score={composite} | "
                f"ofi={ofi_ratio:.2f} vel={tape_vel:.1f}x "
                f"agg={aggressor:.0%} wall={ask_wall:.0%} depth={bid_depth:.0%}"
            )


class _SymbolState:
    """Per-symbol mutable state for the order flow engine."""

    __slots__ = (
        "symbol", "resistance",
        # OFI
        "_ofi_events", "_prev_bid", "_prev_ask",
        "_prev_bid_size", "_prev_ask_size",
        # Tape / aggressor
        "_tape_prints",       # deque of (ts, size, buyer_aggressed bool)
        "_last_volume",       # running total volume to detect new prints
        # Ask wall
        "_wall_original_size",
        # Bid depth
        "_bid_depth_peak",
        # Score output
        "last_score",
    )

    def __init__(self, symbol: str, resistance: float):
        self.symbol     = symbol
        self.resistance = resistance
        # OFI rolling events: deque of (ts, +1 buy / -1 sell)
        self._ofi_events = deque()
        self._prev_bid       = 0.0
        self._prev_ask       = 0.0
        self._prev_bid_size  = 0
        self._prev_ask_size  = 0
        # Tape
        self._tape_prints   = deque()
        self._last_volume   = 0
        # Ask wall
        self._wall_original_size = 0
        # Bid depth
        self._bid_depth_peak = 0
        self.last_score: dict | None = None

    # ── OFI ───────────────────────────────────────────────────────────────────

    def update_ofi(
        self,
        bid: float, ask: float,
        bid_size: int, ask_size: int,
        now: datetime,
    ) -> float:
        """
        Order Flow Imbalance — measures quote-level buy vs sell pressure.
        Returns ratio of buy events in the rolling window (0–1).
        """
        event = None
        if self._prev_bid > 0:
            # Bid increased or ask decreased → buy pressure
            if bid_size > self._prev_bid_size or ask_size < self._prev_ask_size:
                event = 1
            # Bid decreased or ask increased → sell pressure
            elif bid_size < self._prev_bid_size or ask_size > self._prev_ask_size:
                event = -1

        if event is not None:
            self._ofi_events.append((now, event))

        self._prev_bid      = bid
        self._prev_ask      = ask
        self._prev_bid_size = bid_size
        self._prev_ask_size = ask_size

        # Trim stale events
        cutoff = now - timedelta(seconds=_OFI_WINDOW_SEC)
        while self._ofi_events and self._ofi_events[0][0] < cutoff:
            self._ofi_events.popleft()

        if not self._ofi_events:
            return 0.5   # neutral when no data

        buys  = sum(1 for _, e in self._ofi_events if e > 0)
        total = len(self._ofi_events)
        return buys / total

    # ── TAPE VELOCITY + AGGRESSOR ─────────────────────────────────────────────

    def update_tape(
        self, last: float, last_size: int, total_volume: int, now: datetime
    ) -> float:
        """
        Tape velocity: prints/sec relative to rolling 60s baseline.
        Returns multiplier (1.0 = baseline, 2.0 = 2× baseline).
        """
        # Detect new print from volume delta
        if total_volume > self._last_volume and last_size > 0:
            self._last_volume = total_volume
            self._tape_prints.append((now, last_size, None))  # aggressor filled later

        self._trim_tape(now)

        if len(self._tape_prints) < 2:
            return 1.0

        # Prints per second in current window
        window_sec = (
            self._tape_prints[-1][0] - self._tape_prints[0][0]
        ).total_seconds()
        if window_sec <= 0:
            return 1.0

        pps = len(self._tape_prints) / window_sec

        # Baseline: we define 1× as 1 print per second (typical small-cap morning)
        # Multiplier > 2 = tape is burning
        return pps   # caller normalises against MIN_TAPE_VELOCITY

    def update_aggressor(
        self, last: float, last_size: int, midpoint: float, now: datetime
    ) -> float:
        """
        Lee-Ready aggressor ratio: buyer-aggressed volume / total volume.
        Returns ratio 0–1.
        """
        if last_size > 0 and midpoint > 0 and self._last_volume > 0:
            # Update the most recent tape print with aggressor classification
            if self._tape_prints:
                ts, size, agg = self._tape_prints[-1]
                if agg is None:
                    if last > midpoint:
                        buyer_agg = True
                    elif last < midpoint:
                        buyer_agg = False
                    else:
                        buyer_agg = None   # at midpoint — ambiguous
                    self._tape_prints[-1] = (ts, size, buyer_agg)

        self._trim_tape(now)

        classified = [(size, agg) for _, size, agg in self._tape_prints if agg is not None]
        if len(classified) < _MIN_PRINTS_FOR_AGGRESSOR:
            return 0.5   # neutral when not enough data

        buy_vol   = sum(size for size, agg in classified if agg is True)
        total_vol = sum(size for size, agg in classified)
        return buy_vol / total_vol if total_vol > 0 else 0.5

    def _trim_tape(self, now: datetime):
        cutoff = now - timedelta(seconds=_TAPE_WINDOW_SEC)
        while self._tape_prints and self._tape_prints[0][0] < cutoff:
            self._tape_prints.popleft()

    # ── ASK WALL ──────────────────────────────────────────────────────────────

    def update_ask_wall(self, book: dict | None, current_ask: float) -> float:
        """
        Ask wall absorption: fraction of the resistance-level ask still standing.
        Returns remaining fraction (0 = fully absorbed, 1 = untouched).
        Low value = wall being eaten → bullish.
        """
        if not book:
            return 0.5   # neutral when no book data

        asks = book.get("asks", [])
        if not asks:
            return 0.5

        # Find the ask level closest to our tracked resistance
        target_ask = self.resistance if self.resistance > current_ask else current_ask
        wall_size = _find_level_size(asks, target_ask, tolerance=0.05)

        if self._wall_original_size == 0 and wall_size > 0:
            self._wall_original_size = wall_size

        if self._wall_original_size == 0:
            return 0.5

        remaining = wall_size / self._wall_original_size
        return min(1.0, remaining)

    # ── BID DEPTH ─────────────────────────────────────────────────────────────

    def update_bid_depth(self, book: dict | None) -> float:
        """
        Bid side depth ratio: current total bid depth vs peak since watch start.
        Returns ratio (1.0 = at peak, 0.5 = half of peak depth).
        """
        if not book:
            return 0.5

        bids = book.get("bids", [])
        total_bid_depth = sum(size for _, size in bids)

        if total_bid_depth > self._bid_depth_peak:
            self._bid_depth_peak = total_bid_depth

        if self._bid_depth_peak == 0:
            return 0.5

        return min(1.0, total_bid_depth / self._bid_depth_peak)


# ── MODULE-LEVEL HELPERS ───────────────────────────────────────────────────────

def _clamp(x: float) -> float:
    """Clamp to [0, 1]."""
    return max(0.0, min(1.0, x))


def _find_level_size(
    book_side: list[tuple[float, int]],
    target_price: float,
    tolerance: float = 0.05,
) -> int:
    """
    Find total size at levels within tolerance of target_price.
    Used to track how much supply remains at a specific resistance.
    """
    return sum(
        size for price, size in book_side
        if abs(price - target_price) <= tolerance
    )


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)
