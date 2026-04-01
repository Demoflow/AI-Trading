"""Fix scalper indentation error."""
f = open("scripts/scalper_live.py", "r", encoding="utf-8").read()

# The problem: force-close block got inserted inside the weekend check
# Fix: restructure the blocks properly
old = """        if now.weekday() >= 5 or h < 8.4 or h >= 15.1:
            # Force-close all positions at 3:45 PM (15 min before close)
        if 15.65 <= h < 15.75 and cycle > 0:"""

new = """        # Force-close all positions at 3:45 PM (15 min before close)
        if 15.65 <= h < 15.75 and cycle > 0:"""

f = f.replace(old, new)

# Now re-add the weekend/hours check after the force-close block ends
old2 = """                                logger.warning(f"FORCE CLOSED (no quote): {pos['symbol']}")

        if h >= 15.1 and cycle > 0:"""

new2 = """                                logger.warning(f"FORCE CLOSED (no quote): {pos['symbol']}")

        if now.weekday() >= 5 or h < 8.4 or h >= 15.1:
            if h >= 15.1 and cycle > 0:"""

f = f.replace(old2, new2)

open("scripts/scalper_live.py", "w", encoding="utf-8").write(f)

import py_compile
try:
    py_compile.compile("scripts/scalper_live.py", doraise=True)
    print("COMPILE: OK")
except py_compile.PyCompileError as e:
    print(f"ERROR: {e}")