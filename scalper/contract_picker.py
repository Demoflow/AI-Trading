"""
Contract Picker v4 - Level 3.
Supports:
- Long options (calls/puts)
- Credit spreads (bull put / bear call)
- Naked puts, naked calls
- Straddles, strangles
- Ratio spreads (1x2, 1x3)
- Expected move calculation
- Smart 0DTE/1DTE selection
- Dynamic spread width guard
"""

import httpx
from datetime import datetime
from loguru import logger


class ContractPicker:

    TARGET_DELTA = 0.55
    MIN_DELTA = 0.42
    MAX_DELTA = 0.62
    MAX_SPREAD_PCT = 0.08
    MAX_SPREAD_PCT_POWER = 0.12
    MIN_OI = 100

    def __init__(self, schwab_client):
        self.client = schwab_client
        self._spread_history = []

    def _get_max_spread(self):
        h = datetime.now().hour + datetime.now().minute / 60.0
        return self.MAX_SPREAD_PCT_POWER if h >= 14.5 else self.MAX_SPREAD_PCT

    def _smart_dte(self, structure, confidence=70):
        h = datetime.now().hour + datetime.now().minute / 60.0
        # Selling strategies: always 0DTE for max theta
        if structure in ("NAKED_PUT", "NAKED_CALL", "STRADDLE",
                         "STRANGLE", "CREDIT_SPREAD", "IRON_CONDOR",
                         "RATIO_SPREAD"):
            return 0
        # Buying: 1DTE unless very high conviction
        if structure == "LONG_OPTION" and confidence < 85:
            return 1
        if h < 10.5:
            return 0
        return 1

    def should_allow_buy(self, confidence):
        h = datetime.now().hour + datetime.now().minute / 60.0
        if h >= 13.5 and confidence < 85:
            return False, "theta_block_afternoon"
        if h >= 14.5 and confidence < 90:
            return False, "theta_block_power"
        return True, "ok"

    def _check_spread_anomaly(self, spread_pct):
        self._spread_history.append(spread_pct)
        self._spread_history = self._spread_history[-100:]
        if len(self._spread_history) < 10:
            return False
        avg = sum(self._spread_history) / len(self._spread_history)
        return spread_pct > avg * 2.0

    def _get_chain(self, symbol, contract_type="ALL", strikes=15):
        try:
            from schwab.client import Client
            ct_map = {
                "ALL": Client.Options.ContractType.ALL,
                "CALL": Client.Options.ContractType.CALL,
                "PUT": Client.Options.ContractType.PUT,
            }
            resp = self.client.get_option_chain(
                symbol,
                contract_type=ct_map.get(contract_type, Client.Options.ContractType.ALL),
                strike_count=strikes,
                include_underlying_quote=True,
                strategy=Client.Options.Strategy.SINGLE,
            )
            if resp.status_code != httpx.codes.OK:
                return None, 0
            chain = resp.json()
            price = chain.get("underlyingPrice", 0)
            return chain, price
        except Exception:
            return None, 0

    def get_expected_move(self, symbol):
        chain, price = self._get_chain(symbol, "ALL", 5)
        if not chain or price <= 0:
            return None

        call_map = chain.get("callExpDateMap", {})
        put_map = chain.get("putExpDateMap", {})
        atm_call = atm_put = None

        for ek, strikes in call_map.items():
            try:
                dte = int(ek.split(":")[1])
            except (IndexError, ValueError):
                continue
            if dte > 0:
                continue
            for sk, contracts in strikes.items():
                for c in (contracts if isinstance(contracts, list) else [contracts]):
                    d = abs(c.get("delta", 0))
                    if 0.45 <= d <= 0.55:
                        mid = (c.get("bid", 0) + c.get("ask", 0)) / 2
                        if mid > 0:
                            atm_call = mid

        for ek, strikes in put_map.items():
            try:
                dte = int(ek.split(":")[1])
            except (IndexError, ValueError):
                continue
            if dte > 0:
                continue
            for sk, contracts in strikes.items():
                for c in (contracts if isinstance(contracts, list) else [contracts]):
                    d = abs(c.get("delta", 0))
                    if 0.45 <= d <= 0.55:
                        mid = (c.get("bid", 0) + c.get("ask", 0)) / 2
                        if mid > 0:
                            atm_put = mid

        if atm_call and atm_put:
            straddle = atm_call + atm_put
            return {
                "straddle_price": round(straddle, 2),
                "expected_move": round(straddle, 2),
                "expected_move_pct": round(straddle / price * 100, 3),
                "upper_bound": round(price + straddle, 2),
                "lower_bound": round(price - straddle, 2),
                "price": round(price, 2),
            }
        return None

    def pick(self, symbol, direction, max_cost, structure="LONG_OPTION"):
        preferred_dte = self._smart_dte(structure)
        chain, price = self._get_chain(symbol, direction, 10)
        if not chain or price <= 0:
            return None

        exp_map = chain.get(
            "callExpDateMap" if direction == "CALL"
            else "putExpDateMap", {}
        )

        max_spread = self._get_max_spread()
        best = None
        best_score = -999

        for ek, strikes in exp_map.items():
            try:
                dte = int(ek.split(":")[1])
            except (IndexError, ValueError):
                continue
            if dte > 1:
                continue

            dte_bonus = 15 if dte == preferred_dte else 0

            for sk, contracts in strikes.items():
                for c in (contracts if isinstance(contracts, list) else [contracts]):
                    delta = abs(c.get("delta", 0))
                    gamma = c.get("gamma", 0)
                    theta = c.get("theta", 0)
                    bid = c.get("bid", 0)
                    ask = c.get("ask", 0)
                    oi = c.get("openInterest", 0)
                    vol = c.get("totalVolume", 0)

                    if bid <= 0 or ask <= 0:
                        continue
                    mid = (bid + ask) / 2
                    spread = (ask - bid) / mid if mid > 0 else 1

                    if not (self.MIN_DELTA <= delta <= self.MAX_DELTA):
                        continue
                    if spread > max_spread:
                        continue
                    if oi < self.MIN_OI or mid * 100 > max_cost or mid < 0.10:
                        continue

                    score = (1 - abs(delta - self.TARGET_DELTA) * 8) * 30
                    score += min(gamma * 500, 25)
                    score += max(0, (max_spread - spread) * 150)
                    score += (10 if vol >= 500 else 5 if vol >= 200 else 0)
                    score += dte_bonus

                    if score > best_score:
                        best_score = score
                        try:
                            strike_val = float(sk)
                        except ValueError:
                            continue
                        qty = max(1, int(max_cost / (mid * 100)))
                        best = {
                            "symbol": c.get("symbol", ""),
                            "strike": strike_val, "dte": dte,
                            "delta": round(delta, 3),
                            "gamma": round(gamma, 4),
                            "theta": round(theta, 4),
                            "bid": bid, "ask": ask,
                            "mid": round(mid, 2),
                            "spread_pct": round(spread, 4),
                            "qty": qty,
                            "total_cost": round(qty * mid * 100, 2),
                            "oi": oi, "volume": vol,
                            "direction": direction,
                            "underlying_price": round(price, 2),
                        }

        if best:
            logger.info(
                f"Pick: {symbol} {direction} ${best['strike']} "
                f"{best['dte']}DTE d={best['delta']} ${best['mid']} x{best['qty']}"
            )
        return best

    def pick_naked(self, symbol, direction, max_collateral, target_delta=0.15):
        """
        Pick a naked put or naked call to SELL.
        direction=CALL: sell naked call (bearish)
        direction=PUT: sell naked put (bullish)
        target_delta: 0.10-0.20 for OTM naked selling
        """
        chain, price = self._get_chain(symbol, direction, 15)
        if not chain or price <= 0:
            return None

        exp_map = chain.get(
            "callExpDateMap" if direction == "CALL"
            else "putExpDateMap", {}
        )

        best = None
        best_score = -999

        for ek, strikes in exp_map.items():
            try:
                dte = int(ek.split(":")[1])
            except (IndexError, ValueError):
                continue
            if dte > 0:
                continue  # 0DTE only for naked selling

            for sk, contracts in strikes.items():
                for c in (contracts if isinstance(contracts, list) else [contracts]):
                    delta = abs(c.get("delta", 0))
                    bid = c.get("bid", 0)
                    ask = c.get("ask", 0)
                    oi = c.get("openInterest", 0)

                    if bid <= 0:
                        continue
                    if not (0.08 <= delta <= 0.30):
                        continue
                    if oi < 200:
                        continue

                    mid = (bid + ask) / 2
                    try:
                        strike = float(sk)
                    except ValueError:
                        continue

                    # Collateral = strike * 100 * 0.20 (approx 20% margin)
                    collateral_per = strike * 100 * 0.20
                    qty = max(1, int(max_collateral / collateral_per))

                    # Score: closer to target delta = better
                    score = (1 - abs(delta - target_delta) * 10) * 30
                    score += bid * 100  # More premium = better
                    if oi >= 500:
                        score += 10

                    # Buffer from current price
                    if direction == "PUT":
                        buffer = (price - strike) / price
                    else:
                        buffer = (strike - price) / price
                    score += buffer * 200

                    if score > best_score:
                        best_score = score
                        best = {
                            "type": "NAKED_" + direction,
                            "symbol": c.get("symbol", ""),
                            "strike": strike, "dte": 0,
                            "delta": round(delta, 3),
                            "bid": bid, "ask": ask,
                            "mid": round(mid, 2),
                            "premium": round(bid * qty * 100, 2),
                            "collateral": round(collateral_per * qty, 2),
                            "qty": qty,
                            "direction": direction,
                            "buffer_pct": round(buffer * 100, 2),
                            "underlying_price": round(price, 2),
                            "oi": oi,
                        }

        if best:
            logger.info(
                f"Naked {direction}: {symbol} ${best['strike']} "
                f"d={best['delta']} prem=${best['premium']:.0f} "
                f"buffer={best['buffer_pct']:.1f}%"
            )
        return best

    def pick_straddle(self, symbol, max_collateral):
        """
        Pick ATM straddle to SELL.
        Sell ATM call + ATM put at same strike.
        """
        chain, price = self._get_chain(symbol, "ALL", 5)
        if not chain or price <= 0:
            return None

        call_map = chain.get("callExpDateMap", {})
        put_map = chain.get("putExpDateMap", {})

        best_call = None
        best_put = None

        for ek, strikes in call_map.items():
            try:
                dte = int(ek.split(":")[1])
            except (IndexError, ValueError):
                continue
            if dte > 0:
                continue
            for sk, contracts in strikes.items():
                for c in (contracts if isinstance(contracts, list) else [contracts]):
                    d = abs(c.get("delta", 0))
                    if 0.40 <= d <= 0.55 and c.get("bid", 0) > 0:
                        if not best_call or abs(d - 0.50) < abs(best_call["delta"] - 0.50):
                            try:
                                best_call = {
                                    "symbol": c.get("symbol", ""),
                                    "strike": float(sk),
                                    "delta": d,
                                    "bid": c["bid"],
                                    "ask": c.get("ask", 0),
                                    "mid": round((c["bid"]+c.get("ask",0))/2, 2),
                                }
                            except ValueError:
                                pass

        for ek, strikes in put_map.items():
            try:
                dte = int(ek.split(":")[1])
            except (IndexError, ValueError):
                continue
            if dte > 0:
                continue
            for sk, contracts in strikes.items():
                for c in (contracts if isinstance(contracts, list) else [contracts]):
                    d = abs(c.get("delta", 0))
                    if 0.40 <= d <= 0.55 and c.get("bid", 0) > 0:
                        if not best_put or abs(d - 0.50) < abs(best_put["delta"] - 0.50):
                            try:
                                best_put = {
                                    "symbol": c.get("symbol", ""),
                                    "strike": float(sk),
                                    "delta": d,
                                    "bid": c["bid"],
                                    "ask": c.get("ask", 0),
                                    "mid": round((c["bid"]+c.get("ask",0))/2, 2),
                                }
                            except ValueError:
                                pass

        if not best_call or not best_put:
            return None

        total_credit = best_call["bid"] + best_put["bid"]
        # Straddle collateral ~ higher strike * 100 * 0.20
        collateral = max(best_call["strike"], best_put["strike"]) * 100 * 0.20
        qty = max(1, int(max_collateral / collateral))

        result = {
            "type": "STRADDLE",
            "call": best_call,
            "put": best_put,
            "strike": best_call["strike"],
            "total_credit": round(total_credit, 2),
            "premium": round(total_credit * qty * 100, 2),
            "collateral": round(collateral * qty, 2),
            "qty": qty,
            "underlying_price": round(price, 2),
            "expected_move": round(total_credit, 2),
        }

        logger.info(
            f"Straddle: {symbol} ${result['strike']} "
            f"cr=${total_credit:.2f} x{qty} "
            f"prem=${result['premium']:.0f}"
        )
        return result

    def pick_strangle(self, symbol, max_collateral, target_delta=0.20):
        """
        Pick OTM strangle to SELL.
        Sell OTM call + OTM put.
        """
        chain, price = self._get_chain(symbol, "ALL", 15)
        if not chain or price <= 0:
            return None

        call_map = chain.get("callExpDateMap", {})
        put_map = chain.get("putExpDateMap", {})

        best_call = None
        best_put = None

        for ek, strikes in call_map.items():
            try:
                dte = int(ek.split(":")[1])
            except (IndexError, ValueError):
                continue
            if dte > 0:
                continue
            for sk, contracts in strikes.items():
                for c in (contracts if isinstance(contracts, list) else [contracts]):
                    d = abs(c.get("delta", 0))
                    if 0.10 <= d <= 0.25 and c.get("bid", 0) > 0.05:
                        try:
                            strike = float(sk)
                        except ValueError:
                            continue
                        if strike <= price:
                            continue  # Must be OTM call
                        if not best_call or abs(d - target_delta) < abs(best_call["delta"] - target_delta):
                            best_call = {
                                "symbol": c.get("symbol", ""),
                                "strike": strike, "delta": d,
                                "bid": c["bid"], "ask": c.get("ask", 0),
                            }

        for ek, strikes in put_map.items():
            try:
                dte = int(ek.split(":")[1])
            except (IndexError, ValueError):
                continue
            if dte > 0:
                continue
            for sk, contracts in strikes.items():
                for c in (contracts if isinstance(contracts, list) else [contracts]):
                    d = abs(c.get("delta", 0))
                    if 0.10 <= d <= 0.25 and c.get("bid", 0) > 0.05:
                        try:
                            strike = float(sk)
                        except ValueError:
                            continue
                        if strike >= price:
                            continue  # Must be OTM put
                        if not best_put or abs(d - target_delta) < abs(best_put["delta"] - target_delta):
                            best_put = {
                                "symbol": c.get("symbol", ""),
                                "strike": strike, "delta": d,
                                "bid": c["bid"], "ask": c.get("ask", 0),
                            }

        if not best_call or not best_put:
            return None

        total_credit = best_call["bid"] + best_put["bid"]
        collateral = max(best_call["strike"], best_put["strike"]) * 100 * 0.20
        qty = max(1, int(max_collateral / collateral))

        result = {
            "type": "STRANGLE",
            "call": best_call,
            "put": best_put,
            "call_strike": best_call["strike"],
            "put_strike": best_put["strike"],
            "total_credit": round(total_credit, 2),
            "premium": round(total_credit * qty * 100, 2),
            "collateral": round(collateral * qty, 2),
            "qty": qty,
            "width": round(best_call["strike"] - best_put["strike"], 2),
            "underlying_price": round(price, 2),
        }

        logger.info(
            f"Strangle: {symbol} ${best_put['strike']}P/"
            f"${best_call['strike']}C cr=${total_credit:.2f} x{qty}"
        )
        return result

    def pick_ratio_spread(self, symbol, direction, max_collateral):
        """
        Ratio spread: buy 1, sell 2 (or 3).
        Bull: buy 1 ATM call, sell 2 OTM calls.
        Bear: buy 1 ATM put, sell 2 OTM puts.
        Net credit or small debit with directional bias.
        """
        chain, price = self._get_chain(symbol, direction, 15)
        if not chain or price <= 0:
            return None

        exp_map = chain.get(
            "callExpDateMap" if direction == "CALL"
            else "putExpDateMap", {}
        )

        best = None
        best_score = -999

        for ek, strikes in exp_map.items():
            try:
                dte = int(ek.split(":")[1])
            except (IndexError, ValueError):
                continue
            if dte > 0:
                continue

            options = []
            for sk, contracts in strikes.items():
                for c in (contracts if isinstance(contracts, list) else [contracts]):
                    bid = c.get("bid", 0)
                    ask = c.get("ask", 0)
                    if bid <= 0 or ask <= 0:
                        continue
                    try:
                        strike = float(sk)
                    except ValueError:
                        continue
                    options.append({
                        "symbol": c.get("symbol", ""),
                        "strike": strike,
                        "delta": abs(c.get("delta", 0)),
                        "bid": bid, "ask": ask,
                        "mid": round((bid+ask)/2, 2),
                        "oi": c.get("openInterest", 0),
                    })

            options.sort(key=lambda x: x["strike"])

            for i in range(len(options)):
                buy_leg = options[i]
                if not (0.40 <= buy_leg["delta"] <= 0.60):
                    continue

                for j in range(i+1 if direction == "CALL" else 0,
                               len(options) if direction == "CALL" else i):
                    sell_leg = options[j]
                    if not (0.10 <= sell_leg["delta"] <= 0.30):
                        continue

                    width = abs(buy_leg["strike"] - sell_leg["strike"])
                    if width < 2 or width > 10:
                        continue

                    # 1x2 ratio: buy 1, sell 2
                    net = (2 * sell_leg["bid"]) - buy_leg["ask"]
                    if net < -0.50:
                        continue  # Max debit $0.50

                    collateral = width * 100  # Risk on the extra short
                    if collateral > max_collateral:
                        continue

                    score = net * 100 + (sell_leg["oi"] / 100)

                    if score > best_score:
                        best_score = score
                        best = {
                            "type": "RATIO_SPREAD",
                            "buy": buy_leg,
                            "sell": sell_leg,
                            "ratio": "1x2",
                            "net_credit": round(max(net, 0), 2),
                            "net_debit": round(abs(min(net, 0)), 2),
                            "width": round(width, 2),
                            "collateral": round(collateral, 2),
                            "qty": 1,
                            "direction": direction,
                            "underlying_price": round(price, 2),
                        }

        if best:
            logger.info(
                f"Ratio: {symbol} {direction} "
                f"buy ${best['buy']['strike']} / "
                f"sell 2x ${best['sell']['strike']} "
                f"net={'cr' if best['net_credit'] > 0 else 'db'}"
                f"${max(best['net_credit'], best['net_debit']):.2f}"
            )
        return best

    def pick_credit_spread(self, symbol, direction, max_cost, atr=1.0):
        """Pick 0DTE credit spread."""
        chain, price = self._get_chain(
            symbol,
            "PUT" if direction == "CALL" else "CALL",
            15
        )
        if not chain or price <= 0:
            return None

        exp_map = chain.get(
            "putExpDateMap" if direction == "CALL"
            else "callExpDateMap", {}
        )

        best = None
        best_score = -999

        for ek, strikes in exp_map.items():
            try:
                dte = int(ek.split(":")[1])
            except (IndexError, ValueError):
                continue
            if dte > 1:
                continue

            options = []
            for sk, contracts in strikes.items():
                for c in (contracts if isinstance(contracts, list) else [contracts]):
                    bid = c.get("bid", 0)
                    if bid <= 0:
                        continue
                    try:
                        strike = float(sk)
                    except ValueError:
                        continue
                    options.append({
                        "symbol": c.get("symbol", ""),
                        "strike": strike,
                        "delta": abs(c.get("delta", 0)),
                        "bid": bid, "ask": c.get("ask", 0),
                        "mid": round((bid+c.get("ask",0))/2, 2),
                        "oi": c.get("openInterest", 0),
                        "dte": dte,
                    })

            options.sort(key=lambda x: x["strike"])

            for i in range(len(options)):
                for j in range(i+1, min(i+4, len(options))):
                    if direction == "CALL":
                        short_leg = options[j]
                        long_leg = options[i]
                    else:
                        short_leg = options[i]
                        long_leg = options[j]

                    if not (0.10 <= short_leg["delta"] <= 0.30):
                        continue
                    width = abs(short_leg["strike"] - long_leg["strike"])
                    if width < 1 or width > 5:
                        continue
                    credit = short_leg["bid"] - long_leg["ask"]
                    if credit <= 0.05:
                        continue
                    max_loss = width - credit
                    collateral = max_loss * 100
                    if collateral > max_cost:
                        continue
                    qty = max(1, int(max_cost / collateral))
                    score = (credit / width) * 50
                    if short_leg["oi"] >= 200:
                        score += 10
                    buffer = abs(price - short_leg["strike"]) / price
                    if buffer > 0.005:
                        score += buffer * 500

                    if score > best_score:
                        best_score = score
                        best = {
                            "type": "CREDIT_SPREAD",
                            "short": short_leg, "long": long_leg,
                            "credit": round(credit, 2),
                            "max_loss": round(max_loss, 2),
                            "width": round(width, 2),
                            "qty": qty,
                            "total_credit": round(qty * credit * 100, 2),
                            "collateral": round(qty * collateral, 2),
                            "direction": direction, "dte": dte,
                        }

        if best:
            logger.info(
                f"Credit: {symbol} ${best['short']['strike']}/"
                f"${best['long']['strike']} cr=${best['credit']} x{best['qty']}"
            )
        return best

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
            put_exp = next((v for k, v in put_map.items() if int(k.split(":")[1]) == 0), {})
            call_exp = next((v for k, v in call_map.items() if int(k.split(":")[1]) == 0), {})
            
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
