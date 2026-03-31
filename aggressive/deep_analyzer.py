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
                logger.debug(f"Skip {symbol}: {iv_msg}")
                return None

        # Econ calendar check
        econ_mod = 1.0
        if self.econ_cal:
            econ_mod = self.econ_cal.get_conviction_modifier()

        if stock_df is not None and len(stock_df) >= 20:
            return self._full(symbol, stock_df, spy_df, flow_data, econ_mod)
        elif flow_data:
            return self._flow_only(symbol, flow_data, econ_mod)
        return None

    def _flow_only(self, symbol, flow_data, econ_mod=1.0):
        strength = flow_data.get("signal_strength", 0)
        tp = flow_data.get("total_premium", 0)
        price = flow_data.get("price", 0)
        opening = flow_data.get("opening_pct", 50)

        score = 40 + strength * 10
        if tp > 200000:
            score += 10
        if tp > 500000:
            score += 5
        if opening > 70:
            score += 5  # Bonus for new positions
        score = min(score, 100)

        direction = "CALL"
        if flow_data.get("direction") == "BEARISH":
            direction = "PUT"

        if self.market_regime == "TRENDING_DOWN" and direction == "CALL":
            score *= 0.85
        elif self.market_regime == "TRENDING_DOWN" and direction == "PUT":
            score = min(score * 1.10, 100)

        score *= econ_mod
        score = min(score, 100)

        vm = self._vix_modifier()
        iv_mod = 1.0
        if self.iv_analyzer:
            iv_mod = self.iv_analyzer.get_size_modifier(symbol)

        if score >= 80:
            conv = "HIGH"
            sp = 0.08 * vm * iv_mod
        elif score >= 70:
            conv = "MEDIUM"
            sp = 0.06 * vm * iv_mod
        elif score >= 60:
            conv = "LOW"
            sp = 0.03 * vm * iv_mod
        else:
            conv = "SKIP"
            sp = 0

        return {
            "symbol": symbol,
            "price": round(price, 2),
            "composite": round(score, 1),
            "conviction": conv,
            "direction": direction,
            "size_pct": round(sp, 4),
            "sub_scores": {"flow": round(score, 1)},
            "levels": {
                "support": round(price * 0.95, 2),
                "resistance": round(price * 1.10, 2),
                "high_52w": 0, "atr": round(price * 0.02, 2),
                "rr_ratio": 2.0, "pullback_pct": 0, "rsi": 50,
            },
            "regime": self.market_regime,
            "vix": self.vix,
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
                comp *= 0.85
            elif direction == "CALL" and wt == "UP":
                comp *= wt_mod

        # Market regime
        if self.market_regime == "TRENDING_DOWN":
            comp = comp * (0.85 if direction == "CALL" else 1.10)
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