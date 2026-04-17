"""
Scalper Auto-Scheduler
Fully autonomous daily runner for the 0DTE Scalper.

What it does:
  - Runs indefinitely in the background (designed to start at Windows login)
  - Each weekday morning: waits until 8:15 AM CT (15 min before market open)
  - Launches scripts/scalper_live.py as a child process
  - Waits for the session to finish (EOD force-close ~2:45 PM CT)
  - Sleeps until the next trading day and repeats
  - Skips weekends and market holidays
  - Logs activity to logs/scalper_scheduler.log

Start at Windows login via REGISTER_SCALPER_TASK.bat
Stop by closing the terminal window or killing the process.
"""

import json
import logging
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
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
        logging.FileHandler(LOG_DIR / "scalper_scheduler.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("scalper_scheduler")

# ── Configuration ─────────────────────────────────────────────────────────────
# Launch 15 minutes before market open so the scalper can authenticate,
# seed candles, and classify the day before the bell rings.
STARTUP_HOUR_CT   = 8
STARTUP_MINUTE_CT = 15

# EOD safety deadline — scalper force-closes at 2:45 PM CT on its own
EOD_SAFETY_HOUR_CT   = 14
EOD_SAFETY_MINUTE_CT = 55

PYTHON = str(BASE_DIR / "venv" / "Scripts" / "python.exe")
SCRIPT = str(BASE_DIR / "scripts" / "scalper_live.py")

HOLIDAYS_FILE = BASE_DIR / "config" / "holidays_2026.json"


def _load_holidays() -> set:
    if HOLIDAYS_FILE.exists():
        try:
            with open(HOLIDAYS_FILE) as f:
                d = json.load(f)
            return set(d.get("holidays", []))
        except Exception:
            pass
    return set()


def is_trading_day(d: date | None = None) -> bool:
    today = d or date.today()
    if today.weekday() >= 5:
        return False
    return today.isoformat() not in _load_holidays()


def _ct_now() -> datetime:
    """Return current wall-clock time. Assumes system clock is set to Central Time."""
    return datetime.now()


def _next_trading_day_start() -> datetime:
    now = _ct_now()
    startup_today = now.replace(
        hour=STARTUP_HOUR_CT, minute=STARTUP_MINUTE_CT,
        second=0, microsecond=0,
    )
    if is_trading_day() and now < startup_today:
        return startup_today

    candidate = date.today() + timedelta(days=1)
    for _ in range(14):
        if is_trading_day(candidate):
            return datetime(
                candidate.year, candidate.month, candidate.day,
                STARTUP_HOUR_CT, STARTUP_MINUTE_CT,
            )
        candidate += timedelta(days=1)

    d = date.today() + timedelta(days=7)
    return datetime(d.year, d.month, d.day, STARTUP_HOUR_CT, STARTUP_MINUTE_CT)


def _wait_until(target: datetime):
    last_log = 0.0
    while True:
        now   = _ct_now()
        delta = (target - now).total_seconds()
        if delta <= 0:
            return
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
    log.info("=" * 60)
    log.info(f"LAUNCHING SCALPER SESSION — {date.today().isoformat()}")
    log.info(f"  Python : {PYTHON}")
    log.info(f"  Script : {SCRIPT}")
    log.info("=" * 60)

    try:
        proc = subprocess.Popen(
            [PYTHON, SCRIPT],
            cwd=str(BASE_DIR),
        )
        log.info(f"Scalper process started (PID {proc.pid})")

        eod_safety = _ct_now().replace(
            hour=EOD_SAFETY_HOUR_CT, minute=EOD_SAFETY_MINUTE_CT,
            second=0, microsecond=0,
        )
        deadline = (eod_safety - _ct_now()).total_seconds() + 3 * 3600
        deadline = max(deadline, 4 * 3600)

        ret = proc.wait(timeout=deadline)
        log.info(f"Scalper session finished (return code {ret})")
        return ret

    except subprocess.TimeoutExpired:
        log.warning("Scalper exceeded deadline — terminating process")
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
            f"  Script: {SCRIPT}"
        )
        return -2
    except Exception as e:
        log.error(f"Unexpected error launching scalper: {e}")
        return -3


def main():
    log.info("=" * 60)
    log.info("SCALPER AUTO-SCHEDULER STARTED")
    log.info(f"  Launch time: {STARTUP_HOUR_CT:02d}:{STARTUP_MINUTE_CT:02d} CT each trading day")
    log.info(f"  Base dir   : {BASE_DIR}")
    log.info("=" * 60)

    _ran_today: date | None = None

    while True:
        today = date.today()

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
                f"Not a trading day ({today}). "
                f"Next trading day: {next_day.strftime('%A %Y-%m-%d')}"
            )
            _wait_until(next_day)
            continue

        startup = _next_trading_day_start()
        now     = _ct_now()
        if now < startup:
            log.info(
                f"Trading day confirmed. "
                f"Waiting until {startup.strftime('%H:%M CT')} to launch..."
            )
            _wait_until(startup)

        _ran_today = today
        run_trading_session()

        log.info("Session complete. Sleeping 5 minutes before scheduling next day...")
        time.sleep(300)


if __name__ == "__main__":
    main()
