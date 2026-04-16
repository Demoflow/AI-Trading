---
name: trading-alerts
description: Background monitoring skill that watches the scalper and sends Telegram alerts on key events (position open, position close, P&L thresholds, daily loss halt). Runs on a cron schedule every 2 minutes during market hours.
metadata: {"openclaw":{"emoji":"🚨"}}
---

# Trading Alerts Skill

This skill runs in the background every 2 minutes during US market hours (8:30 AM to 3:00 PM Central Time, Monday through Friday) to watch the scalper state and alert on significant events.

## State Tracking

Maintain a small state file at `C:/Users/User/Desktop/trading_system/skills/trading-alerts/state.json` with:
- `last_position_ids`: array of position IDs seen in the last check
- `last_history_count`: number of closed trades seen in the last check
- `alerts_fired_today`: array of alert types already sent today (so we don't spam)
- `last_check_date`: YYYY-MM-DD (reset alerts_fired_today when this rolls over)

## Step 1: Check if market is open

Before doing anything, check current time in Central Time. Skip if:
- Outside 8:30 AM – 3:00 PM CT
- Saturday or Sunday

## Step 2: Read scalper state

Read `C:/Users/User/Desktop/trading_system/config/paper_scalp.json`.

## Step 3: Detect events (compare to last state)

### Event A: New position opened
If a position ID in current OPEN positions is NOT in `last_position_ids`, send:
🟢 POSITION OPENED
SYMBOL STRUCTURE
Entry: $X.XX × Qty
Cost: $XXX
Strategy: TYPE

### Event B: Position closed
If `history` count increased since last check, compare to find the newly closed trade. Send:
✅ WIN  SYMBOL closed +$XX (+XX%)  Xmin
or
❌ LOSS SYMBOL closed -$XX (-XX%)  Xmin

### Event C: Daily P&L threshold crossed
Calculate today's total P&L. If it crosses +$500 or -$500 for the first time today, send:
🎉 DAILY P&L CROSSED +500—nowat+500 — now at +
500—nowat+XXX

or
⚠️ DAILY P&L CROSSED -500—nowat−500 — now at -
500—nowat−XXX

Mark this alert type in `alerts_fired_today` so it doesn't fire again.

### Event D: Daily loss halt
If today's P&L hits -$1000, send:
🛑 DAILY LOSS HALT HIT — P&L at -$XXXX
Scalper should stop new entries. Review immediately.
Mark this in `alerts_fired_today`.

### Event E: Win rate degradation
If today's trades ≥ 5 AND win rate < 40%, send once per day:
⚠️ WIN RATE WARNING — X/X trades winning (XX%)
Consider pausing the scalper.

## Step 4: Update state file

Write the new state (current position IDs, history count, today's date, alerts fired) back to `state.json`.

## Step 5: Return quietly

If no events triggered, return nothing. Do not spam the channel with "all quiet" messages.

## Safety rules

- Never send duplicate alerts for the same event (use the state file to track)
- If the scalper state file is unreadable, try once more after 30 seconds before giving up silently
- Never close positions or take any action — this is a read-only monitoring skill
