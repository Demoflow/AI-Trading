"""
Advanced Strategy Module.
Implements institutional-grade options structures:
- Risk Reversal (zero-cost directional)
- Diagonal Spread (theta + direction)
- Ratio Backspread (unlimited upside, limited risk)
- Jade Lizard (premium collection, zero upside risk)
"""
from loguru import logger


class AdvancedStrategies:

    def evaluate_risk_reversal(self, chain_data, direction, price,
                                support, resistance, max_cost):
        """
        RISK REVERSAL: Buy OTM call + Sell OTM put (bullish)
        or Buy OTM put + Sell OTM call (bearish).
        Net cost: near zero (or small credit/debit).
        Edge: full directional exposure with zero/minimal capital.
        """
        if not chain_data:
            return None

        call_map = chain_data.get("callExpDateMap", {})
        put_map = chain_data.get("putExpDateMap", {})

        best_call = None
        best_put = None

        for ek, strikes in call_map.items():
            try:
                dte = int(ek.split(":")[1])
            except (IndexError, ValueError):
                continue
            if not (21 <= dte <= 45):
                continue
            for sk, contracts in strikes.items():
                for c in (contracts if isinstance(contracts, list) else [contracts]):
                    d = abs(c.get("delta", 0))
                    bid = c.get("bid", 0)
                    ask = c.get("ask", 0)
                    mid = (bid + ask) / 2
                    oi = c.get("openInterest", 0)
                    if mid <= 0 or oi < 50:
                        continue
                    if 0.25 <= d <= 0.35:
                        try:
                            strike = float(sk)
                        except ValueError:
                            continue
                        if strike > price and (not best_call or abs(d - 0.30) < abs(best_call["delta"] - 0.30)):
                            best_call = {
                                "symbol": c.get("symbol", ""),
                                "strike": strike, "delta": d,
                                "bid": bid, "ask": ask, "mid": mid, "dte": dte,
                            }
            break

        for ek, strikes in put_map.items():
            try:
                dte = int(ek.split(":")[1])
            except (IndexError, ValueError):
                continue
            if not (21 <= dte <= 45):
                continue
            for sk, contracts in strikes.items():
                for c in (contracts if isinstance(contracts, list) else [contracts]):
                    d = abs(c.get("delta", 0))
                    bid = c.get("bid", 0)
                    ask = c.get("ask", 0)
                    mid = (bid + ask) / 2
                    oi = c.get("openInterest", 0)
                    if mid <= 0 or oi < 50:
                        continue
                    if 0.25 <= d <= 0.35:
                        try:
                            strike = float(sk)
                        except ValueError:
                            continue
                        if strike < price and (not best_put or abs(d - 0.30) < abs(best_put["delta"] - 0.30)):
                            best_put = {
                                "symbol": c.get("symbol", ""),
                                "strike": strike, "delta": d,
                                "bid": bid, "ask": ask, "mid": mid, "dte": dte,
                            }
            break

        if not best_call or not best_put:
            return None

        if direction == "CALL":
            # Bullish: buy call, sell put
            net_cost = (best_call["ask"] - best_put["bid"]) * 100
            contracts = [
                {"symbol": best_call["symbol"], "leg": "LONG",
                 "strike": best_call["strike"], "delta": best_call["delta"],
                 "mid": best_call["mid"], "qty": 1, "type": "CALL"},
                {"symbol": best_put["symbol"], "leg": "SHORT",
                 "strike": best_put["strike"], "delta": best_put["delta"],
                 "mid": best_put["mid"], "qty": 1, "type": "PUT"},
            ]
        else:
            # Bearish: buy put, sell call
            net_cost = (best_put["ask"] - best_call["bid"]) * 100
            contracts = [
                {"symbol": best_put["symbol"], "leg": "LONG",
                 "strike": best_put["strike"], "delta": best_put["delta"],
                 "mid": best_put["mid"], "qty": 1, "type": "PUT"},
                {"symbol": best_call["symbol"], "leg": "SHORT",
                 "strike": best_call["strike"], "delta": best_call["delta"],
                 "mid": best_call["mid"], "qty": 1, "type": "CALL"},
            ]

        # Risk reversal should be near zero cost
        if abs(net_cost) > max_cost * 0.3:
            return None  # Too expensive

        return {
            "type": "RISK_REVERSAL",
            "description": f"Risk Rev {best_put['strike']}P/{best_call['strike']}C",
            "contracts": contracts,
            "total_cost": round(abs(net_cost), 2),
            "net_cost": round(net_cost, 2),
            "max_profit": "unlimited",
            "max_loss": f"assignment risk below ${best_put['strike']}" if direction == "CALL" else f"assignment risk above ${best_call['strike']}",
            "capital_efficiency": "MAXIMUM",
        }

    def evaluate_diagonal(self, chain_data, direction, price,
                           max_cost):
        """
        DIAGONAL SPREAD: Buy longer-dated option, sell shorter-dated
        at different strike. Captures theta on short leg while
        maintaining directional exposure.
        """
        if not chain_data:
            return None

        opt_map = chain_data.get("callExpDateMap" if direction == "CALL" else "putExpDateMap", {})

        # Find two expirations: front (14-21 DTE) and back (35-60 DTE)
        front_exp = None
        back_exp = None
        front_dte = 0
        back_dte = 0

        for ek in sorted(opt_map.keys()):
            try:
                dte = int(ek.split(":")[1])
            except (IndexError, ValueError):
                continue
            if 14 <= dte <= 25 and not front_exp:
                front_exp = ek
                front_dte = dte
            elif 35 <= dte <= 60 and not back_exp:
                back_exp = ek
                back_dte = dte

        if not front_exp or not back_exp:
            return None

        # Find ATM for back month (buy), OTM for front month (sell)
        back_strikes = opt_map.get(back_exp, {})
        front_strikes = opt_map.get(front_exp, {})

        best_long = None  # Back month, ATM
        best_short = None  # Front month, slightly OTM

        for sk, contracts in back_strikes.items():
            for c in (contracts if isinstance(contracts, list) else [contracts]):
                d = abs(c.get("delta", 0))
                mid = (c.get("bid", 0) + c.get("ask", 0)) / 2
                if mid > 0 and 0.40 <= d <= 0.55:
                    try:
                        strike = float(sk)
                    except ValueError:
                        continue
                    if not best_long or abs(d - 0.45) < abs(best_long["delta"] - 0.45):
                        best_long = {
                            "symbol": c.get("symbol", ""),
                            "strike": strike, "delta": d,
                            "mid": mid, "dte": back_dte,
                            "bid": c.get("bid", 0), "ask": c.get("ask", 0),
                        }

        for sk, contracts in front_strikes.items():
            for c in (contracts if isinstance(contracts, list) else [contracts]):
                d = abs(c.get("delta", 0))
                mid = (c.get("bid", 0) + c.get("ask", 0)) / 2
                if mid > 0 and 0.25 <= d <= 0.40:
                    try:
                        strike = float(sk)
                    except ValueError:
                        continue
                    if direction == "CALL" and strike <= price:
                        continue
                    if direction == "PUT" and strike >= price:
                        continue
                    if not best_short or abs(d - 0.30) < abs(best_short["delta"] - 0.30):
                        best_short = {
                            "symbol": c.get("symbol", ""),
                            "strike": strike, "delta": d,
                            "mid": mid, "dte": front_dte,
                            "bid": c.get("bid", 0), "ask": c.get("ask", 0),
                        }

        if not best_long or not best_short:
            return None

        net_debit = best_long["ask"] - best_short["bid"]
        cost = net_debit * 100

        if cost > max_cost or cost < 20:
            return None

        return {
            "type": "DIAGONAL_SPREAD",
            "description": f"Diagonal {best_short['strike']}/{best_long['strike']} ({front_dte}/{back_dte}DTE)",
            "contracts": [
                {"symbol": best_long["symbol"], "leg": "LONG",
                 "strike": best_long["strike"], "delta": best_long["delta"],
                 "mid": best_long["mid"], "qty": 1,
                 "desc": f"Buy {back_dte}DTE ${best_long['strike']}"},
                {"symbol": best_short["symbol"], "leg": "SHORT",
                 "strike": best_short["strike"], "delta": best_short["delta"],
                 "mid": best_short["mid"], "qty": 1,
                 "desc": f"Sell {front_dte}DTE ${best_short['strike']}"},
            ],
            "total_cost": round(cost, 2),
            "net_debit": round(net_debit, 2),
            "max_profit": round((abs(best_long["strike"] - best_short["strike"]) - net_debit) * 100, 2),
            "theta_advantage": round(best_short["mid"] / best_long["mid"] * 100, 1),
        }

    def evaluate_ratio_backspread(self, chain_data, direction, price,
                                   max_cost):
        """
        RATIO BACKSPREAD: Sell 1 ATM, buy 2 OTM.
        Small debit or credit. Unlimited profit if big move.
        Limited loss if stock stays flat.
        """
        if not chain_data:
            return None

        opt_map = chain_data.get("callExpDateMap" if direction == "CALL" else "putExpDateMap", {})

        for ek, strikes in opt_map.items():
            try:
                dte = int(ek.split(":")[1])
            except (IndexError, ValueError):
                continue
            if not (21 <= dte <= 45):
                continue

            atm = None  # Sell 1
            otm = None  # Buy 2

            for sk, contracts in strikes.items():
                for c in (contracts if isinstance(contracts, list) else [contracts]):
                    d = abs(c.get("delta", 0))
                    mid = (c.get("bid", 0) + c.get("ask", 0)) / 2
                    if mid <= 0:
                        continue
                    try:
                        strike = float(sk)
                    except ValueError:
                        continue

                    if 0.45 <= d <= 0.55:
                        if not atm or abs(d - 0.50) < abs(atm["delta"] - 0.50):
                            atm = {"symbol": c.get("symbol",""), "strike": strike,
                                   "delta": d, "mid": mid,
                                   "bid": c.get("bid",0), "ask": c.get("ask",0)}
                    elif 0.25 <= d <= 0.35:
                        if direction == "CALL" and strike > price:
                            if not otm or abs(d - 0.30) < abs(otm["delta"] - 0.30):
                                otm = {"symbol": c.get("symbol",""), "strike": strike,
                                       "delta": d, "mid": mid,
                                       "bid": c.get("bid",0), "ask": c.get("ask",0)}
                        elif direction == "PUT" and strike < price:
                            if not otm or abs(d - 0.30) < abs(otm["delta"] - 0.30):
                                otm = {"symbol": c.get("symbol",""), "strike": strike,
                                       "delta": d, "mid": mid,
                                       "bid": c.get("bid",0), "ask": c.get("ask",0)}

            if atm and otm:
                # Sell 1 ATM, buy 2 OTM
                net = (2 * otm["ask"]) - atm["bid"]
                cost = net * 100

                if abs(cost) > max_cost:
                    continue

                return {
                    "type": "RATIO_BACKSPREAD",
                    "description": f"1x{atm['strike']} / 2x{otm['strike']}",
                    "contracts": [
                        {"symbol": atm["symbol"], "leg": "SHORT",
                         "strike": atm["strike"], "delta": atm["delta"],
                         "mid": atm["mid"], "qty": 1},
                        {"symbol": otm["symbol"], "leg": "LONG",
                         "strike": otm["strike"], "delta": otm["delta"],
                         "mid": otm["mid"], "qty": 2},
                    ],
                    "total_cost": round(abs(cost), 2),
                    "net_cost": round(cost, 2),
                    "max_profit": "unlimited (beyond OTM strike)",
                    "max_loss": round(abs(atm["strike"] - otm["strike"]) * 100 + cost, 2),
                    "breakeven_up": round(otm["strike"] + abs(atm["strike"] - otm["strike"]) + net, 2) if direction == "CALL" else 0,
                }
            break

        return None

    def score_all(self, chain_data, direction, price, conviction,
                  iv_rank, support, resistance, max_cost):
        """
        Score all advanced strategies and return the best ones.
        Returns list of (score, strategy_dict).
        """
        results = []

        # Risk Reversal: best for high conviction, any IV
        rr = self.evaluate_risk_reversal(
            chain_data, direction, price, support, resistance, max_cost)
        if rr:
            score = 70
            if conviction >= 90:
                score += 15
            if abs(rr["net_cost"]) < 50:
                score += 10  # Near zero cost
            rr["score"] = score
            results.append(rr)

        # Diagonal: best for moderate conviction, moderate IV
        diag = self.evaluate_diagonal(chain_data, direction, price, max_cost)
        if diag:
            score = 60
            if 40 <= iv_rank <= 70:
                score += 15  # Best in moderate IV
            if diag.get("theta_advantage", 0) > 30:
                score += 10  # Good theta ratio
            diag["score"] = score
            results.append(diag)

        # Ratio Backspread: best for high conviction + high IV
        rbs = self.evaluate_ratio_backspread(
            chain_data, direction, price, max_cost)
        if rbs:
            score = 55
            if iv_rank > 50:
                score += 20  # Sell expensive ATM
            if conviction >= 85:
                score += 10
            rbs["score"] = score
            results.append(rbs)

        results.sort(key=lambda x: x.get("score", 0), reverse=True)
        return results
