---
name: market-watchdog
description: Background watchdog that monitors VIX spikes, SPY gaps, and headline risk for held positions. Runs every 5 minutes during market hours. Alerts only on anomalies, never routine moves.
metadata: {"openclaw":{"emoji":"👁️"}}
---

# Market Watchdog Skill

Runs as a cron job every 5 minutes during US market hours (8:30 AM – 3:00 PM CT, Mon-Fri). Alerts on market anomalies and headline risk.

## State tracking

Maintain `C:/Users/User/Desktop/trading_system/skills/market-watchdog/state.json`:
- `last_vix`: last observed VIX value
- `last_spy`: last observed SPY price
- `session_open_vix`: VIX at market open today
- `session_open_spy`: SPY at market open today
- `alerts_fired_today`: dedupe tracker
- `last_check_date`: YYYY-MM-DD

Reset daily alerts when date changes.

## Step 1: Market hours check

Central Time, weekday, between 8:30 AM and 3:00 PM. Otherwise exit.

## Step 2: Fetch current market data

Use the web tool to get current VIX and SPY prices. Parse the numbers.

At 8:30 AM CT (first check of the day), record `session_open_vix` and `session_open_spy`.

## Step 3: Check for anomalies

### Alert 1: VIX spike
If VIX increased by 3+ points in the last 5 minutes since `last_vix`, send:
⚠️ VIX SPIKE
VIX: XX.X → XX.X (+X.X in 5min)
Volatility rising — scalper may see wider spreads.

### Alert 2: VIX regime change
If VIX crossed above 30 (from below) OR above 25 (from below), send once per day:
⚠️ VIX crossed XX — elevated volatility regime

### Alert 3: SPY gap / large move
If SPY moved >1% from `session_open_spy`, send once per threshold per day:
📊 SPY moved X.X% from open
Session open: $XXX.XX → current: $XXX.XX

### Alert 4: Headline check for held positions
Read `paper_scalp.json` for OPEN positions. For each unique underlying symbol:
- Web search "[SYMBOL] news [today's date]"
- If a headline contains keywords like "halt", "SEC", "investigation", "recall", "downgrade", "guidance cut", "fraud", "lawsuit", send:
🚨 HEADLINE RISK: SYMBOL
"[Headline text]"
Source: [domain]
You have an open position — review immediately.
Only fire this alert once per symbol per headline.

## Step 4: Update state

Write current VIX, SPY, and fired alerts back to state.json.

## Rules

- **NEVER** alert on routine moves (VIX ±0.5, SPY ±0.3%)
- **NEVER** send more than 3 alerts in a single run
- Deduplicate rigorously — if an alert type has already fired today, skip it
- On fetch failure, skip this cycle silently (don't alert on "can't fetch VIX")
- Keep alerts short — no commentary, just facts
