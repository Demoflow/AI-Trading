"""Fix 3 scalper issues: session_open, 0DTE expiry filter, ATR check."""

# FIX 1: Add session_open to realtime_data snapshot
rd = open("scalper/realtime_data.py", "r", encoding="utf-8").read()

# Find _build_snapshot and add session_open
if "session_open" not in rd:
    # Find where the snapshot dict is returned
    lines = rd.splitlines()
    for i, line in enumerate(lines):
        if '"price"' in line and "snapshot" in rd[max(0, rd.find(line)-500):rd.find(line)].lower():
            # Find the dict opening
            for j in range(max(0, i-5), i+1):
                if '"price"' in lines[j]:
                    indent = len(lines[j]) - len(lines[j].lstrip())
                    lines.insert(j + 1, " " * indent + '"session_open": candles[0]["open"] if candles else price,')
                    print("1. Added session_open to snapshot")
                    break
            break
    rd = "\n".join(lines)
    open("scalper/realtime_data.py", "w", encoding="utf-8").write(rd)
else:
    print("1. session_open already in snapshot")

# FIX 2: Update ATR check to use session_open
se = open("scalper/signal_engine.py", "r", encoding="utf-8").read()
se = se.replace(
    'day_move = abs(snap.get("price", 0) - snap.get("open", snap.get("price", 0)))',
    'day_move = abs(snap.get("price", 0) - snap.get("session_open", snap.get("price", 0)))'
)
open("scalper/signal_engine.py", "w", encoding="utf-8").write(se)
print("2. ATR check uses session_open")

# FIX 3: pick_iron_condor — filter for 0DTE explicitly
cp = open("scalper/contract_picker.py", "r", encoding="utf-8").read()

cp = cp.replace(
    '            put_exp = next(iter(put_map.values()), {}) if put_map else {}',
    '            put_exp = next((v for k, v in put_map.items() if int(k.split(":")[1]) == 0), {})'
)
cp = cp.replace(
    '            call_exp = next(iter(call_map.values()), {}) if call_map else {}',
    '            call_exp = next((v for k, v in call_map.items() if int(k.split(":")[1]) == 0), {})'
)
open("scalper/contract_picker.py", "w", encoding="utf-8").write(cp)
print("3. pick_iron_condor filters for 0DTE explicitly")

# VERIFY
import py_compile
for p in ["scalper/realtime_data.py", "scalper/signal_engine.py", "scalper/contract_picker.py"]:
    try:
        py_compile.compile(p, doraise=True)
        print(f"  COMPILE: {p} OK")
    except py_compile.PyCompileError as e:
        print(f"  ERROR: {p} - {e}")