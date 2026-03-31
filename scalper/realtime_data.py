"""
Real-Time Data Engine v3.
Dual timeframe: 1-min candles + 5-min candles.
1-min = entry timing. 5-min = directional bias.
VWAP bands, volume profile, all indicators.
"""

import time
import httpx
import numpy as np
from datetime import datetime
from collections import deque
from loguru import logger


class CandleBuilder:

    def __init__(self, interval_minutes=5):
        self.interval = interval_minutes
        self.candles = deque(maxlen=200)
        self._current = None
        self._current_block = None

    def _get_block(self, dt):
        return dt.hour * 60 + dt.minute - (dt.minute % self.interval)

    def add_quote(self, price, volume, timestamp=None):
        if timestamp is None:
            timestamp = datetime.now()
        if price <= 0:
            return None
        block = self._get_block(timestamp)
        if self._current_block != block:
            if self._current is not None:
                self.candles.append(self._current)
            self._current = {
                "time": timestamp, "open": price,
                "high": price, "low": price, "close": price,
                "volume": volume,
            }
            self._current_block = block
            return self._current if len(self.candles) > 0 else None
        else:
            if self._current:
                self._current["high"] = max(self._current["high"], price)
                self._current["low"] = min(self._current["low"], price)
                self._current["close"] = price
                self._current["volume"] += volume
            return None

    def get_closes(self, n=50):
        c = [x["close"] for x in self.candles]
        if self._current:
            c.append(self._current["close"])
        return c[-n:]

    def get_highs(self, n=50):
        h = [x["high"] for x in self.candles]
        if self._current:
            h.append(self._current["high"])
        return h[-n:]

    def get_lows(self, n=50):
        lo = [x["low"] for x in self.candles]
        if self._current:
            lo.append(self._current["low"])
        return lo[-n:]

    def get_volumes(self, n=50):
        v = [x["volume"] for x in self.candles]
        if self._current:
            v.append(self._current["volume"])
        return v[-n:]

    def get_current(self):
        return self._current

    def candle_count(self):
        return len(self.candles)

    def get_all_candles(self):
        result = list(self.candles)
        if self._current:
            result.append(self._current)
        return result


class Indicators:

    @staticmethod
    def ema(data, period):
        if len(data) < period:
            return None
        arr = np.array(data, dtype=float)
        m = 2 / (period + 1)
        v = arr[0]
        for i in range(1, len(arr)):
            v = (arr[i] - v) * m + v
        return round(v, 4)

    @staticmethod
    def ema_series(data, period):
        if len(data) < period:
            return []
        arr = np.array(data, dtype=float)
        m = 2 / (period + 1)
        r = [arr[0]]
        for i in range(1, len(arr)):
            r.append((arr[i] - r[-1]) * m + r[-1])
        return r

    @staticmethod
    def vwap_with_bands(candles):
        if not candles:
            return 0, 0, 0, 0, 0
        cum_pv = 0
        cum_vol = 0
        for c in candles:
            if not isinstance(c, dict):
                continue
            tp = (c["high"] + c["low"] + c["close"]) / 3
            v = c["volume"]
            cum_pv += tp * v
            cum_vol += v
        vwap = cum_pv / cum_vol if cum_vol > 0 else 0
        if vwap <= 0:
            return 0, 0, 0, 0, 0
        sq_devs = []
        for c in candles:
            if not isinstance(c, dict):
                continue
            tp = (c["high"] + c["low"] + c["close"]) / 3
            sq_devs.append(((tp - vwap) ** 2) * c["volume"])
        var = sum(sq_devs) / cum_vol if cum_vol > 0 else 0
        sd = var ** 0.5
        return (
            round(vwap, 4),
            round(vwap + sd, 4), round(vwap - sd, 4),
            round(vwap + 2*sd, 4), round(vwap - 2*sd, 4),
        )

    @staticmethod
    def rsi(data, period=14):
        if len(data) < period + 1:
            return 50
        arr = np.array(data, dtype=float)
        d = np.diff(arr)
        g = np.where(d > 0, d, 0)
        l = np.where(d < 0, -d, 0)
        ag = np.mean(g[:period])
        al = np.mean(l[:period])
        for i in range(period, len(g)):
            ag = (ag * (period-1) + g[i]) / period
            al = (al * (period-1) + l[i]) / period
        if al == 0:
            return 100
        return round(100 - (100 / (1 + ag/al)), 2)

    @staticmethod
    def macd(data, fast=12, slow=26, sig=9):
        if len(data) < slow + sig:
            return 0, 0, 0
        fe = Indicators.ema_series(data, fast)
        se = Indicators.ema_series(data, slow)
        mn = min(len(fe), len(se))
        fe, se = fe[-mn:], se[-mn:]
        ml = [f-s for f, s in zip(fe, se)]
        sl = Indicators.ema_series(ml, sig) if len(ml) >= sig else ml
        if not ml or not sl:
            return 0, 0, 0
        return round(ml[-1], 4), round(sl[-1], 4), round(ml[-1]-sl[-1], 4)

    @staticmethod
    def atr(highs, lows, closes, period=14):
        if len(highs) < period + 1:
            return 0
        trs = []
        for i in range(1, len(highs)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return round(np.mean(trs[-period:]), 4) if len(trs) >= period else 0

    @staticmethod
    def bollinger(data, period=20, mult=2):
        if len(data) < period:
            return 0, 0, 0
        arr = np.array(data[-period:], dtype=float)
        mid = np.mean(arr)
        sd = np.std(arr)
        return round(mid+mult*sd, 4), round(mid, 4), round(mid-mult*sd, 4)

    @staticmethod
    def volume_profile(candles, bins=20):
        if not candles or len(candles) < 5:
            return [], None, None
        prices, vols = [], []
        for c in candles:
            if not isinstance(c, dict):
                continue
            prices.append((c["high"]+c["low"]+c["close"])/3)
            vols.append(c["volume"])
        if not prices:
            return [], None, None
        lo, hi = min(prices), max(prices)
        if hi == lo:
            return [], None, None
        bs = (hi-lo)/bins
        b = {}
        for p, v in zip(prices, vols):
            idx = min(int((p-lo)/bs), bins-1)
            lev = round(lo + (idx+0.5)*bs, 2)
            b[lev] = b.get(lev, 0) + v
        sb = sorted(b.items(), key=lambda x: x[1], reverse=True)
        hvn = [x[0] for x in sb[:3]]
        lvn = [x[0] for x in sb[-3:]] if len(sb) > 3 else []
        return sb, hvn, lvn


class RealtimeDataEngine:

    POLL_INTERVAL = 5

    def __init__(self, schwab_client):
        self.client = schwab_client
        # Dual timeframe builders
        self.builders_5m = {"SPY": CandleBuilder(5), "QQQ": CandleBuilder(5), "AAPL": CandleBuilder(5), "MSFT": CandleBuilder(5), "NVDA": CandleBuilder(5), "TSLA": CandleBuilder(5), "AMZN": CandleBuilder(5), "META": CandleBuilder(5), "GOOGL": CandleBuilder(5), "AMD": CandleBuilder(5), "NFLX": CandleBuilder(5), "COIN": CandleBuilder(5), "BA": CandleBuilder(5), "JPM": CandleBuilder(5), "XOM": CandleBuilder(5)}
        self.builders_1m = {"SPY": CandleBuilder(1), "QQQ": CandleBuilder(1), "AAPL": CandleBuilder(1), "MSFT": CandleBuilder(1), "NVDA": CandleBuilder(1), "TSLA": CandleBuilder(1), "AMZN": CandleBuilder(1), "META": CandleBuilder(1), "GOOGL": CandleBuilder(1), "AMD": CandleBuilder(1), "NFLX": CandleBuilder(1), "COIN": CandleBuilder(1), "BA": CandleBuilder(1), "JPM": CandleBuilder(1), "XOM": CandleBuilder(1)}
        # Keep old reference for compatibility
        self.builders = self.builders_5m
        self.indicators = Indicators()
        self._session_candles = {"SPY": [], "QQQ": [], "AAPL": [], "MSFT": [], "NVDA": [], "TSLA": [], "AMZN": [], "META": [], "GOOGL": [], "AMD": [], "NFLX": [], "COIN": [], "BA": [], "JPM": [], "XOM": []}

    def poll(self):
        results = {}
        for sym in ['SPY', 'QQQ', 'AAPL', 'MSFT', 'NVDA', 'TSLA', 'AMZN', 'META', 'GOOGL', 'AMD', 'NFLX', 'COIN', 'BA', 'JPM', 'XOM']:
            try:
                time.sleep(0.08)
                resp = self.client.get_quote(sym)
                if resp.status_code == httpx.codes.OK:
                    data = resp.json()
                    q = data.get(sym, {}).get("quote", {})
                    price = q.get("lastPrice", 0)
                    volume = q.get("totalVolume", 0)
                    if price > 0:
                        # Feed both timeframes
                        new_5m = self.builders_5m[sym].add_quote(price, volume)
                        self.builders_1m[sym].add_quote(price, volume)
                        if new_5m:
                            self._session_candles[sym].append(new_5m)
                        results[sym] = {
                            "price": price,
                            "bid": q.get("bidPrice", 0),
                            "ask": q.get("askPrice", 0),
                            "volume": volume,
                            "net_pct": q.get("netPercentChange", 0),
                        }
            except Exception as e:
                logger.debug(f"Poll {sym}: {e}")
        return results

    def get_snapshot(self, symbol):
        """5-minute snapshot for directional bias."""
        b5 = self.builders_5m.get(symbol)
        if not b5 or b5.candle_count() < 5:
            return None
        return self._build_snapshot(symbol, b5)

    def get_entry_snapshot(self, symbol):
        """1-minute snapshot for entry timing."""
        b1 = self.builders_1m.get(symbol)
        if not b1 or b1.candle_count() < 5:
            return None
        return self._build_snapshot(symbol, b1)

    def _build_snapshot(self, symbol, builder):
        closes = builder.get_closes(100)
        highs = builder.get_highs(100)
        lows = builder.get_lows(100)
        volumes = builder.get_volumes(100)
        current = builder.get_current()
        if not closes or not current:
            return None

        price = closes[-1]
        ema9 = self.indicators.ema(closes, 9)
        ema21 = self.indicators.ema(closes, 21)

        prev9 = self.indicators.ema(closes[:-1], 9) if len(closes) >= 22 else ema9
        prev21 = self.indicators.ema(closes[:-1], 21) if len(closes) >= 22 else ema21

        cross_up = (prev9 and prev21 and ema9 and ema21 and prev9 <= prev21 and ema9 > ema21)
        cross_dn = (prev9 and prev21 and ema9 and ema21 and prev9 >= prev21 and ema9 < ema21)

        all_c = builder.get_all_candles()
        vwap, v1u, v1d, v2u, v2d = self.indicators.vwap_with_bands(all_c)
        rsi = self.indicators.rsi(closes)
        macd_l, macd_s, macd_h = self.indicators.macd(closes)
        atr = self.indicators.atr(highs, lows, closes)
        bb_u, bb_m, bb_l = self.indicators.bollinger(closes)

        avg_vol = np.mean(volumes[-20:]) if len(volumes) >= 20 else (np.mean(volumes) if volumes else 1)
        cur_vol = volumes[-1] if volumes else 0
        vol_ratio = cur_vol / avg_vol if avg_vol > 0 else 1

        sc = self._session_candles.get(symbol, [])
        or_high = max(c["high"] for c in sc[:3]) if len(sc) >= 3 else (current["high"] if current else price)
        or_low = min(c["low"] for c in sc[:3]) if len(sc) >= 3 else (current["low"] if current else price)

        _, hvn, lvn = self.indicators.volume_profile(all_c)

        trend = "NEUTRAL"
        if ema9 and ema21 and vwap:
            if ema9 > ema21 and price > vwap:
                trend = "BULLISH"
            elif ema9 < ema21 and price < vwap:
                trend = "BEARISH"

        momentum = "NEUTRAL"
        if macd_l > macd_s and macd_h > 0:
            momentum = "BULLISH"
        elif macd_l < macd_s and macd_h < 0:
            momentum = "BEARISH"

        vwap_band = "NEUTRAL"
        if v2u and price >= v2u:
            vwap_band = "EXTREME_OB"
        elif v1u and price >= v1u:
            vwap_band = "OB"
        elif v2d and price <= v2d:
            vwap_band = "EXTREME_OS"
        elif v1d and price <= v1d:
            vwap_band = "OS"

        return {
            "symbol": symbol, "price": round(price, 2),
            "time": datetime.now(),
            "candle_count": builder.candle_count(),
            "ema9": ema9, "ema21": ema21,
            "ema_cross_up": cross_up, "ema_cross_down": cross_dn,
            "ema_trend": "UP" if ema9 and ema21 and ema9 > ema21 else "DOWN",
            "vwap": vwap, "vwap_1sd_up": v1u, "vwap_1sd_dn": v1d,
            "vwap_2sd_up": v2u, "vwap_2sd_dn": v2d, "vwap_band": vwap_band,
            "price_vs_vwap": round(price - vwap, 4) if vwap else 0,
            "vwap_pct": round((price-vwap)/vwap*100, 3) if vwap else 0,
            "rsi": rsi, "macd": macd_l, "macd_signal": macd_s,
            "macd_histogram": macd_h, "atr": atr,
            "bb_upper": bb_u, "bb_mid": bb_m, "bb_lower": bb_l,
            "volume_ratio": round(vol_ratio, 2),
            "volume_surge": vol_ratio > 1.5,
            "or_high": round(or_high, 2), "or_low": round(or_low, 2),
            "or_breakout_up": price > or_high,
            "or_breakout_down": price < or_low,
            "hvn_levels": hvn or [], "lvn_levels": lvn or [],
            "trend": trend, "momentum": momentum,
            "current_candle": current,
        }
