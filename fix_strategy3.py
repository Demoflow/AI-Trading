path = "aggressive/strategy_engine.py"
lines = open(path, encoding="utf-8").readlines()

# Current state (0-indexed):
# 360: '            # Re-sort after penalty\n'
# 361: "            strategies.sort(key=lambda x: x.get('score', 0), reverse=True)\n"  <- GOOD
# 362: "            strategies.sort(key=lambda x: x.get(chr(39)+score', ...)\n"  <- CORRUPTED
# 363: '            pass\n'  <- this was the except body, now orphaned
# Need to: delete 362, insert 'except Exception:\n' before 363

new_lines = []
i = 0
while i < len(lines):
    # Skip the corrupted line (index 361, 0-based)
    if i == 361 and "chr(39)" in lines[i]:
        print(f"Deleted corrupted line {i+1}: {lines[i].strip()}")
        i += 1
        continue
    # Before 'pass' (now at index 362 after deletion), insert except
    if i == 362 and lines[i].strip() == "pass":
        new_lines.append("        except Exception:\n")
        print(f"Inserted 'except Exception:' before line {i+1}")
    new_lines.append(lines[i])
    i += 1

open(path, "w", encoding="utf-8").write("".join(new_lines))

import py_compile
try:
    py_compile.compile(path, doraise=True)
    print("COMPILE OK - strategy_engine.py is clean")
except py_compile.PyCompileError as e:
    print(f"ERROR: {e}")
    # Show context
    lines2 = open(path, encoding="utf-8").readlines()
    err_line = int(str(e).split("line ")[1].split("\n")[0]) if "line " in str(e) else 0
    if err_line:
        for j, l in enumerate(lines2[max(0,err_line-4):err_line+2], max(1,err_line-3)):
            print(f"  {j}: {repr(l)}")
