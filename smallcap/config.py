"""
Small Cap Momentum Trader — Master Configuration
Ross Cameron / Warrior Trading parameters, tuned for a $25,000 account.

All times are Central Time (CT) decimal hours to match the rest of the system.
"""

# ── ACCOUNT ────────────────────────────────────────────────────────────────────
STARTING_EQUITY      = 25_000    # Starting paper equity
MAX_POSITION_VALUE   = 2_500     # Never exceed 10% of account in one position ($25k × 10%)
MAX_SHARES_CAP       = 5_000     # Hard share cap — prevents oversizing on penny stocks

# ── RISK — ROSS CAMERON RULES ──────────────────────────────────────────────────
# Rule 1: Define your risk before every trade. Never guess.
MAX_RISK_PER_TRADE   = 250       # Dollar risk per trade (1% of $25k)
# Rule 2: Daily max loss — stop trading for the entire day when hit.
MAX_DAILY_LOSS       = 500       # -$500 = stop for the day (2% of $25k)
# Rule 3: Three strikes — stop after 3 consecutive losses regardless of P&L.
MAX_CONSECUTIVE_LOSSES = 3
# Rule 4: Minimum reward-to-risk. Never take a trade where you can't make 2x your risk.
MIN_REWARD_RISK      = 2.0
# Rule 5: Never average down. If the trade isn't working, get out.
ALLOW_AVERAGE_DOWN   = False

# ── GAP SCANNER CRITERIA ───────────────────────────────────────────────────────
# Ross focuses on stocks that are already moving hard before the open.
MIN_GAP_PCT          = 10.0      # Minimum pre-market gap (Ross looks for 10–30%+)
MAX_GAP_PCT          = 200.0     # Anything above 200% pre-market is usually a scam/halt trap
MIN_PRICE            = 1.00      # Minimum stock price — avoid sub-$1 (halts too easily)
MAX_PRICE            = 20.00     # Maximum — small cap sweet spot is $2–$15
MIN_PREMARKET_VOL    = 50_000    # Minimum shares traded pre-market
MIN_REL_VOLUME       = 5.0       # Relative volume vs 30-day average — must be 5x+
MAX_FLOAT            = 20_000_000  # Maximum float — under 20M shares
PREFERRED_FLOAT      = 10_000_000  # Ideal — under 10M = most explosive moves
MIN_CATALYST_SCORE   = 15        # Minimum news catalyst score (see CATALYST_KEYWORDS)
MAX_CANDIDATES       = 5         # Track top N candidates from the morning scan
MAX_SIMULTANEOUS_POSITIONS = 2   # Ross's actual practice: focus on 1-2 names at once

# ── SESSION TIMING ─────────────────────────────────────────────────────────────
# All in CT decimal hours. Ross's prime window is 9:30–11:00 AM ET.
PREMARKET_SCAN_START = 7.0       # 7:00 AM CT = 8:00 AM ET — pre-market opens
MARKET_OPEN          = 8.5       # 8:30 AM CT = 9:30 AM ET
PRIME_WINDOW_END     = 10.5      # 10:30 AM CT = 11:30 AM ET — Ross's best window
LATE_ENTRY_CUTOFF    = 11.5      # 11:30 AM CT = 12:30 PM ET — no new entries after this
EOD_FLATTEN          = 14.5      # 2:30 PM CT = 3:30 PM ET — force close all positions
MARKET_CLOSE         = 15.0      # 3:00 PM CT = 4:00 PM ET

# ── ENTRY RULES ────────────────────────────────────────────────────────────────
# Entry is on a 1-minute chart breakout above the prior candle's high.
ENTRY_CANDLE_TF_SEC  = 60        # 1-minute candles
BREAKOUT_HOLD_TICKS  = 2         # Price must hold above breakout for N consecutive ticks
MIN_BREAKOUT_VOL_MULT = 1.5      # Breakout candle volume must be 1.5x the recent average
MIN_CONSOL_BARS      = 2         # Minimum bars of consolidation before valid flag
MAX_CONSOL_BARS      = 8         # Too many bars = momentum stalled, pattern invalid
MAX_CONSOL_RANGE_PCT = 0.03      # Consolidation range must be < 3% (tight flag)

# ── EXIT RULES ─────────────────────────────────────────────────────────────────
# Ross scales out in thirds. Never let a winner turn into a loser.
PARTIAL_1_TARGET_PCT = 0.10      # Sell 1/3 of position at +10% from entry
PARTIAL_2_TARGET_PCT = 0.20      # Sell another 1/3 at +20% from entry
# Remaining 1/3 trails with a stop
TRAIL_STOP_PCT       = 0.05      # Trail stop: 5% below the running high after partial exits
BREAKEVEN_TRIGGER_PCT = 0.05     # Move stop to breakeven after +5% gain
MAX_HOLD_MINUTES     = 90        # Never hold more than 90 minutes — Ross is in and out fast
EOD_STOP_BEFORE_MIN  = 15        # Force close at least 15 min before EOD flatten

# ── ORDER FLOW THRESHOLDS (Breakout Readiness Score) ──────────────────────────
# These define what "feels like a breakout" in quantitative terms.
MIN_OFI_RATIO        = 0.60      # Order flow imbalance: 60%+ buys winning at the quote
MIN_AGGRESSOR_BUY_PCT = 0.65     # 65%+ of tape prints must be buyer-aggressed
MIN_TAPE_VELOCITY    = 2.0       # Tape speed must be 2x the rolling baseline
MAX_ASK_WALL_REMAIN  = 0.55      # Ask wall at resistance: must be <55% of original size
MIN_BID_DEPTH_RATIO  = 0.90      # Bid depth must not be collapsing (stay above 90% of peak)
MIN_BREAKOUT_SCORE   = 65        # Composite score threshold to trigger entry (0–100)

# Score weights (must sum to 100)
SCORE_WEIGHT_OFI          = 25
SCORE_WEIGHT_TAPE_VEL     = 20
SCORE_WEIGHT_AGGRESSOR    = 20
SCORE_WEIGHT_ASK_WALL     = 20
SCORE_WEIGHT_BID_DEPTH    = 15

# ── CATALYST KEYWORDS & WEIGHTS ────────────────────────────────────────────────
# News scoring. Positive = bullish catalyst. Negative = dilution/bad news.
# Ross will NOT trade a gap without a clear reason behind it.
CATALYST_KEYWORDS = {
    # Tier 1 — Ross's favorites (regulatory/M&A events = explosive, predictable move)
    "fda":              30,
    "approved":         28,
    "approval":         28,
    "nda":              25,
    "anda":             20,
    "clearance":        22,
    "acquisition":      25,
    "merger":           25,
    "acquired":         25,
    "buyout":           25,
    "takeover":         22,
    "tender":           20,

    # Tier 2 — solid catalyst (earnings beat, contracts, clinical data)
    "earnings":         15,
    "beat":             15,
    "revenue":          10,
    "guidance":         10,
    "contract":         18,
    "partnership":      15,
    "agreement":        12,
    "license":          14,
    "grant":            12,
    "clinical":         18,
    "phase":            15,
    "trial":            14,
    "data":             10,
    "results":          10,
    "positive":         8,

    # Tier 3 — supporting context
    "upgrade":          12,
    "nasdaq":           8,
    "listing":          8,
    "short":            8,
    "squeeze":          12,
    "government":       10,
    "military":         10,
    "defense":          10,
    "patent":           10,
    "exclusive":        10,

    # Negative — penalize hard
    "offering":         -20,
    "secondary":        -20,
    "dilution":         -25,
    "dilutive":         -25,
    "atm":              -18,
    "shelf":            -12,
    "reverse split":    -30,
    "reverse-split":    -30,
    "investigation":    -30,
    "fraud":            -35,
    "lawsuit":          -15,
    "subpoena":         -25,
    "bankruptcy":       -40,
    "default":          -25,
    "delisting":        -35,
    "withdraw":         -15,
    "failed":           -15,
    "failure":          -15,
}

# ── DATA PATHS ─────────────────────────────────────────────────────────────────
FLOAT_CACHE_PATH       = "config/smallcap_float_cache.json"
UNIVERSE_PATH          = "config/smallcap_universe.txt"
PORTFOLIO_PATH         = "config/smallcap_portfolio.json"
SESSION_CANDIDATES_PATH = "config/smallcap_candidates.json"

# ── CACHE TTL ──────────────────────────────────────────────────────────────────
FLOAT_CACHE_TTL_DAYS   = 3       # Refresh float data every 3 days
                                 # (float rarely changes day-to-day for small caps)

# ── SCAN INTERVALS ─────────────────────────────────────────────────────────────
PREMARKET_SCAN_INTERVAL_SEC = 60  # Full universe scan every 60s pre-market
OFI_UPDATE_INTERVAL_SEC     = 1   # Order flow score refresh every 1s during active trading
CANDLE_BUILD_INTERVAL_SEC   = 60  # 1-minute candle build frequency (Schwab stream)
# Pattern engine scans at this interval — independent of candle frequency.
# Schwab pushes candle updates in real time (mid-candle ticks), so patterns
# can form and be detected well within the 1-minute candle window.
# 5s matches the Dux engine and prevents missing breakouts for up to a full minute.
PATTERN_SCAN_INTERVAL_SEC   = 5   # Re-scan for patterns every 5 seconds

# ── EXECUTION ──────────────────────────────────────────────────────────────────
BUY_SLIPPAGE_PCT     = 0.005     # Assume 0.5% slippage on buys (small cap spreads are wide)
SELL_SLIPPAGE_PCT    = 0.003     # 0.3% slippage on planned sells (partial targets)
STOP_SELL_SLIPPAGE_PCT = 0.001   # 0.1% below bid for urgent sells — near-guaranteed fill
USE_LIMIT_ORDERS     = True      # Always use limit orders — never market on small caps
LIMIT_ORDER_OFFSET   = 0.05      # Place limit $0.05 above ask for buys (avoid missing fills)
QUOTE_STALENESS_SEC  = 10        # Reject entry if last streaming quote is older than this

# ── IOC ORDER FILL POLLING ──────────────────────────────────────────────────────
# After placing an IOC entry order, poll fill status for up to this many seconds.
# IOC orders resolve in <1s normally; 3s is a conservative safety ceiling.
ORDER_TTF_TIMEOUT_SEC = 3

# ── DYNAMIC LIMIT OFFSET TIERS ─────────────────────────────────────────────────
# Offset is scaled by available L2 liquidity within $0.15 of the touch price.
# More shares available → smaller offset needed to get filled.
DYN_OFFSET_HIGH_LIQ   = 0.01    # >= 10,000 shares available
DYN_OFFSET_MED_LIQ    = 0.03    # >= 3,000 shares
DYN_OFFSET_LOW_LIQ    = 0.05    # >= 1,000 shares (same as old LIMIT_ORDER_OFFSET)
DYN_OFFSET_THIN_LIQ   = 0.10    # < 1,000 shares — thin book, need wider offset
