"""
Small Cap Universe Manager.

Responsibilities:
  - Maintain a list of small cap tickers to scan (~150–500 symbols)
  - Cache float share counts with a configurable TTL (yfinance source)
  - Compute relative volume from prior-day average volume
  - Filter batch quotes into gap candidates at scan time

The universe lives in config/smallcap_universe.txt (one ticker per line).
If the file does not exist it is created from SEED_TICKERS on first run.
Float data is cached in config/smallcap_float_cache.json.

Usage:
    mgr = UniverseManager()
    mgr.load()                         # populate internal list
    candidates = mgr.filter_gappers(quotes_dict)  # called each scan cycle
"""

import os
import json
import math
import time
import threading
from datetime import date, datetime
from loguru import logger

from smallcap.config import (
    FLOAT_CACHE_PATH, UNIVERSE_PATH,
    MIN_GAP_PCT, MAX_GAP_PCT,
    MIN_PRICE, MAX_PRICE,
    MIN_PREMARKET_VOL, MIN_REL_VOLUME,
    MAX_FLOAT, PREFERRED_FLOAT, FLOAT_CACHE_TTL_DAYS,
    MAX_CANDIDATES, MIN_CATALYST_SCORE,
)

# ── SEED UNIVERSE ──────────────────────────────────────────────────────────────
# Curated list of frequently active small-cap momentum names.
# Drawn from biotech, clean-tech, crypto-adjacent, and high-volatility sectors
# that Ross Cameron and the Warrior Trading community commonly focus on.
# The user can edit config/smallcap_universe.txt to add/remove tickers.
SEED_TICKERS = [
    # Biotech / Pharma — FDA plays, the bread and butter
    "OCGN", "NVAX", "AGEN", "SRNE", "ATOS", "CRBP", "FREQ",
    "TTOO", "CLNN", "BIOR", "NXPL", "PXMD", "GLYC", "APVO",
    "SIGA", "IMVT", "ACST", "ELEV", "CLOV", "IMGO", "PRAX",
    "ALDX", "SLRX", "ATXI", "TPIC", "MFIN", "ATNX", "ADTX",
    "MYNZ", "RVSN", "GFAI", "ABOS", "PAVS", "CANF", "SINT",

    # Clean energy / EV — high retail interest
    "WKHS", "GOEV", "NKLA", "HYLN", "MULN", "FFIE", "BLNK",
    "CHPT", "FCEL", "PLUG", "BE", "SPWR", "NOVA", "ARRY",

    # Crypto-adjacent (miners, exchanges)
    "MARA", "RIOT", "HUT", "BITF", "CIFR", "SDIG", "CLSK",
    "BTBT", "ARBK", "HIVE",

    # Cannabis — occasionally volatile on federal news
    "SNDL", "TLRY", "ACB", "CGC", "CRON",

    # High-volatility fintech / SPACs / misc
    "XELA", "ATER", "BBIG", "IDE", "LKCO", "SOPA",
    "VERB", "EXPR", "MEGL", "HPNN", "GTII", "PNTM",

    # Defense / government contracts
    "CODA", "KULR", "GSAT", "SPCE", "RKLB", "ASTR",

    # Small cap retail favorites with frequent volume
    "AMC", "GME", "KOSS", "CRTX", "BKSY", "OPAD",
    "IRNT", "GREE", "SDC", "MVIS", "NKTR", "SKLZ", "WATT",

    # Sector ETFs that occasionally gap (leveraged small caps behave similarly)
    "LABU", "SOXS", "UVXY", "VIXY",
]


class UniverseManager:
    """
    Manages the scannable universe and float cache.
    Thread-safe: float refresh runs in a background thread.
    """

    def __init__(self):
        self._tickers: list[str] = []
        self._float_cache: dict   = {}   # {symbol: {"float": int, "date": str}}
        self._avg_vol_cache: dict = {}   # {symbol: avg_30d_volume}
        self._lock = threading.Lock()

    # ── PUBLIC API ─────────────────────────────────────────────────────────────

    def load(self) -> list[str]:
        """
        Load universe from file (or create from seed), then load float cache.
        Returns the list of tickers.
        """
        self._ensure_universe_file()
        self._tickers = self._read_universe_file()
        self._float_cache = self._load_float_cache()
        logger.info(
            f"Universe: {len(self._tickers)} tickers | "
            f"Float cache: {len(self._float_cache)} entries"
        )
        return list(self._tickers)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    def get_float(self, symbol: str) -> int | None:
        """
        Return cached float for symbol, or None if not cached / stale.
        Does NOT fetch live — call refresh_floats() for that.
        """
        with self._lock:
            entry = self._float_cache.get(symbol.upper())
        if not entry:
            return None
        age = (date.today() - date.fromisoformat(entry["date"])).days
        if age > FLOAT_CACHE_TTL_DAYS:
            return None
        return entry.get("float")

    def set_float(self, symbol: str, float_shares: int):
        """Manually set (or overwrite) float for a symbol."""
        with self._lock:
            self._float_cache[symbol.upper()] = {
                "float": int(float_shares),
                "date":  date.today().isoformat(),
            }

    def refresh_floats(self, symbols: list[str] | None = None, background: bool = True):
        """
        Fetch float data from yfinance for symbols that are missing or stale.
        If symbols=None, refreshes the full universe.
        If background=True, runs in a daemon thread (non-blocking).
        """
        targets = [s.upper() for s in (symbols or self._tickers)]
        stale = [
            s for s in targets
            if self.get_float(s) is None
        ]
        if not stale:
            logger.debug("Float cache: all entries fresh, no refresh needed")
            return

        logger.info(f"Float refresh: fetching {len(stale)} symbols from yfinance...")

        def _run():
            self._fetch_floats_yfinance(stale)

        if background:
            t = threading.Thread(target=_run, daemon=True, name="FloatRefresh")
            t.start()
        else:
            _run()

    def filter_gappers(
        self,
        quotes: dict,
        catalyst_scores: dict | None = None,
    ) -> list[dict]:
        """
        Given a dict of {symbol: quote_data} from a Schwab batch quote call,
        return a sorted list of gap candidates meeting all scanner criteria.

        quote_data expected keys (Schwab get_quotes response):
          lastPrice, openPrice, closePrice (prior close), totalVolume,
          bidPrice, askPrice, mark, netPercentChange

        catalyst_scores: optional {symbol: score} from the news scanner.
        """
        # Normalize catalyst_scores to uppercase keys once up front
        catalyst_scores = {k.upper(): v for k, v in (catalyst_scores or {}).items()}
        candidates = []

        for sym, q in quotes.items():
            sym = sym.upper()

            # ── Price filter ──
            price = q.get("lastPrice") or q.get("mark") or 0
            if not (MIN_PRICE <= price <= MAX_PRICE):
                continue

            # ── Gap calculation ──
            prior_close = q.get("closePrice", 0)
            open_price  = q.get("openPrice", 0)

            # Use the larger of the two reference prices for pre-market gap
            # (openPrice may be 0 pre-market; closePrice is yesterday's close)
            ref_price = prior_close if prior_close > 0 else open_price
            if ref_price <= 0:
                continue

            gap_pct = (price - ref_price) / ref_price * 100
            if not (MIN_GAP_PCT <= gap_pct <= MAX_GAP_PCT):
                continue

            # ── Volume filters ──
            volume = q.get("totalVolume", 0)
            if volume < MIN_PREMARKET_VOL:
                continue

            avg_vol = self._avg_vol_cache.get(sym, 0)
            rel_vol = (volume / avg_vol) if avg_vol > 0 else 0
            if avg_vol > 0 and rel_vol < MIN_REL_VOLUME:
                continue

            # ── Float filter ──
            float_shares = self.get_float(sym)
            if float_shares is not None and float_shares > MAX_FLOAT:
                continue

            # ── Catalyst score filter ──
            cat_score = catalyst_scores.get(sym, 0)
            if cat_score < -10:
                # Hard block on clearly negative catalysts (dilution, bankruptcy, fraud)
                # regardless of whether float is known
                continue
            if cat_score < MIN_CATALYST_SCORE and float_shares is not None:
                # If we have float data and no catalyst, skip
                # (if float unknown, give benefit of the doubt pre-market)
                continue

            # ── Composite rank — additive weighted score ──────────────────
            # catalyst 40%: normalized 0–1 (capped at score=100), non-negative
            cat_norm = min(1.0, max(0.0, cat_score / 100.0))

            # rel_vol 35%: log scale so 50x doesn't dominate 10x
            # log(5)=1.61, log(20)=3.0 → divide by log(20) → 0–1 range
            rv = max(rel_vol, 1.0) if rel_vol else 1.0
            rvol_norm = min(1.0, math.log(rv) / math.log(20))

            # gap 15%: normalize across useful range [MIN_GAP_PCT, 100%]
            gap_norm = min(1.0, max(0.0, (gap_pct - MIN_GAP_PCT) / (100.0 - MIN_GAP_PCT)))

            # float bonus 10%: binary — under preferred float is best
            float_bonus = 1.0 if float_shares and float_shares < PREFERRED_FLOAT else 0.0

            rank = (
                cat_norm   * 0.40 +
                rvol_norm  * 0.35 +
                gap_norm   * 0.15 +
                float_bonus * 0.10
            )

            candidates.append({
                "symbol":       sym,
                "price":        round(price, 2),
                "prior_close":  round(prior_close, 2),
                "gap_pct":      round(gap_pct, 2),
                "volume":       int(volume),
                "rel_volume":   round(rel_vol, 1) if avg_vol > 0 else None,
                "float":        float_shares,
                "catalyst_score": cat_score,
                "_rank":        round(rank, 4),
            })

        candidates.sort(key=lambda x: x["_rank"], reverse=True)
        return candidates[:MAX_CANDIDATES]

    def update_avg_volumes(self, avg_vols: dict):
        """
        Provide a mapping of {symbol: average_daily_volume} from yesterday's
        data. Called once at startup. Used for relative volume calculation.
        """
        self._avg_vol_cache.update({k.upper(): v for k, v in avg_vols.items()})

    def add_ticker(self, symbol: str):
        """Add a ticker to the universe (e.g. discovered via news)."""
        sym = symbol.upper().strip()
        if sym not in self._tickers:
            self._tickers.append(sym)
            self._append_to_universe_file(sym)
            logger.info(f"Universe: added {sym}")

    # ── PRIVATE HELPERS ────────────────────────────────────────────────────────

    def _ensure_universe_file(self):
        """Create universe file from seed if it doesn't exist."""
        os.makedirs(os.path.dirname(UNIVERSE_PATH), exist_ok=True)
        if not os.path.exists(UNIVERSE_PATH):
            with open(UNIVERSE_PATH, "w") as f:
                for ticker in sorted(set(SEED_TICKERS)):
                    f.write(ticker + "\n")
            logger.info(
                f"Created universe file: {UNIVERSE_PATH} "
                f"({len(SEED_TICKERS)} seed tickers)"
            )

    def _read_universe_file(self) -> list[str]:
        tickers = []
        with open(UNIVERSE_PATH) as f:
            for line in f:
                sym = line.strip().upper()
                if sym and not sym.startswith("#"):
                    tickers.append(sym)
        return list(dict.fromkeys(tickers))   # preserve order, deduplicate

    def _append_to_universe_file(self, symbol: str):
        with open(UNIVERSE_PATH, "a") as f:
            f.write(symbol + "\n")

    def _load_float_cache(self) -> dict:
        os.makedirs(os.path.dirname(FLOAT_CACHE_PATH), exist_ok=True)
        if not os.path.exists(FLOAT_CACHE_PATH):
            return {}
        try:
            with open(FLOAT_CACHE_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_float_cache(self):
        with self._lock:
            data = dict(self._float_cache)
        os.makedirs(os.path.dirname(FLOAT_CACHE_PATH), exist_ok=True)
        with open(FLOAT_CACHE_PATH, "w") as f:
            json.dump(data, f, indent=2)

    def _fetch_floats_yfinance(self, symbols: list[str]):
        """
        Fetch float data from yfinance in batches.
        Writes results directly to cache and persists to disk.
        """
        try:
            import yfinance as yf
        except ImportError:
            logger.warning("yfinance not available — float data unavailable")
            return

        fetched = 0
        failed  = 0

        for sym in symbols:
            try:
                info = yf.Ticker(sym).info
                float_shares = info.get("floatShares") or info.get("sharesFloat")
                if float_shares and float_shares > 0:
                    with self._lock:
                        self._float_cache[sym] = {
                            "float": int(float_shares),
                            "date":  date.today().isoformat(),
                        }
                    fetched += 1
                else:
                    failed += 1
                # Polite rate limiting — yfinance has no official rate limit
                # but hammering it triggers 429s
                time.sleep(0.3)
            except Exception as e:
                logger.debug(f"Float fetch failed for {sym}: {e}")
                failed += 1

        self._save_float_cache()
        logger.info(
            f"Float refresh complete: {fetched} fetched, {failed} failed"
        )
