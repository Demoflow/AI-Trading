"""
LETF Sector Analyzer.
Scores each sector across 10 dimensions for high-conviction entries.
"""
import time
from datetime import datetime
from loguru import logger


class SectorAnalyzer:

    def __init__(self, client):
        self.client = client

    def _get_quote(self, symbol):
        try:
            time.sleep(0.08)
            r = self.client.get_quote(symbol)
            if r.status_code == 200:
                return r.json().get(symbol, {}).get("quote", {})
        except Exception:
            pass
        return {}

    def _get_price_history(self, symbol, days=20):
        try:
            time.sleep(0.08)
            r = self.client.get_price_history_every_day(symbol)
            if r.status_code == 200:
                candles = r.json().get("candles", [])
                return candles[-days:] if len(candles) >= days else candles
        except Exception:
            pass
        return []

    def analyze_sector(self, sector_name, sector_info):
        """
        Score a sector 0-100 for bull and bear conviction.
        Returns: {"bull_score": int, "bear_score": int, "signals": dict}
        """
        underlying = sector_info["underlying"]
        quote = self._get_quote(underlying)
        if not quote:
            return {"bull_score": 0, "bear_score": 0, "signals": {}}

        price = quote.get("lastPrice", 0)
        change_pct = quote.get("netPercentChangeInDouble", 0)
        volume = quote.get("totalVolume", 0)
        avg_volume = quote.get("averageVolume", 1)
        high_52 = quote.get("52WeekHigh", price)
        low_52 = quote.get("52WeekLow", price)

        bull_score = 50
        bear_score = 50

        # Cache SPY data
        spy_quote = self._get_quote("SPY")
        spy_change = spy_quote.get("netPercentChangeInDouble", 0) if spy_quote else 0
        signals = {}

        # 1. TREND: Price relative to 52-week range
        range_pct = (price - low_52) / max(high_52 - low_52, 0.01) * 100
        signals["range_pct"] = round(range_pct, 1)
        if range_pct > 70:
            bull_score += 8
            bear_score -= 5
        elif range_pct < 30:
            bear_score += 8
            bull_score -= 5

        # 2. MOMENTUM: Today's change
        signals["change_pct"] = round(change_pct, 2)
        if change_pct > 1.5:
            bull_score += 10
        elif change_pct > 0.5:
            bull_score += 5
        elif change_pct < -1.5:
            bear_score += 10
        elif change_pct < -0.5:
            bear_score += 5

        # 3. VOLUME: Above average = conviction
        vol_ratio = volume / max(avg_volume, 1)
        signals["vol_ratio"] = round(vol_ratio, 2)
        if vol_ratio > 1.5:
            if change_pct > 0:
                bull_score += 8
            else:
                bear_score += 8
        elif vol_ratio < 0.5:
            # Low volume = low conviction
            bull_score -= 5
            bear_score -= 5
            signals["low_volume"] = True

        # 4. RELATIVE STRENGTH vs SPY (use cached SPY data)
        # spy_change already set at top of method
        rs = change_pct - spy_change
        signals["rs_vs_spy"] = round(rs, 2)
        if rs > 1.0:
            bull_score += 10
        elif rs > 0.3:
            bull_score += 5
        elif rs < -1.0:
            bear_score += 10
        elif rs < -0.3:
            bear_score += 5

        # 5. VIX CONTEXT
        vix_quote = self._get_quote("$VIX")
        vix = vix_quote.get("lastPrice", 20) if vix_quote else 20
        signals["vix"] = vix
        if vix > 30:
            bear_score += 5  # Fear elevated
        elif vix < 15:
            bull_score += 5  # Complacency

        # 6. PRICE HISTORY ANALYSIS
        candles = self._get_price_history(underlying)
        if len(candles) >= 10:
            closes = [c["close"] for c in candles]

            # 5-day momentum
            mom_5d = (closes[-1] - closes[-5]) / closes[-5] * 100 if len(closes) >= 5 else 0
            signals["mom_5d"] = round(mom_5d, 2)
            if mom_5d > 3:
                bull_score += 8
            elif mom_5d < -3:
                bear_score += 8

            # 10-day momentum
            mom_10d = (closes[-1] - closes[-10]) / closes[-10] * 100 if len(closes) >= 10 else 0
            signals["mom_10d"] = round(mom_10d, 2)
            if mom_10d > 5:
                bull_score += 5
            elif mom_10d < -5:
                bear_score += 5

            # Trend: higher highs and higher lows (last 5 candles)
            recent = candles[-5:]
            highs_rising = sum(1 for i in range(1,len(recent)) if recent[i]["high"]>=recent[i-1]["high"]) >= 3
            lows_rising = sum(1 for i in range(1,len(recent)) if recent[i]["low"]>=recent[i-1]["low"]) >= 3
            highs_falling = sum(1 for i in range(1,len(recent)) if recent[i]["high"]<=recent[i-1]["high"]) >= 3
            lows_falling = sum(1 for i in range(1,len(recent)) if recent[i]["low"]<=recent[i-1]["low"]) >= 3

            if highs_rising and lows_rising:
                bull_score += 10
                signals["structure"] = "UPTREND"
            elif highs_falling and lows_falling:
                bear_score += 10
                signals["structure"] = "DOWNTREND"
            else:
                signals["structure"] = "MIXED"

            # RSI approximation (14-period)
            if len(closes) >= 15:
                gains = []
                losses = []
                for i in range(1, min(15, len(closes))):
                    diff = closes[i] - closes[i-1]
                    if diff > 0:
                        gains.append(diff)
                        losses.append(0)
                    else:
                        gains.append(0)
                        losses.append(abs(diff))
                avg_gain = sum(gains) / 14
                avg_loss = sum(losses) / 14
                if avg_loss > 0:
                    rs_val = avg_gain / avg_loss
                    rsi = 100 - (100 / (1 + rs_val))
                else:
                    rsi = 100
                signals["rsi"] = round(rsi, 1)

                if rsi > 70:
                    bear_score += 5  # Overbought
                    bull_score -= 3
                elif rsi < 30:
                    bull_score += 5  # Oversold
                    bear_score -= 3

            # Mean reversion check: if down big but structure intact
            if mom_5d < -5 and signals.get("structure") != "DOWNTREND":
                bull_score += 8  # Oversold bounce candidate
                signals["bounce_setup"] = True

            # IMPROVEMENT 9: Multi-timeframe (20-day trend)
            if len(closes) >= 20:
                mom_20d = (closes[-1] - closes[-20]) / closes[-20] * 100
                signals["mom_20d"] = round(mom_20d, 2)

                # Both 5d and 20d agree = strong signal
                if mom_5d > 2 and mom_20d > 3:
                    bull_score += 10
                    signals["multi_tf"] = "BULL_ALIGNED"
                elif mom_5d < -2 and mom_20d < -3:
                    bear_score += 10
                    signals["multi_tf"] = "BEAR_ALIGNED"
                elif mom_5d > 0 and mom_20d < -5:
                    # Short term bounce in long term downtrend - careful
                    bull_score -= 5
                    signals["multi_tf"] = "COUNTER_TREND"
                elif mom_5d < 0 and mom_20d > 5:
                    bear_score -= 5
                    signals["multi_tf"] = "COUNTER_TREND"

            # IMPROVEMENT 12: Momentum acceleration
            if len(closes) >= 10:
                mom_first_half = (closes[-5] - closes[-10]) / closes[-10] * 100
                mom_second_half = mom_5d
                acceleration = mom_second_half - mom_first_half
                signals["momentum_accel"] = round(acceleration, 2)

                if acceleration > 2:
                    # Momentum accelerating upward
                    bull_score += 8
                elif acceleration < -2:
                    # Momentum accelerating downward
                    bear_score += 8

        # 7. INTERMARKET: Check correlated assets
        if sector_name == "gold":
            dxy = self._get_quote("DXY")
            if dxy:
                dxy_change = dxy.get("netPercentChangeInDouble", 0)
                if dxy_change < -0.3:
                    bull_score += 5  # Dollar weak = gold strong
                elif dxy_change > 0.3:
                    bear_score += 5

        if sector_name == "energy":
            uso = self._get_quote("USO")
            if uso:
                oil_change = uso.get("netPercentChangeInDouble", 0)
                if oil_change > 1.0:
                    bull_score += 8
                elif oil_change < -1.0:
                    bear_score += 8

        # Cap scores at 100
        bull_score = min(100, max(0, bull_score))
        bear_score = min(100, max(0, bear_score))

        return {
            "sector": sector_name,
            "underlying": underlying,
            "price": price,
            "bull_score": bull_score,
            "bear_score": bear_score,
            "signals": signals,
        }
