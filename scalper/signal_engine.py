"""
Scalper Signal Engine v6 - Long Options Only.
Philosophy: directional conviction buys only.
- VWAP pullback (trend + mean-reversion entry)
- EMA momentum (trend continuation)
- ORB breakout (opening range expansion)
- Momentum fade (overbought/oversold reversal)

All require minimum 3:1 R:R. All produce LONG_OPTION signals.
Min confidence: 70. Strategy cooldown: 5 min per symbol-strategy pair.
"""

from datetime import datetime
from loguru import logger


class ScalperSignal:

    MIN_ATR = 0.20
    MIN_CONFIDENCE = 70
    MAX_PER_STRATEGY = 20   # Effectively uncapped for a trading day
    STRATEGY_COOLDOWN = 300  # 5 min cooldown per symbol-strategy pair
    MIN_RR_DIRECTIONAL = 3.0  # 3:1 minimum R:R on all buys

    def __init__(self):
        self.recent_signals = []
        self._cooldown = {}          # Per-symbol 5-min cooldown after any signal
        self._strategy_cooldown = {} # Per strategy-symbol pair
        self._strategy_count = {}
        self._trade_date = None

    def _reset_daily(self):
        today = datetime.now().date()
        if self._trade_date != today:
            self._strategy_count = {}
            self._cooldown.clear()           # Stale cooldowns from yesterday block today's trades
            self._strategy_cooldown.clear()  # Same for strategy-level cooldowns
            self._trade_date = today

    def _can_use(self, strat, sym):
        self._reset_daily()
        count = self._strategy_count.get(strat, 0)
        if count >= self.MAX_PER_STRATEGY:
            return False
        key = f"{strat}_{sym}"
        if key in self._strategy_cooldown:
            elapsed = (datetime.now() - self._strategy_cooldown[key]).total_seconds()
            if elapsed < self.STRATEGY_COOLDOWN:
                return False
        return True

    def _record(self, strat, sym):
        self._strategy_count[strat] = self._strategy_count.get(strat, 0) + 1
        self._strategy_cooldown[f"{strat}_{sym}"] = datetime.now()

    def _check_rr(self, entry, stop, target):
        """Enforce minimum 3:1 R:R on all directional buys."""
        risk = abs(entry - stop)
        reward = abs(target - entry)
        if risk <= 0:
            return False, 0
        rr = reward / risk
        return rr >= self.MIN_RR_DIRECTIONAL, round(rr, 1)

    def scan(self, snapshot_5m, snapshot_1m=None,
             allowed_strategies=None, gex_profile=None,
             breadth=None, expected_move=None,
             pattern_context=None, time_context=None,
             divergence=None, gex_wall_context=None):
        """
        Dual timeframe scan: 5m bias, 1m entry confirmation.
        Only long calls and puts.

        New in v6:
          pattern_context  - from PatternEngine.analyze()
          time_context     - from TimeContextFilter.get_context()
          divergence       - from MarketInternals.get_divergence()
          gex_wall_context - from IntradayGEX.get_wall_context()
        """
        if not snapshot_5m or snapshot_5m["candle_count"] < 5:
            return []

        # ── TIME CONTEXT GATE ──
        # Hard gate: if this window doesn't allow entries, stop immediately.
        if time_context and not time_context.get("entry_allowed", True):
            return []

        signals = []
        sym = snapshot_5m["symbol"]
        now = datetime.now()
        self._reset_daily()

        # Per-symbol cooldown
        if sym in self._cooldown:
            if (now - self._cooldown[sym]).total_seconds() < 300:
                return []

        if snapshot_5m["atr"] < self.MIN_ATR:
            return []

        if not allowed_strategies:
            allowed_strategies = ["VWAP_PULLBACK", "EMA_MOMENTUM"]

        em_filter = self._get_em_context(snapshot_5m, expected_move)

        # Effective minimum confidence = base + time context boost
        time_boost = time_context.get("min_confidence_boost", 0) if time_context else 0
        effective_min = self.MIN_CONFIDENCE + time_boost

        for strat in allowed_strategies:
            if not self._can_use(strat, sym):
                continue

            sig = None
            if strat == "VWAP_PULLBACK":
                sig = self._vwap_pullback(snapshot_5m, snapshot_1m, gex_profile, breadth, em_filter)
            elif strat in ("DIRECTIONAL_BUY", "EMA_MOMENTUM"):
                sig = self._ema_momentum(snapshot_5m, snapshot_1m, gex_profile, breadth, em_filter)
            elif strat == "ORB_BREAKOUT":
                sig = self._orb_breakout(snapshot_5m, snapshot_1m, breadth, em_filter)
            elif strat == "MOMENTUM_FADE":
                sig = self._momentum_fade(snapshot_5m, snapshot_1m)

            if not sig:
                continue

            # ── PATTERN CONTEXT WEIGHT ──
            if pattern_context and pattern_context.get("pattern"):
                bias = pattern_context.get("direction_bias", "NEUTRAL")
                wt   = pattern_context.get("confidence_weight", 0)
                if bias == sig["direction"]:
                    sig["confidence"] += wt          # Pattern aligns → boost
                    sig["reason"] += f" | pat:{pattern_context['pattern']}+{wt}"
                elif bias != "NEUTRAL":
                    sig["confidence"] -= wt          # Pattern opposes → penalize
                    sig["reason"] += f" | pat:{pattern_context['pattern']}-{wt}"

            # ── DIVERGENCE FILTER ──
            if divergence and divergence.get("type") not in ("NEUTRAL", None):
                div_bias  = divergence.get("signal_bias", "NEUTRAL")
                div_score = divergence.get("score", 0)
                # Distribution while taking a CALL: hard confidence penalty
                if div_bias == "PUT" and sig["direction"] == "CALL":
                    sig["confidence"] -= min(abs(div_score) // 4, 20)
                    sig["reason"] += f" | div:{divergence['type']}"
                # Accumulation while taking a PUT: hard confidence penalty
                elif div_bias == "CALL" and sig["direction"] == "PUT":
                    sig["confidence"] -= min(abs(div_score) // 4, 20)
                    sig["reason"] += f" | div:{divergence['type']}"
                # Aligned divergence: small boost
                elif div_bias == sig["direction"]:
                    sig["confidence"] += min(abs(div_score) // 8, 8)

            # ── GEX WALL CONTEXT ──
            if gex_wall_context:
                approaching = gex_wall_context.get("approaching_wall")
                if approaching:
                    # Near call wall → bearish; near put wall → bullish
                    call_wall = gex_wall_context.get("call_wall", 0)
                    put_wall  = gex_wall_context.get("put_wall",  0)
                    cws = gex_wall_context.get("call_wall_score")
                    pws = gex_wall_context.get("put_wall_score")

                    if approaching == call_wall and cws:
                        if cws["recommendation"] == "FADE" and sig["direction"] == "PUT":
                            sig["confidence"] += 8  # Third test fade → boost PUT near call wall
                            sig["reason"] += f" | gex_wall_fade(wall ${call_wall})"
                        elif cws["recommendation"] == "MOMENTUM" and sig["direction"] == "CALL":
                            sig["confidence"] += 8  # Wall absorbed → boost CALL
                            sig["reason"] += f" | gex_wall_momentum(wall ${call_wall})"

                    if approaching == put_wall and pws:
                        if pws["recommendation"] == "FADE" and sig["direction"] == "CALL":
                            sig["confidence"] += 8
                            sig["reason"] += f" | gex_wall_fade(wall ${put_wall})"
                        elif pws["recommendation"] == "MOMENTUM" and sig["direction"] == "PUT":
                            sig["confidence"] += 8
                            sig["reason"] += f" | gex_wall_momentum(wall ${put_wall})"

            # ── STANDARD HARD BLOCKS ──
            if sig["confidence"] < effective_min:
                continue

            if breadth and not self._breadth_ok(breadth, sig["direction"]):
                continue

            if gex_profile and gex_profile.get("regime") == "POSITIVE":
                # POSITIVE GEX pins price at walls — directional options decay fast.
                # Steeper penalty (was -10) to force higher-conviction entries only.
                sig["confidence"] -= 20
                if sig["confidence"] < effective_min:
                    continue

            # ── GAP ALIGNMENT GATE (opening window only) ──
            if time_context and time_context.get("gap_aligned_only"):
                gap_dir = time_context.get("gap_direction", "FLAT")
                if gap_dir != "FLAT":
                    needed = "CALL" if gap_dir == "UP" else "PUT"
                    if sig["direction"] != needed:
                        logger.debug(
                            f"  Opening gap block: {sym} {sig['direction']} "
                            f"vs gap {gap_dir}"
                        )
                        continue

            self._record(strat, sym)
            signals.append(sig)

        if signals:
            self._cooldown[sym] = now

        return signals

    def _get_em_context(self, snap, em):
        if not em:
            return {"inside": True, "pct_used": 0, "exhausted": False}
        price = snap["price"]
        move_from_open = abs(price - (em["upper_bound"] + em["lower_bound"]) / 2)
        pct_used = move_from_open / em["expected_move"] if em["expected_move"] > 0 else 0
        inside = em["lower_bound"] <= price <= em["upper_bound"]
        return {
            "inside": inside,
            "pct_used": round(pct_used, 2),
            "exhausted": pct_used > 0.8,
        }

    def _breadth_ok(self, breadth, direction):
        sig = breadth.get("signal", "MIXED")
        if direction == "CALL" and sig in ("STRONG_BEARISH", "BEARISH"):
            return False
        if direction == "PUT" and sig in ("STRONG_BULLISH", "BULLISH"):
            return False
        return True

    def _gex_bonus(self, gex):
        """GEX negative = supportive for directional long options."""
        if not gex:
            return 0
        r = gex.get("regime", "")
        if r == "NEGATIVE":
            return 10
        if r == "POSITIVE":
            return -10
        return 0

    # ── DIRECTIONAL STRATEGIES ─────────────────────────────────────────────────

    def _vwap_pullback(self, snap5, snap1, gex, breadth, em_ctx):
        """VWAP pullback entry: price returns to VWAP in trending environment."""
        price = snap5["price"]
        vwap = snap5["vwap"]
        if not vwap:
            return None
        vd = abs(snap5["vwap_pct"])
        # Require a meaningful pullback (0.10%+). Anything closer than 0.10%
        # is noise on a flat/pinned day — the spread eats the move before it starts.
        if not (0.10 <= vd <= 0.50):
            return None
        if em_ctx.get("exhausted"):
            return None

        atr = snap5["atr"]
        cur = snap5.get("current_candle", {})

        # BULLISH PULLBACK
        if (snap5["ema_trend"] == "UP" and snap5["rsi"] < 60 and
                -0.50 <= snap5["price_vs_vwap"] <= 0.30):
            stop = round(vwap - atr * 0.5, 2)
            target = round(price + atr * 2.0, 2)
            rr_ok, rr = self._check_rr(price, stop, target)
            if not rr_ok:
                return None

            conf = 60
            if snap5["volume_ratio"] > 1.3:
                conf += 8
            if snap5["rsi"] < 42:
                conf += 8
            if snap5["macd_histogram"] > 0:
                conf += 8
            if snap1 and snap1.get("momentum") == "BULLISH":
                conf += 10
            if cur and cur.get("close", 0) > cur.get("open", 0):
                conf += 5
            # EMA slope: rising EMA9 confirms pullback entry
            if snap5.get("ema9_slope") == "UP":
                conf += 6
            # EMA50 macro trend alignment
            if snap5.get("ema50_trend") == "UP":
                conf += 6
            elif snap5.get("ema50_trend") == "DOWN":
                conf -= 8   # Counter-trend to macro — penalize
            # GEX handled in outer scan() loop — do not apply here (avoid double-count)

            return {
                "type": "VWAP_PULLBACK", "structure": "LONG_OPTION",
                "direction": "CALL", "symbol": snap5["symbol"],
                "price": price, "confidence": min(conf, 95),
                "vwap": vwap, "rr_ratio": rr,
                "reason": f"VWAP PB UP RSI:{snap5['rsi']:.0f} RR:{rr}:1",
                "stop_level": stop, "target_level": target,
                "time": datetime.now(),
            }

        # BEARISH PULLBACK
        if (snap5["ema_trend"] == "DOWN" and snap5["rsi"] > 40 and
                -0.30 <= snap5["price_vs_vwap"] <= 0.50):
            stop = round(vwap + atr * 0.5, 2)
            target = round(price - atr * 2.0, 2)
            rr_ok, rr = self._check_rr(price, stop, target)
            if not rr_ok:
                return None

            conf = 60
            if snap5["volume_ratio"] > 1.3:
                conf += 8
            if snap5["rsi"] > 58:
                conf += 8
            if snap5["macd_histogram"] < 0:
                conf += 8
            if snap1 and snap1.get("momentum") == "BEARISH":
                conf += 10
            if cur and cur.get("close", 0) < cur.get("open", 0):
                conf += 5
            # EMA slope: falling EMA9 confirms pullback entry
            if snap5.get("ema9_slope") == "DOWN":
                conf += 6
            # EMA50 macro trend alignment
            if snap5.get("ema50_trend") == "DOWN":
                conf += 6
            elif snap5.get("ema50_trend") == "UP":
                conf -= 8   # Counter-trend to macro — penalize
            # GEX handled in outer scan() loop — do not apply here (avoid double-count)

            return {
                "type": "VWAP_PULLBACK", "structure": "LONG_OPTION",
                "direction": "PUT", "symbol": snap5["symbol"],
                "price": price, "confidence": min(conf, 95),
                "vwap": vwap, "rr_ratio": rr,
                "reason": f"VWAP PB DN RSI:{snap5['rsi']:.0f} RR:{rr}:1",
                "stop_level": stop, "target_level": target,
                "time": datetime.now(),
            }
        return None

    def _ema_momentum(self, snap5, snap1, gex, breadth, em_ctx):
        """EMA crossover momentum continuation."""
        price = snap5["price"]
        if em_ctx.get("exhausted"):
            return None
        atr = snap5["atr"]

        # BULLISH CROSS
        if snap5["ema_cross_up"] and snap5["macd_histogram"] > 0 and snap5["price_vs_vwap"] > 0:
            stop = round((snap5["ema21"] or price) - atr * 0.5, 2)
            target = round(price + atr * 2.5, 2)
            rr_ok, rr = self._check_rr(price, stop, target)
            if not rr_ok:
                return None

            conf = 65
            if snap5["volume_surge"]:
                conf += 12
            if 50 < snap5["rsi"] < 70:
                conf += 8
            if snap1 and snap1.get("ema_cross_up"):
                conf += 10
            # EMA slope: rising EMA9 adds momentum confirmation
            if snap5.get("ema9_slope") == "UP":
                conf += 6
            # Donchian structural breakout: EMA cross + channel breakout = high conviction
            if snap5.get("dc_breakout_up"):
                conf += 12
            # EMA50 macro alignment
            if snap5.get("ema50_trend") == "UP":
                conf += 6
            elif snap5.get("ema50_trend") == "DOWN":
                conf -= 8
            # GEX handled in outer scan() loop — do not apply here (avoid double-count)

            dc_tag = " DC_BRK" if snap5.get("dc_breakout_up") else ""
            return {
                "type": "EMA_MOMENTUM", "structure": "LONG_OPTION",
                "direction": "CALL", "symbol": snap5["symbol"],
                "price": price, "confidence": min(conf, 95),
                "vwap": snap5["vwap"], "rr_ratio": rr,
                "reason": f"EMA cross UP RSI:{snap5['rsi']:.0f} RR:{rr}:1{dc_tag}",
                "stop_level": stop, "target_level": target,
                "time": datetime.now(),
            }

        # BEARISH CROSS
        if snap5["ema_cross_down"] and snap5["macd_histogram"] < 0 and snap5["price_vs_vwap"] < 0:
            stop = round((snap5["ema21"] or price) + atr * 0.5, 2)
            target = round(price - atr * 2.5, 2)
            rr_ok, rr = self._check_rr(price, stop, target)
            if not rr_ok:
                return None

            conf = 65
            if snap5["volume_surge"]:
                conf += 12
            if 30 < snap5["rsi"] < 50:
                conf += 8
            if snap1 and snap1.get("ema_cross_down"):
                conf += 10
            # EMA slope: falling EMA9 adds momentum confirmation
            if snap5.get("ema9_slope") == "DOWN":
                conf += 6
            # Donchian structural breakout below channel
            if snap5.get("dc_breakout_down"):
                conf += 12
            # EMA50 macro alignment
            if snap5.get("ema50_trend") == "DOWN":
                conf += 6
            elif snap5.get("ema50_trend") == "UP":
                conf -= 8
            # GEX handled in outer scan() loop — do not apply here (avoid double-count)

            dc_tag = " DC_BRK" if snap5.get("dc_breakout_down") else ""
            return {
                "type": "EMA_MOMENTUM", "structure": "LONG_OPTION",
                "direction": "PUT", "symbol": snap5["symbol"],
                "price": price, "confidence": min(conf, 95),
                "vwap": snap5["vwap"], "rr_ratio": rr,
                "reason": f"EMA cross DN RSI:{snap5['rsi']:.0f} RR:{rr}:1{dc_tag}",
                "stop_level": stop, "target_level": target,
                "time": datetime.now(),
            }
        return None

    def _orb_breakout(self, snap5, snap1, breadth, em_ctx):
        """Opening range breakout — first 30 min only."""
        h = datetime.now().hour + datetime.now().minute / 60.0
        if not (8.58 <= h <= 9.5):
            return None
        if em_ctx.get("exhausted"):
            return None

        price = snap5["price"]
        or_h, or_l = snap5["or_high"], snap5["or_low"]
        if or_h == or_l:
            return None
        atr = snap5["atr"]

        # BULLISH BREAK
        if snap5["or_breakout_up"] and snap5["volume_surge"] and snap5["macd_histogram"] > 0:
            stop = round(or_h - atr * 0.3, 2)
            target = round(price + (or_h - or_l) * 1.5, 2)
            rr_ok, rr = self._check_rr(price, stop, target)
            if not rr_ok:
                return None

            conf = 72
            if snap5["price_vs_vwap"] > 0:
                conf += 8
            if snap1 and snap1.get("momentum") == "BULLISH":
                conf += 8

            return {
                "type": "ORB_BREAKOUT", "structure": "LONG_OPTION",
                "direction": "CALL", "symbol": snap5["symbol"],
                "price": price, "confidence": min(conf, 95),
                "vwap": snap5["vwap"], "rr_ratio": rr,
                "reason": f"ORB UP RR:{rr}:1",
                "stop_level": stop, "target_level": target,
                "time": datetime.now(),
            }

        # BEARISH BREAK
        if snap5["or_breakout_down"] and snap5["volume_surge"] and snap5["macd_histogram"] < 0:
            stop = round(or_l + atr * 0.3, 2)
            target = round(price - (or_h - or_l) * 1.5, 2)
            rr_ok, rr = self._check_rr(price, stop, target)
            if not rr_ok:
                return None

            conf = 72
            if snap5["price_vs_vwap"] < 0:
                conf += 8
            if snap1 and snap1.get("momentum") == "BEARISH":
                conf += 8

            return {
                "type": "ORB_BREAKOUT", "structure": "LONG_OPTION",
                "direction": "PUT", "symbol": snap5["symbol"],
                "price": price, "confidence": min(conf, 95),
                "vwap": snap5["vwap"], "rr_ratio": rr,
                "reason": f"ORB DN RR:{rr}:1",
                "stop_level": stop, "target_level": target,
                "time": datetime.now(),
            }
        return None

    def _momentum_fade(self, snap5, snap1):
        """Fade overbought/oversold extremes back toward VWAP."""
        price = snap5["price"]
        atr = snap5["atr"]

        # FADE OVERBOUGHT → buy put
        # Hard block: don't fade a Donchian structural breakout upward
        if snap5.get("dc_breakout_up"):
            return None

        if (snap5["rsi"] > 78 and
                snap5["vwap_band"] in ("EXTREME_OB", "OB") and
                price > snap5["bb_upper"]):
            stop = round(price + atr * 0.5, 2)
            target = round(snap5["vwap"], 2)
            rr_ok, rr = self._check_rr(price, stop, target)
            if not rr_ok:
                return None

            conf = 65
            if snap5["volume_ratio"] < 1.0:
                conf += 8
            if snap1 and snap1.get("rsi", 50) > 80:
                conf += 8

            return {
                "type": "MOMENTUM_FADE", "structure": "LONG_OPTION",
                "direction": "PUT", "symbol": snap5["symbol"],
                "price": price, "confidence": min(conf, 88),
                "vwap": snap5["vwap"], "rr_ratio": rr,
                "reason": f"Fade OB RSI:{snap5['rsi']:.0f} RR:{rr}:1",
                "stop_level": stop, "target_level": target,
                "time": datetime.now(),
            }

        # FADE OVERSOLD → buy call
        # Hard block: don't fade a Donchian structural breakout downward
        if snap5.get("dc_breakout_down"):
            return None

        if (snap5["rsi"] < 22 and
                snap5["vwap_band"] in ("EXTREME_OS", "OS") and
                price < snap5["bb_lower"]):
            stop = round(price - atr * 0.5, 2)
            target = round(snap5["vwap"], 2)
            rr_ok, rr = self._check_rr(price, stop, target)
            if not rr_ok:
                return None

            conf = 65
            if snap5["volume_ratio"] < 1.0:
                conf += 8
            if snap1 and snap1.get("rsi", 50) < 20:
                conf += 8

            return {
                "type": "MOMENTUM_FADE", "structure": "LONG_OPTION",
                "direction": "CALL", "symbol": snap5["symbol"],
                "price": price, "confidence": min(conf, 88),
                "vwap": snap5["vwap"], "rr_ratio": rr,
                "reason": f"Fade OS RSI:{snap5['rsi']:.0f} RR:{rr}:1",
                "stop_level": stop, "target_level": target,
                "time": datetime.now(),
            }
        return None
