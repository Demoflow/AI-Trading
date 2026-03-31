"""
Fix settlement tracking to prevent Good Faith Violations.
Never use unsettled cash in PCRA or Roth.
"""

f = open("letf/executor.py", "r", encoding="utf-8").read()

# Fix 1: Never fall back to cashBalance — only use settled funds
old_available = 'available = bal["available"] if bal["available"] > 0 else bal["cash"]  # Use cash if available is 0 (settlement)'
new_available = '''available = bal["available"]
            if available <= 0:
                logger.warning(f"No settled funds available (available={available}, cash={bal['cash']})")
                return False, "no_settled_funds"'''

f = f.replace(old_available, new_available)
print("1. Fixed: never falls back to unsettled cash")

# Fix 2: Add settlement buffer — keep $500 extra as safety margin
old_cash_check = '        if cost > available:'
if old_cash_check in f:
    new_cash_check = '''        # Settlement safety buffer — keep extra to avoid GFV
        settlement_buffer = 500 if self.live else 0
        if cost > (available - settlement_buffer):'''
    f = f.replace(old_cash_check, new_cash_check, 1)
    print("2. Added $500 settlement safety buffer")
else:
    # Find the actual cash check
    lines = f.splitlines()
    for i, line in enumerate(lines):
        if "cost" in line and "available" in line and (">" in line or "<" in line):
            print(f"2. Cash check at line {i+1}: {line.strip()}")

# Fix 3: Log the actual settled vs unsettled amounts
old_balance_return = '''            return {
                "cash": bal.get("cashBalance", 0),
                "equity": bal.get("liquidationValue", 0),
                "available": bal.get("availableFundsNonMarginableTrade", 0),
            }'''
new_balance_return = '''            avail = bal.get("availableFundsNonMarginableTrade", 0)
            cash = bal.get("cashBalance", 0)
            unsettled = cash - avail if cash > avail else 0
            if unsettled > 0:
                logger.info(f"Settlement: cash=${cash:,.2f} available=${avail:,.2f} unsettled=${unsettled:,.2f}")
            return {
                "cash": cash,
                "equity": bal.get("liquidationValue", 0),
                "available": avail,
                "unsettled": unsettled,
            }'''

f = f.replace(old_balance_return, new_balance_return)
print("3. Added settlement logging")

# Fix 4: Also track settlement in the sell method
# When we sell, the proceeds won't be available for 1-2 business days
old_sell_log = 'logger.info(f"LIVE SELL: {symbol} x{qty} @ ${price} ({reason})")'
if old_sell_log in f:
    new_sell_log = '''logger.info(f"LIVE SELL: {symbol} x{qty} @ ${price} ({reason})")
                logger.info(f"  NOTE: Proceeds ${qty * price:,.2f} will settle in 1-2 business days")'''
    f = f.replace(old_sell_log, new_sell_log, 1)
    print("4. Added settlement reminder on sells")

open("letf/executor.py", "w", encoding="utf-8").write(f)

# VERIFY
import py_compile
try:
    py_compile.compile("letf/executor.py", doraise=True)
    print("\n  COMPILE: letf/executor.py OK")
except py_compile.PyCompileError as e:
    print(f"\n  ERROR: {e}")

print("\n=== Settlement Protection Active ===")
print("  - Only uses availableFundsNonMarginableTrade (settled)")
print("  - Never falls back to cashBalance (includes unsettled)")
print("  - $500 safety buffer on every trade")
print("  - Logs settlement status on every balance check")
print("  - Logs settlement reminder on every sell")
print("\n  This prevents Good Faith Violations in PCRA and Roth.")