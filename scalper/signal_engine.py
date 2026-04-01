"""
Scalper Signal Engine v5 - Quant Rebuild.
Philosophy: sell premium by default, buy direction only
when conviction is overwhelming.

Iron condor is the bread-and-butter (range days).
Directional buys require 3:1 minimum R:R.
Expected move filters everything.
Max 8 trades per day.
"""

from datetime import datetime
from loguru import logger


class ScalperSignal:

    MIN_ATR = 0.20
    MIN_CONFIDENCE = 70  # Raised from 65
    MAX_PER_STRATEGY = 2  # Tighter: max 2 per type
    STRATEGY_COOLDOWN = 1200  # 20 min cooldown
    MIN_RR_DIRECTIONAL = 3.0  # 3:1 minimum for buys

    def __init__(self):
        self.recent_signals = []
        self._cooldown = {}
        self._strategy_cooldown = {}
        self._strategy_count = {}
        self._eod_pin_used = set()
        self._trade_date = None

    def _reset_daily(self):
        today = datetime.now().date()
        if self._trade_date != today:
            self._strategy_count = {}
            self._eod_pin_used = set()
            self._trade_date = today

    def _can_use(self, strat, sym):
        self._reset_daily()
        count = self._strategy_count.get(strat, 0)
        if count >= self.MAX_PER_STRATEGY:
            return False
        if strat == "EOD_PIN" and sym in self._eod_pin_used:
            return False
        key = f"{strat}_{sym}"
        if key in self._strategy_cooldown:
            if (datetime.now() - self._strategy_cooldown[key]).total_seconds() < self.STRATEGY_COOLDOWN:
                return False
        return True

    def _record(self, strat, sym):
        self._strategy_count[strat] = self._strategy_count.get(strat, 0) + 1
        self._strategy_cooldown[f"{strat}_{sym}"] = datetime.now()
        if strat == "EOD_PIN":
            self._eod_pin_used.add(sym)

    def _check_rr(self, entry, stop, target):
        """Enforce minimum 3:1 R:R on directional buys."""
        risk = abs(entry - stop)
        reward = abs(target - entry)
        if risk <= 0:
            return False, 0
        rr = reward / risk
        return rr >= self.MIN_RR_DIRECTIONAL, round(rr, 1)

    def scan(self, snapshot_5m, snapshot_1m=None,
             allowed_strategies=None, gex_profile=None,
             breadth=None, expected_move=None):
        """
        Dual timeframe scan.
        5m = directional bias. 1m = entry confirmation.
        """
        if not snapshot_5m or snapshot_5m["candle_count"] < 5:
            return []

        signals = []
        sym = snapshot_5m["symbol"]
        now = datetime.now()
        self._reset_daily()

        if sym in self._cooldown:
            if (now - self._cooldown[sym]).total_seconds() < 300:
                return []

        if snapshot_5m["atr"] < self.MIN_ATR:
            return []

        if not allowed_strategies:
            allowed_strategies = ["IRON_CONDOR", "CREDIT_SPREAD", "PREMIUM_SELL"]

        # Expected move filter - #1 priority
        em_filter = self._get_em_context(snapshot_5m, expected_move)

        for strat in allowed_strategies:
            if not self._can_use(strat, sym):
                continue

            sig = None
            if strat == "IRON_CONDOR":
                sig = self._iron_condor(snapshot_5m, expected_move, gex_profile)
            elif strat == "CREDIT_SPREAD":
                sig = self._credit_spread(snapshot_5m, gex_profile, expected_move)
            elif strat == "PREMIUM_SELL":
                sig = self._premium_sell(snapshot_5m, gex_profile)
            elif strat == "VWAP_PULLBACK":
                sig = self._vwap_pullback(snapshot_5m, snapshot_1m, gex_profile, breadth, em_filter)
            elif strat in ("DIRECTIONAL_BUY", "EMA_MOMENTUM"):
                sig = self._ema_momentum(snapshot_5m, snapshot_1m, gex_profile, breadth, em_filter)
            elif strat == "ORB_BREAKOUT":
                sig = self._orb_breakout(snapshot_5m, snapshot_1m, breadth, em_filter)
            elif strat == "MOMENTUM_FADE":
                sig = self._momentum_fade(snapshot_5m, snapshot_1m)

            elif strat == "NAKED_PUT":
                sig = self._naked_put(snapshot_5m, gex_profile, expected_move)
            elif strat == "NAKED_CALL":
                sig = self._naked_call(snapshot_5m, gex_profile, expected_move)
            elif strat == "STRADDLE_SELL":
                sig = self._straddle_sell(snapshot_5m, gex_profile, expected_move)
            elif strat == "STRANGLE_SELL":
                sig = self._strangle_sell(snapshot_5m, gex_profile, expected_move)
            elif strat == "RATIO_SPREAD":
                sig = self._ratio_spread(snapshot_5m, gex_profile, breadth)
            elif strat == "EOD_PIN":
                sig = self._eod_pin(snapshot_5m, gex_profile)

            if sig and sig["confidence"] >= self.MIN_CONFIDENCE:
                # Hard blocks
                if sig["structure"] == "LONG_OPTION":
                    if breadth and not self._breadth_ok(breadth, sig["direction"]):
                        continue
                    if gex_profile and gex_profile.get("regime") == "POSITIVE":
                        sig["confidence"] -= 10
                        if sig["confidence"] < self.MIN_CONFIDENCE:
                            continue
                elif sig["structure"] in ("CREDIT_SPREAD", "IRON_CONDOR"):
                    if gex_profile and gex_profile.get("regime") == "NEGATIVE" and sig["structure"] in ("CREDIT_SPREAD", "IRON_CONDOR", "NAKED_PUT", "NAKED_CALL", "STRADDLE", "STRANGLE"):
                        continue

                self._record(strat, sym)
                signals.append(sig)

        if signals:
            self._cooldown[sym] = now

        return signals

    def _get_em_context(self, snap, em):
        """Expected move context."""
        if not em:
            return {"inside": True, "pct_used": 0}
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

    def _gex_bonus(self, gex, structure):
        if not gex:
            return 0
        r = gex.get("regime", "")
        if r == "POSITIVE" and structure in ("CREDIT_SPREAD", "IRON_CONDOR", "NAKED_PUT", "NAKED_CALL", "STRADDLE", "STRANGLE", "RATIO_SPREAD"):
            return 10
        if r == "NEGATIVE" and structure == "LONG_OPTION":
            return 10
        if r == "POSITIVE" and structure == "LONG_OPTION":
            return -10
        return 0

    # â”€â”€ PREMIUM SELLING STRATEGIES (DEFAULT) â”€â”€

    def _iron_condor(self, snap, em=None, gex=None):
        """Bread-and-butter: iron condor on range days."""
        price = snap["price"]
        vwap = snap["vwap"]
        if not vwap:
            return None
        if abs(snap["vwap_pct"]) > 0.12:
            return None
        # ATR-relative move check: block IC on gap/volatile days
        atr = snap.get("atr", 1)
        day_move = abs(snap.get("price", 0) - snap.get("session_open", snap.get("price", 0)))
        if atr > 0 and day_move / atr > 1.5:
            return None
        if snap["rsi"] < 35 or snap["rsi"] > 65:
            return None

        # Must be inside expected move
        if em:
            if price > em["upper_bound"] or price < em["lower_bound"]:
                return None

        conf = 72
        if not snap["volume_surge"]:
            conf += 5
        if 42 < snap["rsi"] < 58:
            conf += 8
        conf += self._gex_bonus(gex, "IRON_CONDOR")
        if em:
            conf += 5

        return {
            "type": "IRON_CONDOR", "structure": "IRON_CONDOR",
            "direction": "NEUTRAL", "symbol": snap["symbol"],
            "price": price, "confidence": min(conf, 92),
            "vwap": vwap,
            "reason": f"IC range RSI:{snap['rsi']:.0f} VWAP:{snap['vwap_pct']:+.2f}%",
            "stop_level": round(price - snap["atr"] * 2, 2),
            "target_level": round(price, 2),
            "time": datetime.now(),
        }

    def _credit_spread(self, snap, gex=None, em=None):
        price = snap["price"]
        vwap = snap["vwap"]
        if not vwap:
            return None
        if abs(snap["macd_histogram"]) > 0.5:
            return None
        if snap["rsi"] < 30 or snap["rsi"] > 70:
            return None
        if abs(snap["vwap_pct"]) > 0.15:
            return None
        if em and not (em["lower_bound"] <= price <= em["upper_bound"]):
            return None

        conf = 68
        if not snap["volume_surge"]:
            conf += 5
        if 40 < snap["rsi"] < 60:
            conf += 8
        conf += self._gex_bonus(gex, "CREDIT_SPREAD")

        return {
            "type": "CREDIT_SPREAD", "structure": "CREDIT_SPREAD",
            "direction": "CALL",
            "symbol": snap["symbol"],
            "price": price, "confidence": min(conf, 90),
            "vwap": vwap,
            "reason": f"Credit RSI:{snap['rsi']:.0f}",
            "stop_level": round(price - snap["atr"] * 1.5, 2),
            "target_level": round(price, 2),
            "time": datetime.now(),
        }

    def _premium_sell(self, snap, gex=None):
        h = datetime.now().hour + datetime.now().minute / 60.0
        if h < 13.0:
            return None
        price = snap["price"]
        if abs(snap["vwap_pct"]) > 0.20:
            return None

        conf = 68
        if h >= 14.0:
            conf += 8
        if 40 < snap["rsi"] < 60:
            conf += 8
        conf += self._gex_bonus(gex, "CREDIT_SPREAD")

        direction = "CALL" if price > snap["vwap"] else "PUT"
        return {
            "type": "PREMIUM_SELL", "structure": "CREDIT_SPREAD",
            "direction": direction,
            "symbol": snap["symbol"],
            "price": price, "confidence": min(conf, 90),
            "vwap": snap["vwap"],
            "reason": f"Theta sell {h:.1f}h",
            "stop_level": round(
                price - snap["atr"]*1.5 if direction == "CALL"
                else price + snap["atr"]*1.5, 2),
            "target_level": round(price, 2),
            "time": datetime.now(),
        }

    def _eod_pin(self, snap, gex=None):
        h = datetime.now().hour + datetime.now().minute / 60.0
        if h < 14.0 or not gex:
            return None
        price = snap["price"]
        pin = gex.get("pin_level", price)
        if abs(price - pin) / price > 0.003:
            return None

        conf = 72
        if gex.get("regime") == "POSITIVE":
            conf += 10
        if 40 < snap["rsi"] < 60:
            conf += 5

        return {
            "type": "EOD_PIN", "structure": "CREDIT_SPREAD",
            "direction": "CALL",
            "symbol": snap["symbol"],
            "price": price, "confidence": min(conf, 90),
            "vwap": snap["vwap"],
            "reason": f"Pin ${pin} GEX:{gex['regime']}",
            "stop_level": round(pin - snap["atr"], 2),
            "target_level": round(pin, 2),
            "time": datetime.now(),
        }

    # â”€â”€ DIRECTIONAL STRATEGIES (EXCEPTION, NOT RULE) â”€â”€

    def _vwap_pullback(self, snap5, snap1, gex, breadth, em_ctx):
        price = snap5["price"]
        vwap = snap5["vwap"]
        if not vwap:
            return None
        vd = abs(snap5["vwap_pct"])
        if not (0.03 <= vd <= 0.25):
            return None
        # Don't buy direction if move is exhausted
        if em_ctx.get("exhausted"):
            return None

        atr = snap5["atr"]
        cur = snap5.get("current_candle", {})

        # BULLISH
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
            # 1-min confirmation
            if snap1 and snap1.get("momentum") == "BULLISH":
                conf += 10
            if cur and cur.get("close", 0) > cur.get("open", 0):
                conf += 5
            conf += self._gex_bonus(gex, "LONG_OPTION")

            return {
                "type": "VWAP_PULLBACK", "structure": "LONG_OPTION",
                "direction": "CALL", "symbol": snap5["symbol"],
                "price": price, "confidence": min(conf, 95),
                "vwap": vwap, "rr_ratio": rr,
                "reason": f"VWAP PB UP RSI:{snap5['rsi']:.0f} RR:{rr}:1",
                "stop_level": stop, "target_level": target,
                "time": datetime.now(),
            }

        # BEARISH
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
            conf += self._gex_bonus(gex, "LONG_OPTION")

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
        price = snap5["price"]
        if em_ctx.get("exhausted"):
            return None

        atr = snap5["atr"]

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
                conf += 10  # 1-min confirms
            conf += self._gex_bonus(gex, "LONG_OPTION")

            return {
                "type": "EMA_MOMENTUM", "structure": "LONG_OPTION",
                "direction": "CALL", "symbol": snap5["symbol"],
                "price": price, "confidence": min(conf, 95),
                "vwap": snap5["vwap"], "rr_ratio": rr,
                "reason": f"EMA cross UP RSI:{snap5['rsi']:.0f} RR:{rr}:1",
                "stop_level": stop, "target_level": target,
                "time": datetime.now(),
            }

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
            conf += self._gex_bonus(gex, "LONG_OPTION")

            return {
                "type": "EMA_MOMENTUM", "structure": "LONG_OPTION",
                "direction": "PUT", "symbol": snap5["symbol"],
                "price": price, "confidence": min(conf, 95),
                "vwap": snap5["vwap"], "rr_ratio": rr,
                "reason": f"EMA cross DN RSI:{snap5['rsi']:.0f} RR:{rr}:1",
                "stop_level": stop, "target_level": target,
                "time": datetime.now(),
            }
        return None

    def _orb_breakout(self, snap5, snap1, breadth, em_ctx):
        h = datetime.now().hour + datetime.now().minute / 60.0
        if not (8.75 <= h <= 9.5):
            return None
        if em_ctx.get("exhausted"):
            return None

        price = snap5["price"]
        or_h, or_l = snap5["or_high"], snap5["or_low"]
        if or_h == or_l:
            return None
        atr = snap5["atr"]

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
        price = snap5["price"]
        atr = snap5["atr"]

        if snap5["rsi"] > 78 and snap5["vwap_band"] in ("EXTREME_OB", "OB") and price > snap5["bb_upper"]:
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

        if snap5["rsi"] < 22 and snap5["vwap_band"] in ("EXTREME_OS", "OS") and price < snap5["bb_lower"]:
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

    # ══ LEVEL 3 STRATEGIES ══

    def _naked_put(self, snap5, gex=None, em=None):
        """
        Sell naked put: bullish, collect premium.
        Best on range-bound or mildly bullish days.
        GEX positive preferred (mean-reverting).
        """
        price = snap5["price"]
        vwap = snap5["vwap"]
        if not vwap:
            return None
        # Only sell puts when price is above VWAP (bullish bias)
        if snap5["price_vs_vwap"] < -0.10:
            return None
        if snap5["rsi"] < 35:
            return None  # Don't sell puts into weakness
        if em and price < em["lower_bound"]:
            return None  # Outside expected move

        conf = 70
        if snap5["ema_trend"] == "UP":
            conf += 8
        if 45 < snap5["rsi"] < 65:
            conf += 5
        conf += self._gex_bonus(gex, "NAKED_PUT")

        return {
            "type": "NAKED_PUT", "structure": "NAKED_PUT",
            "direction": "PUT", "symbol": snap5["symbol"],
            "price": price, "confidence": min(conf, 92),
            "vwap": vwap,
            "reason": f"Naked put sell RSI:{snap5['rsi']:.0f} EMA:{snap5['ema_trend']}",
            "stop_level": round(price - snap5["atr"] * 2, 2),
            "target_level": round(price, 2),
            "time": datetime.now(),
        }

    def _naked_call(self, snap5, gex=None, em=None):
        """
        Sell naked call: bearish, collect premium.
        Best on range-bound or mildly bearish days.
        """
        price = snap5["price"]
        vwap = snap5["vwap"]
        if not vwap:
            return None
        if snap5["price_vs_vwap"] > 0.10:
            return None
        if snap5["rsi"] > 65:
            return None
        if em and price > em["upper_bound"]:
            return None

        conf = 70
        if snap5["ema_trend"] == "DOWN":
            conf += 8
        if 35 < snap5["rsi"] < 55:
            conf += 5
        conf += self._gex_bonus(gex, "NAKED_CALL")

        return {
            "type": "NAKED_CALL", "structure": "NAKED_CALL",
            "direction": "CALL", "symbol": snap5["symbol"],
            "price": price, "confidence": min(conf, 92),
            "vwap": vwap,
            "reason": f"Naked call sell RSI:{snap5['rsi']:.0f} EMA:{snap5['ema_trend']}",
            "stop_level": round(price + snap5["atr"] * 2, 2),
            "target_level": round(price, 2),
            "time": datetime.now(),
        }

    def _straddle_sell(self, snap5, gex=None, em=None):
        """
        Sell ATM straddle: neutral, max premium collection.
        Only on very range-bound days with low momentum.
        GEX must be positive (pinning environment).
        """
        price = snap5["price"]
        vwap = snap5["vwap"]
        if not vwap:
            return None
        if abs(snap5["vwap_pct"]) > 0.08:
            return None  # Must be very close to VWAP
        if snap5["rsi"] < 40 or snap5["rsi"] > 60:
            return None
        if abs(snap5["macd_histogram"]) > 0.3:
            return None  # Low momentum required
        # GEX must be positive for straddle selling
        if gex and gex.get("regime") != "POSITIVE":
            return None

        conf = 72
        if not snap5["volume_surge"]:
            conf += 5
        if 45 < snap5["rsi"] < 55:
            conf += 8
        if em:
            conf += 5

        return {
            "type": "STRADDLE_SELL", "structure": "STRADDLE",
            "direction": "NEUTRAL", "symbol": snap5["symbol"],
            "price": price, "confidence": min(conf, 92),
            "vwap": vwap,
            "reason": f"Straddle sell RSI:{snap5['rsi']:.0f} GEX:POS",
            "stop_level": round(price - snap5["atr"] * 2.5, 2),
            "target_level": round(price, 2),
            "time": datetime.now(),
        }

    def _strangle_sell(self, snap5, gex=None, em=None):
        """
        Sell OTM strangle: neutral with wider cushion.
        More forgiving than straddle.
        """
        price = snap5["price"]
        vwap = snap5["vwap"]
        if not vwap:
            return None
        if abs(snap5["vwap_pct"]) > 0.12:
            return None
        if snap5["rsi"] < 35 or snap5["rsi"] > 65:
            return None

        conf = 70
        if not snap5["volume_surge"]:
            conf += 5
        if 40 < snap5["rsi"] < 60:
            conf += 8
        conf += self._gex_bonus(gex, "STRANGLE")
        if em:
            conf += 3

        return {
            "type": "STRANGLE_SELL", "structure": "STRANGLE",
            "direction": "NEUTRAL", "symbol": snap5["symbol"],
            "price": price, "confidence": min(conf, 92),
            "vwap": vwap,
            "reason": f"Strangle sell RSI:{snap5['rsi']:.0f}",
            "stop_level": round(price - snap5["atr"] * 3, 2),
            "target_level": round(price, 2),
            "time": datetime.now(),
        }

    def _ratio_spread(self, snap5, gex=None, breadth=None):
        """
        Ratio spread: directional + premium income.
        Buy 1 ATM, sell 2 OTM. Net credit or small debit.
        """
        price = snap5["price"]
        if snap5["rsi"] < 30 or snap5["rsi"] > 70:
            return None

        direction = None
        if snap5["ema_trend"] == "UP" and snap5["momentum"] == "BULLISH":
            direction = "CALL"
        elif snap5["ema_trend"] == "DOWN" and snap5["momentum"] == "BEARISH":
            direction = "PUT"

        if not direction:
            return None

        conf = 68
        if snap5["volume_surge"]:
            conf += 8
        conf += self._gex_bonus(gex, "RATIO_SPREAD")

        return {
            "type": "RATIO_SPREAD", "structure": "RATIO_SPREAD",
            "direction": direction, "symbol": snap5["symbol"],
            "price": price, "confidence": min(conf, 88),
            "vwap": snap5["vwap"],
            "reason": f"Ratio {direction} RSI:{snap5['rsi']:.0f}",
            "stop_level": round(
                price - snap5["atr"] * 1.5 if direction == "CALL"
                else price + snap5["atr"] * 1.5, 2),
            "target_level": round(
                price + snap5["atr"] * 1.5 if direction == "CALL"
                else price - snap5["atr"] * 1.5, 2),
            "time": datetime.now(),
        }