"""
Steven Dux Integration — Configuration
All Dux strategy parameters in one place.

All times are Central Time (CT) decimal hours to match the rest of the system.
"""

# ── UNIVERSE FILTERS ───────────────────────────────────────────────────────────
# Dux's universe: momentum names that already made a significant move.
# Wider float ceiling than Ross (100M vs 20M) because Dux trades later in
# the lifecycle, after the initial catalyst move has expanded float awareness.
DUX_MIN_PRICE           = 3.00           # $3+ minimum — low-priced names halt too easily
DUX_MAX_FLOAT           = 100_000_000    # 100M share float ceiling
DUX_MIN_PREMARKET_VOL   = 1_000_000      # 1M+ premarket shares = adequate short liquidity
DUX_MIN_PREV_DAY_MOVE   = 20.0           # Prior session must have moved 20%+ to qualify

# ── FRD (FIRST RED DAY) ────────────────────────────────────────────────────────
# FRD: stock ran hard on Day 1, latecomers are trapped long, Day 2 we short
# the first candle that closes below the prior candle's open.  Best signal in
# the first 75 minutes of trading, before the move has had time to stabilise.
FRD_MIN_PREV_DAY_MOVE      = 100.0       # Prior day ran 100%+; ideal is 150-300%
FRD_PRIME_WINDOW_END_CT    = 9.75        # 9:45 AM CT (10:45 ET) — reliability drops after
FRD_MIN_RED_BODY_RATIO     = 0.30        # Red candle body >= 30% of prior candle's range
FRD_MIN_VOL_RATIO          = 0.80        # Red candle volume >= 80% of 5-candle average

# ── SPIKE SHORT ────────────────────────────────────────────────────────────────
# Spike Short: intraday parabolic move of 15%+ from open, then a clear
# reversal candle at a resistance level (round number / prior-day high).
SPIKE_MIN_INTRADAY_MOVE    = 0.15        # 15%+ intraday move from open = parabolic
SPIKE_MAX_CANDLES          = 20          # Move must have occurred within last 20 candles
SPIKE_RESISTANCE_WINDOW    = 0.01        # Within 1% of a resistance level = "at resistance"
SPIKE_MIN_REVERSAL_MULT    = 1.5         # Reversal candle range >= 1.5x 5-candle avg
SPIKE_MIN_VOL_RATIO        = 1.5         # Volume >= 1.5x 5-candle avg on reversal bar

# ── HEAD & SHOULDERS ──────────────────────────────────────────────────────────
# H&S: topping formation confirming distribution after a spike or FRD run.
# Neckline break is the short entry trigger.
HS_LOOKBACK_CANDLES        = 30          # Scan last 30 candles for the structure
HS_SHOULDER_SYMMETRY       = 0.15        # Right shoulder within 15% of left shoulder height
HS_MAX_NECKLINE_SLOPE      = 0.008       # Neckline slope < 0.8% of price (approximately flat)
HS_PRIME_WINDOW_END_CT     = 10.0        # 10:00 AM CT (11:00 ET) — must resolve before lunch

# ── DIP PANIC BUY ──────────────────────────────────────────────────────────────
# Dip Panic: stock crashes 20%+ from HOD, panic flush candle prints, then
# first recovery bar.  Dux goes LONG here for a mean-reversion bounce to VWAP.
DIP_MIN_DRAWDOWN_FROM_HOD  = 0.20        # Down 20%+ from HOD required
DIP_FLUSH_RANGE_MULT       = 3.0         # Flush candle range >= 3x average candle range
DIP_FLUSH_CLOSE_RATIO      = 0.25        # Flush closes in bottom 25% of its own range
DIP_MIN_BELOW_VWAP         = 0.05        # Price must be 5%+ below VWAP (oversold)
DIP_RECOVERY_VOL_RATIO     = 0.70        # Recovery candle volume >= 70% of flush candle

# ── RISK RULES ─────────────────────────────────────────────────────────────────
DUX_MAX_RISK_PER_TRADE     = 250         # Max dollar risk per trade
DUX_MAX_POSITION_VALUE     = 2_500       # Never exceed $2,500 per position
DUX_MAX_DAILY_LOSS         = 750         # Session halt at -$750
DUX_MAX_CONSECUTIVE        = 3           # 3 consecutive losses = session halt
DUX_MIN_REWARD_RISK        = 2.0         # Minimum 2:1 R:R at T1
DUX_WIN_RATE_GATE          = 0.65        # 65% win rate required (after gate activates)
DUX_MIN_TRADES_FOR_GATE    = 5           # Win rate gate only activates after 5 trades
DUX_MAX_SIMULTANEOUS       = 2           # Never hold more than 2 Dux positions at once

# Error reduction: after a loss that exceeds 1.5× expected risk (stop blown
# through via halt or gap), the next 3 trades are sized at 50%.
DUX_ERROR_SIZE_MULT        = 0.50        # Multiplier applied in error mode
DUX_ERROR_TRADES           = 3           # Number of reduced-size trades after error
DUX_ERROR_LOSS_TRIGGER     = 1.5         # Triggers if actual_loss > expected_risk × this

# ── EXIT RULES ─────────────────────────────────────────────────────────────────
DUX_MAX_HOLD_MINUTES       = 90          # Hard time stop: 90 min — Dux exits in the morning session
DUX_PARTIAL1_FRAC          = 0.50        # Cover 50% of short at T1 (VWAP target)
DUX_TRAIL_PCT              = 0.05        # Trail remaining 50% with 5% stop from running low
DUX_BREAKEVEN_TRIGGER      = 0.05        # Move stop to breakeven after 5% profit
DUX_EOD_STOP_BEFORE_MIN    = 15          # Force close at least 15 min before EOD flatten

# ── TIMING ─────────────────────────────────────────────────────────────────────
DUX_START_CT               = 8.5         # 8:30 AM CT (9:30 ET) — earliest Dux entry
DUX_LATE_ENTRY_CUTOFF_CT   = 10.5        # 10:30 AM CT (11:30 ET) — no new entries after
DUX_EOD_FLATTEN_CT         = 14.5        # 2:30 PM CT — force close all positions

# ── SIGNAL QUALITY ─────────────────────────────────────────────────────────────
DUX_MIN_SIGNAL_STRENGTH    = 50          # Minimum pattern strength (0-100) to attempt entry
DUX_SIGNAL_EXPIRY_MIN      = 3           # Signals expire after 3 minutes

# ── EXECUTION ──────────────────────────────────────────────────────────────────
# Short sell entry: SELL SHORT limit is the *minimum* acceptable sale price.
# Setting at (bid - offset) means "fill me at current bid or better", which
# guarantees an immediate fill.  The offset absorbs spread noise.
DUX_SHORT_ENTRY_OFFSET     = 0.05        # Accept up to $0.05 below bid for fill
# Cover orders: different urgency levels (planned vs stop vs halt).
DUX_COVER_SLIPPAGE_PCT     = 0.003       # Planned cover: last × (1 + 0.3%)
DUX_STOP_COVER_SLIPPAGE    = 0.005       # Urgent cover: ask × (1 + 0.5%)
# Signal entry drift: reject if live price moved more than this % from signal entry.
# Prevents entering with stale risk metrics when the main loop lags pattern detection.
DUX_MAX_ENTRY_DRIFT_PCT    = 0.02        # 2% drift tolerance before rejecting signal
DUX_QUOTE_STALENESS_SEC    = 10          # Reject entry if quote older than this (seconds)

# ── PORTFOLIO PERSISTENCE ──────────────────────────────────────────────────────
DUX_PORTFOLIO_PATH         = "config/dux_portfolio.json"
