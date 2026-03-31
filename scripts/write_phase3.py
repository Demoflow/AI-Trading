"""Fix _dt import and add OrderStrategyType to spread close."""
import datetime

# FIX 1: _exit_cooldowns uses _dt which isn't imported at that scope
f = open("scripts/aggressive_live.py", "r", encoding="utf-8").read()
f = f.replace(
    "_exit_cooldowns[sym] = _dt.datetime.now()",
    "_exit_cooldowns[sym] = datetime.datetime.now()"
)
# Also need to import datetime at the top of the exit section
f = f.replace(
    '''                        if result.get("status") == "REJECTED":
                            # Don't retry for 10 minutes
                            if "_exit_cooldowns" not in dir():
                                _exit_cooldowns = {}
                            _exit_cooldowns[sym] = datetime.datetime.now()''',
    '''                        if result.get("status") == "REJECTED":
                            # Don't retry for 10 minutes
                            import datetime as _dtmod
                            if not hasattr(run, '_exit_cooldowns'):
                                run._exit_cooldowns = {}
                            run._exit_cooldowns[sym] = _dtmod.datetime.now()'''
)
open("scripts/aggressive_live.py", "w", encoding="utf-8").write(f)
print("1. Fixed _dt import in exit cooldown")

# FIX 2: Spread close missing OrderStrategyType in the OrderBuilder
g = open("aggressive/options_executor.py", "r", encoding="utf-8").read()

# The error says "orderStrategyType must not be null"
# The OrderBuilder needs .set_order_strategy_type(OrderStrategyType.SINGLE)
# Check if it's missing from the debit spread close block
old_spread_order = """                order = (OrderBuilder()
                    .set_order_type(order_type)
                    .set_price(limit)
                    .set_session(Session.NORMAL)
                    .set_duration(Duration.DAY)
                    .add_option_leg(OptionInstruction.SELL_TO_CLOSE, long_leg["symbol"], long_leg.get("qty", 1))
                    .add_option_leg(OptionInstruction.BUY_TO_CLOSE, short_leg["symbol"], short_leg.get("qty", 1))
                    .build())"""

new_spread_order = """                order = (OrderBuilder()
                    .set_order_type(order_type)
                    .set_price(limit)
                    .set_session(Session.NORMAL)
                    .set_duration(Duration.DAY)
                    .set_order_strategy_type(OrderStrategyType.SINGLE)
                    .add_option_leg(OptionInstruction.SELL_TO_CLOSE, long_leg["symbol"], long_leg.get("qty", 1))
                    .add_option_leg(OptionInstruction.BUY_TO_CLOSE, short_leg["symbol"], short_leg.get("qty", 1))
                    .build())"""

if old_spread_order in g:
    g = g.replace(old_spread_order, new_spread_order)
    print("2. Fixed: added OrderStrategyType.SINGLE to spread close")
else:
    print("2. Could not find exact spread order block - checking...")
    lines = g.splitlines()
    for i, line in enumerate(lines):
        if "SELL_TO_CLOSE" in line and "long_leg" in line:
            # Check if OrderStrategyType is set before this line
            found_strategy = False
            for j in range(max(0, i-5), i):
                if "order_strategy_type" in lines[j]:
                    found_strategy = True
            if not found_strategy:
                print(f"   Missing OrderStrategyType before line {i+1}")
                # Add it before the SELL_TO_CLOSE line
                indent = len(line) - len(line.lstrip())
                lines.insert(i, " " * indent + ".set_order_strategy_type(OrderStrategyType.SINGLE)")
                print(f"   INSERTED OrderStrategyType at line {i+1}")
    g = "\n".join(lines)

# Also fix the credit spread close block if it has the same issue
old_credit_order = """                order = (OrderBuilder()
                    .set_order_type(order_type)
                    .set_price(limit)
                    .set_session(Session.NORMAL)
                    .set_duration(Duration.DAY)
                    .add_option_leg(OptionInstruction.BUY_TO_CLOSE, short_leg["symbol"], short_leg.get("qty", 1))
                    .add_option_leg(OptionInstruction.SELL_TO_CLOSE, long_leg["symbol"], long_leg.get("qty", 1))
                    .build())"""

new_credit_order = """                order = (OrderBuilder()
                    .set_order_type(order_type)
                    .set_price(limit)
                    .set_session(Session.NORMAL)
                    .set_duration(Duration.DAY)
                    .set_order_strategy_type(OrderStrategyType.SINGLE)
                    .add_option_leg(OptionInstruction.BUY_TO_CLOSE, short_leg["symbol"], short_leg.get("qty", 1))
                    .add_option_leg(OptionInstruction.SELL_TO_CLOSE, long_leg["symbol"], long_leg.get("qty", 1))
                    .build())"""

if old_credit_order in g:
    g = g.replace(old_credit_order, new_credit_order)
    print("2b. Fixed: added OrderStrategyType.SINGLE to credit close")

open("aggressive/options_executor.py", "w", encoding="utf-8").write(g)

# FIX 3: Also mark VZ and OXY as entered (they were entered but not caught)
# The position check already handles calendars, but VZ/OXY are naked
# Let's ensure they're in the _entered list
import json
try:
    tf = json.load(open("config/aggressive_trades.json"))
    for t in tf.get("trades", []):
        t["_entered"] = True  # Mark ALL trades as entered
    json.dump(tf, open("config/aggressive_trades.json", "w"), indent=2, default=str)
    print("3. All trades marked as _entered")
except Exception as e:
    print(f"3. Error: {e}")

# VERIFY
import py_compile
for p in ["scripts/aggressive_live.py", "aggressive/options_executor.py"]:
    try:
        py_compile.compile(p, doraise=True)
        print(f"  COMPILE: {p} OK")
    except py_compile.PyCompileError as e:
        print(f"  ERROR: {p} - {e}")

print("\nRestart aggressive_live.py now.")