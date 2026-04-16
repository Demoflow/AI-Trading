"""
Small Cap News / Catalyst Engine.

Responsibilities:
  - Poll multiple news sources for material events
  - Score each headline against CATALYST_KEYWORDS
  - Extract ticker symbols from headline text + filing metadata
  - Maintain a rolling 4-hour score window per symbol
  - Dynamically expand the universe when high-scoring tickers are discovered
  - Expose get_scores() → {symbol: score} for the gap scanner
  - Run in a background daemon thread; non-blocking for the main scan loop

Sources (in priority order):
  1. EDGAR EFTS full-text search — queries filing BODY text for FDA/M&A keywords
     Faster discovery than title-only RSS; tickers are authoritative from SEC data.
  2. EDGAR 8-K RSS — filing titles feed; broad coverage, 40 most recent 8-Ks
  3. GlobeNewswire pharma RSS — real-time pharma/biotech press releases
  4. GlobeNewswire biotech RSS — real-time biotech press releases
  5. PR Newswire RSS — broad business news; filter by industry keyword
  6. Yahoo Finance RSS — per-ticker feed for universe members

Score semantics:
  - Positive score  → bullish catalyst (FDA, acquisition, beat, contract…)
  - Negative score  → bearish / dilutive news (offering, reverse split, fraud…)
  - Score = 0       → no news found in the look-back window

Dynamic universe expansion:
  When EDGAR EFTS or GlobeNewswire find a ticker with score >= MIN_CATALYST_SCORE
  that is NOT in the current universe, it is added automatically so the gap scanner
  can discover and rank it.

Usage:
    engine = CatalystEngine(universe_manager)
    engine.start()                      # begin background polling
    scores = engine.get_scores()        # {symbol: score} — thread-safe
    engine.stop()                       # graceful shutdown
"""

import os
import re
import time
import json
import threading
import html
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from urllib.error import URLError
from loguru import logger

# ── LLM SENTIMENT (optional — degrades gracefully if anthropic not installed) ──
# Used to refine catalyst scoring beyond keyword matching.
# Only called for headlines that already pass MIN_CATALYST_SCORE so that API
# usage is minimal (high-value events only, not every filing).
_anthropic_client = None
_LLM_AVAILABLE    = False

def _init_llm():
    global _anthropic_client, _LLM_AVAILABLE
    try:
        import anthropic as _ant
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if api_key:
            _anthropic_client = _ant.Anthropic(api_key=api_key)
            _LLM_AVAILABLE = True
            logger.info("CatalystEngine: Claude LLM sentiment enabled")
        else:
            logger.debug("CatalystEngine: ANTHROPIC_API_KEY not set — keyword scoring only")
    except ImportError:
        logger.debug("CatalystEngine: anthropic not installed — keyword scoring only")

_LLM_LAST_CALL = 0.0       # monotonic timestamp of last LLM call (rate limit)
_LLM_MIN_INTERVAL = 1.5    # minimum seconds between LLM calls

try:
    import feedparser
    _FEEDPARSER_OK = True
except ImportError:
    _FEEDPARSER_OK = False

from smallcap.config import CATALYST_KEYWORDS, MIN_CATALYST_SCORE

# ── FEED URLs ──────────────────────────────────────────────────────────────────
_EDGAR_RSS = (
    "https://www.sec.gov/cgi-bin/browse-edgar"
    "?action=getcurrent&type=8-K&dateb=&owner=include"
    "&count=40&search_text=&output=atom"
)
_EDGAR_EFTS_BASE = "https://efts.sec.gov/LATEST/search-index"

_GNW_PHARMA_RSS  = "https://www.globenewswire.com/RssFeed/subjectcode/15-Pharmaceutical"
_GNW_BIOTECH_RSS = "https://www.globenewswire.com/RssFeed/subjectcode/16-Biotechnology"
_PRN_RSS         = "https://www.prnewswire.com/rss/news-releases-list.rss"
# ── EDGAR EFTS QUERIES ─────────────────────────────────────────────────────────
# Each query is a string we pass to the SEC full-text search engine.
# Results mean the filing BODY contains these terms — far more reliable than
# parsing filing titles, which are generic (e.g., "Item 8.01: Other Events").
_EDGAR_EFTS_QUERIES = [
    '"FDA" "approved"',
    '"FDA" "clearance"',
    '"FDA" "approval"',
    '"NDA" "approved"',
    '"ANDA" "approved"',
    '"acquisition"',
    '"merger agreement"',
    '"buyout"',
    '"clinical trial" "positive"',
    '"phase" "results" "positive"',
    '"contract" "government"',
    '"partnership" "license"',
]

# PRN industries worth watching (partial match against prn_industry field)
_PRN_INDUSTRIES = {
    "biotechnology", "pharmaceuticals", "medical", "health sciences",
    "financial services", "technology", "energy", "defense",
}

# ── POLL INTERVALS (seconds) ───────────────────────────────────────────────────
_EDGAR_POLL_INTERVAL  = 90    # 8-K RSS — filings arrive in bursts; 90s is fine
_GNW_POLL_INTERVAL    = 120   # GlobeNewswire — real-time but polite polling
_PRN_POLL_INTERVAL    = 120   # PR Newswire
# EFTS is the highest-value source (reads filing bodies, not just titles).
# An FDA approval 8-K can move a stock from $3 to $15 in under 60 seconds —
# the previous 180s interval meant arriving 2+ minutes after the move started.
_EFTS_POLL_INTERVAL   = 60    # EDGAR EFTS — poll aggressively; 12 queries × 0.5s = 6s cost
_SLEEP_TICK           = 5     # Inner loop tick
# Yahoo Finance RSS feeds (feeds.finance.yahoo.com) have been progressively
# degraded since 2022 and return empty or 404 responses the majority of the
# time.  They have been removed; EDGAR + GlobeNewswire cover the same stocks.

# Score window: discard headlines older than this
_SCORE_WINDOW_HOURS = 4

# HTTP headers
_USER_AGENT   = "smallcap-trader/2.0 austinbult@gmail.com"
_EFTS_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept":     "application/json",
}

# ── REGEX ──────────────────────────────────────────────────────────────────────
# Matches "(TICK)" in EDGAR display_names like "REVIVA PHARMACEUTICALS (RVPH) (CIK)"
_EDGAR_TICKER_RE = re.compile(r"\(([A-Z]{1,5})\)")

# Common English words to exclude from ticker extraction
_COMMON_WORDS = frozenset({
    "A", "AN", "AS", "AT", "BE", "BY", "DO", "GO", "IF", "IN", "IS", "IT",
    "NO", "OF", "ON", "OR", "SO", "TO", "UP", "US", "WE", "PM", "AM", "CEO",
    "CFO", "COO", "CTO", "LLC", "LTD", "INC", "PLC", "AG", "SA", "NV", "SE",
    "ET", "EPS", "ALL", "AND", "FOR", "THE", "NEW", "NOT", "BUT", "WHO",
    "FDA", "SEC", "CIK", "IPO", "NDA", "PR", "UK", "EU", "UN",
})


class CatalystEngine:
    """
    Background news scorer. Polls EDGAR, GlobeNewswire, PR Newswire, and Yahoo
    RSS, scores headlines, and maintains a rolling per-symbol score window.
    Expands the universe automatically on high-confidence catalyst discoveries.
    """

    def __init__(self, universe_manager):
        self._universe = universe_manager
        # {symbol: [(timestamp, score), ...]}  — raw headline events
        self._events: dict[str, list[tuple[datetime, int]]] = defaultdict(list)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._seen_ids: set[str] = set()  # deduplicate feed entries
        # LLM result cache: headline_id → adjusted_score (avoids re-scoring same headline)
        self._llm_cache: dict[str, int] = {}

        if not _FEEDPARSER_OK:
            logger.warning(
                "feedparser not installed — catalyst engine running in limited mode. "
                "Run: pip install feedparser"
            )
        _init_llm()

    # ── PUBLIC API ─────────────────────────────────────────────────────────────

    def start(self):
        """Launch background polling thread. Safe to call multiple times."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="CatalystEngine"
        )
        self._thread.start()
        logger.info(
            "CatalystEngine started "
            "(EDGAR EFTS + EDGAR RSS + GlobeNewswire + PRN)"
        )

    def stop(self):
        """Signal the background thread to stop."""
        self._stop_event.set()

    def get_scores(self) -> dict[str, int]:
        """
        Return {symbol: aggregate_score} for all symbols with news in the
        rolling window. Symbols with no news are not included (caller treats
        missing keys as score=0). Thread-safe.
        """
        self._purge_old_events()
        cutoff = _utcnow() - timedelta(hours=_SCORE_WINDOW_HOURS)
        scores: dict[str, int] = {}
        with self._lock:
            for sym, events in self._events.items():
                recent = [score for ts, score in events if ts >= cutoff]
                if recent:
                    scores[sym] = sum(recent)
        return scores

    def inject_headline(self, symbol: str, headline: str, score: int | None = None):
        """
        Manually inject a headline (useful for testing or external news hooks).
        Zero-score headlines are ignored.
        """
        if score is None:
            score = _score_text(headline)
        if score == 0:
            return
        sym = symbol.upper().strip()
        with self._lock:
            self._events[sym].append((_utcnow(), score))
        logger.debug(f"Injected headline for {sym}: score={score} | {headline[:80]}")

    # ── BACKGROUND LOOP ────────────────────────────────────────────────────────

    def _run(self):
        last_edgar = 0.0
        last_gnw   = 0.0
        last_prn   = 0.0
        last_efts  = 0.0

        while not self._stop_event.is_set():
            now = time.monotonic()

            if now - last_efts >= _EFTS_POLL_INTERVAL:
                try:
                    self._poll_edgar_efts()
                except Exception as e:
                    logger.debug(f"EDGAR EFTS poll error: {e}")
                last_efts = time.monotonic()

            if now - last_edgar >= _EDGAR_POLL_INTERVAL:
                try:
                    self._poll_edgar()
                except Exception as e:
                    logger.debug(f"EDGAR RSS poll error: {e}")
                last_edgar = time.monotonic()

            if now - last_gnw >= _GNW_POLL_INTERVAL:
                try:
                    self._poll_globenewswire()
                except Exception as e:
                    logger.debug(f"GlobeNewswire poll error: {e}")
                last_gnw = time.monotonic()

            if now - last_prn >= _PRN_POLL_INTERVAL:
                try:
                    self._poll_prnewswire()
                except Exception as e:
                    logger.debug(f"PR Newswire poll error: {e}")
                last_prn = time.monotonic()

            self._stop_event.wait(_SLEEP_TICK)

    # ── SOURCE: EDGAR EFTS FULL-TEXT SEARCH ───────────────────────────────────

    def _poll_edgar_efts(self):
        """
        Search EDGAR full-text for filings whose body contains catalyst keywords.
        This catches filings like "FDA has approved..." where the title only says
        "Item 8.01: Other Events" — a major blind spot in the RSS approach.
        Tickers extracted here are highly reliable (from SEC company metadata).
        Supports dynamic universe expansion.
        """
        today = datetime.now().strftime("%Y-%m-%d")
        new_count = 0

        for query in _EDGAR_EFTS_QUERIES:
            if self._stop_event.is_set():
                break
            try:
                params = urlencode({
                    "q":          query,
                    "forms":      "8-K",
                    "dateRange":  "custom",
                    "startdt":    today,
                    "enddt":      today,
                })
                url = f"{_EDGAR_EFTS_BASE}?{params}"
                req = Request(url, headers=_EFTS_HEADERS)
                with urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read())

                hits = data.get("hits", {}).get("hits", [])
                for hit in hits:
                    source  = hit.get("_source", {})
                    hit_id  = hit.get("_id", "")

                    entry_key = f"efts:{hit_id}"
                    if entry_key in self._seen_ids:
                        continue
                    self._seen_ids.add(entry_key)

                    # Extract tickers from display_names list
                    display_names = source.get("display_names", [])
                    if isinstance(display_names, str):
                        display_names = [display_names]

                    tickers = []
                    for name in display_names:
                        tickers.extend(_extract_tickers_edgar(name))

                    if not tickers:
                        continue

                    # Score from the query terms we searched for — we KNOW
                    # those terms appear in the filing body
                    score = _score_text(query.replace('"', ''))
                    if score <= 0:
                        continue

                    ts = _utcnow()
                    headline = f"EDGAR 8-K ({query}): {', '.join(display_names[:2])}"

                    for sym in tickers:
                        self._record_catalyst(
                            sym, score, ts, headline,
                            expand_universe=True,  # EDGAR tickers are authoritative
                            headline_id=f"efts:{hit_id}",
                        )
                        new_count += 1

            except (URLError, json.JSONDecodeError, OSError) as e:
                logger.debug(f"EDGAR EFTS query '{query}': {e}")

            # Polite delay between queries
            if not self._stop_event.is_set():
                time.sleep(0.5)

        if new_count:
            logger.debug(f"EDGAR EFTS: {new_count} new catalyst event(s)")

    # ── SOURCE: EDGAR 8-K RSS ─────────────────────────────────────────────────

    def _poll_edgar(self):
        """Fetch EDGAR 8-K RSS and score new filings by title."""
        if not _FEEDPARSER_OK:
            return

        feed = feedparser.parse(_EDGAR_RSS, request_headers={"User-Agent": _USER_AGENT})
        new_count = 0

        for entry in feed.entries:
            entry_id = entry.get("id", "")
            if entry_id in self._seen_ids:
                continue
            self._seen_ids.add(entry_id)

            title   = html.unescape(entry.get("title", ""))
            summary = html.unescape(entry.get("summary", ""))
            text    = f"{title} {summary}"

            tickers = _extract_tickers_edgar(title)
            if not tickers:
                continue

            score = _score_text(text)
            if score == 0:
                continue

            ts = _parse_feed_time(entry)
            for sym in tickers:
                # EDGAR RSS: only record for universe members (no expansion —
                # filing titles are too vague to trust for new tickers)
                self._record_catalyst(sym, score, ts, title,
                                      expand_universe=False, headline_id=entry_id)
                new_count += 1

            if new_count:
                logger.info(
                    f"EDGAR 8-K: {', '.join(tickers)} "
                    f"score={score:+d} | {title[:80]}"
                )

        if new_count:
            logger.debug(f"EDGAR RSS: {new_count} new catalyst event(s)")

    # ── SOURCE: GLOBENEWSWIRE ─────────────────────────────────────────────────

    def _poll_globenewswire(self):
        """
        Poll GlobeNewswire pharma and biotech RSS feeds.
        These are real-time press releases — faster than Yahoo RSS for FDA/biotech news.
        Supports dynamic universe expansion via dc_keyword tags.
        """
        if not _FEEDPARSER_OK:
            return

        feeds = [
            ("pharma",  _GNW_PHARMA_RSS),
            ("biotech", _GNW_BIOTECH_RSS),
        ]
        new_count = 0

        for feed_name, feed_url in feeds:
            if self._stop_event.is_set():
                break
            try:
                feed = feedparser.parse(
                    feed_url, request_headers={"User-Agent": _USER_AGENT}
                )
            except Exception as e:
                logger.debug(f"GlobeNewswire {feed_name} fetch error: {e}")
                continue

            for entry in feed.entries:
                entry_id = entry.get("id", entry.get("link", ""))
                if entry_id in self._seen_ids:
                    continue
                self._seen_ids.add(entry_id)

                title   = html.unescape(entry.get("title", ""))
                summary = html.unescape(entry.get("summary", ""))
                text    = f"{title} {summary}"

                score = _score_text(text)
                if score == 0:
                    continue

                ts = _parse_feed_time(entry)

                # Extract tickers from dc_keyword tags (most reliable GNW source)
                tickers: list[str] = []
                dc_keyword = entry.get("dc_keyword", "") or entry.get("tags", "")
                if isinstance(dc_keyword, str):
                    tickers.extend(_extract_tickers_text(dc_keyword))

                # Also check tags list
                for tag in entry.get("tags", []):
                    term = tag.get("term", "")
                    tickers.extend(_extract_tickers_text(term))

                # Fall back to title text extraction
                if not tickers:
                    tickers = _extract_tickers_text(text)

                tickers = list(dict.fromkeys(tickers))  # deduplicate preserving order
                if not tickers:
                    continue

                expand = bool(dc_keyword)  # expand universe only when ticker is explicit
                for sym in tickers:
                    if self._record_catalyst(sym, score, ts, title,
                                             expand_universe=expand, headline_id=entry_id):
                        new_count += 1

                if tickers:
                    logger.info(
                        f"GlobeNewswire ({feed_name}): {', '.join(tickers)} "
                        f"score={score:+d} | {title[:80]}"
                    )

        if new_count:
            logger.debug(f"GlobeNewswire: {new_count} new catalyst event(s)")

    # ── SOURCE: PR NEWSWIRE ───────────────────────────────────────────────────

    def _poll_prnewswire(self):
        """
        Poll PR Newswire general RSS feed and filter by relevant industries.
        Less precise than GlobeNewswire (no guaranteed ticker tags), but broader
        coverage for M&A and government contracts.
        """
        if not _FEEDPARSER_OK:
            return

        try:
            feed = feedparser.parse(
                _PRN_RSS, request_headers={"User-Agent": _USER_AGENT}
            )
        except Exception as e:
            logger.debug(f"PR Newswire fetch error: {e}")
            return

        new_count = 0

        for entry in feed.entries:
            entry_id = entry.get("id", entry.get("link", ""))
            if entry_id in self._seen_ids:
                continue

            # Filter by industry — ignore press releases outside our domain
            industry = (entry.get("prn_industry") or entry.get("category") or "").lower()
            if industry and not any(kw in industry for kw in _PRN_INDUSTRIES):
                # Still check the title for clear catalyst words before skipping
                title_check = html.unescape(entry.get("title", ""))
                if _score_text(title_check) < 20:
                    self._seen_ids.add(entry_id)
                    continue

            self._seen_ids.add(entry_id)

            title   = html.unescape(entry.get("title", ""))
            summary = html.unescape(entry.get("summary", ""))
            text    = f"{title} {summary}"

            score = _score_text(text)
            if score < 15:  # Higher threshold for PRN — less precise attribution
                continue

            ts = _parse_feed_time(entry)
            tickers = _extract_tickers_text(text)
            if not tickers:
                continue

            for sym in tickers:
                # PRN: only record for universe members — free-form text extraction
                # has too many false positives to trust for universe expansion
                self._record_catalyst(sym, score, ts, title, expand_universe=False)
                new_count += 1

            if tickers:
                logger.info(
                    f"PRN: {', '.join(tickers)} "
                    f"score={score:+d} | {title[:80]}"
                )

        if new_count:
            logger.debug(f"PR Newswire: {new_count} new catalyst event(s)")

    # ── SOURCE: YAHOO FINANCE RSS ─────────────────────────────────────────────

    def _poll_yahoo(self):
        """
        Fetch Yahoo Finance RSS for universe tickers in batches.
        Ticker-scoped feed: attribution is reliable, no expansion needed.
        """
        if not _FEEDPARSER_OK:
            return

        tickers = self._universe.get_tickers()
        batches  = list(_chunk(tickers, _YAHOO_BATCH_SIZE))
        new_count = 0

        for batch in batches:
            if self._stop_event.is_set():
                break
            url  = _YAHOO_RSS_TEMPLATE.format(symbols=",".join(batch))
            feed = feedparser.parse(url, request_headers={"User-Agent": _USER_AGENT})

            for entry in feed.entries:
                entry_id = entry.get("id", "")
                if entry_id in self._seen_ids:
                    continue
                self._seen_ids.add(entry_id)

                title   = html.unescape(entry.get("title", ""))
                summary = html.unescape(entry.get("summary", ""))
                text    = f"{title} {summary}"

                score = _score_text(text)
                if score == 0:
                    continue

                mentioned = _extract_tickers_text(text)
                batch_set = set(batch)
                targets   = [t for t in mentioned if t in batch_set]
                if not targets:
                    targets = batch  # attribute to all batch members if no explicit mention

                ts = _parse_feed_time(entry)
                for sym in targets:
                    self._record_catalyst(sym, score, ts, title, expand_universe=False)
                    new_count += 1

                logger.info(
                    f"Yahoo: {', '.join(targets)} "
                    f"score={score:+d} | {title[:80]}"
                )

            if not self._stop_event.is_set():
                time.sleep(0.3)

        if new_count:
            logger.debug(f"Yahoo poll: {new_count} new catalyst event(s)")

    # ── LLM SENTIMENT REFINEMENT ──────────────────────────────────────────────

    def _llm_refine_score(
        self,
        headline_id: str,
        symbol: str,
        headline: str,
        keyword_score: int,
    ) -> int:
        """
        Use Claude to refine a keyword-matched catalyst score.

        Only called when:
          - LLM is available (ANTHROPIC_API_KEY set)
          - keyword_score >= MIN_CATALYST_SCORE (don't waste API calls on weak signals)
          - headline not already in LLM cache

        Returns the refined score. Key improvements over keyword matching:
          - Distinguishes "FDA approved drug X" (bullish) from "FDA investigating Y" (bearish)
          - Detects disguised offerings ("at-the-market equity program") keyword matching misses
          - Catches context: "did NOT meet primary endpoint" is a loss even if "phase" matches
          - Suppresses (returns negative score) on dilutive/negative news

        Rate-limited to _LLM_MIN_INTERVAL seconds between calls.
        """
        global _LLM_LAST_CALL

        if not _LLM_AVAILABLE or not _anthropic_client:
            return keyword_score

        if headline_id in self._llm_cache:
            return self._llm_cache[headline_id]

        # Rate limit
        elapsed = time.monotonic() - _LLM_LAST_CALL
        if elapsed < _LLM_MIN_INTERVAL:
            time.sleep(_LLM_MIN_INTERVAL - elapsed)

        prompt = f"""You are scoring a news headline for a small cap momentum trading system.

Symbol: {symbol}
Headline: {headline}
Initial keyword score: {keyword_score}

Rules:
1. BULLISH events (positive score 15-50): FDA approval/clearance, M&A/buyout announcement,
   earnings beat with raised guidance, major contract win, positive clinical trial PRIMARY endpoint
2. BEARISH events (negative score -15 to -50): stock offering/dilution/ATM, FDA rejection,
   trial FAILURE, reverse split, fraud/investigation, bankruptcy
3. NEUTRAL/AMBIGUOUS (score 5-10): filing with vague language, mixed results, routine update
4. Context matters: "FDA approved" = bullish; "FDA investigating" = bearish
   "Phase 3 positive" = bullish; "did not meet primary endpoint" = bearish despite matching "phase"

Respond with JSON only: {{"score": N, "sentiment": "bullish|bearish|neutral", "reason": "5 words max"}}
Score range: -50 to 50"""

        try:
            _LLM_LAST_CALL = time.monotonic()
            msg = _anthropic_client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=80,
                messages=[{"role": "user", "content": prompt}],
            )
            text = msg.content[0].text.strip()
            if "{" in text:
                text = text[text.index("{"):text.rindex("}") + 1]
            result   = json.loads(text)
            llm_score = int(result.get("score", keyword_score))
            sentiment = result.get("sentiment", "neutral")
            reason    = result.get("reason", "")

            # Clamp to reasonable range
            llm_score = max(-50, min(50, llm_score))
            self._llm_cache[headline_id] = llm_score

            if abs(llm_score - keyword_score) >= 10:
                logger.info(
                    f"LLM catalyst [{symbol}]: score {keyword_score:+d}→{llm_score:+d} "
                    f"({sentiment}) — {reason} | {headline[:60]}"
                )
            return llm_score

        except Exception as e:
            logger.debug(f"LLM catalyst scoring error for {symbol}: {e}")
            return keyword_score

    # ── SHARED RECORDING LOGIC ────────────────────────────────────────────────

    def _record_catalyst(
        self,
        sym: str,
        score: int,
        ts: datetime,
        headline: str,
        expand_universe: bool = False,
        headline_id: str = "",
    ) -> bool:
        """
        Record a catalyst event for sym.

        If sym is not in the universe:
          - expand_universe=True and score >= MIN_CATALYST_SCORE → add it
          - otherwise → silently skip

        For high-scoring headlines, refines the score via Claude LLM to catch
        context keyword matching misses (e.g. "did NOT meet endpoint", disguised offerings).

        Returns True if the event was recorded, False if skipped.
        """
        if score == 0:
            return False

        sym = sym.upper().strip()

        # Skip single-letter and common-word false-positives
        if len(sym) <= 1 or sym in _COMMON_WORDS:
            return False

        # LLM refinement: only for headlines that passed the keyword threshold.
        # This catches false positives (bad context) and false negatives (negative news
        # that matched a positive keyword like "phase" in "did not meet phase 3 endpoint").
        if score >= MIN_CATALYST_SCORE:
            cache_key = headline_id or f"{sym}:{headline[:80]}"
            score = self._llm_refine_score(cache_key, sym, headline, score)

        # After LLM refinement, re-check score threshold
        if score == 0:
            return False

        universe_set = set(self._universe.get_tickers())
        if sym not in universe_set:
            if expand_universe and score >= MIN_CATALYST_SCORE:
                self._universe.add_ticker(sym)
                logger.info(
                    f"Universe expanded: added {sym} "
                    f"(catalyst score={score:+d}) | {headline[:60]}"
                )
            else:
                return False

        with self._lock:
            self._events[sym].append((ts, score))
        return True

    # ── HELPERS ────────────────────────────────────────────────────────────────

    def _purge_old_events(self):
        """Remove events outside the score window to keep memory bounded."""
        cutoff = _utcnow() - timedelta(hours=_SCORE_WINDOW_HOURS)
        with self._lock:
            for sym in list(self._events.keys()):
                self._events[sym] = [
                    (ts, sc) for ts, sc in self._events[sym]
                    if ts >= cutoff
                ]
                if not self._events[sym]:
                    del self._events[sym]


# ── MODULE-LEVEL HELPERS ───────────────────────────────────────────────────────

def _score_text(text: str) -> int:
    """
    Score a headline/summary against CATALYST_KEYWORDS.
    Returns sum of matched keyword weights. Can be negative.
    Matching is case-insensitive, whole-word.
    """
    text_lower = text.lower()
    total = 0
    for keyword, weight in CATALYST_KEYWORDS.items():
        pattern = r"\b" + re.escape(keyword) + r"\b"
        if re.search(pattern, text_lower):
            total += weight
    return total


def _extract_tickers_edgar(title: str) -> list[str]:
    """
    Extract ticker symbols from EDGAR title strings like:
      "8-K - Some Company Name (RVPH) (Filer)"
    Returns list of uppercase ticker strings (common words excluded).
    """
    found = _EDGAR_TICKER_RE.findall(title)
    return [t for t in found if t not in _COMMON_WORDS]


def _extract_tickers_text(text: str) -> list[str]:
    """
    Extract standalone ticker mentions from free-form text.
    Only matches $TICK prefix or (TICK) parenthetical — these are the highest
    precision patterns; bare all-caps words have too many false positives.
    """
    dollar_tickers = re.findall(r"\$([A-Z]{1,5})\b", text)
    paren_tickers  = re.findall(r"\(([A-Z]{1,5})\)", text)
    seen   = set()
    result = []
    for t in dollar_tickers + paren_tickers:
        if t not in seen and t not in _COMMON_WORDS and len(t) > 1:
            seen.add(t)
            result.append(t)
    return result


def _parse_feed_time(entry) -> datetime:
    """
    Parse entry published/updated time into a UTC-aware datetime.
    Falls back to current UTC time if parsing fails.
    """
    try:
        parsed = entry.get("published_parsed") or entry.get("updated_parsed")
        if parsed:
            import calendar
            ts = calendar.timegm(parsed)
            return datetime.fromtimestamp(ts, tz=timezone.utc)
    except Exception:
        pass
    return _utcnow()


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _chunk(lst: list, n: int):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]
