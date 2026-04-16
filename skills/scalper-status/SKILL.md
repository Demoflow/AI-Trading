---
name: scalper-status
description: Report the current state of the 0DTE scalper — open positions, today's P&L, win rate, recent trades. Use when the user asks "how's the scalper", "what's my P&L", "show me positions", or anything about current scalper state.
metadata: {"openclaw":{"emoji":"📊"}}
---

# Scalper Status Skill

When the user asks about scalper status, open positions, current P&L, or how the scalper is doing today, follow these steps:

## Step 1: Read the scalper state file

Read `C:/Users/User/Desktop/trading_system/config/paper_scalp.json` using the bash tool or file read tool.

## Step 2: Extract key information

From the JSON, pull:
- `equity` — total account value
- `cash` — available cash
- `positions` — array of all positions (filter where `status == "OPEN"`)
- `history` — closed trades (filter today's entries by matching `entry_time` prefix to today's date YYYY-MM-DD)
- `daily_stats` — today's stats keyed by today's date

## Step 3: Calculate today's P&L for closed trades

Sum the `pnl` field across all entries in `history` where `entry_time` starts with today's date.

## Step 4: Format the response

Send a Telegram message in this exact format (use markdown):
📊 SCALPER STATUS
━━━━━━━━━━━━━━━━
💰 Equity: $XX,XXX
💵 Cash: $XX,XXX
📈 Today P&L: +$XXX (X trades, X wins, X losses)
🎯 Win Rate: XX%
📍 Open Positions: X

SYMBOL STRUCTURE — entry $X.XX — held Xmin
SYMBOL STRUCTURE — entry $X.XX — held Xmin

✅ Closed Today: X

WIN  SYMBOL +$XX (XX%)  X.Xmin
LOSS SYMBOL -$XX (-XX%) X.Xmin


If there are no open positions, say "No open positions — waiting for setups."
If there are no closed trades today, say "No trades closed yet today."

## Step 5: Keep it concise

Don't add commentary or analysis unless the user specifically asks. Just deliver the data cleanly.
