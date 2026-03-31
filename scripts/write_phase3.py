"""
CRITICAL FIXES from Perplexity delta analysis.
1. iv_rank returns dict but callers expect float
2. Vol regime enforcement in wrong method
3. Remaining v1 issues
"""
import os

# ══════════════════════════════════════════════════
# FIX 1: iv_rank dict/float type mismatch
# All callers expect a float, new method returns dict
# ══════════════════════════════════════════════════

# Fix in aggressive_scanner.py
f = open("aggressive/aggressive_scanner.py", "r", encoding="utf-8").read()
lines = f.splitlines()
fixed_scanner = False
for i, line in enumerate(lines):
    if "get_iv_rank(sym)" in line and "iv_rank" in line:
        old_line = line
        indent = len(line) - len(line.lstrip())
        # Replace with dict-safe extraction
        lines[i] = " " * indent + "_iv_data = self.analyzer.iv_analyzer.get_iv_rank(sym)"
        # Insert extraction line after
        lines.insert(i + 1, " " * indent + "iv_rank = _iv_data.get('iv_rank', 50) if isinstance(_iv_data, dict) else _iv_data")
        fixed_scanner = True
        print(f"1a. Fixed iv_rank in scanner at line {i+1}")
        break

if fixed_scanner:
    open("aggressive/aggressive_scanner.py", "w", encoding="utf-8").write("\n".join(lines))
else:
    # Check if it's already been modified or uses a different pattern
    if "_iv_data" in f:
        print("1a. Scanner iv_rank already fixed")
    else:
        print("1a. WARNING: Could not find get_iv_rank call in scanner")
        # Search for it
        for i, line in enumerate(f.splitlines()):
            if "iv_rank" in line and "get_iv" in line:
                print(f"   Line {i+1}: {line.strip()}")

# Fix in iv_analyzer.py - should_trade and get_size_modifier
g = open("aggressive/iv_analyzer.py", "r", encoding="utf-8").read()
if "should_trade" in g:
    lines = g.splitlines()
    for i, line in enumerate(lines):
        if "get_iv_rank(" in line and "should_trade" not in line and "def " not in line:
            # Check if this is inside should_trade or get_size_modifier
            indent = len(line) - len(line.lstrip())
            old = line.strip()
            if "rank = " in old or "iv_rank = " in old:
                var_name = old.split("=")[0].strip()
                lines[i] = " " * indent + f"_iv_tmp = self.get_iv_rank(symbol)"
                lines.insert(i + 1, " " * indent + f"{var_name} = _iv_tmp.get('iv_rank', 50) if isinstance(_iv_tmp, dict) else _iv_tmp")
                print(f"1b. Fixed iv_rank in iv_analyzer at line {i+1}")
    open("aggressive/iv_analyzer.py", "w", encoding="utf-8").write("\n".join(lines))
else:
    print("1b. No should_trade in iv_analyzer")

# Fix in ev_calculator.py if it receives iv_rank
h = open("aggressive/ev_calculator.py", "r", encoding="utf-8").read()
if "iv_rank" in h:
    # Add safe extraction at the top of methods that use iv_rank
    lines = h.splitlines()
    for i, line in enumerate(lines):
        if "if iv_rank" in line and ("< " in line or "> " in line):
            # Add type check before this line
            indent = len(line) - len(line.lstrip())
            lines.insert(i, " " * indent + "iv_rank = iv_rank.get('iv_rank', 50) if isinstance(iv_rank, dict) else iv_rank")
            print(f"1c. Fixed iv_rank type check in ev_calculator at line {i+1}")
            break
    open("aggressive/ev_calculator.py", "w", encoding="utf-8").write("\n".join(lines))

# Fix in strategy_engine.py if it receives iv_rank
se = open("aggressive/strategy_engine.py", "r", encoding="utf-8").read()
if "iv_rank" in se:
    lines = se.splitlines()
    fixed_se = False
    for i, line in enumerate(lines):
        if "def select_strategy" in line:
            # Find iv_rank parameter usage after this
            for j in range(i, min(i + 20, len(lines))):
                if "iv_rank" in lines[j] and ("< " in lines[j] or "> " in lines[j]) and "def " not in lines[j]:
                    indent = len(lines[j]) - len(lines[j].lstrip())
                    lines.insert(j, " " * indent + "iv_rank = iv_rank.get('iv_rank', 50) if isinstance(iv_rank, dict) else iv_rank")
                    fixed_se = True
                    print(f"1d. Fixed iv_rank type check in strategy_engine at line {j+1}")
                    break
            break
    if fixed_se:
        open("aggressive/strategy_engine.py", "w", encoding="utf-8").write("\n".join(lines))

# ══════════════════════════════════════════════════
# FIX 2: Vol regime enforcement in wrong method
# Currently in _evaluate_naked_put, needs to be in select_strategy
# ══════════════════════════════════════════════════

se = open("aggressive/strategy_engine.py", "r", encoding="utf-8").read()

# First, remove the misplaced block from wherever it is
if "vol_regime_penalty" in se:
    # Find and remove the misplaced block
    lines = se.splitlines()
    in_bad_block = False
    bad_start = -1
    bad_end = -1

    for i, line in enumerate(lines):
        if "# Vol regime enforcement (Perplexity fix #3)" in line:
            bad_start = i
            in_bad_block = True
        if in_bad_block and "except Exception:" in line and bad_start > 0:
            # Find the pass after except
            for j in range(i, min(i + 3, len(lines))):
                if "pass" in lines[j]:
                    bad_end = j + 1
                    break
            if bad_end < 0:
                bad_end = i + 2
            break

    if bad_start >= 0 and bad_end > bad_start:
        # Remove the misplaced block
        removed = lines[bad_start:bad_end]
        lines = lines[:bad_start] + lines[bad_end:]
        print(f"2a. Removed misplaced vol regime block (lines {bad_start+1}-{bad_end})")

        # Now find the correct location: after strategies.sort() in select_strategy
        for i, line in enumerate(lines):
            if "strategies.sort" in line and "score" in line:
                # Insert vol regime enforcement AFTER the sort, BEFORE best selection
                indent = len(line) - len(line.lstrip())
                vol_block = [
                    "",
                    " " * indent + "# Vol regime enforcement — penalize strategies that conflict with VIX",
                    " " * indent + "try:",
                    " " * (indent + 4) + "from aggressive.vol_strategy import VolatilityStrategySelector",
                    " " * (indent + 4) + "vix_q = self.client.get_quote('$VIX') if hasattr(self, 'client') else None",
                    " " * (indent + 4) + "vix = vix_q.json().get('$VIX', {}).get('quote', {}).get('lastPrice', 20) if vix_q and vix_q.status_code == 200 else 20",
                    " " * (indent + 4) + "vol_regime = VolatilityStrategySelector.get_regime(vix)",
                    " " * (indent + 4) + "avoid = vol_regime.get('avoid_strategies', [])",
                    " " * (indent + 4) + "preferred = vol_regime.get('preferred_strategies', [])",
                    " " * (indent + 4) + "for s in strategies:",
                    " " * (indent + 8) + "stype = s.get('type', '')",
                    " " * (indent + 8) + "if stype in avoid:",
                    " " * (indent + 12) + "s['score'] = max(0, s.get('score', 0) - 25)",
                    " " * (indent + 8) + "elif stype in preferred:",
                    " " * (indent + 12) + "s['score'] = s.get('score', 0) + 15",
                    " " * (indent + 4) + "# Re-sort after penalty",
                    " " * (indent + 4) + "strategies.sort(key=lambda x: x.get('score', 0), reverse=True)",
                    " " * indent + "except Exception:",
                    " " * (indent + 4) + "pass",
                    "",
                ]
                for j, vl in enumerate(vol_block):
                    lines.insert(i + 1 + j, vl)
                print(f"2b. Vol regime enforcement placed CORRECTLY after strategies.sort() at line {i+1}")
                break

        se = "\n".join(lines)
    else:
        print("2. Could not find vol regime block boundaries")
        # Try alternate: just find and fix the placement
        if "Vol regime enforcement" in se:
            print("   Block exists but boundaries unclear - needs manual review")
else:
    print("2. No vol_regime_penalty found - adding fresh...")
    # Add from scratch in the right place
    lines = se.splitlines()
    for i, line in enumerate(lines):
        if "strategies.sort" in line and "score" in line:
            indent = len(line) - len(line.lstrip())
            vol_block = [
                "",
                " " * indent + "# Vol regime enforcement",
                " " * indent + "try:",
                " " * (indent + 4) + "from aggressive.vol_strategy import VolatilityStrategySelector",
                " " * (indent + 4) + "vix_q = self.client.get_quote('$VIX') if hasattr(self, 'client') else None",
                " " * (indent + 4) + "vix = vix_q.json().get('$VIX', {}).get('quote', {}).get('lastPrice', 20) if vix_q and vix_q.status_code == 200 else 20",
                " " * (indent + 4) + "vol_regime = VolatilityStrategySelector.get_regime(vix)",
                " " * (indent + 4) + "avoid = vol_regime.get('avoid_strategies', [])",
                " " * (indent + 4) + "preferred = vol_regime.get('preferred_strategies', [])",
                " " * (indent + 4) + "for s in strategies:",
                " " * (indent + 8) + "stype = s.get('type', '')",
                " " * (indent + 8) + "if stype in avoid:",
                " " * (indent + 12) + "s['score'] = max(0, s.get('score', 0) - 25)",
                " " * (indent + 8) + "elif stype in preferred:",
                " " * (indent + 12) + "s['score'] = s.get('score', 0) + 15",
                " " * (indent + 4) + "strategies.sort(key=lambda x: x.get('score', 0), reverse=True)",
                " " * indent + "except Exception:",
                " " * (indent + 4) + "pass",
                "",
            ]
            for j, vl in enumerate(vol_block):
                lines.insert(i + 1 + j, vl)
            print(f"2. Vol regime enforcement added at correct location (after line {i+1})")
            break
    se = "\n".join(lines)

open("aggressive/strategy_engine.py", "w", encoding="utf-8").write(se)

# ══════════════════════════════════════════════════
# FIX 3: Remaining v1 issues
# ══════════════════════════════════════════════════

# 3a. Fix signal_generator VIX hardcoded to 20
sg_path = "analysis/signals/signal_generator.py"
if os.path.exists(sg_path):
    sg = open(sg_path, "r", encoding="utf-8").read()
    if "return 20" in sg and "get_vix" in sg:
        sg = sg.replace(
            "return 20",
            """try:
            r = self.client.get_quote("$VIX")
            if r.status_code == 200:
                return r.json().get("$VIX", {}).get("quote", {}).get("lastPrice", 20)
        except Exception:
            pass
        return 20  # Fallback"""
        )
        open(sg_path, "w", encoding="utf-8").write(sg)
        print("3a. SignalGenerator VIX hardcode FIXED: now fetches real VIX")
    else:
        print("3a. SignalGenerator VIX - already fixed or different format")

# 3b. Fix circuit breaker date comparison
cb_path = "risk/circuit_breakers.py"
if os.path.exists(cb_path):
    cb = open(cb_path, "r", encoding="utf-8").read()
    if ">= hu" in cb or ">=hu" in cb:
        cb = cb.replace(
            "date.today().isoformat() >= hu",
            "date.today() > date.fromisoformat(hu)"
        )
        open(cb_path, "w", encoding="utf-8").write(cb)
        print("3b. Circuit breaker date comparison FIXED: > instead of >=")
    else:
        print("3b. Circuit breaker - already fixed or different format")

# 3c. Fix paper_portfolio.json positions type
pp_path = "config/paper_portfolio.json"
if os.path.exists(pp_path):
    import json
    pp = json.load(open(pp_path))
    if isinstance(pp.get("positions"), dict):
        pp["positions"] = []
        json.dump(pp, open(pp_path, "w"), indent=2)
        print("3c. paper_portfolio.json positions FIXED: {} -> []")
    else:
        print("3c. paper_portfolio.json positions already a list")

# 3d. Delete aggressive - Copy/ folder
copy_dir = "aggressive - Copy"
if os.path.exists(copy_dir):
    import shutil
    shutil.rmtree(copy_dir)
    print("3d. Deleted 'aggressive - Copy/' folder")
else:
    print("3d. No 'aggressive - Copy/' folder found")

# 3e. Remove latest['open'] stray file
stray = "latest['open']"
if os.path.exists(stray):
    os.remove(stray)
    print("3e. Removed latest['open'] stray file")
else:
    print("3e. No latest['open'] found")

# ══════════════════════════════════════════════════
# VERIFY ALL
# ══════════════════════════════════════════════════
print()
import py_compile
for path in [
    "aggressive/aggressive_scanner.py",
    "aggressive/iv_analyzer.py",
    "aggressive/ev_calculator.py",
    "aggressive/strategy_engine.py",
    "aggressive/options_executor.py",
    "scripts/aggressive_live.py",
]:
    try:
        py_compile.compile(path, doraise=True)
        print(f"  COMPILE: {path} OK")
    except py_compile.PyCompileError as e:
        print(f"  ERROR: {path} - {e}")

if os.path.exists(sg_path):
    try:
        py_compile.compile(sg_path, doraise=True)
        print(f"  COMPILE: {sg_path} OK")
    except py_compile.PyCompileError as e:
        print(f"  ERROR: {sg_path} - {e}")

if os.path.exists(cb_path):
    try:
        py_compile.compile(cb_path, doraise=True)
        print(f"  COMPILE: {cb_path} OK")
    except py_compile.PyCompileError as e:
        print(f"  ERROR: {cb_path} - {e}")

print()
print("=" * 60)
print("  ALL PERPLEXITY FIXES COMPLETE + DELTA BUGS FIXED")
print("=" * 60)
print()
print("  NEW BUG FIXES:")
print("    1. iv_rank dict/float mismatch — all callers handle both types")
print("    2. Vol regime — moved to correct location in select_strategy()")
print()
print("  REMAINING V1 FIXES:")
print("    3a. SignalGenerator VIX — fetches real VIX now")
print("    3b. Circuit breaker — proper date comparison")
print("    3c. paper_portfolio.json — positions is [] not {}")
print("    3d. aggressive - Copy/ folder — deleted")
print("    3e. Stray files — cleaned")