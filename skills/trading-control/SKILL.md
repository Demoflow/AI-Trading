---
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
cd C:/Users/User/Desktop/trading_system && start "Scalper" cmd /k "venv\Scriptsctivate && python scripts/scalper_live.py"
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
