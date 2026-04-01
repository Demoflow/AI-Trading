# Fix strategy_engine.py - move sort line inside try block
path = "aggressive/strategy_engine.py"
lines = open(path, encoding="utf-8").readlines()

# Find and fix line 362 (index 361) - needs 12 spaces not 8
for i, line in enumerate(lines):
    if "strategies.sort(key=lambda x: x.get('score', 0), reverse=True)" in line and line.startswith("        s"):
        lines[i] = "            strategies.sort(key=lambda x: x.get('score', 0), reverse=True)\n"
        print(f"Fixed line {i+1}: added correct indentation")
        break

open(path, "w", encoding="utf-8").write("".join(lines))

import py_compile
try:
    py_compile.compile(path, doraise=True)
    print("COMPILE OK")
except py_compile.PyCompileError as e:
    print(f"COMPILE ERROR: {e}")
