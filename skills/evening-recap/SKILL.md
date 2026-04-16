---
name: evening-recap
description: Generate an end-of-day recap at 4:30 PM Central Time. Reviews today's scalper performance, identifies patterns, logs insights to the journal, and suggests adjustments for tomorrow.
metadata: {"openclaw":{"emoji":"🌙"}}
---

# Evening Recap Skill

Runs as a cron job at 4:30 PM Central Time, Monday through Friday. Delivers a performance recap and logs insights to the journal.

## Step 1: Read scalper state

Read `C:/Users/User/Desktop/trading_system/config/paper_scalp.json`.

## Step 2: Analyze today's trades

Filter `history` for trades where `entry_time` starts with today's date. For each trade extract:
- symbol, structure, direction
- entry_time, exit_time (calculate duration in minutes)
- entry_cost, pnl, pnl_pct
- signal_type, confidence

## Step 3: Calculate metrics

- Total trades, wins, losses, win rate
- Total P&L, average win, average loss
- Largest winner, largest loser
- Best-performing structure type
- Average hold time for wins vs losses
- Trades by time-of-day bucket (morning 9-11, midday 11-13, afternoon 13-15)

## Step 4: Identify patterns

Look for observations:
- "Wins averaged Xmin held, losses averaged Xmin — you're holding losers too long"
- "All winners used STRUCTURE_X, no wins from STRUCTURE_Y today"
- "Morning trades: X/X winners. Afternoon: X/X winners."
- "Largest loss was on SYMBOL which had LOWER confidence than the day's average"

Only report 2-4 observations. Don't overfit to small samples.

## Step 5: Send Telegram recap
🌙 EVENING RECAP — [DATE]
━━━━━━━━━━━━━━━━━━━━
Today's Performance

Trades: X (XW/XL)
P&L: ±$XXX
Win Rate: XX%
Best: SYMBOL +$XX
Worst: SYMBOL -$XX

Time-of-Day

Morning (9-11): X/X
Midday (11-13): X/X
Afternoon (13-15): X/X

Observations

Insight 1
Insight 2

Tomorrow

[1-2 sentence forward note based on today's patterns]


## Step 6: Update journal

Append to `C:/Users/User/Desktop/trading_system/journal.md`:
[DATE] Evening Recap
Stats: X trades, X wins, X losses, P&L ±$XXX, WR XX%
Trades:
TimeSymbolStructureConfP&LDuration09:22SPY 663CVWAP_PULLBACK84+$458.2min...
Observations:

Observation 1
Observation 2


## Rules

- Only analyze today (don't pull history from multiple days)
- If there were zero trades, still send a recap: "No trades today. Scalper was in scan mode."
- Keep observations factual and based on the actual numbers — no speculation
