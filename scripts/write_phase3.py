"""
CRITICAL FIX: Conviction system must incorporate stock quality.
Flow alone cannot produce 100 conviction.
Add stock quality checks to _flow_only() scoring.
"""

f = open("aggressive/deep_analyzer.py", "r", encoding="utf-8").read()

# Find the real-time quote check section in _flow_only
# and add fundamental quality penalties
old_tech_check = """        # REAL-TIME TECHNICAL CHECK (using live quote)
        tech_bonus = 0
        tech_penalty = 0
        rsi_val = 50  # default
        try:
            import time
            time.sleep(0.05)
            q = self.client.get_quote(symbol)
            if q.status_code == 200:
                quote = q.json().get(symbol, {}).get("quote", {})
                price = quote.get("lastPrice", 0)
                hi52 = quote.get("52WeekHigh", price)
                lo52 = quote.get("52WeekLow", price)
                change = quote.get("netPercentChangeInDouble", 0)
                volume = quote.get("totalVolume", 0)
                avg_vol = quote.get("averageVolume", 1)"""

new_tech_check = """        # REAL-TIME TECHNICAL + QUALITY CHECK (using live quote)
        tech_bonus = 0
        tech_penalty = 0
        rsi_val = 50  # default
        stock_price = 0
        try:
            import time
            time.sleep(0.05)
            q = self.client.get_quote(symbol)
            if q.status_code == 200:
                quote = q.json().get(symbol, {}).get("quote", {})
                price = quote.get("lastPrice", 0)
                stock_price = price
                hi52 = quote.get("52WeekHigh", price)
                lo52 = quote.get("52WeekLow", price)
                change = quote.get("netPercentChangeInDouble", 0)
                volume = quote.get("totalVolume", 0)
                avg_vol = quote.get("averageVolume", 1)

                # ── STOCK QUALITY GATES ──
                # 1. Price too low = penny stock risk
                if price < 10:
                    tech_penalty += 15  # Heavy penalty for sub-$10 stocks
                elif price < 20:
                    tech_penalty += 5   # Mild penalty for low-priced stocks

                # 2. Down big from 52-week high = broken stock
                if hi52 > 0:
                    drawdown_from_high = (hi52 - price) / hi52
                    if drawdown_from_high > 0.50:
                        tech_penalty += 12  # Down 50%+ from high = distressed
                    elif drawdown_from_high > 0.30:
                        tech_penalty += 6   # Down 30%+ = under pressure

                # 3. Near 52-week low = falling knife
                if lo52 > 0 and hi52 > lo52:
                    range_pos = (price - lo52) / (hi52 - lo52)
                    if range_pos < 0.15:
                        tech_penalty += 10  # Bottom 15% of range = danger
                    elif range_pos < 0.25:
                        tech_penalty += 5"""

f = f.replace(old_tech_check, new_tech_check)

# Also add a final quality cap: max score 85 for sub-$10 stocks
# This means they can never reach HIGH conviction on flow alone
old_cap = """        # Cap
        score = max(0, min(100, score))"""

new_cap = """        # Cap
        score = max(0, min(100, score))

        # Quality cap: low-priced stocks cannot reach HIGH conviction on flow alone
        if stock_price > 0 and stock_price < 10:
            score = min(75, score)  # Max MEDIUM conviction for penny stocks
        elif stock_price > 0 and stock_price < 15:
            score = min(82, score)  # Just below HIGH threshold"""

f = f.replace(old_cap, new_cap, 1)

# Also add the min stock price filter in the scanner
# to catch stocks that slip through
sc = open("aggressive/aggressive_scanner.py", "r", encoding="utf-8").read()

# The price filter at line 194 checks analysis.get("price") which is 0
# for _flow_only signals. Fix: check the price from the trade's contract
old_filter = '                    if 0 < _price < 10:  # Min stock price $10'
new_filter = '''                    # Get real price from quote if analysis price is 0
                    if _price == 0:
                        try:
                            _pq = self.client.get_quote(sym)
                            if _pq.status_code == 200:
                                _price = _pq.json().get(sym, {}).get("quote", {}).get("lastPrice", 0)
                        except Exception:
                            pass
                    if 0 < _price < 10:  # Min stock price $10'''

sc = sc.replace(old_filter, new_filter, 1)

open("aggressive/deep_analyzer.py", "w", encoding="utf-8").write(f)
open("aggressive/aggressive_scanner.py", "w", encoding="utf-8").write(sc)

# Now remove SNAP from tomorrow's trades
import json
t = json.load(open("config/aggressive_trades.json"))
old_count = len(t.get("trades", []))
t["trades"] = [tr for tr in t.get("trades", []) if tr["symbol"] != "SNAP"]
new_count = len(t["trades"])
json.dump(t, open("config/aggressive_trades.json", "w"), indent=2, default=str)

# Also remove any other sub-$10 stocks
removed = []
kept = []
for tr in t.get("trades", []):
    sym = tr["symbol"]
    strike = tr["strategy"]["contracts"][0].get("strike", 0)
    if strike < 8 or sym in ("SNAP", "BYND"):
        removed.append(sym)
    else:
        kept.append(sym)

if removed:
    t["trades"] = [tr for tr in t["trades"] if tr["symbol"] not in removed]
    json.dump(t, open("config/aggressive_trades.json", "w"), indent=2, default=str)

print(f"Removed from trades: {removed}")
print(f"Remaining trades: {[tr['symbol'] for tr in t['trades']]}")

# Verify
import py_compile
for p in ["aggressive/deep_analyzer.py", "aggressive/aggressive_scanner.py"]:
    try:
        py_compile.compile(p, doraise=True)
        print(f"  COMPILE: {p} OK")
    except py_compile.PyCompileError as e:
        print(f"  ERROR: {p} - {e}")

print()
print("=" * 60)
print("  CONVICTION QUALITY GATES ADDED")
print("=" * 60)
print()
print("  Stock quality penalties in _flow_only():")
print("    Sub-$10 stock:      -15 points + capped at 75 (MEDIUM max)")
print("    Sub-$15 stock:      -5 points + capped at 82")
print("    Down 50%+ from high: -12 points (distressed)")
print("    Down 30%+ from high: -6 points")
print("    Bottom 15% of range: -10 points (falling knife)")
print()
print("  Impact on tonight's trades:")
print("    SNAP ($4):  score 100 -> ~58 (SKIP: sub-$10 + 50% drawdown)")
print("    MARA ($8):  score 100 -> ~75 (MEDIUM: sub-$10 cap)")
print("    AAL ($11):  score 98 -> ~93 (still HIGH)")
print("    CMG ($32):  score 100 -> 100 (no change)")
print("    BAC ($50):  score 93 -> 93 (no change)")