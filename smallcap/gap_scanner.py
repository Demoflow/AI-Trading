"""
Small Cap Pre-Market Gap Scanner.

Responsibilities:
  - Fetch batch quotes from Schwab for the full universe
  - Bootstrap average daily volume from price history (30-day)
  - Run filter_gappers() to produce ranked candidate list
  - Persist candidates to SESSION_CANDIDATES_PATH for downstream stages
  - Refresh average volumes once per session (heavyweight call, done at startup)

Usage:
    scanner = GapScanner(client, universe_manager)
    scanner.bootstrap_avg_volumes()          # called once at startup
    candidates = scanner.scan(catalyst_scores)  # called each pre-market cycle
"""

import json
import time
import os
from datetime import datetime, timedelta
from loguru import logger

from smallcap.config import (
    SESSION_CANDIDATES_PATH,
    MAX_CANDIDATES,
)

# Max symbols per batch quote request — Schwab supports up to 500
_QUOTE_BATCH_SIZE = 500

# Max symbols to fetch price history for in one bootstrap run
# (each call is ~1 HTTP request; be polite at startup)
_AVG_VOL_BATCH_SLEEP = 0.15   # seconds between history calls


class GapScanner:
    """
    Fetches batch quotes from Schwab and delegates filtering to UniverseManager.
    Thread-safe enough for single-threaded use; bootstrap runs synchronously.
    """

    def __init__(self, client, universe_manager):
        """
        Args:
            client: Authenticated schwab-py Client instance.
            universe_manager: Loaded UniverseManager instance.
        """
        self._client = client
        self._universe = universe_manager
        self._avg_vol_bootstrapped = False

    # ── PUBLIC API ─────────────────────────────────────────────────────────────

    def bootstrap_avg_volumes(self):
        """
        Fetch 30-day daily OHLCV history for every ticker in the universe and
        compute average daily volume. Writes results into the UniverseManager
        for relative-volume calculations.

        This is a blocking call — intended to run once at startup before the
        pre-market scan loop begins. Takes ~30–90s for a 200-ticker universe.
        """
        tickers = self._universe.get_tickers()
        logger.info(
            f"Bootstrapping avg volumes for {len(tickers)} tickers "
            f"(this takes ~{len(tickers) * _AVG_VOL_BATCH_SLEEP:.0f}s)..."
        )

        avg_vols = {}
        failed = 0
        end_dt = datetime.now()
        start_dt = end_dt - timedelta(days=45)  # extra buffer for weekends/holidays

        for i, sym in enumerate(tickers):
            try:
                resp = self._client.get_price_history_every_day(
                    sym,
                    start_datetime=start_dt,
                    end_datetime=end_dt,
                    need_extended_hours_data=False,
                )
                if resp.status_code != 200:
                    failed += 1
                    continue

                data = resp.json()
                candles = data.get("candles", [])
                if len(candles) >= 5:
                    # Use last 30 trading days (or however many we got)
                    recent = candles[-30:]
                    avg_vol = sum(c["volume"] for c in recent) / len(recent)
                    avg_vols[sym] = avg_vol

                time.sleep(_AVG_VOL_BATCH_SLEEP)

            except Exception as e:
                logger.debug(f"Avg vol fetch failed for {sym}: {e}")
                failed += 1

            # Progress log every 50 symbols
            if (i + 1) % 50 == 0:
                logger.info(
                    f"  Avg vol bootstrap: {i + 1}/{len(tickers)} done "
                    f"({failed} failed so far)"
                )

        self._universe.update_avg_volumes(avg_vols)
        self._avg_vol_bootstrapped = True
        logger.info(
            f"Avg volume bootstrap complete: "
            f"{len(avg_vols)} loaded, {failed} failed"
        )

    def scan(self, catalyst_scores: dict | None = None) -> list[dict]:
        """
        Fetch current quotes for the universe and return ranked gap candidates.

        Args:
            catalyst_scores: Optional {symbol: score} from the news scanner.
                             Missing symbols are treated as score=0.

        Returns:
            List of candidate dicts (up to MAX_CANDIDATES), sorted by rank.
            Also persists to SESSION_CANDIDATES_PATH.
        """
        tickers = self._universe.get_tickers()
        if not tickers:
            logger.warning("Gap scan: universe is empty")
            return []

        if not self._avg_vol_bootstrapped:
            logger.warning(
                "Gap scan running without avg volume data — "
                "relative volume will be unavailable. "
                "Call bootstrap_avg_volumes() at startup."
            )

        # Fetch in batches to stay within API limits
        quotes = self._fetch_quotes_batched(tickers)

        if not quotes:
            logger.warning("Gap scan: no quotes returned from Schwab")
            return []

        candidates = self._universe.filter_gappers(quotes, catalyst_scores)

        # Enrich with prior-day change for the Dux FRD pattern filter.
        # Only runs for the small candidate list (MAX_CANDIDATES = 5), so
        # the extra API calls are negligible (~5 calls per scan cycle).
        for c in candidates:
            c["prev_day_change_pct"] = self._fetch_prev_day_change(c["symbol"])

        self._persist_candidates(candidates)

        logger.info(
            f"Gap scan complete: {len(quotes)} quotes fetched, "
            f"{len(candidates)} candidates"
        )
        if candidates:
            top = candidates[0]
            logger.info(
                f"  Top candidate: {top['symbol']} "
                f"gap={top['gap_pct']:+.1f}% "
                f"price=${top['price']:.2f} "
                f"vol={top['volume']:,} "
                f"float={_fmt_float(top['float'])} "
                f"catalyst={top['catalyst_score']}"
            )

        return candidates

    # ── PRIVATE HELPERS ────────────────────────────────────────────────────────

    def _fetch_quotes_batched(self, tickers: list[str]) -> dict:
        """
        Call get_quotes() in batches of _QUOTE_BATCH_SIZE.
        Returns merged {symbol: quote_data} dict.
        """
        from schwab.client import Client

        all_quotes: dict = {}
        batches = _chunk(tickers, _QUOTE_BATCH_SIZE)

        for batch in batches:
            try:
                resp = self._client.get_quotes(
                    batch,
                    fields=[Client.Quote.Fields.QUOTE],
                )
                if resp.status_code != 200:
                    logger.warning(
                        f"get_quotes returned HTTP {resp.status_code} "
                        f"for batch of {len(batch)}"
                    )
                    continue

                data = resp.json()
                # Response is {symbol: {quoteType: {...}, quote: {...}}}
                # We need to flatten to the format filter_gappers expects:
                # {symbol: {lastPrice, closePrice, openPrice, totalVolume, ...}}
                for sym, payload in data.items():
                    flat = _flatten_quote(sym, payload)
                    if flat:
                        all_quotes[sym.upper()] = flat

            except Exception as e:
                logger.warning(f"Batch quote fetch failed: {e}")

        return all_quotes

    def _fetch_prev_day_change(self, symbol: str) -> float:
        """
        Fetch the prior session's full-day percentage change for a symbol.

        Returns the percentage change of the prior trading day:
          (prior_day_close − two_days_ago_close) / two_days_ago_close × 100

        Used by the Dux FRD detector to confirm the stock made a large move
        on Day 1 before we attempt a First-Red-Day short on Day 2.

        Returns 0.0 on any failure (non-blocking — FRD simply won't qualify).
        """
        try:
            from datetime import timedelta
            end_dt   = datetime.now()
            start_dt = end_dt - timedelta(days=5)   # buffer for weekends/holidays

            resp = self._client.get_price_history_every_day(
                symbol,
                start_datetime=start_dt,
                end_datetime=end_dt,
                need_extended_hours_data=False,
            )
            if resp.status_code != 200:
                return 0.0

            candles = resp.json().get("candles", [])
            if len(candles) < 2:
                return 0.0

            # candles[-1] = today (partial), candles[-2] = yesterday (complete)
            # We want the prior FULL day's change: (yesterday_close - day_before_close)
            # Use candles[-2] (yesterday) and candles[-3] (day before) when available.
            if len(candles) >= 3:
                prior_close    = candles[-2]["close"]
                d2_ago_close   = candles[-3]["close"]
            else:
                # Only 2 days of data: use today's open vs yesterday's close
                prior_close  = candles[-1]["open"]
                d2_ago_close = candles[-2]["close"]

            if d2_ago_close <= 0:
                return 0.0

            return round((prior_close - d2_ago_close) / d2_ago_close * 100, 2)

        except Exception as e:
            logger.debug(f"prev_day_change fetch failed for {symbol}: {e}")
            return 0.0

    def _persist_candidates(self, candidates: list[dict]):
        """Save current candidate list to disk for monitoring / downstream use."""
        os.makedirs(os.path.dirname(SESSION_CANDIDATES_PATH), exist_ok=True)
        out = {
            "timestamp": datetime.now().isoformat(),
            "candidates": [
                {k: v for k, v in c.items() if not k.startswith("_")}
                for c in candidates
            ],
        }
        try:
            with open(SESSION_CANDIDATES_PATH, "w") as f:
                json.dump(out, f, indent=2)
        except OSError as e:
            logger.warning(f"Could not persist candidates: {e}")


# ── MODULE-LEVEL HELPERS ───────────────────────────────────────────────────────

def _chunk(lst: list, n: int):
    """Split list into chunks of at most n items."""
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def _flatten_quote(sym: str, payload: dict) -> dict | None:
    """
    Extract a flat quote dict from Schwab's nested response structure.

    Schwab returns either:
      {"quote": {"lastPrice": ..., "closePrice": ..., ...}}
    or equity-specific nesting under "equityResponse" / "fundamental" etc.

    We target the "quote" sub-object which contains the fields we need.
    """
    # Schwab wraps quotes under a key that matches the quote type
    # For equities it's typically "quote" directly in the top-level dict
    quote_obj = payload.get("quote", {})

    if not quote_obj:
        return None

    last  = quote_obj.get("lastPrice") or quote_obj.get("mark") or 0
    close = quote_obj.get("closePrice") or quote_obj.get("regularMarketLastPrice") or 0
    open_ = quote_obj.get("openPrice") or 0
    vol   = quote_obj.get("totalVolume") or quote_obj.get("regularMarketVolume") or 0
    bid   = quote_obj.get("bidPrice") or 0
    ask   = quote_obj.get("askPrice") or 0

    return {
        "lastPrice":        last,
        "closePrice":       close,
        "openPrice":        open_,
        "totalVolume":      vol,
        "bidPrice":         bid,
        "askPrice":         ask,
        "mark":             quote_obj.get("mark", last),
        "netPercentChange": quote_obj.get("netPercentChangeInDouble")
                            or quote_obj.get("netPercentChange") or 0,
    }


def _fmt_float(float_shares: int | None) -> str:
    if float_shares is None:
        return "unknown"
    if float_shares >= 1_000_000:
        return f"{float_shares / 1_000_000:.1f}M"
    if float_shares >= 1_000:
        return f"{float_shares / 1_000:.0f}K"
    return str(float_shares)
