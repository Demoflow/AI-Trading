"""
OpenClaw helper: recent scalper log activity.
Called by the scalper_log skill.
"""
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

LOG_DIR = Path("C:/Users/User/Desktop/trading_system/logs")

KEYWORDS = [
    "SIGNAL", "EXIT", "FILLED", "CLOSED", "REGIME",
    "ERROR", "WARNING", "profit", "stop", "trail",
    "breakeven", "FORCE", "EOD", "Blocked",
    "Day Type", "TRANSITION", "SCALPER v6",
]


def main():
    today    = datetime.now().strftime("%Y-%m-%d")
    log_file = LOG_DIR / f"trading_{today}.log"

    print(f"SCALPER LOG  —  {today}")
    print("=" * 55)

    if not log_file.exists():
        print(f"No log file found for today.")
        print(f"Expected: {log_file}")
        print()
        print("The scalper has not run today, or logs are in a different location.")
        print("To start: python C:/Users/User/Desktop/trading_system/scripts/scalper_live.py")
        return

    with open(log_file, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    total_lines = len(lines)

    # Filter to meaningful events
    important = []
    for line in lines:
        line_upper = line.upper()
        if any(kw.upper() in line_upper for kw in KEYWORDS):
            important.append(line.rstrip())

    print(f"Log: {total_lines} total lines, {len(important)} key events")
    print()

    if not important:
        print("No significant events yet today.")
        return

    # Show last 30 key events
    shown = important[-30:]
    print(f"Most recent {len(shown)} events:")
    print()

    for line in shown:
        # Parse: "2026-04-15 09:12:34.123 | INFO    | module:fn:line - message"
        parts = line.split(" | ", 2)
        if len(parts) >= 3:
            timestamp = parts[0].strip()[:19]
            level     = parts[1].strip()
            msg_part  = parts[2]
            # Strip "module:function:line - " prefix
            if " - " in msg_part:
                msg = msg_part.split(" - ", 1)[1]
            else:
                msg = msg_part
            print(f"  {timestamp}  {level:7s}  {msg}")
        else:
            print(f"  {line}")


if __name__ == "__main__":
    main()
