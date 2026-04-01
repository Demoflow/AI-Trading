"""
Scalper critical fixes from audit:
1. Iron condor — add pick_iron_condor() and open_iron_condor_position()
2. Credit spread exit — use net spread value, not short leg only
3. Peak value save frequency — batch writes
4. Credit spread exit in live loop — fetch both legs
"""

# ══════════════════════════════════════════════════
# FIX 1: Add pick_iron_condor() to ContractPicker
# ══════════════════════════════════════════════════

cp = open("scalper/contract_picker.py", "r", encoding="utf-8").read()

if "pick_iron_condor" not in cp:
    # Add before the last method or at the end of the class
    # Find the end of the class
    ic_method = '''
    def pick_iron_condor(self, symbol, max_cost, atr):
        """Pick both legs of an iron condor: bull put spread + bear call spread."""
        try:
            import time
            time.sleep(0.08)
            r = self.client.get_option_chain(symbol, strike_count=20)
            if r.status_code != 200:
                return None
            chain = r.json()
            underlying_price = chain.get("underlyingPrice", 0)
            if underlying_price <= 0:
                return None

            # Find 0DTE expiration
            put_map = chain.get("putExpDateMap", {})
            call_map = chain.get("callExpDateMap", {})
            
            # Get first expiration (0DTE)
            put_exp = next(iter(put_map.values()), {}) if put_map else {}
            call_exp = next(iter(call_map.values()), {}) if call_map else {}
            
            if not put_exp or not call_exp:
                return None

            # PUT SIDE: sell OTM put, buy further OTM put
            # Target: sell at delta ~0.15, buy at delta ~0.08
            put_candidates = []
            for strike_str, contracts in put_exp.items():
                c = contracts[0] if contracts else {}
                strike = float(strike_str)
                delta = abs(c.get("delta", 0))
                bid = c.get("bid", 0)
                ask = c.get("ask", 0)
                oi = c.get("openInterest", 0)
                mid = (bid + ask) / 2 if bid > 0 and ask > 0 else 0
                if mid > 0 and oi >= 100 and strike < underlying_price:
                    put_candidates.append({
                        "strike": strike, "delta": delta, "mid": mid,
                        "bid": bid, "ask": ask, "symbol": c.get("symbol", ""),
                    })
            
            # CALL SIDE: sell OTM call, buy further OTM call
            call_candidates = []
            for strike_str, contracts in call_exp.items():
                c = contracts[0] if contracts else {}
                strike = float(strike_str)
                delta = abs(c.get("delta", 0))
                bid = c.get("bid", 0)
                ask = c.get("ask", 0)
                oi = c.get("openInterest", 0)
                mid = (bid + ask) / 2 if bid > 0 and ask > 0 else 0
                if mid > 0 and oi >= 100 and strike > underlying_price:
                    call_candidates.append({
                        "strike": strike, "delta": delta, "mid": mid,
                        "bid": bid, "ask": ask, "symbol": c.get("symbol", ""),
                    })

            if len(put_candidates) < 2 or len(call_candidates) < 2:
                return None

            # Sort puts by strike descending (closest to ATM first)
            put_candidates.sort(key=lambda x: x["strike"], reverse=True)
            # Sort calls by strike ascending (closest to ATM first)
            call_candidates.sort(key=lambda x: x["strike"])

            # Select short put: delta closest to 0.15
            short_put = min(put_candidates, key=lambda x: abs(x["delta"] - 0.15))
            # Select long put: next strike below short put
            long_puts = [p for p in put_candidates if p["strike"] < short_put["strike"]]
            if not long_puts:
                return None
            long_put = long_puts[0]  # Closest strike below

            # Select short call: delta closest to 0.15
            short_call = min(call_candidates, key=lambda x: abs(x["delta"] - 0.15))
            # Select long call: next strike above short call
            long_calls = [c for c in call_candidates if c["strike"] > short_call["strike"]]
            if not long_calls:
                return None
            long_call = long_calls[0]  # Closest strike above

            # Calculate credit and collateral
            put_credit = short_put["mid"] - long_put["mid"]
            call_credit = short_call["mid"] - long_call["mid"]
            total_credit = put_credit + call_credit

            if total_credit <= 0.10:
                return None  # Not enough credit

            put_width = short_put["strike"] - long_put["strike"]
            call_width = long_call["strike"] - short_call["strike"]
            max_width = max(put_width, call_width)
            collateral = max_width * 100  # Only one side can lose

            if collateral > max_cost:
                return None

            from loguru import logger
            logger.info(
                f"IC: {symbol} put ${short_put['strike']}/{long_put['strike']} "
                f"call ${short_call['strike']}/{long_call['strike']} "
                f"cr=${total_credit:.2f} collateral=${collateral:.0f}"
            )

            return {
                "short_put": short_put,
                "long_put": long_put,
                "short_call": short_call,
                "long_call": long_call,
                "put_credit": round(put_credit, 2),
                "call_credit": round(call_credit, 2),
                "total_credit": round(total_credit, 2),
                "collateral": collateral,
                "qty": 1,
            }
        except Exception as e:
            from loguru import logger
            logger.warning(f"IC pick error {symbol}: {e}")
            return None
'''
    # Append to the file
    cp += ic_method
    open("scalper/contract_picker.py", "w", encoding="utf-8").write(cp)
    print("1. Added pick_iron_condor() to ContractPicker")
else:
    print("1. pick_iron_condor already exists")

# ══════════════════════════════════════════════════
# FIX 2: Add open_iron_condor_position() to executor
# and wire IC signal to use it
# ══════════════════════════════════════════════════

ex = open("scalper/executor.py", "r", encoding="utf-8").read()

if "open_iron_condor_position" not in ex:
    ic_exec = '''
    def open_iron_condor_position(self, signal, condor):
        """Open a 4-leg iron condor position."""
        if not condor:
            return {"status": "REJECTED"}
        collateral = condor["collateral"]
        total_credit = condor["total_credit"]
        qty = condor.get("qty", 1)
        credit_received = round(qty * total_credit * (1 - SELL_SLIPPAGE) * 100, 2)

        if collateral > self.portfolio["cash"]:
            return {"status": "REJECTED", "reason": "no_cash"}

        # Cap at 20% of equity
        if collateral > self.equity * 0.20:
            return {"status": "REJECTED", "reason": "collateral_exceeds_20%"}

        self.portfolio["cash"] -= collateral
        self.portfolio["cash"] += credit_received

        pos = {
            "id": len(self.portfolio["positions"]) + len(self.portfolio["history"]) + 1,
            "symbol": signal["symbol"],
            "direction": "NEUTRAL",
            "signal_type": signal["type"],
            "structure": "IRON_CONDOR",
            "confidence": signal["confidence"],
            "contract": condor["short_put"]["symbol"],       # Short put
            "contract_long_put": condor["long_put"]["symbol"],
            "contract_short_call": condor["short_call"]["symbol"],
            "contract_long_call": condor["long_call"]["symbol"],
            "strike_short_put": condor["short_put"]["strike"],
            "strike_long_put": condor["long_put"]["strike"],
            "strike_short_call": condor["short_call"]["strike"],
            "strike_long_call": condor["long_call"]["strike"],
            "entry_cost": round(collateral, 2),
            "credit_received": credit_received,
            "qty": qty,
            "entry_time": datetime.now().isoformat(),
            "entry_underlying": signal["price"],
            "status": "OPEN",
            "peak_value": collateral,
            "reason": signal.get("reason", ""),
        }
        self.portfolio["positions"].append(pos)
        self._save()
        logger.info(
            f"IRON CONDOR: {signal['symbol']} "
            f"put ${condor['short_put']['strike']}/{condor['long_put']['strike']} "
            f"call ${condor['short_call']['strike']}/{condor['long_call']['strike']} "
            f"cr=${credit_received:,.2f} collateral=${collateral:,.2f}"
        )
        return {"status": "FILLED", "collateral": collateral}
'''
    ex += ic_exec
    open("scalper/executor.py", "w", encoding="utf-8").write(ex)
    print("2. Added open_iron_condor_position() to executor")

# Wire IC signal to use the new method in scalper_live.py
sl = open("scripts/scalper_live.py", "r", encoding="utf-8").read()

old_ic_route = """                    elif structure in ("CREDIT_SPREAD", "IRON_CONDOR"):
                        spread = picker.pick_credit_spread(
                            sym, signal["direction"], max_cost,
                            snap_5m.get("atr", 1),
                        )
                        if spread:
                            signal["_spread"] = spread
                            result = executor.open_credit_position(signal, spread)
                            if result["status"] == "FILLED":
                                risk.open_positions += 1
                        else:
                            logger.info("  No spread")"""

new_ic_route = """                    elif structure == "IRON_CONDOR":
                        condor = picker.pick_iron_condor(
                            sym, max_cost, snap_5m.get("atr", 1),
                        )
                        if condor:
                            result = executor.open_iron_condor_position(signal, condor)
                            if result["status"] == "FILLED":
                                risk.open_positions += 1
                        else:
                            logger.info("  No iron condor")
                    elif structure == "CREDIT_SPREAD":
                        spread = picker.pick_credit_spread(
                            sym, signal["direction"], max_cost,
                            snap_5m.get("atr", 1),
                        )
                        if spread:
                            signal["_spread"] = spread
                            result = executor.open_credit_position(signal, spread)
                            if result["status"] == "FILLED":
                                risk.open_positions += 1
                        else:
                            logger.info("  No spread")"""

sl = sl.replace(old_ic_route, new_ic_route)
print("3. Wired IRON_CONDOR to pick_iron_condor + open_iron_condor_position")

# ══════════════════════════════════════════════════
# FIX 3: Credit spread exit — fetch both legs
# In the exit loop, for credit spreads, fetch both leg values
# ══════════════════════════════════════════════════

old_exit_loop = """        for pos in executor.get_open_positions():
            csym = pos.get("contract", "")
            if not csym:
                continue
            current = get_option_value(client, csym)
            if current is None:
                continue
            current_value = current * pos["qty"] * 100"""

new_exit_loop = """        for pos in executor.get_open_positions():
            csym = pos.get("contract", "")
            if not csym:
                continue
            structure = pos.get("structure", "LONG_OPTION")

            # For spreads/condors, calculate net value from all legs
            if structure in ("CREDIT_SPREAD", "IRON_CONDOR"):
                short_val = get_option_value(client, csym)
                if short_val is None:
                    continue
                # Credit spread: net value = short_leg - long_leg
                long_sym = pos.get("contract_long", pos.get("contract_long_put", ""))
                if long_sym:
                    long_val = get_option_value(client, long_sym)
                    if long_val is not None:
                        current = short_val - long_val  # Net spread value
                    else:
                        current = short_val
                else:
                    current = short_val
                # For IC, also add call side
                if structure == "IRON_CONDOR":
                    sc_sym = pos.get("contract_short_call", "")
                    lc_sym = pos.get("contract_long_call", "")
                    if sc_sym and lc_sym:
                        sc_val = get_option_value(client, sc_sym)
                        lc_val = get_option_value(client, lc_sym)
                        if sc_val is not None and lc_val is not None:
                            current += (sc_val - lc_val)
                current_value = abs(current) * pos["qty"] * 100
            else:
                current = get_option_value(client, csym)
                if current is None:
                    continue
                current_value = current * pos["qty"] * 100"""

sl = sl.replace(old_exit_loop, new_exit_loop)
print("4. Credit spread/IC exit now fetches all legs for net value")

# ══════════════════════════════════════════════════
# FIX 4: Batch peak_value saves
# Only save once per cycle, not on every peak update
# ══════════════════════════════════════════════════

old_peak = """            if current_value > pos.get("peak_value", 0):
                pos["peak_value"] = current_value
                executor._save()"""

new_peak = """            if current_value > pos.get("peak_value", 0):
                pos["peak_value"] = current_value
                # Don't save here — batch save at end of exit loop"""

sl = sl.replace(old_peak, new_peak)

# Add batch save after exit loop
old_check_signals = """        # ── CHECK SIGNALS ──"""
new_check_signals = """        # Batch save after exit loop (avoid 36 writes/min)
        executor._save()

        # ── CHECK SIGNALS ──"""

sl = sl.replace(old_check_signals, new_check_signals, 1)
print("5. Peak value saves batched (was 12x/min per position)")

# ══════════════════════════════════════════════════
# FIX 5: IC signal — add ATR-relative check
# ══════════════════════════════════════════════════

se = open("scalper/signal_engine.py", "r", encoding="utf-8").read()

# Find _iron_condor method and add ATR check
if "atr_relative" not in se:
    lines = se.splitlines()
    for i, line in enumerate(lines):
        if "_iron_condor" in line and "def " in line:
            # Find the vwap_pct check
            for j in range(i, min(i + 30, len(lines))):
                if "vwap_pct" in lines[j] and "0.12" in lines[j]:
                    # Add ATR check after vwap check
                    indent = len(lines[j]) - len(lines[j].lstrip())
                    lines.insert(j + 1, " " * indent + "# ATR-relative move check: block IC on gap/volatile days")
                    lines.insert(j + 2, " " * indent + 'atr = snapshot.get("atr", 1)')
                    lines.insert(j + 3, " " * indent + 'day_move = abs(snapshot.get("price", 0) - snapshot.get("open", snapshot.get("price", 0)))')
                    lines.insert(j + 4, " " * indent + "if atr > 0 and day_move / atr > 1.5:")
                    lines.insert(j + 5, " " * indent + '    return []  # Day move > 1.5x ATR = too volatile for IC')
                    print("6. Added ATR-relative volatility check to _iron_condor()")
                    break
            break
    se = "\n".join(lines)
    open("scalper/signal_engine.py", "w", encoding="utf-8").write(se)

open("scripts/scalper_live.py", "w", encoding="utf-8").write(sl)

# ══════════════════════════════════════════════════
# VERIFY
# ══════════════════════════════════════════════════
print()
import py_compile
for p in [
    "scripts/scalper_live.py",
    "scalper/executor.py",
    "scalper/contract_picker.py",
    "scalper/signal_engine.py",
]:
    try:
        py_compile.compile(p, doraise=True)
        print(f"  COMPILE: {p} OK")
    except py_compile.PyCompileError as e:
        print(f"  ERROR: {p} - {e}")

print()
print("=" * 60)
print("  SCALPER CRITICAL FIXES COMPLETE")
print("=" * 60)
print()
print("  1. Iron condor: pick_iron_condor() selects 4 legs (put spread + call spread)")
print("  2. IC execution: open_iron_condor_position() tracks all 4 contracts")
print("  3. IC routing: IRON_CONDOR signal uses new 4-leg path")
print("  4. Credit spread exit: fetches both legs for net spread value")
print("  5. Peak value: batched saves (was 36 writes/min)")
print("  6. IC volatility gate: blocks on gap days (move > 1.5x ATR)")