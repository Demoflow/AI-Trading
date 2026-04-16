"""
Candle Sequence Pattern Engine v1.
Encodes the last 5-6 candles into a normalized feature vector
and scores against four known 0DTE intraday patterns.

Output: pattern_context dict consumed by signal_engine.scan()
  pattern           - name of detected pattern, or None
  confidence_weight - integer (+/-) applied to signal confidence
  direction_bias    - "CALL", "PUT", or "NEUTRAL"
  pattern_reason    - human-readable description logged with the signal
"""

from loguru import logger


class PatternEngine:

    def analyze(self, candles, vwap=0.0, atr=1.0, expected_move=None):
        """
        Analyze the last 5-6 completed candles.
        candles: list of OHLCV dicts from CandleBuilder.get_all_candles()
        vwap:    current VWAP float
        atr:     current ATR float
        expected_move: dict from ContractPicker.get_expected_move(), or None
        """
        if not candles or len(candles) < 4:
            return self._empty()
        if atr <= 0:
            atr = 1.0

        c = candles[-6:] if len(candles) >= 6 else candles[-len(candles):]

        # Run all pattern detectors and pick the strongest hit
        candidates = []
        for fn in (
            self._tight_consolidation_breakout,
            self._failed_auction,
            self._wick_rejection,
            self._volume_climax_exhaustion,
        ):
            result = fn(c, vwap, atr, expected_move)
            if result:
                candidates.append(result)

        if not candidates:
            return self._empty()

        best = max(candidates, key=lambda x: x["confidence_weight"])
        logger.debug(
            f"Pattern: {best['pattern']} bias={best['direction_bias']} "
            f"weight=+{best['confidence_weight']} | {best['pattern_reason']}"
        )
        return best

    # ── HELPERS ────────────────────────────────────────────────────────────────

    @staticmethod
    def _empty():
        return {
            "pattern": None,
            "confidence_weight": 0,
            "direction_bias": "NEUTRAL",
            "pattern_reason": "",
        }

    @staticmethod
    def _body(c):
        return c["close"] - c["open"]

    @staticmethod
    def _body_size(c):
        return abs(c["close"] - c["open"])

    @staticmethod
    def _upper_wick(c):
        return c["high"] - max(c["open"], c["close"])

    @staticmethod
    def _lower_wick(c):
        return min(c["open"], c["close"]) - c["low"]

    # ── PATTERN 1: 3-BAR TIGHT CONSOLIDATION AFTER THRUST ─────────────────────

    def _tight_consolidation_breakout(self, c, vwap, atr, em):
        """
        Thrust candle (large body ≥ 0.6×ATR) followed by 2-3 inside
        or near-inside bars with small ranges (≤ 0.4×ATR each).
        Enter with the trend on the next break of the consolidation range.
        """
        if len(c) < 4:
            return None

        thrust = c[-4]
        if self._body_size(thrust) < atr * 0.6:
            return None

        tight = 0
        for bar in c[-3:]:
            bar_range = bar["high"] - bar["low"]
            inside = bar["high"] <= thrust["high"] and bar["low"] >= thrust["low"]
            small = bar_range <= atr * 0.45
            if inside or small:
                tight += 1

        if tight < 2:
            return None

        direction = "CALL" if self._body(thrust) > 0 else "PUT"
        weight = 8 + tight * 2  # 8/10/12 depending on how many tight bars

        return {
            "pattern": "TIGHT_CONSOL_BREAKOUT",
            "confidence_weight": weight,
            "direction_bias": direction,
            "pattern_reason": (
                f"Thrust+{tight}bar consolidation → {direction} continuation break"
            ),
        }

    # ── PATTERN 2: FAILED AUCTION ──────────────────────────────────────────────

    def _failed_auction(self, c, vwap, atr, em):
        """
        Price breaks through a prior 3-bar swing high or low, finds no
        follow-through within 2-3 candles (low volume on the extension),
        and snaps back through the broken level.
        Strong fade entry against the failed break direction.
        """
        if len(c) < 5:
            return None

        anchor = c[:-3]  # first 2-3 bars establish the prior high/low
        if not anchor:
            return None
        prior_high = max(b["high"] for b in anchor)
        prior_low = min(b["low"] for b in anchor)

        last3 = c[-3:]
        last = c[-1]
        vols = [b.get("volume", 0) for b in c]
        avg_vol = sum(vols) / len(vols) if vols else 1

        # Failed auction UP
        if any(b["high"] > prior_high * 1.001 for b in last3):
            if last["close"] < prior_high:
                # Snap-back confirmed; validate no volume conviction on the extension
                ext_bar = max(last3, key=lambda b: b["high"])
                ext_vol = ext_bar.get("volume", 0)
                if ext_vol < avg_vol * 1.8:
                    return {
                        "pattern": "FAILED_AUCTION_HIGH",
                        "confidence_weight": 12,
                        "direction_bias": "PUT",
                        "pattern_reason": (
                            f"Failed auction above ${prior_high:.2f} "
                            f"(ext vol {ext_vol/max(avg_vol,1):.1f}x avg) → PUT fade"
                        ),
                    }

        # Failed auction DOWN
        if any(b["low"] < prior_low * 0.999 for b in last3):
            if last["close"] > prior_low:
                ext_bar = min(last3, key=lambda b: b["low"])
                ext_vol = ext_bar.get("volume", 0)
                if ext_vol < avg_vol * 1.8:
                    return {
                        "pattern": "FAILED_AUCTION_LOW",
                        "confidence_weight": 12,
                        "direction_bias": "CALL",
                        "pattern_reason": (
                            f"Failed auction below ${prior_low:.2f} "
                            f"(ext vol {ext_vol/max(avg_vol,1):.1f}x avg) → CALL fade"
                        ),
                    }

        return None

    # ── PATTERN 3: WICK REJECTION AT VWAP / EXPECTED MOVE BOUNDARY ────────────

    def _wick_rejection(self, c, vwap, atr, em):
        """
        A long wick (≥ 2× body) touches VWAP or the expected-move boundary
        then the bar closes well off its extreme.
        High-probability mean-reversion with defined risk at the wick tip.
        """
        if not c:
            return None

        last = c[-1]
        body = self._body_size(last)
        if body <= 0:
            return None

        uw = self._upper_wick(last)
        lw = self._lower_wick(last)

        # Upper wick rejection → bearish
        if uw >= max(body * 2, atr * 0.35):
            weight = 8
            reason = "upper wick rejection"
            if vwap and abs(last["high"] - vwap) <= atr * 0.15:
                weight += 5
                reason = "upper wick rejection at VWAP"
            if em and abs(last["high"] - em["upper_bound"]) <= atr * 0.25:
                weight += 7
                reason = "upper wick rejection at EM upper bound"
            return {
                "pattern": "WICK_REJECTION_HIGH",
                "confidence_weight": weight,
                "direction_bias": "PUT",
                "pattern_reason": f"{reason} → PUT reversal",
            }

        # Lower wick rejection → bullish
        if lw >= max(body * 2, atr * 0.35):
            weight = 8
            reason = "lower wick rejection"
            if vwap and abs(last["low"] - vwap) <= atr * 0.15:
                weight += 5
                reason = "lower wick rejection at VWAP"
            if em and abs(last["low"] - em["lower_bound"]) <= atr * 0.25:
                weight += 7
                reason = "lower wick rejection at EM lower bound"
            return {
                "pattern": "WICK_REJECTION_LOW",
                "confidence_weight": weight,
                "direction_bias": "CALL",
                "pattern_reason": f"{reason} → CALL reversal",
            }

        return None

    # ── PATTERN 4: VOLUME CLIMAX + 2 INSIDE BARS ──────────────────────────────

    def _volume_climax_exhaustion(self, c, vwap, atr, em):
        if len(c) < 4:
            return None
        vols = [b.get("volume", 0) for b in c]
        avg_vol = sum(vols) / len(vols) if vols else 0
        if avg_vol <= 0:
            return None
        climax = c[-3]
        if climax.get("volume", 0) < avg_vol * 2.5:
            return None
        inside_count = 0
        for bar in c[-2:]:
            is_inside = bar["high"] <= climax["high"] and bar["low"] >= climax["low"]
            is_quiet = bar.get("volume", 0) < avg_vol * 0.7
            if is_inside and is_quiet:
                inside_count += 1
        if inside_count < 2:
            return None
        direction = "PUT" if self._body(climax) > 0 else "CALL"
        return {
            "pattern": "VOLUME_CLIMAX_EXHAUSTION",
            "confidence_weight": 10,
            "direction_bias": direction,
            "pattern_reason": (
                f"Climax {climax.get('volume',0)/max(avg_vol,1):.1f}x vol "
                f"+ {inside_count} inside bars → {direction} exhaustion fade"
            ),
        }
