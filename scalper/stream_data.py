"""
Schwab WebSocket Streaming Data Client v1.0

Replaces REST polling with real-time data:
  - Level 1 equity quotes  → sub-second price/volume updates
  - Chart equity (1-min)   → exchange OHLCV candles for accurate VWAP

Runs in a background daemon thread — non-blocking from the main loop.
Falls back cleanly if the stream cannot connect.

Usage:
    stream = StreamDataClient(schwab_client, symbols)
    connected = stream.start()           # blocks up to 10s
    quote  = stream.get_quote("NVDA")   # always safe, returns {} if not yet received
    candles = stream.drain_new_candles("NVDA")  # pops and returns pending 1-min bars
    stream.stop()
"""

import asyncio
import threading
import time
from collections import deque
from datetime import datetime
from loguru import logger

try:
    from zoneinfo import ZoneInfo
    _CT_TZ = ZoneInfo("America/Chicago")
except ImportError:
    _CT_TZ = None


def _ts_to_dt(ts_ms):
    if ts_ms:
        return (
            datetime.fromtimestamp(ts_ms / 1000, tz=_CT_TZ)
            if _CT_TZ
            else datetime.fromtimestamp(ts_ms / 1000)
        )
    return datetime.now(tz=_CT_TZ) if _CT_TZ else datetime.now()


class StreamDataClient:
    """
    Persistent Schwab WebSocket connection.

    Provides:
      get_quote(symbol)            -> latest L1 dict  (price, bid, ask, volume, …)
      get_all_quotes()             -> {symbol: quote_dict, …}
      drain_new_candles(symbol)    -> [candle, …]  (1-min OHLCV, clears buffer)
      is_connected                 -> bool
    """

    # L1 field indices (LevelOneEquityFields enum values)
    _L1 = {
        "bid":     "1",
        "ask":     "2",
        "price":   "3",   # LAST_PRICE
        "volume":  "8",   # TOTAL_VOLUME (cumulative day)
        "high":    "10",
        "low":     "11",
        "open":    "17",
        "net_pct": "42",  # NET_CHANGE_PERCENT
        "trade_ts": "35", # TRADE_TIME_MILLIS
    }

    # ChartEquity field indices (ChartEquityFields enum values)
    _CHART = {
        "open":   "2",
        "high":   "3",
        "low":    "4",
        "close":  "5",
        "volume": "6",
        "ts_ms":  "7",
    }

    def __init__(self, schwab_client, symbols: list):
        self._client   = schwab_client
        self._symbols  = [s.upper() for s in symbols]

        # Thread-safe state
        self._lock         = threading.Lock()
        self._quotes       = {s: {} for s in self._symbols}
        self._new_candles  = {s: deque() for s in self._symbols}

        # Internals
        self._loop         = None
        self._thread       = None
        self._stream       = None
        self._connected    = False
        self._stop_event   = threading.Event()
        self._candle_count = 0  # diagnostic

    # ── Public API ────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def candles_received(self) -> int:
        return self._candle_count

    def start(self, timeout: float = 12.0) -> bool:
        """Launch the background stream thread. Blocks up to `timeout` seconds."""
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="SchwabStream"
        )
        self._thread.start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._connected:
                return True
            time.sleep(0.25)
        logger.warning(f"Stream: did not connect within {timeout:.0f}s")
        return False

    def stop(self):
        self._stop_event.set()
        if self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)

    def get_quote(self, symbol: str) -> dict:
        """Latest L1 quote dict for symbol. Returns {} if not yet received."""
        with self._lock:
            return dict(self._quotes.get(symbol.upper(), {}))

    def get_all_quotes(self) -> dict:
        """Latest L1 quotes for all subscribed symbols."""
        with self._lock:
            return {s: dict(q) for s, q in self._quotes.items()}

    def drain_new_candles(self, symbol: str) -> list:
        """Pop and return all pending 1-min candles for symbol since last call."""
        sym = symbol.upper()
        with self._lock:
            q = self._new_candles.get(sym)
            if not q:
                return []
            candles = list(q)
            q.clear()
            return candles

    # ── Background asyncio thread ──────────────────────────────

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._stream_loop())
        except Exception as e:
            logger.error(f"Stream thread exited: {e}")
        finally:
            self._connected = False
            try:
                self._loop.close()
            except Exception:
                pass

    _BACKOFF = [1, 2, 4, 8, 16, 30]  # seconds — matches smallcap stream_manager

    async def _stream_loop(self):
        from schwab.streaming import StreamClient
        attempt = 0

        while not self._stop_event.is_set():
            try:
                self._stream = StreamClient(self._client)
                await self._stream.login()

                # Register handlers
                self._stream.add_level_one_equity_handler(self._on_l1)
                self._stream.add_chart_equity_handler(self._on_chart)

                # Subscribe — Level 1 real-time quotes
                from schwab.streaming import StreamClient as SC
                l1_fields = [
                    SC.LevelOneEquityFields.SYMBOL,
                    SC.LevelOneEquityFields.BID_PRICE,
                    SC.LevelOneEquityFields.ASK_PRICE,
                    SC.LevelOneEquityFields.LAST_PRICE,
                    SC.LevelOneEquityFields.TOTAL_VOLUME,
                    SC.LevelOneEquityFields.HIGH_PRICE,
                    SC.LevelOneEquityFields.LOW_PRICE,
                    SC.LevelOneEquityFields.OPEN_PRICE,
                    SC.LevelOneEquityFields.NET_CHANGE_PERCENT,
                    SC.LevelOneEquityFields.TRADE_TIME_MILLIS,
                ]
                await self._stream.level_one_equity_subs(
                    self._symbols, fields=l1_fields
                )

                # Subscribe — 1-min OHLCV candles
                chart_fields = [
                    SC.ChartEquityFields.SYMBOL,
                    SC.ChartEquityFields.OPEN_PRICE,
                    SC.ChartEquityFields.HIGH_PRICE,
                    SC.ChartEquityFields.LOW_PRICE,
                    SC.ChartEquityFields.CLOSE_PRICE,
                    SC.ChartEquityFields.VOLUME,
                    SC.ChartEquityFields.CHART_TIME_MILLIS,
                ]
                await self._stream.chart_equity_subs(
                    self._symbols, fields=chart_fields
                )

                self._connected = True
                attempt = 0  # reset backoff on successful connect
                logger.info(
                    f"Stream: connected | {len(self._symbols)} symbols | "
                    f"L1 quotes + 1-min candles"
                )

                # Message loop — runs until disconnect
                while not self._stop_event.is_set():
                    await self._stream.handle_message()

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._connected = False
                if not self._stop_event.is_set():
                    backoff = self._BACKOFF[min(attempt, len(self._BACKOFF) - 1)]
                    logger.warning(
                        f"Stream disconnected ({e}) — "
                        f"reconnecting in {backoff}s (attempt {attempt + 1})"
                    )
                    await asyncio.sleep(backoff)
                    attempt += 1

    async def _shutdown(self):
        if self._stream:
            try:
                await self._stream.logout()
            except Exception:
                pass

    # ── Message handlers (called from asyncio thread) ──────────

    def _on_l1(self, message):
        """Handle Level 1 equity quote updates."""
        try:
            for item in message.get("content", []):
                sym = item.get("key", "").upper()
                if sym not in self._quotes:
                    continue
                with self._lock:
                    q = self._quotes[sym]
                    f = self._L1
                    if f["price"] in item:
                        q["price"] = float(item[f["price"]])
                    if f["bid"] in item:
                        q["bid"] = float(item[f["bid"]])
                    if f["ask"] in item:
                        q["ask"] = float(item[f["ask"]])
                    if f["volume"] in item:
                        q["volume"] = int(item[f["volume"]])
                    if f["high"] in item:
                        q["high"] = float(item[f["high"]])
                    if f["low"] in item:
                        q["low"] = float(item[f["low"]])
                    if f["open"] in item:
                        q["open"] = float(item[f["open"]])
                    if f["net_pct"] in item:
                        q["net_pct"] = float(item[f["net_pct"]])
                    q["symbol"] = sym
                    q["updated"] = time.time()
        except Exception as e:
            logger.debug(f"L1 handler error: {e}")

    def _on_chart(self, message):
        """Handle completed 1-minute OHLCV candles from the exchange."""
        try:
            for item in message.get("content", []):
                sym = item.get("key", "").upper()
                if sym not in self._new_candles:
                    continue
                f = self._CHART
                open_p  = item.get(f["open"])
                high_p  = item.get(f["high"])
                low_p   = item.get(f["low"])
                close_p = item.get(f["close"])
                volume  = item.get(f["volume"])
                ts_ms   = item.get(f["ts_ms"])

                if None in (open_p, high_p, low_p, close_p, volume):
                    continue

                candle = {
                    "time":   _ts_to_dt(ts_ms),
                    "open":   float(open_p),
                    "high":   float(high_p),
                    "low":    float(low_p),
                    "close":  float(close_p),
                    "volume": int(volume),
                }
                with self._lock:
                    self._new_candles[sym].append(candle)
                    self._candle_count += 1

        except Exception as e:
            logger.debug(f"Chart handler error: {e}")
