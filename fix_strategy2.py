path = "aggressive/strategy_engine.py"
lines = open(path, encoding="utf-8").readlines()

# Find the corrupted/misindented sort line and replace it completely
fixed = False
for i, line in enumerate(lines):
    if "strategies.sort" in line and ("x.get(" in line or "chr(39)" in line) and not line.startswith("            "):
        lines[i] = "            strategies.sort(key=lambda x: x.get('score', 0), reverse=True)\n"
        print(f"Fixed line {i+1}")
        fixed = True
        break

if not fixed:
    print("Line not found - showing lines 358-368:")
    for i, l in enumerate(lines[357:368], 358):
        print(f"  {i}: {repr(l)}")
else:
    open(path, "w", encoding="utf-8").write("".join(lines))
    import py_compile
    try:
        py_compile.compile(path, doraise=True)
        print("COMPILE OK")
    except py_compile.PyCompileError as e:
        print(f"ERROR: {e}")
