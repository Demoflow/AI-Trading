---
name: scalper-insights
description: Deep analysis of recent scalper history. Identifies patterns across the last N trades — best/worst strategies, time-of-day performance, confidence calibration, structural insights. Use when the user asks "analyze my trades", "what patterns do you see", "what's working", or "what should I change".
metadata: {"openclaw":{"emoji":"🔍"}}
---

# Scalper Insights Skill

On-demand deep analysis of scalper trading history. This is NOT a daily recap (that's `evening-recap`). This is for when the user explicitly asks for pattern analysis across multiple days.

## Step 1: Parse the user's request

Default analysis window: last 20 closed trades.
If user says "last week" → 5 trading days worth
If user says "last N trades" → N trades
If user says "this month" → current month's trades

## Step 2: Load trade history

Read `C:/Users/User/Desktop/trading_system/config/paper_scalp.json`, extract `history` array. Sort by `exit_time` descending. Take the requested window.

## Step 3: Compute metrics

### Overall
- Total P&L, win rate, profit factor (gross wins / gross losses)
- Average win, average loss, largest win, largest loss
- Average duration held

### By structure type
Group trades by `structure` (LONG_OPTION, CREDIT_SPREAD, IRON_CONDOR, NAKED_PUT, etc.):
- Trade count, win rate, avg P&L per trade

### By signal type
Group by `signal_type` (VWAP_PULLBACK, ORB_BREAKOUT, PREMIUM_SELL, etc.):
- Trade count, win rate, avg P&L

### By time of day
Bucket by entry hour: 9-10, 10-11, 11-12, 12-13, 13-14, 14-15:
- Trade count, win rate per bucket

### Confidence calibration
Bucket by confidence score: 70-79, 80-89, 90+:
- Actual win rate per bucket
- Is the scalper more profitable at higher confidence, or is confidence miscalibrated?

### Hold time vs outcome
- Average hold time for winners
- Average hold time for losers
- Are losers being held too long?

## Step 4: Identify 3-5 actionable insights

Only include insights with clear signal:
- "Your IRON_CONDOR trades: 8W/2L (80%), +$340 total. Best structure by far."
- "VWAP_PULLBACK signals at conf 80-89: 12W/8L. At conf 90+: 6W/1L. Consider raising threshold."
- "Morning trades (9-11 AM): 15W/5L. Afternoon trades: 4W/11L. Afternoon is a problem."
- "Average winning hold: 6.2min. Average losing hold: 18.4min. Losers held 3x longer — exit discipline issue."

Skip insights if the sample is too small (< 5 trades in a bucket).

## Step 5: Send Telegram report
🔍 SCALPER INSIGHTS — Last X Trades
━━━━━━━━━━━━━━━━━━━━
Overall

Total P&L: ±$XXX
Win Rate: XX% (XW/XL)
Profit Factor: X.XX
Avg Win: +XX∣AvgLoss:−XX | Avg Loss: -
XX∣AvgLoss:−XX


By Structure

IRON_CONDOR: X/X (XX%) avg ±$XX
LONG_OPTION: X/X (XX%) avg ±$XX
CREDIT_SPREAD: X/X (XX%) avg ±$XX

By Time of Day

Morning (9-11): XX% win rate
Midday (11-13): XX% win rate
Afternoon (13-15): XX% win rate

Confidence Calibration

70-79: XX% | 80-89: XX% | 90+: XX%

Actionable Insights

Insight with specific numbers
Insight with specific numbers
Insight with specific numbers


## Rules

- Only report findings backed by the actual data
- Minimum 5 trades in a bucket before commenting on it
- If total trades < 10, say so and decline deep analysis
- Never recommend specific trades — only structural/behavioral observations
- Keep the report under 500 words
