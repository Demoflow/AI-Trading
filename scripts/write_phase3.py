"""
Scanner Fix - Phase 4
Fixes two filters that were blocking all trades in high-VIX environments:

1. MAX_IV_RANK 70 -> 90 (iv_analyzer.py)
   - At VIX=30, stocks with IV>46% were blocked before scoring even started
   - Strategy engine already handles IV appropriately via vol regime logic

2. CALL penalty 0.85 -> 0.88 (deep_analyzer.py, both paths)
   - _flow_only(): score *= 0.85 -> 0.88
   - _full():      comp * 0.85  -> 0.88
   - 0.85 was too aggressive: strength-5 CALL = 76.5 (below 85 threshold)
   - 0.88 means strength-6 CALL = 88.0 (passes), strength-5 = 79.2 (still blocked)
   - High-quality institutional flow (strength 6+) can now enter even in downtrends

Result: In tonight's TRENDING_DOWN + VIX=30.6 environment:
   PUT strength 4+ -> score 88+ -> PASS
   CALL strength 6+ -> score 88+ -> hits SMA regime gate (correct behavior)
   CALL strength 5  -> score 79  -> BLOCKED (correct, needs stronger signal)
"""

import os
import py_compile

fixes_applied = 0

# ══════════════════════════════════════════════════
# FIX 1: iv_analyzer.py - Raise MAX_IV_RANK to 90
# ══════════════════════════════════════════════════

path = "aggressive/iv_analyzer.py"
f = open(path, "r", encoding="utf-8").read()

old = "MAX_IV_RANK = 70  # Don't buy when IV above 70th percentile"
new = "MAX_IV_RANK = 90  # Strategy engine handles IV via vol regime; only block extreme outliers"

if old in f:
    f = f.replace(old, new, 1)
    open(path, "w", encoding="utf-8").write(f)
    print(f"1. FIXED: iv_analyzer MAX_IV_RANK 70 -> 90")
    fixes_applied += 1
elif "MAX_IV_RANK = 90" in f:
    print(f"1. ALREADY DONE: iv_analyzer MAX_IV_RANK already 90")
else:
    print(f"1. ERROR: Could not find MAX_IV_RANK line in {path}")

# ══════════════════════════════════════════════════
# FIX 2: deep_analyzer.py _flow_only() - 0.85 -> 0.88
# ══════════════════════════════════════════════════

path = "aggressive/deep_analyzer.py"
f = open(path, "r", encoding="utf-8").read()

# Fix the _flow_only path (score *= 0.85)
old2 = "            score *= 0.85\n        elif self.market_regime == \"TRENDING_DOWN\" and direction == \"PUT\":"
new2 = "            score *= 0.88\n        elif self.market_regime == \"TRENDING_DOWN\" and direction == \"PUT\":"

if old2 in f:
    f = f.replace(old2, new2, 1)
    print(f"2. FIXED: deep_analyzer _flow_only CALL penalty 0.85 -> 0.88")
    fixes_applied += 1
elif "score *= 0.88" in f:
    print(f"2. ALREADY DONE: _flow_only penalty already 0.88")
else:
    print(f"2. ERROR: Could not find _flow_only CALL penalty in {path}")

# Fix the _full path (comp * 0.85)
old3 = "            comp = comp * (0.85 if direction == \"CALL\" else 1.10)"
new3 = "            comp = comp * (0.88 if direction == \"CALL\" else 1.10)"

if old3 in f:
    f = f.replace(old3, new3, 1)
    print(f"3. FIXED: deep_analyzer _full CALL penalty 0.85 -> 0.88")
    fixes_applied += 1
elif "0.88 if direction" in f:
    print(f"3. ALREADY DONE: _full penalty already 0.88")
else:
    print(f"3. ERROR: Could not find _full CALL penalty in {path}")

open(path, "w", encoding="utf-8").write(f)

# ══════════════════════════════════════════════════
# VERIFY
# ══════════════════════════════════════════════════

print()
print("Verifying...")

for path in ["aggressive/iv_analyzer.py", "aggressive/deep_analyzer.py"]:
    try:
        py_compile.compile(path, doraise=True)
        print(f"  COMPILE OK: {path}")
    except py_compile.PyCompileError as e:
        print(f"  COMPILE ERROR: {path} - {e}")

iv = open("aggressive/iv_analyzer.py").read()
da = open("aggressive/deep_analyzer.py").read()
print(f"  MAX_IV_RANK = 90:   {'OK' if 'MAX_IV_RANK = 90' in iv else 'MISSING'}")
print(f"  score *= 0.88:      {'OK' if 'score *= 0.88' in da else 'MISSING'}")
print(f"  comp * 0.88:        {'OK' if '0.88 if direction' in da else 'MISSING'}")

print()
print("=" * 60)
print(f"  {fixes_applied} fix(es) applied")
print("=" * 60)
print()
print("  Expected scan results after this fix:")
print("  - PUT strength 4+: score 88+ -> PASS")
print("  - CALL strength 6+: score 88 -> hits SMA gate")
print("  - CALL strength 5:  score 79 -> BLOCKED (correct)")
print()
print("  Run: python scripts/aggressive_scan.py")