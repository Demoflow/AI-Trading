---
name: morning-briefing
description: Generate and send a pre-market briefing at 8:45 AM Central Time every weekday morning. Covers VIX, SPY futures, economic events, yesterday's results, and what to watch today.
metadata: {"openclaw":{"emoji":"🌅"}}
---

# Morning Briefing Skill

Runs as a cron job at 8:45 AM Central Time, Monday through Friday. Delivers a concise pre-market briefing via Telegram.

## Step 1: Check it's a trading day

Skip if it's Saturday, Sunday, or a US market holiday (Jan 1, MLK Day, Presidents Day, Good Friday, Memorial Day, Juneteenth, July 4, Labor Day, Thanksgiving, Christmas).

## Step 2: Gather data

### A. Yesterday's scalper results
Read `C:/Users/User/Desktop/trading_system/config/paper_scalp.json`. From `daily_stats`, get yesterday's entry (yesterday's date string). Extract trades, wins, losses, P&L.

### B. Current VIX and SPY futures
Use the web tool to fetch:
- VIX quote (Yahoo Finance, Bloomberg, or similar)
- S&P 500 futures (/ES)
- Nasdaq futures (/NQ)

### C. Economic calendar
Use web search to find: "economic calendar [today's date] FOMC CPI NFP jobs". Look for any high-impact events scheduled for today.

### D. Market-moving overnight news
Use web search for: "market news overnight [today's date]". Identify 2-3 headlines that could affect the open.

## Step 3: Build the briefing

Send this format via Telegram (markdown):
🌅 MORNING BRIEFING — [DAY, DATE]
━━━━━━━━━━━━━━━━━━━━
Pre-Market Snapshot

VIX: XX.X (±X.X%)
/ES: X,XXX (±X.X%)
/NQ: XX,XXX (±X.X%)

Yesterday's Scalper

Trades: X (XW/XL)
P&L: ±$XXX
Win Rate: XX%

Economic Events Today

8:30 AM CPI Release
1:00 PM FOMC Minutes
(or "No high-impact events scheduled")

Overnight Headlines

Headline 1
Headline 2

Trading Notes

[1-2 sentences: if VIX is elevated, note it. If futures are gapping, note direction. If it's FOMC day, warn about chop.]

Have a good trading day 🦞

## Step 4: Log the briefing

Append the briefing text to `C:/Users/User/Desktop/trading_system/journal.md` under a heading `## [DATE] Morning Briefing`.

## Rules

- Keep the briefing under 400 words — it should be skimmable
- Never give trading advice or predictions
- Be factual, not dramatic
- If web searches fail, send the briefing with whatever you have and note "data unavailable" for missing sections
