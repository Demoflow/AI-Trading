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

try:
    from zoneinfo import ZoneInfo
    _CT_TZ = ZoneInfo("America/Chicago")
except ImportError:
    _CT_TZ = None


def _now_ct():
    return datetime.now(tz=_CT_TZ) if _CT_TZ else datetime.now()


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
            timestamp = _now_ct()
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

    def get_current_block(self):
        """Return the current candle time block identifier."""
        return self._current_block

    def get_all_candles(self):
        result = list(self.candles)
        if self._current:
            result.append(self._current)
        return result

    def seed_candles(self, candles):
        """Pre-populate with historical OHLCV candles so indicators have data immediately.
        Clears existing data first to prevent duplicate candles on reconnect."""
        self.candles.clear()
        self._current = None
        self._current_block = None
        for c in candles:
            self.candles.append(c)

    def ingest_candle(self, candle):
        """
        Merge a pre-built OHLCV candle (e.g. from the streaming client) into
        the current bar, properly preserving high/low/open/close/volume.
        Used by StreamingDataEngine to feed exchange 1-min candles.

        Returns the just-completed bar if a new block started, else None.
        """
        dt = candle.get("time")
        if dt is None:
            dt = _now_ct()
        block = self._get_block(dt)

        if self._current_block != block:
            completed = None
            if self._current is not None:
                self.candles.append(self._current)
                completed = self._current
            self._current = {
                "time":   dt,
                "open":   candle["open"],
                "high":   candle["high"],
                "low":    candle["low"],
                "close":  candle["close"],
                "volume": candle.get("volume", 0),
            }
            self._current_block = block
            return completed
        else:
            if self._current:
                self._current["high"]   = max(self._current["high"],  candle["high"])
                self._current["low"]    = min(self._current["low"],   candle["low"])
                self._current["close"]  = candle["close"]
                self._current["volume"] += candle.get("volume", 0)
            return None


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
    def donchian(highs, lows, period=20):
        """Donchian Channel: N-bar highest high / lowest low / midpoint."""
        if len(highs) < period or len(lows) < period:
            return None, None, None
        dc_high = max(highs[-period:])
        dc_low  = min(lows[-period:])
        dc_mid  = round((dc_high + dc_low) / 2, 4)
        return round(dc_high, 4), round(dc_low, 4), dc_mid

    @staticmethod
    def ema_slope(closes, period=9, lookback=3):
        """Returns 'UP', 'DOWN', or 'FLAT' based on EMA direction over last N bars."""
        if len(closes) < period + lookback:
            return "FLAT"
        arr = np.array(closes, dtype=float)
        m = 2 / (period + 1)
        v = arr[0]
        ema_vals = []
        for i in range(1, len(arr)):
            v = (arr[i] - v) * m + v
            if i >= len(arr) - lookback - 1:
                ema_vals.append(v)
        if len(ema_vals) < 2:
            return "FLAT"
        delta = ema_vals[-1] - ema_vals[0]
        if delta > 0.005:
            return "UP"
        if delta < -0.005:
            return "DOWN"
        return "FLAT"

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
        _SYMS = [
            "SPY", "QQQ", "IWM",
            "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL",
            "TSLA", "AMD", "NFLX", "PLTR", "MSTR",
            "SMH",
            "COIN", "JPM", "XOM",
            "TQQQ", "SOXL",
        ]
        self.builders_5m = {s: CandleBuilder(5) for s in _SYMS}
        self.builders_1m = {s: CandleBuilder(1) for s in _SYMS}
        # Keep old reference for compatibility
        self.builders = self.builders_5m
        self.indicators = Indicators()
        self._session_candles = {s: [] for s in _SYMS}
        # Track previous totalVolume per symbol for delta calculation
        self._prev_total_volume = {s: 0 for s in _SYMS}

    def seed_history(self):
        """
        Pre-populate candle builders with today's historical data from Schwab so
        RSI/EMA/MACD indicators are valid from the first poll cycle instead of
        returning defaults for the first 75–175 minutes of the session.
        Seeds 5m builders with up to 60 candles and 1m builders with up to 60 candles.
        """
        import httpx
        from datetime import datetime, timedelta

        def _parse_candles(resp_json):
            raw = resp_json.get("candles", [])
            result = []
            for c in raw:
                try:
                    ts_ms = c.get("datetime", 0)
                    if ts_ms:
                        dt = datetime.fromtimestamp(ts_ms / 1000, tz=_CT_TZ) if _CT_TZ else datetime.fromtimestamp(ts_ms / 1000)
                    else:
                        dt = _now_ct()
                    result.append({
                        "time": dt,
                        "open": float(c["open"]),
                        "high": float(c["high"]),
                        "low": float(c["low"]),
                        "close": float(c["close"]),
                        "volume": int(c.get("volume", 0)),
                    })
                except Exception:
                    continue
            return result

        seeded_5m = 0
        seeded_1m = 0
        for sym in list(self.builders_5m.keys()):
            try:
                time.sleep(0.1)
                r5 = self.client.get_price_history_every_five_minutes(sym)
                if r5.status_code == httpx.codes.OK:
                    candles_5m = _parse_candles(r5.json())
                    if candles_5m:
                        # Drop the last (still-forming) candle; keep up to 60
                        self.builders_5m[sym].seed_candles(candles_5m[:-1][-60:])
                        seeded_5m += 1
            except Exception as e:
                logger.debug(f"seed_history 5m {sym}: {e}")

            try:
                time.sleep(0.1)
                r1 = self.client.get_price_history_every_minute(sym)
                if r1.status_code == httpx.codes.OK:
                    candles_1m = _parse_candles(r1.json())
                    if candles_1m:
                        self.builders_1m[sym].seed_candles(candles_1m[:-1][-60:])
                        seeded_1m += 1
            except Exception as e:
                logger.debug(f"seed_history 1m {sym}: {e}")

        logger.info(
            f"History seeded: {seeded_5m} symbols (5m), {seeded_1m} symbols (1m)"
        )

    def poll(self):
        results = {}
        for sym in self.builders_5m:
            try:
                time.sleep(0.08)
                resp = self.client.get_quote(sym)
                if resp.status_code == httpx.codes.OK:
                    data = resp.json()
                    q = data.get(sym, {}).get("quote", {})
                    price = q.get("lastPrice", 0)
                    total_volume = q.get("totalVolume", 0)
                    if price > 0:
                        # Compute volume delta (totalVolume is cumulative for the day)
                        prev_vol = self._prev_total_volume.get(sym, 0)
                        vol_delta = max(total_volume - prev_vol, 0) if total_volume > 0 else 0
                        self._prev_total_volume[sym] = total_volume
                        # Feed both timeframes with volume delta
                        new_5m = self.builders_5m[sym].add_quote(price, vol_delta)
                        self.builders_1m[sym].add_quote(price, vol_delta)
                        if new_5m:
                            self._session_candles[sym].append(new_5m)
                        results[sym] = {
                            "price": price,
                            "bid": q.get("bidPrice", 0),
                            "ask": q.get("askPrice", 0),
                            "volume": total_volume,
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
        ema9  = self.indicators.ema(closes, 9)
        ema21 = self.indicators.ema(closes, 21)
        ema50 = self.indicators.ema(closes, 50)

        prev9  = self.indicators.ema(closes[:-1], 9)  if len(closes) >= 22 else ema9
        prev21 = self.indicators.ema(closes[:-1], 21) if len(closes) >= 22 else ema21

        cross_up = (prev9 and prev21 and ema9 and ema21 and prev9 <= prev21 and ema9 > ema21)
        cross_dn = (prev9 and prev21 and ema9 and ema21 and prev9 >= prev21 and ema9 < ema21)

        ema9_slope = self.indicators.ema_slope(closes, period=9, lookback=3)

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

        # Donchian Channel: use all-but-current-candle highs/lows so price can "break" the channel
        dc_highs = highs[:-1] if len(highs) > 1 else highs
        dc_lows  = lows[:-1]  if len(lows) > 1  else lows
        dc_h, dc_l, dc_m = self.indicators.donchian(dc_highs, dc_lows, 20)
        dc_breakout_up   = bool(dc_h and price > dc_h)
        dc_breakout_down = bool(dc_l and price < dc_l)

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
            "session_open": sc[0]["open"] if sc else price,
            "time": _now_ct(),
            "candle_count": builder.candle_count(),
            "ema9": ema9, "ema21": ema21, "ema50": ema50,
            "ema9_slope": ema9_slope,
            "ema_cross_up": cross_up, "ema_cross_down": cross_dn,
            "ema_trend": "UP" if ema9 and ema21 and ema9 > ema21 else "DOWN",
            "ema50_trend": ("UP" if ema50 and price > ema50 else "DOWN") if ema50 else "UNKNOWN",
            "dc_high": dc_h, "dc_low": dc_l, "dc_mid": dc_m,
            "dc_breakout_up": dc_breakout_up, "dc_breakout_down": dc_breakout_down,
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


# ─────────────────────────────────────────────────────────────────────────────
# StreamingDataEngine — drop-in replacement for RealtimeDataEngine that uses
# the Schwab WebSocket stream instead of sequential REST polling.
#
# Same public interface: poll(), get_snapshot(), get_entry_snapshot(),
# seed_history(), builders_5m, builders_1m.
#
# Additions:
#   start_stream()          — connect WebSocket (call after construction)
#   get_latest_price(sym)   — sub-second price from L1 cache
#   is_streaming            — True while WebSocket is connected
# ─────────────────────────────────────────────────────────────────────────────

class StreamingDataEngine(RealtimeDataEngine):
    """
    Streaming-backed data engine.

    1-min exchange candles arrive via WebSocket → fed into builders_1m (exact)
    and aggregated into builders_5m (by 5-min block).
    L1 quotes update a low-latency price cache for exit checking.

    poll() drains pending candles and returns latest prices — no HTTP calls.
    """

    def __init__(self, schwab_client):
        super().__init__(schwab_client)
        self._stream_client = None
        self._is_streaming  = False
        # Per-symbol prev L1 volume for delta tracking (live candle updates)
        self._l1_prev_vol   = {s: 0 for s in self.builders_5m}

    @property
    def is_streaming(self) -> bool:
        return self._is_streaming and (
            self._stream_client is not None and self._stream_client.is_connected
        )

    def start_stream(self, symbols: list = None) -> bool:
        """
        Connect the WebSocket stream. Call once after construction.

        symbols: override list; defaults to all symbols tracked by builders.
        Returns True if connected successfully.
        """
        from scalper.stream_data import StreamDataClient

        watch = symbols or list(self.builders_5m.keys())
        self._stream_client = StreamDataClient(self.client, watch)
        ok = self._stream_client.start(timeout=12.0)
        self._is_streaming = ok
        if ok:
            logger.info(
                f"StreamingDataEngine: stream active | "
                f"{len(watch)} symbols | REST polling disabled"
            )
        else:
            logger.warning(
                "StreamingDataEngine: stream failed — will fall back to REST poll()"
            )
        return ok

    def get_latest_price(self, symbol: str):
        """
        Return the latest known price for symbol.
        Uses stream L1 cache if connected, falls back to candle close.
        Returns None if no data at all.
        """
        if self._stream_client and self._is_streaming:
            q = self._stream_client.get_quote(symbol)
            p = q.get("price")
            if p and p > 0:
                return float(p)
        # Fallback: last close from 1m builder
        b = self.builders_1m.get(symbol)
        if b:
            cur = b.get_current()
            if cur:
                return cur["close"]
        return None

    def poll(self):
        """
        Drain pending 1-min candles from stream into builders.
        Returns latest quote dict in same format as RealtimeDataEngine.poll().
        Falls back to REST polling if stream is not connected.
        """
        if not (self._stream_client and self._stream_client.is_connected):
            # Stream down — use parent REST implementation
            return super().poll()

        results = {}

        for sym in self.builders_5m:
            # ── Ingest completed 1-min exchange candles ──
            new_candles = self._stream_client.drain_new_candles(sym)
            for c in new_candles:
                # Feed to 1-min builder (exact exchange candle)
                self.builders_1m[sym].ingest_candle(c)
                # Feed to 5-min builder (aggregates by block)
                completed_5m = self.builders_5m[sym].ingest_candle(c)
                if completed_5m:
                    self._session_candles[sym].append(completed_5m)

            # ── Latest L1 quote for result dict ──
            q = self._stream_client.get_quote(sym)
            price = q.get("price", 0)
            if price and price > 0:
                # Live update current 5-min candle with latest L1 price + vol delta
                total_vol = q.get("volume", 0)
                prev_vol  = self._l1_prev_vol.get(sym, 0)
                vol_delta = max(int(total_vol) - prev_vol, 0) if total_vol else 0
                if vol_delta > 0:
                    self._l1_prev_vol[sym] = int(total_vol)
                    # Only update the "current" open candle — do not ingest as a
                    # completed candle; just refresh close/high/low in-place.
                    cur5 = self.builders_5m[sym].get_current()
                    if cur5:
                        cur5["high"]  = max(cur5["high"],  price)
                        cur5["low"]   = min(cur5["low"],   price)
                        cur5["close"] = price
                        cur5["volume"] = cur5.get("volume", 0) + vol_delta

                results[sym] = {
                    "price":   price,
                    "bid":     q.get("bid", 0),
                    "ask":     q.get("ask", 0),
                    "volume":  q.get("volume", 0),
                    "net_pct": q.get("net_pct", 0),
                }

        return results