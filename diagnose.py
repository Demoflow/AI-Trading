r"""
Run from C:\Users\User\Desktop\trading_system
python diagnose.py

This will tell us exactly where Python is loading files from
and what the current values are.
"""
import sys
import os

print("=" * 60)
print("DIAGNOSTIC REPORT")
print("=" * 60)
print()

# 1. Show working directory
print(f"Working directory: {os.getcwd()}")
print()

# 2. Check the actual file content on disk right now
print("--- iv_analyzer.py on disk ---")
path = "aggressive/iv_analyzer.py"
if os.path.exists(path):
    with open(path) as f:
        for i, line in enumerate(f, 1):
            if "MAX_IV_RANK" in line or "IV_RANK" in line:
                print(f"  Line {i}: {line.rstrip()}")
else:
    print(f"  FILE NOT FOUND: {path}")

print()
print("--- deep_analyzer.py on disk ---")
path = "aggressive/deep_analyzer.py"
if os.path.exists(path):
    with open(path) as f:
        for i, line in enumerate(f, 1):
            if "0.85" in line or "0.88" in line or "score *=" in line or "comp * (" in line:
                print(f"  Line {i}: {line.rstrip()}")
else:
    print(f"  FILE NOT FOUND: {path}")

print()

# 3. Check if there are .pyc cache files that might be stale
print("--- Checking for stale .pyc cache files ---")
for root, dirs, files in os.walk("aggressive"):
    for f in files:
        if f.endswith(".pyc") and ("iv_analyzer" in f or "deep_analyzer" in f):
            full = os.path.join(root, f)
            print(f"  FOUND: {full}")
            print(f"  Delete with: del \"{full}\"")

print()

# 4. Try importing and check actual runtime values
print("--- Runtime import check ---")
try:
    sys.path.insert(0, os.getcwd())
    # Clear any cached imports
    for key in list(sys.modules.keys()):
        if "iv_analyzer" in key or "deep_analyzer" in key:
            del sys.modules[key]

    from aggressive.iv_analyzer import IVAnalyzer
    print(f"  IVAnalyzer.MAX_IV_RANK = {IVAnalyzer.MAX_IV_RANK}")
    print(f"  Loaded from: {IVAnalyzer.__module__}")

    import aggressive.iv_analyzer as iv_mod
    print(f"  Module file: {iv_mod.__file__}")
except Exception as e:
    print(f"  Import error: {e}")

print()
print("=" * 60)
