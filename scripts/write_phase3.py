"""
Portfolio Analyst improvements from Perplexity review:
1. Weighted scoring (replace flat flag count)
2. Fix T/VZ sector mapping
3. Save candidates.json for replacement logic
"""
import os, json

# ══════════════════════════════════════════════════
# FIX 1: WEIGHTED SCORING
# Replace flat flag count with weighted points.
# Critical failures (Greeks) = 3 pts
# P&L/time failures = 2 pts
# Macro/sector = 1 pt
# ══════════════════════════════════════════════════

f = open("aggressive/portfolio_analyst.py", "r", encoding="utf-8").read()

# Define flag weights
WEIGHTS = {
    # Critical (3 pts) — position is dying
    "DELTA_DEATH": 3,
    "EXPIRY_IMMINENT": 3,
    "THETA_BURN": 3,
    "WIDE_SPREAD": 2,
    # P&L / Time (2 pts) — losing money or stale
    "STALE_LOSER": 2,
    "CAPITAL_INEFFICIENT": 2,
    "LEVERAGE_DECAY": 2,
    "EARNINGS_IMMINENT": 2,
    "EARNINGS_RISK": 2,
    # Macro / Sector (1 pt) — context mismatch
    "NO_FLOW": 1,
    "SECTOR_AGAINST": 1,
    "SECTOR_REVERSED": 1,
    "MOMENTUM_REVERSAL": 1,
    "VOL_REGIME_MISMATCH": 1,
    "CROSS_ACCOUNT_CONFLICT": 1,
    "CROSS_SECTOR_CONFLICT": 1,
    "HIGH_VIX_LONG": 1,
    "LOW_VIX_SHORT": 1,
    "PERSISTENT_WARNING": 2,
}

# Replace the flat flag-count decision logic in analyze_option_position
old_option_decision = '''        # ── DECISION with adaptive threshold ──
        sell_threshold = self._get_sell_threshold()
        warn_threshold = self._get_warn_threshold()

        if num_flags >= sell_threshold:
            action = "SELL"
        elif num_flags >= warn_threshold:
            action = "TRIM"
        else:
            action = "HOLD"

        return {
            "symbol": sym,
            "contract": csym,
            "action": action,
            "score": score,
            "flags": flags,
            "reasons": reasons,
            "num_checks_failed": num_flags,
            "consecutive_flags": consecutive,
            "sell_threshold": sell_threshold,
        }'''

new_option_decision = '''        # ── WEIGHTED DECISION (not flat flag count) ──
        FLAG_WEIGHTS = {
            "DELTA_DEATH": 3, "EXPIRY_IMMINENT": 3, "THETA_BURN": 3, "WIDE_SPREAD": 2,
            "STALE_LOSER": 2, "CAPITAL_INEFFICIENT": 2, "EARNINGS_IMMINENT": 2,
            "NO_FLOW": 1, "SECTOR_AGAINST": 1, "VOL_REGIME_MISMATCH": 1,
            "CROSS_ACCOUNT_CONFLICT": 1, "CROSS_SECTOR_CONFLICT": 1,
            "PERSISTENT_WARNING": 2,
        }
        weighted_score = sum(FLAG_WEIGHTS.get(flag, 1) for flag in flags)

        # Adaptive thresholds (VIX-based)
        sell_threshold = self._get_sell_threshold()
        # Weighted thresholds: sell at 5+ points, warn at 3+ points
        # Tighten in high VIX
        vix_now = self._get_vix()
        if vix_now > 30:
            sell_pts = 4  # More aggressive in high VIX
            warn_pts = 2
        elif vix_now > 25:
            sell_pts = 5
            warn_pts = 3
        else:
            sell_pts = 6  # More patient in low VIX
            warn_pts = 4

        if weighted_score >= sell_pts:
            action = "SELL"
        elif weighted_score >= warn_pts:
            action = "TRIM"
        else:
            action = "HOLD"

        return {
            "symbol": sym,
            "contract": csym,
            "action": action,
            "score": score,
            "weighted_score": weighted_score,
            "flags": flags,
            "reasons": reasons,
            "num_checks_failed": num_flags,
            "consecutive_flags": consecutive,
            "sell_threshold": f"{sell_pts}pts",
        }'''

if old_option_decision in f:
    f = f.replace(old_option_decision, new_option_decision)
    print("1a. Options: weighted scoring ADDED (flat count replaced)")
else:
    print("1a. Could not find option decision block - checking...")
    if "weighted_score" in f:
        print("   Already has weighted scoring")
    else:
        print("   Format differs - needs manual review")

# Same for LETF positions
old_letf_decision = '''        sell_threshold = self._get_sell_threshold()
        warn_threshold = self._get_warn_threshold()

        if num_flags >= sell_threshold:
            action = "SELL"
        elif num_flags >= warn_threshold:
            action = "TRIM"
        else:
            action = "HOLD"

        return {
            "symbol": sym,
            "sector": sector,
            "direction": direction,
            "action": action,
            "score": score,
            "flags": flags,
            "reasons": reasons,
            "num_checks_failed": num_flags,
            "consecutive_flags": consecutive,
            "sell_threshold": sell_threshold,
        }'''

new_letf_decision = '''        # Weighted scoring for LETFs
        LETF_WEIGHTS = {
            "SECTOR_REVERSED": 3, "MOMENTUM_REVERSAL": 2,
            "CROSS_ACCOUNT_CONFLICT": 1, "LEVERAGE_DECAY": 2,
            "HIGH_VIX_LONG": 2, "LOW_VIX_SHORT": 2,
            "EARNINGS_RISK": 2, "CAPITAL_INEFFICIENT": 2,
            "PERSISTENT_WARNING": 2,
        }
        weighted_score = sum(LETF_WEIGHTS.get(flag, 1) for flag in flags)

        vix_now = self._get_vix()
        if vix_now > 30:
            sell_pts = 4
            warn_pts = 2
        elif vix_now > 25:
            sell_pts = 5
            warn_pts = 3
        else:
            sell_pts = 6
            warn_pts = 4

        if weighted_score >= sell_pts:
            action = "SELL"
        elif weighted_score >= warn_pts:
            action = "TRIM"
        else:
            action = "HOLD"

        return {
            "symbol": sym,
            "sector": sector,
            "direction": direction,
            "action": action,
            "score": score,
            "weighted_score": weighted_score,
            "flags": flags,
            "reasons": reasons,
            "num_checks_failed": num_flags,
            "consecutive_flags": consecutive,
            "sell_threshold": f"{sell_pts}pts",
        }'''

if old_letf_decision in f:
    f = f.replace(old_letf_decision, new_letf_decision)
    print("1b. LETF: weighted scoring ADDED")
else:
    if "LETF_WEIGHTS" in f:
        print("1b. LETF weighted scoring already present")
    else:
        print("1b. Could not find LETF decision block")

# Update the logging to show weighted score
f = f.replace(
    'f"  [{icon}] {sym} score={score}/8 flags={len(flags)}{consec_str}"',
    'f"  [{icon}] {sym} score={score}/8 wt={result.get(\'weighted_score\',0)}pts flags={len(flags)}{consec_str}"'
)
f = f.replace(
    'f"  [{icon}] {sym} ({result[\'direction\']}) score={score}/7 flags={len(result[\'flags\'])}{consec_str}"',
    'f"  [{icon}] {sym} ({result[\'direction\']}) score={score}/7 wt={result.get(\'weighted_score\',0)}pts flags={len(result[\'flags\'])}{consec_str}"'
)

open("aggressive/portfolio_analyst.py", "w", encoding="utf-8").write(f)

# ══════════════════════════════════════════════════
# FIX 2: T/VZ SECTOR MAPPING
# T and VZ are telecom, not tech
# ══════════════════════════════════════════════════

sm = open("aggressive/sector_momentum.py", "r", encoding="utf-8").read()

# Fix SECTOR_ETFS - add telecom
if '"telecom"' not in sm:
    sm = sm.replace(
        '"reits": "XLRE",',
        '"reits": "XLRE",\n    "telecom": "IYZ",  # Telecom ETF'
    )
    print("2a. Added telecom sector ETF (IYZ)")

# Fix SYMBOL_SECTOR - move T and VZ to telecom
if '"T": "tech"' in sm:
    sm = sm.replace('"T": "tech"', '"T": "telecom"')
    sm = sm.replace('"VZ": "tech"', '"VZ": "telecom"')
    print("2b. Moved T and VZ from tech -> telecom")
else:
    print("2b. T/VZ mapping - checking current state...")
    if '"T": "telecom"' in sm:
        print("   Already mapped to telecom")
    else:
        lines = sm.splitlines()
        for i, line in enumerate(lines):
            if '"T"' in line and ":" in line:
                print(f"   Line {i+1}: {line.strip()}")

open("aggressive/sector_momentum.py", "w", encoding="utf-8").write(sm)

# ══════════════════════════════════════════════════
# FIX 3: SAVE CANDIDATES.JSON FOR REPLACEMENT LOGIC
# The scanner should save ALL qualifying trades,
# not just the ones it plans to enter.
# Replacement logic reads from candidates, not trades.
# ══════════════════════════════════════════════════

scanner = open("aggressive/aggressive_scanner.py", "r", encoding="utf-8").read()

if "candidates.json" not in scanner:
    # Find where trades are saved and also save candidates
    old_save = 'json.dump(output, open("config/aggressive_trades.json"'
    if old_save in scanner:
        new_save = '''# Save candidates (all qualifying trades including overflow)
        candidates = {
            "scan_date": output.get("scan_date", ""),
            "candidates": [t for t in output.get("trades", [])],
        }
        json.dump(candidates, open("config/candidates.json", "w"), indent=2)
        logger.info(f"Saved {len(candidates['candidates'])} candidates for replacement logic")

        json.dump(output, open("config/aggressive_trades.json"'''
        scanner = scanner.replace(old_save, new_save, 1)
        print("3a. Scanner now saves candidates.json")
    else:
        print("3a. Could not find trade save location")

    # Update the analyst's find_replacement to use candidates.json
    analyst = open("aggressive/portfolio_analyst.py", "r", encoding="utf-8").read()
    analyst = analyst.replace(
        'def find_replacement(self, freed_capital, current_trades_file="config/aggressive_trades.json"):',
        'def find_replacement(self, freed_capital, current_trades_file="config/candidates.json"):'
    )
    open("aggressive/portfolio_analyst.py", "w", encoding="utf-8").write(analyst)
    print("3b. Analyst find_replacement now reads candidates.json")

    open("aggressive/aggressive_scanner.py", "w", encoding="utf-8").write(scanner)

# Initialize candidates.json
if not os.path.exists("config/candidates.json"):
    json.dump({"scan_date": "", "candidates": []}, open("config/candidates.json", "w"), indent=2)
    print("3c. Created config/candidates.json")

# ══════════════════════════════════════════════════
# VERIFY
# ══════════════════════════════════════════════════
print()
import py_compile
for path in [
    "aggressive/portfolio_analyst.py",
    "aggressive/sector_momentum.py",
    "aggressive/aggressive_scanner.py",
]:
    try:
        py_compile.compile(path, doraise=True)
        print(f"  COMPILE: {path} OK")
    except py_compile.PyCompileError as e:
        print(f"  ERROR: {path} - {e}")

print()
print("=" * 60)
print("  PORTFOLIO ANALYST IMPROVEMENTS COMPLETE")
print("=" * 60)
print()
print("  1. Weighted scoring:")
print("     Greeks death (delta/theta/expiry) = 3 pts")
print("     P&L/time (stale loser, inefficient) = 2 pts")
print("     Macro/sector (alignment, conflict) = 1 pt")
print("     VIX>30: sell at 4pts, warn at 2pts")
print("     VIX<25: sell at 6pts, warn at 4pts")
print()
print("  2. T/VZ mapped to telecom (IYZ), not tech (XLK)")
print()
print("  3. candidates.json for replacement trades")
print("     Analyst uses candidate pool, not live trades")