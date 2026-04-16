"""Build all 7 OpenClaw skills for the trading system."""
import os
import json

BASE = r"C:\Users\User\Desktop\trading_system"
SKILLS_DIR = os.path.join(BASE, "skills")

# Create skills directory
os.makedirs(SKILLS_DIR, exist_ok=True)

# ══════════════════════════════════════════════════
# Shared config that all skills reference
# ══════════════════════════════════════════════════
config = {
    "trading_system_path": BASE.replace("\\", "/"),
    "scalper_state_file": f"{BASE}/config/paper_scalp.json".replace("\\", "/"),
    "log_dir": f"{BASE}/logs".replace("\\", "/"),
    "journal_file": f"{BASE}/journal.md".replace("\\", "/"),
    "channel": "telegram",
    "alert_thresholds": {
        "daily_pnl_alert": 500,
        "daily_loss_halt": 1000,
        "win_rate_warning": 40,
        "vix_spike_points": 3,
        "spy_gap_pct": 1.0
    }
}

with open(os.path.join(SKILLS_DIR, "config.json"), "w") as f:
    json.dump(config, f, indent=2)
print("0. skills/config.json created (shared by all skills)")

# ══════════════════════════════════════════════════
# Helper: write a skill with SKILL.md
# ══════════════════════════════════════════════════
def write_skill(name, skill_md):
    skill_path = os.path.join(SKILLS_DIR, name)
    os.makedirs(skill_path, exist_ok=True)
    with open(os.path.join(skill_path, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write(skill_md)

# ══════════════════════════════════════════════════
# SKILL 1: scalper-status (on-demand)
# ══════════════════════════════════════════════════
write_skill("scalper-status", '''---
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
''')
print("1. scalper-status skill created")

# ══════════════════════════════════════════════════
# SKILL 2: trading-alerts (cron, background)
# ══════════════════════════════════════════════════
write_skill("trading-alerts", '''---
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
''')
print("2. trading-alerts skill created")

# ══════════════════════════════════════════════════
# SKILL 3: trading-control (on-demand, requires approval)
# ══════════════════════════════════════════════════
write_skill("trading-control", '''---
name: trading-control
description: Execute control commands against the scalper — pause, resume, close all positions, get latest log output, restart. Use when the user explicitly asks to control the scalper with phrases like "pause scalper", "stop scalper", "close all positions", "show log", "restart scalper".
metadata: {"openclaw":{"emoji":"🎛️"}}
---

# Trading Control Skill

This skill lets the user control the scalper remotely via chat. All destructive actions require confirmation before executing.

## Supported Commands

### "show log" / "what's the latest" / "show recent activity"
Read the last 30 lines of the most recent log file in `C:/Users/User/Desktop/trading_system/logs/trading_YYYY-MM-DD.log` and send them in a code block via Telegram. Strip loguru prefix timestamps for cleaner reading.

### "pause scalper" / "stop scalper"
This terminates the running scalper process (it's in its own CMD window named "Scalper").
1. First confirm with the user: "Are you sure you want to stop the scalper? Open positions will stay open but no new entries will happen."
2. If confirmed, run this PowerShell command via bash:
powershell -Command "Get-Process python | Where-Object { $_.MainWindowTitle -like 'Scalper' } | Stop-Process -Force"
   Alternative safer approach: kill by process command line matching `scalper_live.py`:
powershell -Command "Get-CimInstance Win32_Process | Where-Object { $.CommandLine -like 'scalper_live.py' } | ForEach-Object { Stop-Process -Id $.ProcessId -Force }"
3. Confirm: "Scalper stopped."

### "resume scalper" / "start scalper" / "restart scalper"
Launch the scalper in a new window:
cd C:/Users/User/Desktop/trading_system && start "Scalper" cmd /k "venv\Scripts\activate && python scripts/scalper_live.py"
Confirm: "Scalper restarted."

### "close all positions" / "emergency close"
**HIGH RISK ACTION — REQUIRE DOUBLE CONFIRMATION.**
1. First message: "⚠️ This will close ALL open scalper positions at market. Confirm with 'yes close all' to proceed."
2. Only if user replies with exactly "yes close all", proceed.
3. Read `config/paper_scalp.json`, find all OPEN positions, and for each one modify the JSON to mark them CLOSED with current time and a note "manual_emergency_close". Since this is paper trading, we just update the file — no real orders.
4. Reply: "Closed X positions. Paper state updated."

### "how many trades today" / "stats"
Delegate to the scalper-status skill instead.

## Safety Rules

1. **Always confirm destructive actions** (pause, close all, restart). Never execute these on the first mention.
2. **Read-only commands** (show log, stats) execute immediately without confirmation.
3. **Never modify the scalper code files** from this skill. Code changes happen separately.
4. **Log every control action** to `C:/Users/User/Desktop/trading_system/skills/trading-control/audit.log` with timestamp and action taken.

## Format responses as code blocks

When showing logs, use Telegram markdown:
09:45:12 SIGNAL: VWAP_PULLBACK SPY conf:82
09:45:18 LIMIT FILL: SPY 663C @ $1.42
''')
print("3. trading-control skill created")

# ══════════════════════════════════════════════════
# SKILL 4: morning-briefing (cron, 8:45 AM CT daily)
# ══════════════════════════════════════════════════
write_skill("morning-briefing", '''---
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
''')
print("4. morning-briefing skill created")

# ══════════════════════════════════════════════════
# SKILL 5: evening-recap (cron, 4:30 PM CT daily)
# ══════════════════════════════════════════════════
write_skill("evening-recap", '''---
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
''')
print("5. evening-recap skill created")

# ══════════════════════════════════════════════════
# SKILL 6: market-watchdog (cron, every 5 min during market hours)
# ══════════════════════════════════════════════════
write_skill("market-watchdog", '''---
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
''')
print("6. market-watchdog skill created")

# ══════════════════════════════════════════════════
# SKILL 7: scalper-insights (on-demand, deep analysis)
# ══════════════════════════════════════════════════
write_skill("scalper-insights", '''---
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
''')
print("7. scalper-insights skill created")

# ══════════════════════════════════════════════════
# README for the skills folder
# ══════════════════════════════════════════════════
readme = '''# Trading System OpenClaw Skills

This folder contains 7 OpenClaw skills that wrap the trading system with chat-based monitoring, alerts, and control via Telegram.

## Skills

| Skill | Type | When |
|---|---|---|
| scalper-status | on-demand | User asks about current state |
| trading-alerts | cron (2min, market hours) | Position open/close, P&L thresholds |
| trading-control | on-demand | User commands pause/resume/close |
| morning-briefing | cron (8:45 AM CT daily) | Pre-market summary |
| evening-recap | cron (4:30 PM CT daily) | End-of-day performance review |
| market-watchdog | cron (5min, market hours) | VIX spikes, SPY gaps, headline risk |
| scalper-insights | on-demand | User asks for pattern analysis |

## Setup

1. Ensure OpenClaw is running and Telegram is connected
2. The skills auto-load from this workspace folder
3. Set up cron schedules for the background skills:
   - `trading-alerts`: every 2 minutes, Mon-Fri 8:30 AM – 3:00 PM CT
   - `market-watchdog`: every 5 minutes, Mon-Fri 8:30 AM – 3:00 PM CT
   - `morning-briefing`: 8:45 AM CT, Mon-Fri
   - `evening-recap`: 4:30 PM CT, Mon-Fri

Use `openclaw cron add` to register each scheduled skill — the OpenClaw agent itself can set these up if you tell it to.

## Testing

After installation, message your Telegram bot:
- "How's the scalper?" → triggers scalper-status
- "Show me recent logs" → triggers trading-control (read-only)
- "Analyze my last 20 trades" → triggers scalper-insights

## Config

See `config.json` for shared paths and alert thresholds.
'''

with open(os.path.join(SKILLS_DIR, "README.md"), "w", encoding="utf-8") as f:
    f.write(readme)
print("8. README.md created")

# ══════════════════════════════════════════════════
# Verify
# ══════════════════════════════════════════════════
print()
print("=" * 60)
print("  7 OPENCLAW SKILLS BUILT")
print("=" * 60)
print()
print(f"  Location: {SKILLS_DIR}")
print()

skills_list = [
    ("scalper-status", "on-demand", "📊 Current state report"),
    ("trading-alerts", "cron 2min", "🚨 Event alerts"),
    ("trading-control", "on-demand", "🎛️ Remote control"),
    ("morning-briefing", "cron 8:45 AM", "🌅 Pre-market summary"),
    ("evening-recap", "cron 4:30 PM", "🌙 Daily performance"),
    ("market-watchdog", "cron 5min", "👁️ Anomaly detection"),
    ("scalper-insights", "on-demand", "🔍 Pattern analysis"),
]

for name, typ, desc in skills_list:
    print(f"  {desc}")
    print(f"     skills/{name}/SKILL.md — {typ}")
    print()

print("=" * 60)
print("  NEXT STEPS")
print("=" * 60)
print()
print("  1. Tell OpenClaw to load the workspace:")
print(f'     "Load skills from {SKILLS_DIR}"')
print()
print("  2. Test the on-demand skills first:")
print('     Via Telegram: "How is the scalper doing?"')
print('     Via Telegram: "Show me the latest log"')
print()
print("  3. Ask OpenClaw to set up cron jobs:")
print('     "Schedule morning-briefing to run at 8:45 AM CT weekdays"')
print('     "Schedule evening-recap to run at 4:30 PM CT weekdays"')
print('     "Schedule trading-alerts to run every 2 minutes during market hours"')
print('     "Schedule market-watchdog to run every 5 minutes during market hours"')
print()
print("  OpenClaw will handle the cron registration itself via its cron tool.")