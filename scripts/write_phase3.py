"""Fix self-test dates and live script syntax error."""
from datetime import date

# FIX 1: Update self-test to use today's date
em = open("aggressive/exit_manager.py", "r", encoding="utf-8").read()
today = date.today().isoformat()

em = em.replace(
    '"entry_date": "2026-01-01", "max_hold_days": 21}',
    f'"entry_date": "{today}", "max_hold_days": 21}}'
)
em = em.replace(
    '"entry_date": "2026-01-01", "max_hold_days": 14',
    f'"entry_date": "{today}", "max_hold_days": 14'
)

# Actually need dynamic date, not hardcoded
em = em.replace(
    f'"entry_date": "{today}", "max_hold_days": 21}}',
    '"entry_date": date.today().isoformat(), "max_hold_days": 21}'
)
em = em.replace(
    f'"entry_date": "{today}", "max_hold_days": 14',
    '"entry_date": date.today().isoformat(), "max_hold_days": 14'
)

# Need to import date in self_test
em = em.replace(
    '    @staticmethod\n    def self_test():',
    '    @staticmethod\n    def self_test():\n        from datetime import date'
)
# Remove duplicate import if present
em = em.replace(
    'from datetime import date\n        """Run on startup to verify exit logic is correct."""\n        from loguru import logger',
    '"""Run on startup to verify exit logic is correct."""\n        from datetime import date\n        from loguru import logger'
)

open("aggressive/exit_manager.py", "w", encoding="utf-8").write(em)
print("1. Self-test uses today's date (no max_hold trigger)")

# FIX 2: Fix live script syntax error
live = open("scripts/aggressive_live.py", "r", encoding="utf-8").read()

# The self-test block was inserted inside a try block
# Find and fix the placement
old_bad = '''    logger.info("Schwab connected")

    # Run exit manager self-test before trading
    from aggressive.exit_manager import ExitManager
    _test_em = ExitManager()
    if not _test_em.self_test():
        logger.error("EXIT MANAGER FAILED SELF-TEST — ABORTING")
        return'''

# Check if it's inside a try block
idx = live.find("# Run exit manager self-test")
if idx > 0:
    # Look at what's before it
    before = live[max(0,idx-200):idx]
    print(f"2. Context before self-test:\n{before[-100:]}")

# Remove the bad insertion and place it correctly
live = live.replace(old_bad, '    logger.info("Schwab connected")')

# Find a safe place to insert — after the executor is created
old_safe = '    logger.info(f"Loaded {len(trades)} trades'
new_safe = '''    # Run exit manager self-test before trading
    from aggressive.exit_manager import ExitManager as _EM
    _test_em = _EM()
    if not _test_em.self_test():
        logger.error("EXIT MANAGER FAILED SELF-TEST — ABORTING")
        return

    logger.info(f"Loaded {len(trades)} trades'''

live = live.replace(old_safe, new_safe, 1)
print("2. Self-test moved to safe location (after executor init)")

open("scripts/aggressive_live.py", "w", encoding="utf-8").write(live)

# VERIFY
import py_compile
for p in ["aggressive/exit_manager.py", "scripts/aggressive_live.py"]:
    try:
        py_compile.compile(p, doraise=True)
        print(f"  COMPILE: {p} OK")
    except py_compile.PyCompileError as e:
        print(f"  ERROR: {p} - {e}")

# Run self-test
print()
import sys
sys.path.insert(0, ".")
try:
    # Reload
    import importlib
    import aggressive.exit_manager
    importlib.reload(aggressive.exit_manager)
    from aggressive.exit_manager import ExitManager
    result = ExitManager.self_test()
    if result:
        print("  SELF-TEST: ALL 6 PASSED")
    else:
        print("  SELF-TEST: FAILED")
except Exception as e:
    print(f"  SELF-TEST ERROR: {e}")