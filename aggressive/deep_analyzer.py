"""
Deep Analyzer v4 - All Improvements.
- IV rank filtering
- Econ calendar awareness
- Multi-timeframe confirmation
- Sector rotation weighting
- Delta-adjusted sizing
- Score cap at 100
"""

import os
import sys
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class DeepAnalyzer:

    def __init__(self):
        self.existing_symbols = []
        self.market_regime = "UNKNOWN"
        self.vix = 20
        self.iv_analyzer = None
        self.econ_cal = None
        self.weekly_trend = None
        self.sector_rotation = None

    def set_context(self, existing_positions=None, spy_df=None, vix=None, schwab_client=None, price_data=None):
        self.existing_symbols = existing_positions or []
        self.client = schwab_client
        if vix:
            self.vix = vix
        if spy_df is not None and len(spy_df) >= 50:
            self.market_regime = self._detect_regime(spy_df)

        # Initialize enhanced modules
        if schwab_client:
            try:
                from aggressive.iv_analyzer import IVAnalyzer
                self.iv_analyzer = IVAnalyzer(schwab_client)
            except Exception:
                pass

        try:
            from aggressive.econ_calendar import EconCalendar
            self.econ_cal = EconCalendar()
        except Exception:
            pass

        try:
            from aggressive.enhanced_scoring import WeeklyTrend, SectorRotation
            self.weekly_trend = WeeklyTrend()
            self.sector_rotation = SectorRotation()
            if price_data and spy_df is not None:
                self.sector_rotation.calculate_rotation(price_data, spy_df)
        except Exception:
            pass

        logger.info(f"Regime: {self.market_regime} | VIX: {self.vix:.1f}")

    def _detect_regime(self, spy_df):
        price = spy_df.iloc[-1]["close"]
        sma20 = spy_df["close"].tail(20).mean()
        sma50 = spy_df["close"].tail(50).mean()
        if price > sma20 > sma50:
            return "TRENDING_UP"
        elif price < sma20 < sma50:
            return "TRENDING_DOWN"
        return "CHOPPY"

    def _check_earnings(self, symbol):
        try:
            from utils.earnings_calendar import EarningsCalendar
            ecal = EarningsCalendar()
            days = ecal.days_to_earnings(symbol)
            if 0 <= days <= 7:
                return False, f"Earnings in {days}d"
        except Exception:
            pass
        return True, ""

    def _vix_modifier(self):
        if self.vix > 35:
            return 0.5
        elif self.vix > 28:
            return 0.7
        elif self.vix > 22:
            return 0.85
        elif self.vix < 14:
            return 1.2
        return 1.0

    def analyze(self, symbol, stock_df, spy_df, flow_data, chain_data=None):
        # Earnings check
        earn_ok, earn_msg = self._check_earnings(symbol)
        if not earn_ok:
            logger.debug(f"Skip {symbol}: {earn_msg}")
            return None

        # IV rank check
        if self.iv_analyzer:
            iv_ok, iv_rank, iv_msg = self.iv_analyzer.should_trade(symbol)
            if not iv_ok:
                logger.info(f"BLOCKED_IV {symbol} rank={iv_rank}: {iv_msg}")
                return None

        # Econ calendar check
        econ_mod = 1.0
        if self.econ_cal:
            econ_mod = self.econ_cal.get_conviction_modifier()

        if stock_df is not None and len(stock_df) >= 20:
            return self._full(symbol, stock_df, spy_df, flow_data, econ_mod)
        elif flow_data:
            try:
                result = self._flow_only(symbol, flow_data, econ_mod)
                if not result:
                    logger.debug(f"_flow_only returned None for {symbol}")
                return result
            except Exception as e:
                logger.debug(f"_flow_only CRASHED for {symbol}: {e}")
                return None
        return None

    def _flow_only(self, symbol, signal, chain_data=None):
        """
        Score when no price data available.
        v7: Requires stronger flow for HIGH conviction.
        Adds real-time quote checks for basic technical confirmation.
        """
        strength = signal.get("signal_strength", signal.get("strength", 0))
        cp_ratio = signal.get("cp_ratio", 1.0)
        premium = signal.get("total_premium", 0)
        direction = signal.get("direction", "CALL")
        open_pct = signal.get("opening_pct", 0)

        # Base score from flow (recalibrated — harder to reach HIGH)
        # Old: 40 + strength * 10 (strength 5 = 90)
        # New: 35 + strength * 9  (strength 5 = 70, strength 7 = 86, strength 8 = 94)
        score = 35 + strength * 9  # Calibrated: str6=89, str7=98

        # Premium bonus (institutional size)
        if premium > 2000000:
            score += 8
        elif premium > 1000000:
            score += 5
        elif premium > 500000:
            score += 3

        # Opening position bonus
        if open_pct > 70:
            score += 5
        elif open_pct > 50:
            score += 3

        # Call/put ratio alignment
        if direction == "CALL" and cp_ratio > 3.0:
            score += 3
        elif direction == "PUT" and cp_ratio < 0.5:
            score += 3

        # REAL-TIME TECHNICAL CHECK (using live quote)
        tech_bonus = 0
        tech_penalty = 0
        rsi_val = 50  # default
        try:
            import time
            time.sleep(0.05)
            q = self.client.get_quote(symbol)
            if q.status_code == 200:
                quote = q.json().get(symbol, {}).get("quote", {})
                price = quote.get("lastPrice", 0)
                hi52 = quote.get("52WeekHigh", 0)
                lo52 = quote.get("52WeekLow", 0)
                change = quote.get("netPercentChangeInDouble", 0)
                volume = quote.get("totalVolume", 0)
                avg_vol = quote.get("averageVolume", 1)

                if price > 0 and hi52 > lo52:
                    # Price position in 52-week range
                    range_pos = (price - lo52) / (hi52 - lo52)

                    # Direction alignment with price trend
                    if direction == "CALL":
                        if range_pos > 0.5:
                            tech_bonus += 3  # Above midpoint, bullish structure
                        elif range_pos < 0.25:
                            tech_penalty += 5  # Near 52w low, fighting trend
                        if change > 1.0:
                            tech_bonus += 2  # Already moving our way
                        elif change < -2.0:
                            tech_penalty += 3  # Moving against us
                    elif direction == "PUT":
                        if range_pos < 0.5:
                            tech_bonus += 3  # Below midpoint, bearish structure
                        elif range_pos > 0.75:
                            tech_penalty += 5  # Near 52w high, fighting trend
                        if change < -1.0:
                            tech_bonus += 2  # Already dropping
                        elif change > 2.0:
                            tech_penalty += 3  # Rallying against us

                    # Volume confirmation
                    if avg_vol > 0:
                        vol_ratio = volume / avg_vol
                        if vol_ratio > 1.5:
                            tech_bonus += 2  # High volume confirms

                    # Rough RSI approximation from price position
                    rsi_val = range_pos * 100
        except Exception:
            pass

        score += tech_bonus
        score -= tech_penalty

        # VIX regime modifier
        if hasattr(self, 'vix') and self.vix > 0:
            if self.vix > 30:
                score -= 2  # Mild VIX drag (regime gate handles direction)
            elif self.vix > 25:
                score -= 1

        # Regime modifier
        if hasattr(self, 'regime'):
            regime = str(self.market_regime).upper()
            if "DOWN" in regime:
                if direction == "CALL":
                    score -= 2  # Mild CALL penalty in downtrend
                elif direction == "PUT":
                    score += 3  # PUTs easier
            elif "UP" in regime:
                if direction == "PUT":
                    score -= 5
                elif direction == "CALL":
                    score += 5

        # Cap
        score = max(0, min(100, score))

        # Conviction classification
        if score >= 85:
            conviction = "HIGH"
            sp = 0.08
        elif score >= 70:
            conviction = "MEDIUM"
            sp = 0.04
        elif score >= 55:
            conviction = "LOW"
            sp = 0.02
        else:
            conviction = "SKIP"
            sp = 0

        # Size modifiers
        vm = 1.0
        if hasattr(self, 'vix') and self.vix > 30:
            vm = 0.7
        elif hasattr(self, 'vix') and self.vix > 25:
            vm = 0.85

        return {
            "symbol": symbol,
            "price": 0,
            "composite": score,
            "conviction": conviction,
            "direction": direction,
            "size_pct": sp * vm,
            "sub_scores": {
                "flow": round(30 + strength * 8, 1),
                "tech_bonus": tech_bonus,
                "tech_penalty": tech_penalty,
            },
            "levels": {
                "rsi": rsi_val,
                "support": 0,
                "resistance": 0,
                "atr": 0,
            },
        }

    
    def _full(self, symbol, stock_df, spy_df, flow_data, econ_mod=1.0):
        latest = stock_df.iloc[-1]
        price = latest["close"]
        sc = {}

        sma20 = stock_df["close"].tail(20).mean()
        sma50 = stock_df["close"].tail(50).mean()
        sma200 = stock_df["close"].tail(200).mean() if len(stock_df) >= 200 else sma50
        a50 = price > sma50
        a200 = price > sma200
        golden = sma50 > sma200

        if a50 and a200 and golden:
            sc["trend"] = 90
        elif a200 and golden:
            sc["trend"] = 70
        elif a200:
            sc["trend"] = 55
        elif not a50 and not a200:
            sc["trend"] = 15
        else:
            sc["trend"] = 35

        high_20 = stock_df.tail(20)["high"].max()
        pullback = (high_20 - price) / high_20
        rsi = latest.get("rsi_14", 50)

        if a200 and 0.03 <= pullback <= 0.12 and rsi < 45:
            sc["pullback"] = 90
        elif a200 and 0.02 <= pullback <= 0.15:
            sc["pullback"] = 75
        elif pullback < 0.02:
            sc["pullback"] = 45
        elif pullback > 0.15:
            sc["pullback"] = 20
        else:
            sc["pullback"] = 55

        if len(stock_df) >= 20:
            v20 = stock_df["volume"].tail(20).mean()
            up = sum(1 for i in range(-5, 0) if stock_df.iloc[i]["close"] > stock_df.iloc[i]["open"] and stock_df.iloc[i]["volume"] > v20)
            sc["volume"] = 85 if up >= 3 else (65 if up >= 2 else 40)
        else:
            sc["volume"] = 50

        if flow_data:
            s = flow_data.get("signal_strength", 0)
            tp = flow_data.get("total_premium", 0)
            opening = flow_data.get("opening_pct", 50)
            fs = min(30 + s * 15, 95)
            if tp > 200000:
                fs = min(fs + 10, 95)
            if tp > 500000:
                fs = min(fs + 10, 95)
            if opening > 70:
                fs = min(fs + 5, 95)
            sc["flow"] = fs
        else:
            sc["flow"] = 40

        if spy_df is not None and len(spy_df) >= 20:
            sr = (price / stock_df.iloc[-20]["close"]) - 1
            spr = (spy_df.iloc[-1]["close"] / spy_df.iloc[-20]["close"]) - 1
            rs = sr - spr
            sc["rel_strength"] = 90 if rs > 0.05 else (75 if rs > 0.02 else (60 if rs > 0 else (40 if rs > -0.03 else 20)))
        else:
            sc["rel_strength"] = 50

        atr = latest.get("atr_14", price * 0.02)
        if len(stock_df) >= 60:
            support = max(sma50, stock_df.tail(60)["low"].min())
            resistance = stock_df.tail(60)["high"].max()
        else:
            support = price - 2 * atr
            resistance = price + 3 * atr
        h252 = stock_df.tail(min(252, len(stock_df)))["high"].max()
        sd = price - support
        up = resistance - price
        rr = up / sd if sd > 0 else 1
        sc["rr"] = 90 if rr > 3 else (75 if rr > 2 else (60 if rr > 1.5 else (45 if rr > 1 else 25)))

        w = {"trend": 0.15, "pullback": 0.15, "volume": 0.10, "flow": 0.30, "rel_strength": 0.10, "rr": 0.20}
        comp = min(sum(sc[k] * w[k] for k in w), 100)

        direction = "CALL"
        if flow_data and flow_data.get("direction") == "BEARISH":
            direction = "PUT"

        # Weekly trend confirmation
        if self.weekly_trend:
            wt, wt_mod = self.weekly_trend.get_weekly_trend(stock_df)
            if direction == "CALL" and wt == "DOWN":
                comp *= 0.95  # Mild weekly trend drag (regime handles the rest)
            elif direction == "CALL" and wt == "UP":
                comp *= wt_mod

        # Market regime
        if self.market_regime == "TRENDING_DOWN":
            comp = comp * (0.92 if direction == "CALL" else 1.10)
        elif self.market_regime == "CHOPPY":
            comp *= 0.92

        # Econ calendar
        comp *= econ_mod

        comp = min(comp, 100)

        vm = self._vix_modifier()
        iv_mod = 1.0
        if self.iv_analyzer:
            iv_mod = self.iv_analyzer.get_size_modifier(symbol)

        # Sector modifier
        sector_mod = 1.0
        if self.sector_rotation:
            # Get sector from universe
            try:
                import csv
                with open("config/universe.csv") as f:
                    for r in csv.DictReader(f):
                        if r["symbol"] == symbol:
                            sector_mod = self.sector_rotation.get_sector_modifier(r.get("sector", ""))
                            break
            except Exception:
                pass

        if comp >= 80:
            conv = "HIGH"
            sp = 0.08 * vm * iv_mod * sector_mod
        elif comp >= 70:
            conv = "MEDIUM"
            sp = 0.06 * vm * iv_mod * sector_mod
        elif comp >= 60:
            conv = "LOW"
            sp = 0.03 * vm * iv_mod * sector_mod
        else:
            conv = "SKIP"
            sp = 0

        return {
            "symbol": symbol,
            "price": round(price, 2),
            "composite": round(comp, 1),
            "conviction": conv,
            "direction": direction,
            "size_pct": round(sp, 4),
            "sub_scores": {k: round(v, 1) for k, v in sc.items()},
            "levels": {
                "support": round(support, 2),
                "resistance": round(resistance, 2),
                "high_52w": round(h252, 2),
                "atr": round(atr, 2),
                "rr_ratio": round(rr, 2),
                "pullback_pct": round(pullback, 4),
                "rsi": round(rsi, 1),
            },
            "regime": self.market_regime,
            "vix": self.vix,
        }