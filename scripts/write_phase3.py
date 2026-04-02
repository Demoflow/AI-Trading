"""Fix bracket stop indentation and remove duplicate _entered block."""

f = open("scripts/aggressive_live.py", "r", encoding="utf-8").read()

# Fix the bracket stop block — sleep and place_stop need to be inside the if entry_mid block
old = """                                if entry_mid > 0 and csym:
                                    ah_stop = next(a["hashValue"] for a in client.get_account_numbers().json() if a["accountNumber"] == "28135437")
                                    # Wait for Schwab to process entry before placing stop
                        import time as _tw
                        _tw.sleep(3)
                        bracket_mgr.place_stop(csym, qty, entry_mid, stype, ah_stop)"""

new = """                                if entry_mid > 0 and csym:
                                    ah_stop = next(a["hashValue"] for a in client.get_account_numbers().json() if a["accountNumber"] == "28135437")
                                    # Wait for Schwab to process entry before placing stop
                                    import time as _tw
                                    _tw.sleep(3)
                                    bracket_mgr.place_stop(csym, qty, entry_mid, stype, ah_stop)"""

f = f.replace(old, new, 1)
print("1. Fixed bracket stop indentation (now inside if entry_mid block)")

# Remove the duplicate _entered block
# There are TWO blocks that save _entered to JSON — remove the second one
old_dup = """                    trade["_entered_time"] = str(datetime.now())
                    try:
                        _tf = json.load(open("config/aggressive_trades.json"))
                        for _t in _tf.get("trades", []):
                            if _t.get("symbol") == sym:
                                _t["_entered"] = True
                        json.dump(_tf, open("config/aggressive_trades.json", "w"), indent=2, default=str)
                    except Exception:
                        pass
                    # Place GTC stop at broker level"""

new_dup = """                    # Place GTC stop at broker level"""

f = f.replace(old_dup, new_dup, 1)
print("2. Removed duplicate _entered block")

open("scripts/aggressive_live.py", "w", encoding="utf-8").write(f)

import py_compile
try:
    py_compile.compile("scripts/aggressive_live.py", doraise=True)
    print("  COMPILE: OK")
except py_compile.PyCompileError as e:
    print(f"  ERROR: {e}")

# Verify bracket stop indentation
f2 = open("scripts/aggressive_live.py", "r", encoding="utf-8").read()
lines = f2.splitlines()
for i, line in enumerate(lines):
    if "bracket_mgr.place_stop" in line and "BracketStopManager" not in line:
        indent = len(line) - len(line.lstrip())
        print(f"  Bracket stop at line {i+1}: indent={indent} (should be 36)")

# Verify only one _entered = True in the entry block
count = f2.count('trade["_entered"] = True')
print(f"  _entered = True occurrences: {count} (should be 1)")