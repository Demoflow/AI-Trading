"""
Small Cap Streaming Infrastructure.

Responsibilities:
  - Wrap Schwab StreamClient in an asyncio event loop running in a daemon thread
  - Subscribe to Level 1 equity (bid/ask/last/size/volume/security_status)
  - Subscribe to Level 2 NASDAQ/NYSE book (bid/ask depth arrays)
  - Subscribe to 1-minute chart candles (OHLCV)
  - Detect trading halts via SECURITY_STATUS field
  - Maintain thread-safe shared state dicts for the order flow engine and
    pattern engine to read without holding any async context
  - Auto-reconnect on disconnect with exponential back-off

Data flow:
    StreamManager.start(symbols)
        → async loop in background thread
        → on each message, update self.quotes / self.books / self.candles
        → order flow engine and pattern engine read those dicts synchronously

Usage:
    sm = StreamManager(client, account_id)
    sm.start(candidate_symbols)           # non-blocking
    quote = sm.get_quote("NVAX")          # thread-safe read
    book  = sm.get_book("NVAX")
    candles = sm.get_candles("NVAX")
    sm.stop()
"""

import asyncio
import threading
import time
from collections import deque
from datetime import datetime, timezone
from loguru import logger

from schwab.streaming import StreamClient

from smallcap.config import MIN_GAP_PCT, MIN_PRICE, MAX_PRICE

# ── CONSTANTS ──────────────────────────────────────────────────────────────────
# Level 1 fields we care about (subset keeps bandwidth down)
_L1_FIELDS = [
    StreamClient.LevelOneEquityFields.BID_PRICE,
    StreamClient.LevelOneEquityFields.ASK_PRICE,
    StreamClient.LevelOneEquityFields.LAST_PRICE,
    StreamClient.LevelOneEquityFields.BID_SIZE,
    StreamClient.LevelOneEquityFields.ASK_SIZE,
    StreamClient.LevelOneEquityFields.LAST_SIZE,
    StreamClient.LevelOneEquityFields.TOTAL_VOLUME,
    StreamClient.LevelOneEquityFields.OPEN_PRICE,
    StreamClient.LevelOneEquityFields.CLOSE_PRICE,
    StreamClient.LevelOneEquityFields.MARK,
    StreamClient.LevelOneEquityFields.SECURITY_STATUS,
    StreamClient.LevelOneEquityFields.MARK_CHANGE_PERCENT,
]

# Max candle history kept per symbol (covers the full prime window + buffer)
_MAX_CANDLES = 120   # 2 hours of 1-min candles

# Halt statuses we treat as "halted" (subset of Schwab SECURITY_STATUS values)
_HALT_STATUSES = frozenset({
    "Halted", "Trading Halt", "HALTED", "H", "T"
})

# Reconnect back-off: [1, 2, 4, 8, 16, 30, 30, ...] seconds
_BACKOFF_SEQUENCE = [1, 2, 4, 8, 16, 30]

# Schwab equity screener subscription keys.
# Format: {EXCHANGE}_{SORT_FIELD}_{FREQUENCY}
#   EXCHANGE    : NASDAQ, NYSE, OTCBB, PINK
#   SORT_FIELD  : PERCENT_UP, PERCENT_DOWN, VOLUME, TRADES, AVERAGE_PERCENT_VOLUME
#   FREQUENCY   : 0 (all day), 1 (1 min), 5 (5 min), 10, 30, 60
#
# We subscribe to top % gainers on NASDAQ and NYSE (all-day window) to discover
# gapping stocks outside our fixed seed universe in real time.
_SCREENER_KEYS = [
    "NASDAQ_PERCENT_UP_0",
    "NYSE_PERCENT_UP_0",
    "OTCBB_PERCENT_UP_0",
]

# Fields we want from each screener update
_SCREENER_FIELDS = [
    StreamClient.ScreenerFields.SYMBOL,
    StreamClient.ScreenerFields.SORT_FIELD,
    StreamClient.ScreenerFields.FREQUENCY,
    StreamClient.ScreenerFields.ITEMS,
]


class StreamManager:
    """
    Wraps Schwab StreamClient with auto-reconnect, halt detection, and
    thread-safe state accessors for synchronous callers.
    """

    def __init__(self, client, account_id: str):
        """
        Args:
            client: Authenticated schwab-py Client.
            account_id: Schwab account number (not hash) for streaming auth.
        """
        self._client     = client
        self._account_id = account_id

        # ── Shared state — written by async loop, read by sync callers ──────
        self._quotes: dict[str, dict]          = {}   # sym → L1 field dict
        self._books:  dict[str, dict]          = {}   # sym → {bids, asks, time}
        self._candles: dict[str, deque]        = {}   # sym → deque of candle dicts
        self._halted:  set[str]                = set()
        # Screener hits: symbols discovered via equity screener that pass basic
        # price/gap criteria.  Main loop calls get_screener_hits() to drain it.
        # Each entry: {"symbol": str, "pct_change": float, "price": float,
        #              "volume": int, "screener_key": str}
        self._screener_hits: list[dict]        = []
        self._lock = threading.Lock()

        # ── Thread / loop plumbing ──────────────────────────────────────────
        self._loop:   asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None          = None
        self._stop_event = threading.Event()
        self._symbols: list[str] = []
        self._connected = False

        # Symbols queued for dynamic subscription (added after stream start).
        # Written by subscribe_symbols() from the main thread; drained in the
        # async receive loop before each handle_message() call.
        self._pending_subscriptions: list[str] = []

        # Monotonic timestamp of the last L1 message received — used by the
        # main loop watchdog to detect a silently stalled stream.
        self._last_message_time: float = 0.0

    # ── PUBLIC API ─────────────────────────────────────────────────────────────

    def start(self, symbols: list[str]):
        """
        Start streaming for the given symbols.
        Non-blocking — launches a daemon thread with its own asyncio event loop.
        Safe to call before the candidate list is finalized; pass an empty list
        and call update_symbols() once candidates are known.
        """
        self._symbols = [s.upper() for s in symbols]
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="StreamManager"
        )
        self._thread.start()
        logger.info(
            f"StreamManager started: {len(self._symbols)} symbol(s) — "
            f"{', '.join(self._symbols[:5])}{'...' if len(self._symbols) > 5 else ''}"
        )

    def stop(self):
        """Signal the background thread to stop. Non-blocking."""
        self._stop_event.set()
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

    def update_symbols(self, symbols: list[str]):
        """
        Replace the subscribed symbol list. The streaming loop will re-subscribe
        on its next reconnect cycle. For immediate effect, call stop() then
        start() with the new list.
        """
        with self._lock:
            self._symbols = [s.upper() for s in symbols]
        logger.info(f"StreamManager: symbol list updated to {self._symbols}")

    def subscribe_symbols(self, symbols: list[str]):
        """
        Dynamically add symbols to the running stream session.

        The addition is processed asynchronously — the async receive loop drains
        _pending_subscriptions before each handle_message() call, so new symbols
        receive L1/L2/chart data within one WebSocket message interval (~<1s
        during market hours).

        Safe to call from any thread while the stream is running.
        """
        new = [s.upper() for s in symbols]
        with self._lock:
            existing = set(self._symbols)
            to_add = [s for s in new if s not in existing]
            if to_add:
                self._symbols.extend(to_add)
                self._pending_subscriptions.extend(to_add)
        if to_add:
            logger.info(
                f"StreamManager: queued {len(to_add)} symbol(s) for subscription: "
                f"{', '.join(to_add)}"
            )

    def is_connected(self) -> bool:
        return self._connected

    def seconds_since_last_message(self) -> float:
        """
        Seconds elapsed since the last L1 equity message was received.
        Returns infinity if no message has ever been received this session.
        Used by the main loop watchdog to detect a silently stalled stream.
        """
        t = self._last_message_time
        return (time.monotonic() - t) if t > 0 else float("inf")

    # ── THREAD-SAFE ACCESSORS ──────────────────────────────────────────────────

    def get_quote(self, symbol: str) -> dict | None:
        """
        Return the latest Level 1 quote for symbol, or None if not yet received.
        Keys: bid, ask, last, bid_size, ask_size, last_size, volume,
              open, close, mark, security_status, mark_change_pct, ts
        """
        with self._lock:
            return dict(self._quotes.get(symbol.upper(), {})) or None

    def get_book(self, symbol: str) -> dict | None:
        """
        Return the latest Level 2 book snapshot for symbol, or None.
        Keys: bids [(price, size, count), ...], asks [...], time (ms epoch)
        """
        with self._lock:
            b = self._books.get(symbol.upper())
            return dict(b) if b else None

    def get_candles(self, symbol: str, n: int | None = None) -> list[dict]:
        """
        Return up to n completed 1-minute candles for symbol (oldest first).
        Keys: open, high, low, close, volume, time (datetime UTC), sequence
        """
        with self._lock:
            dq = self._candles.get(symbol.upper())
            if not dq:
                return []
            result = list(dq)
        return result[-n:] if n else result

    def is_halted(self, symbol: str) -> bool:
        with self._lock:
            return symbol.upper() in self._halted

    def get_all_quotes(self) -> dict[str, dict]:
        """Snapshot of all latest quotes. Used by order flow engine."""
        with self._lock:
            return {sym: dict(q) for sym, q in self._quotes.items()}

    def get_screener_hits(self) -> list[dict]:
        """
        Return and clear screener-discovered symbols since the last call.
        Each dict: {symbol, pct_change, price, volume, screener_key}.
        Called by the main session loop to discover new gap candidates.
        """
        with self._lock:
            hits = list(self._screener_hits)
            self._screener_hits.clear()
        return hits

    # ── ASYNC LOOP (runs in background thread) ─────────────────────────────────

    def _run_loop(self):
        """Entry point for the background thread. Owns the asyncio event loop."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._stream_with_reconnect())
        except Exception as e:
            logger.error(f"StreamManager loop exited with error: {e}")
        finally:
            self._connected = False
            self._loop.close()

    async def _stream_with_reconnect(self):
        """Outer reconnect loop — retries with exponential back-off."""
        attempt = 0
        while not self._stop_event.is_set():
            try:
                await self._stream_session()
                attempt = 0   # successful run resets back-off
            except Exception as e:
                if self._stop_event.is_set():
                    break
                backoff = _BACKOFF_SEQUENCE[min(attempt, len(_BACKOFF_SEQUENCE) - 1)]
                logger.warning(
                    f"Stream disconnected (attempt {attempt + 1}): {e} — "
                    f"reconnecting in {backoff}s"
                )
                self._connected = False
                attempt += 1
                await asyncio.sleep(backoff)

    async def _stream_session(self):
        """
        Single streaming session: login → subscribe → receive until disconnect.
        """
        symbols = self._symbols[:]   # snapshot at connect time

        stream = StreamClient(
            self._client,
            account_id=self._account_id,
            enforce_enums=True,
        )

        # ── Register handlers ──────────────────────────────────────────────
        stream.add_level_one_equity_handler(self._on_l1_equity)
        stream.add_nasdaq_book_handler(self._on_book)
        stream.add_nyse_book_handler(self._on_book)
        stream.add_chart_equity_handler(self._on_chart)
        stream.add_screener_equity_handler(self._on_screener)

        await stream.login()
        self._connected = True
        logger.info(f"Stream connected — subscribing to {len(symbols)} symbol(s)")

        if symbols:
            # Level 1 — price, size, volume, halt status
            await stream.level_one_equity_add(symbols, fields=_L1_FIELDS)

            # Level 2 books — NASDAQ for NASDAQ-listed, NYSE for NYSE-listed
            # We subscribe both since we don't know exchange ahead of time
            await stream.nasdaq_book_add(symbols)
            await stream.nyse_book_add(symbols)

            # 1-minute chart candles
            await stream.chart_equity_add(symbols)

        # ── Equity screener — discover gap candidates across the full market ──
        # Runs regardless of candidate list size. If the subscription key format
        # isn't accepted by the server it will silently fail (no crash).
        try:
            await stream.screener_equity_add(_SCREENER_KEYS, fields=_SCREENER_FIELDS)
            logger.info(
                f"Screener subscribed: {', '.join(_SCREENER_KEYS)}"
            )
        except Exception as e:
            logger.warning(f"Screener subscription failed (non-fatal): {e}")

        # Receive loop — drain pending subscriptions before each message
        while not self._stop_event.is_set():
            # Check for symbols that were added via subscribe_symbols() after start
            pending = []
            with self._lock:
                if self._pending_subscriptions:
                    pending = self._pending_subscriptions[:]
                    self._pending_subscriptions.clear()

            if pending:
                try:
                    await stream.level_one_equity_add(pending, fields=_L1_FIELDS)
                    await stream.nasdaq_book_add(pending)
                    await stream.nyse_book_add(pending)
                    await stream.chart_equity_add(pending)
                    logger.info(
                        f"Stream: dynamically subscribed {len(pending)} symbol(s): "
                        f"{', '.join(pending)}"
                    )
                except Exception as e:
                    logger.warning(
                        f"Stream: dynamic subscription failed for "
                        f"{', '.join(pending)}: {e}"
                    )

            await stream.handle_message()

    # ── MESSAGE HANDLERS ───────────────────────────────────────────────────────

    def _on_l1_equity(self, msg: dict):
        """Handle Level 1 equity updates."""
        content = msg.get("content", [])
        for item in content:
            sym = item.get("key", "").upper()
            if not sym:
                continue

            q = {
                "bid":              item.get("BID_PRICE"),
                "ask":              item.get("ASK_PRICE"),
                "last":             item.get("LAST_PRICE"),
                "bid_size":         item.get("BID_SIZE"),
                "ask_size":         item.get("ASK_SIZE"),
                "last_size":        item.get("LAST_SIZE"),
                "volume":           item.get("TOTAL_VOLUME"),
                "open":             item.get("OPEN_PRICE"),
                "close":            item.get("CLOSE_PRICE"),
                "mark":             item.get("MARK"),
                "security_status":  item.get("SECURITY_STATUS"),
                "mark_change_pct":  item.get("MARK_CHANGE_PERCENT"),
                "ts":               _utcnow(),
            }

            # Remove None values so callers can detect missing fields
            q = {k: v for k, v in q.items() if v is not None}

            status = item.get("SECURITY_STATUS", "")
            halted = status in _HALT_STATUSES

            with self._lock:
                # Merge: keep existing fields if new message omits them
                existing = self._quotes.get(sym, {})
                existing.update(q)
                self._quotes[sym] = existing
                self._last_message_time = time.monotonic()

                if halted:
                    if sym not in self._halted:
                        logger.warning(f"HALT detected: {sym} | status={status}")
                    self._halted.add(sym)
                else:
                    self._halted.discard(sym)

    def _on_book(self, msg: dict):
        """Handle Level 2 book updates (NASDAQ or NYSE)."""
        content = msg.get("content", [])
        for item in content:
            sym = item.get("key", "").upper()
            if not sym:
                continue

            raw_bids = item.get("BIDS", [])
            raw_asks = item.get("ASKS", [])

            # Each entry is a dict with "price", "totalSize", "numMarketMakers"
            # or similar fields depending on book type
            bids = _parse_book_side(raw_bids)
            asks = _parse_book_side(raw_asks)

            with self._lock:
                self._books[sym] = {
                    "bids": bids,
                    "asks": asks,
                    "time": item.get("BOOK_TIME"),
                    "ts":   _utcnow(),
                }

    def _on_chart(self, msg: dict):
        """Handle 1-minute chart candle updates."""
        content = msg.get("content", [])
        for item in content:
            sym = item.get("key", "").upper()
            if not sym:
                continue

            chart_time_ms = item.get("CHART_TIME_MILLIS")
            candle = {
                "open":     item.get("OPEN_PRICE"),
                "high":     item.get("HIGH_PRICE"),
                "low":      item.get("LOW_PRICE"),
                "close":    item.get("CLOSE_PRICE"),
                "volume":   item.get("VOLUME"),
                "sequence": item.get("SEQUENCE"),
                "time":     (
                    datetime.fromtimestamp(chart_time_ms / 1000, tz=timezone.utc)
                    if chart_time_ms else _utcnow()
                ),
            }

            # Only store candles with valid OHLCV data
            if None in (candle["open"], candle["high"], candle["low"],
                        candle["close"], candle["volume"]):
                continue

            with self._lock:
                if sym not in self._candles:
                    self._candles[sym] = deque(maxlen=_MAX_CANDLES)
                dq = self._candles[sym]
                # Replace if same sequence (partial candle update), else append
                if dq and dq[-1].get("sequence") == candle["sequence"]:
                    dq[-1] = candle
                else:
                    dq.append(candle)

    def _on_screener(self, msg: dict):
        """
        Handle SCREENER_EQUITY updates.

        The server sends the current top-N list for each screener key whenever
        the ranking changes.  Each content item has:
          key   = screener key ("NASDAQ_PERCENT_UP_0", etc.)
          ITEMS = list of dicts, each with symbol/lastPrice/netPercentChange/volume

        We filter by our gap/price criteria and store qualifying symbols in
        _screener_hits for the main loop to process.
        """
        content = msg.get("content", [])
        for item in content:
            screener_key = item.get("key", "")
            raw_items    = item.get("ITEMS") or []

            for stock in raw_items:
                # Schwab may send items as dicts with various field names
                sym = (
                    stock.get("symbol") or
                    stock.get("SYMBOL") or
                    stock.get("0") or ""
                ).upper().strip()
                if not sym:
                    continue

                price = float(
                    stock.get("lastPrice") or
                    stock.get("LAST_PRICE") or
                    stock.get("3") or 0
                )
                pct_change = float(
                    stock.get("netPercentChange") or
                    stock.get("PERCENT_CHANGE") or
                    stock.get("SORT_FIELD") or
                    stock.get("2") or 0
                )
                volume = int(
                    stock.get("volume") or
                    stock.get("TOTAL_VOLUME") or
                    stock.get("8") or 0
                )

                # Apply basic gap/price criteria — full scoring happens in the
                # gap scanner when the main loop promotes these to candidates
                if not (MIN_PRICE <= price <= MAX_PRICE):
                    continue
                if pct_change < MIN_GAP_PCT:
                    continue

                with self._lock:
                    # Avoid duplicating an already-known symbol in the hit list
                    if not any(h["symbol"] == sym for h in self._screener_hits):
                        self._screener_hits.append({
                            "symbol":       sym,
                            "pct_change":   round(pct_change, 2),
                            "price":        round(price, 2),
                            "volume":       volume,
                            "screener_key": screener_key,
                        })
                        logger.info(
                            f"Screener hit: {sym} "
                            f"+{pct_change:.1f}% @ ${price:.2f} "
                            f"vol={volume:,} [{screener_key}]"
                        )


# ── MODULE-LEVEL HELPERS ───────────────────────────────────────────────────────

def _parse_book_side(raw: list) -> list[tuple[float, int]]:
    """
    Normalize a book side from Schwab's variable format into a list of
    (price, total_size) tuples sorted best-first (descending for bids,
    ascending for asks — caller is responsible for sorting if needed).

    Schwab book entries are dicts; field names vary by exchange type.
    We attempt multiple key names for robustness.
    """
    result = []
    for entry in raw:
        if isinstance(entry, dict):
            price = (
                entry.get("price") or
                entry.get("0") or 0
            )
            size = (
                entry.get("totalSize") or
                entry.get("total_size") or
                entry.get("size") or
                entry.get("1") or 0
            )
            if price and size:
                result.append((float(price), int(size)))
    return result


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)
