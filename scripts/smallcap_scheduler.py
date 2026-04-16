"""
Small Cap Auto-Scheduler
Fully autonomous daily runner for the Small Cap Momentum Trader.

What it does:
  - Runs indefinitely in the background (designed to start at Windows login)
  - Each weekday morning: waits until 6:50 AM CT (10 min before pre-market scan)
  - Launches scripts/smallcap_live.py as a child process
  - Waits for the session to finish (EOD flatten ~2:30 PM CT)
  - Sleeps until the next trading day and repeats
  - Skips weekends and market holidays
  - Logs activity to logs/smallcap_scheduler.log

Start at Windows login via REGISTER_SMALLCAP_TASK.bat
Stop by closing the terminal window or killing the process.
"""

import os
import sys
import time
import subprocess
import logging
from datetime import datetime, date, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

# ── Logging ──────────────────────────────────────────────────────────────────
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "smallcap_scheduler.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("scheduler")

# ── Configuration ─────────────────────────────────────────────────────────────
# Startup time: 6:50 AM CT = 7:50 AM ET (10 min before pre-market scan at 7:00 CT)
STARTUP_HOUR_CT   = 6
STARTUP_MINUTE_CT = 50

# Expected EOD: 2:45 PM CT — the script exits on its own, but this is our safety
# deadline: if the script is still running we let it run until this time
EOD_SAFETY_HOUR_CT   = 14
EOD_SAFETY_MINUTE_CT = 45

# Python executable from the venv
PYTHON  = str(BASE_DIR / "venv" / "Scripts" / "python.exe")
SCRIPT  = str(BASE_DIR / "scripts" / "smallcap_live.py")

# Market calendar: skip weekends + holidays
HOLIDAYS_FILE = BASE_DIR / "config" / "holidays_2026.json"


def _load_holidays() -> set:
    if HOLIDAYS_FILE.exists():
        import json
        try:
            with open(HOLIDAYS_FILE) as f:
                d = json.load(f)
            return set(d.get("holidays", []))
        except Exception:
            pass
    return set()


def is_trading_day(d: date | None = None) -> bool:
    today = d or date.today()
    if today.weekday() >= 5:          # Sat=5, Sun=6
        return False
    holidays = _load_holidays()
    return today.isoformat() not in holidays


def _ct_now() -> datetime:
    """
    Return current wall-clock time as a naive datetime treated as CT.
    Assumes Windows system clock is set to Central Time.
    If your clock is set to ET, subtract 1 hour from STARTUP_HOUR_CT above.
    """
    return datetime.now()


def _next_trading_day_start() -> datetime:
    """
    Return the next datetime we should launch the trader.
    If today is a trading day and we haven't passed the startup window, return today.
    Otherwise, find the next trading weekday.
    """
    now = _ct_now()
    startup_today = now.replace(
        hour=STARTUP_HOUR_CT, minute=STARTUP_MINUTE_CT,
        second=0, microsecond=0,
    )

    # If today is a trading day and startup is still in the future
    if is_trading_day() and now < startup_today:
        return startup_today

    # Otherwise find the next trading day
    candidate = date.today() + timedelta(days=1)
    for _ in range(14):  # look ahead up to 2 weeks
        if is_trading_day(candidate):
            return datetime(
                candidate.year, candidate.month, candidate.day,
                STARTUP_HOUR_CT, STARTUP_MINUTE_CT,
            )
        candidate += timedelta(days=1)

    # Fallback: 7 days out (should never happen)
    d = date.today() + timedelta(days=7)
    return datetime(d.year, d.month, d.day, STARTUP_HOUR_CT, STARTUP_MINUTE_CT)


def _wait_until(target: datetime):
    """Sleep in 30s chunks until target time, logging progress periodically."""
    last_log = 0.0
    while True:
        now   = _ct_now()
        delta = (target - now).total_seconds()
        if delta <= 0:
            return
        # Log every 30 minutes while waiting
        if time.monotonic() - last_log >= 1800:
            hrs  = int(delta // 3600)
            mins = int((delta % 3600) // 60)
            log.info(
                f"Waiting for market open — target {target.strftime('%A %Y-%m-%d %H:%M CT')} "
                f"({hrs}h {mins}m away)"
            )
            last_log = time.monotonic()
        time.sleep(min(30, delta))


def run_trading_session():
    """
    Launch smallcap_live.py and wait for it to complete.
    Returns the process return code.
    """
    log.info("=" * 60)
    log.info(f"LAUNCHING TRADING SESSION — {date.today().isoformat()}")
    log.info(f"  Python : {PYTHON}")
    log.info(f"  Script : {SCRIPT}")
    log.info("=" * 60)

    try:
        proc = subprocess.Popen(
            [PYTHON, SCRIPT],
            cwd=str(BASE_DIR),
            # Inherit stdout/stderr so the terminal shows live output
        )
        log.info(f"Session process started (PID {proc.pid})")

        # Wait for the session to finish
        # The script exits at EOD_FLATTEN (~2:30 PM CT) on its own.
        # We add a generous 3-hour safety timeout beyond expected EOD just in case.
        eod_safety = _ct_now().replace(
            hour=EOD_SAFETY_HOUR_CT, minute=EOD_SAFETY_MINUTE_CT,
            second=0, microsecond=0,
        )
        # Allow up to 3 hours past the safety time before killing
        deadline = (eod_safety - _ct_now()).total_seconds() + 3 * 3600
        deadline = max(deadline, 4 * 3600)  # at least 4 hours regardless

        ret = proc.wait(timeout=deadline)
        log.info(f"Trading session finished (return code {ret})")
        return ret

    except subprocess.TimeoutExpired:
        log.warning("Session exceeded deadline — terminating process")
        proc.terminate()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
        return -1
    except FileNotFoundError:
        log.error(
            f"Could not find Python or script.\n"
            f"  Python: {PYTHON}\n"
            f"  Script: {SCRIPT}\n"
            f"Make sure you ran REGISTER_SMALLCAP_TASK.bat from inside the venv."
        )
        return -2
    except Exception as e:
        log.error(f"Unexpected error launching session: {e}")
        return -3


def main():
    log.info("=" * 60)
    log.info("SMALL CAP AUTO-SCHEDULER STARTED")
    log.info(f"  Launch time: {STARTUP_HOUR_CT:02d}:{STARTUP_MINUTE_CT:02d} CT each trading day")
    log.info(f"  Base dir   : {BASE_DIR}")
    log.info("=" * 60)

    _ran_today: date | None = None   # track which day we already ran

    while True:
        today = date.today()

        # Already ran today — sleep until tomorrow
        if _ran_today == today:
            tomorrow = _next_trading_day_start()
            log.info(
                f"Session already ran today ({today}). "
                f"Next session: {tomorrow.strftime('%A %Y-%m-%d %H:%M CT')}"
            )
            _wait_until(tomorrow)
            continue

        if not is_trading_day():
            next_day = _next_trading_day_start()
            log.info(
                f"Not a trading day ({today}, weekday={today.weekday()}). "
                f"Next trading day: {next_day.strftime('%A %Y-%m-%d')}"
            )
            _wait_until(next_day)
            continue

        # Trading day — wait until startup time
        startup = _next_trading_day_start()
        now     = _ct_now()
        if now < startup:
            log.info(
                f"Trading day confirmed. "
                f"Waiting until {startup.strftime('%H:%M CT')} to launch..."
            )
            _wait_until(startup)

        # ── Launch the session ─────────────────────────────────────────────
        _ran_today = today
        run_trading_session()

        # After the session ends, sleep briefly then loop to schedule the next day
        log.info("Session complete. Sleeping 5 minutes before scheduling next day...")
        time.sleep(300)


if __name__ == "__main__":
    main()
