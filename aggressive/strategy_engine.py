"""
Elite Strategy Engine v2.
Evaluates SIX strategy types:
1. Naked Long (low IV, strong directional)
2. Debit Spread (moderate IV, capital efficient)
3. Credit Spread (high IV, theta harvesting)
4. Calendar Spread (IV skew plays)
5. Broken Wing Butterfly (high IV, zero-risk one side)
6. Dual-Strategy (layered positions for max flow signals)

Uses expected move calculation for precise strike targeting.
"""

import math
import httpx
from datetime import datetime, date
from loguru import logger


class StrategyEngine:
    # Calendar spreads disabled until valuation/exit bugs are fixed
    # Cash account: only NAKED_LONG allowed (no spreads at any level)
    BLOCKED_STRATEGIES = {"CALENDAR_SPREAD", "RISK_REVERSAL", "RATIO_BACKSPREAD", "BROKEN_WING_BUTTERFLY", "DEBIT_SPREAD", "CREDIT_SPREAD", "NAKED_PUT_SELL", "NAKED_CALL_SELL", "SHORT_STRANGLE", "DUAL_STRATEGY"}


    def __init__(self, schwab_client):
        self.client = schwab_client

    def _expected_move(self, price, iv, dte):
        """Calculate the expected move for a given IV and DTE."""
        if iv <= 0 or dte <= 0 or price <= 0:
            return price * 0.05
        return price * (iv / 100) * math.sqrt(dte / 365)



    def _evaluate_naked_put(self, symbol, chain_data, price,
                            conviction_score, iv_rank, support,
                            max_cost, equity):
        """
        NAKED PUT: Sell OTM put. Bullish + premium income.
        Best when: HIGH conviction CALL, IV rank > 40%, GEX positive.
        """
        if not chain_data:
            return None

        put_map = chain_data.get("putExpDateMap", {})

        best = None
        best_score = -999

        for ek, strikes in put_map.items():
            try:
                dte = int(ek.split(":")[1])
            except (IndexError, ValueError):
                continue
            if not (14 <= dte <= 45):
                continue

            for sk, contracts in strikes.items():
                for c in (contracts if isinstance(contracts, list) else [contracts]):
                    delta = abs(c.get("delta", 0))
                    bid = c.get("bid", 0)
                    oi = c.get("openInterest", 0)

                    if bid <= 0 or oi < 50:
                        continue
                    if not (0.15 <= delta <= 0.30):
                        continue

                    try:
                        strike = float(sk)
                    except ValueError:
                        continue

                    # Collateral = strike * 100 * 0.20
                    collateral = strike * 100 * 0.20
                    if collateral > max_cost:
                        continue

                    premium = bid * 100
                    roi = premium / collateral if collateral > 0 else 0

                    score = roi * 100 + (30 - abs(delta * 100 - 20))
                    if strike <= support:
                        score += 20  # Below support = safer

                    if score > best_score:
                        best_score = score
                        best = {
                            "type": "NAKED_PUT",
                            "description": f"Sell {strike} PUT",
                            "contracts": [{
                                "leg": "SHORT",
                                "symbol": c.get("symbol", ""),
                                "desc": f"{symbol} {dte}DTE ${strike} P",
                                "strike": strike,
                                "delta": round(delta, 3),
                                "mid": round(bid, 2),
                                "qty": 1,
                            }],
                            "total_cost": round(collateral, 2),
                            "max_profit": round(premium, 2),
                            "max_loss": round(strike * 100 - premium, 2),
                            "premium": round(premium, 2),
                            "collateral": round(collateral, 2),
                            "roi_pct": round(roi * 100, 1),
                        }

        return best

    def _evaluate_naked_call(self, symbol, chain_data, price,
                             conviction_score, iv_rank, resistance,
                             max_cost, equity):
        """
        NAKED CALL: Sell OTM call. Bearish + premium income.
        Best when: HIGH conviction PUT, IV rank > 40%, GEX positive.
        """
        if not chain_data:
            return None

        call_map = chain_data.get("callExpDateMap", {})
        best = None
        best_score = -999

        for ek, strikes in call_map.items():
            try:
                dte = int(ek.split(":")[1])
            except (IndexError, ValueError):
                continue
            if not (14 <= dte <= 45):
                continue

            for sk, contracts in strikes.items():
                for c in (contracts if isinstance(contracts, list) else [contracts]):
                    delta = abs(c.get("delta", 0))
                    bid = c.get("bid", 0)
                    oi = c.get("openInterest", 0)

                    if bid <= 0 or oi < 50:
                        continue
                    if not (0.15 <= delta <= 0.30):
                        continue

                    try:
                        strike = float(sk)
                    except ValueError:
                        continue

                    collateral = strike * 100 * 0.20
                    if collateral > max_cost:
                        continue

                    premium = bid * 100
                    roi = premium / collateral if collateral > 0 else 0

                    score = roi * 100 + (30 - abs(delta * 100 - 20))
                    if strike >= resistance:
                        score += 20

                    if score > best_score:
                        best_score = score
                        best = {
                            "type": "NAKED_CALL",
                            "description": f"Sell {strike} CALL",
                            "contracts": [{
                                "leg": "SHORT",
                                "symbol": c.get("symbol", ""),
                                "desc": f"{symbol} {dte}DTE ${strike} C",
                                "strike": strike,
                                "delta": round(delta, 3),
                                "mid": round(bid, 2),
                                "qty": 1,
                            }],
                            "total_cost": round(collateral, 2),
                            "max_profit": round(premium, 2),
                            "max_loss": "unlimited",
                            "premium": round(premium, 2),
                            "collateral": round(collateral, 2),
                            "roi_pct": round(roi * 100, 1),
                        }

        return best

    def _evaluate_short_strangle(self, symbol, chain_data, price,
                                  conviction_score, iv_rank, support,
                                  resistance, max_cost, equity):
        """
        SHORT STRANGLE: Sell OTM put + OTM call.
        Best when: IV rank > 50%, range-bound, GEX positive.
        """
        if not chain_data or iv_rank < 40:
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
                    if bid > 0 and 0.15 <= d <= 0.25:
                        try:
                            strike = float(sk)
                        except ValueError:
                            continue
                        if strike <= price:
                            continue
                        if not best_call or abs(d - 0.20) < abs(best_call["delta"] - 0.20):
                            best_call = {
                                "symbol": c.get("symbol", ""),
                                "strike": strike, "delta": d,
                                "bid": bid, "dte": dte,
                            }

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
                    if bid > 0 and 0.15 <= d <= 0.25:
                        try:
                            strike = float(sk)
                        except ValueError:
                            continue
                        if strike >= price:
                            continue
                        if not best_put or abs(d - 0.20) < abs(best_put["delta"] - 0.20):
                            best_put = {
                                "symbol": c.get("symbol", ""),
                                "strike": strike, "delta": d,
                                "bid": bid, "dte": dte,
                            }

        if not best_call or not best_put:
            return None

        total_credit = (best_call["bid"] + best_put["bid"]) * 100
        collateral = max(best_call["strike"], best_put["strike"]) * 100 * 0.20

        if collateral > max_cost:
            return None

        return {
            "type": "SHORT_STRANGLE",
            "description": f"Sell {best_put['strike']}P/{best_call['strike']}C Strangle",
            "contracts": [
                {
                    "leg": "SHORT",
                    "symbol": best_put["symbol"],
                    "desc": f"{symbol} ${best_put['strike']} P",
                    "strike": best_put["strike"],
                    "delta": round(best_put["delta"], 3),
                    "mid": round(best_put["bid"], 2),
                    "qty": 1,
                },
                {
                    "leg": "SHORT",
                    "symbol": best_call["symbol"],
                    "desc": f"{symbol} ${best_call['strike']} C",
                    "strike": best_call["strike"],
                    "delta": round(best_call["delta"], 3),
                    "mid": round(best_call["bid"], 2),
                    "qty": 1,
                },
            ],
            "total_cost": round(collateral, 2),
            "max_profit": round(total_credit, 2),
            "max_loss": "undefined (manage with stops)",
            "premium": round(total_credit, 2),
            "collateral": round(collateral, 2),
            "width": round(best_call["strike"] - best_put["strike"], 2),
        }

    def select_strategy(self, symbol, direction, chain_data,
                        price, conviction_score, iv_rank,
                        support, resistance, days_to_catalyst,
                        max_cost, equity):
        if not chain_data:
            return None

        atm_iv = self._get_atm_iv(chain_data, direction)
        iv_skew = self._get_iv_skew(chain_data, direction)
        term_structure = self._get_term_structure(chain_data, direction)
        exp_move = self._expected_move(price, atm_iv, 30)

        strategies = []

        naked = self._score_naked(
            direction, atm_iv, iv_rank, conviction_score,
            price, support, resistance, max_cost, chain_data,
        )
        if naked:
            strategies.append(naked)

        debit = self._score_debit_spread(
            direction, atm_iv, iv_rank, conviction_score,
            price, support, resistance, max_cost, chain_data,
            exp_move,
        )
        if debit:
            strategies.append(debit)

        credit = self._score_credit_spread(
            direction, atm_iv, iv_rank, conviction_score,
            price, support, resistance, max_cost, chain_data,
        )
        if credit:
            strategies.append(credit)

        calendar = self._score_calendar(
            direction, atm_iv, iv_rank, term_structure,
            price, days_to_catalyst, max_cost, chain_data,
        )
        if calendar:
            strategies.append(calendar)

        bwb = self._score_bwb(
            direction, atm_iv, iv_rank, conviction_score,
            price, support, resistance, max_cost, chain_data,
            exp_move,
        )
        if bwb:
            strategies.append(bwb)

        if not strategies:
            return None

        strategies.sort(key=lambda x: x["score"], reverse=True)

        # Vol regime enforcement — penalize strategies that conflict with VIX
        try:
            from aggressive.vol_strategy import VolatilityStrategySelector
            vix_q = self.client.get_quote('$VIX') if hasattr(self, 'client') else None
            vix = vix_q.json().get('$VIX', {}).get('quote', {}).get('lastPrice', 20) if vix_q and vix_q.status_code == 200 else 20
            vol_regime = VolatilityStrategySelector.get_regime(vix)
            avoid = vol_regime.get('avoid_strategies', [])
            preferred = vol_regime.get('preferred_strategies', [])
            for s in strategies:
                stype = s.get('type', '')
                if stype in avoid:
                    s['score'] = max(0, s.get('score', 0) - 25)
                elif stype in preferred:
                    s['score'] = s.get('score', 0) + 15
            # Re-sort after penalty
            strategies.sort(key=lambda x: x.get('score', 0), reverse=True)
        except Exception:
            pass

        # Remove blocked strategies
        strategies = [s for s in strategies if s.get("type") not in self.BLOCKED_STRATEGIES]
        if not strategies:
            return None

        # If vol regime prefers spreads but only NAKED_LONG available,
        # reduce its score to reflect suboptimal strategy selection
        try:
            from aggressive.vol_strategy import VolatilityStrategySelector
            _vq = self.client.get_quote("$VIX") if hasattr(self, 'client') else None
            _vix = _vq.json().get("$VIX", {}).get("quote", {}).get("lastPrice", 20) if _vq and _vq.status_code == 200 else 20
            if _vix > 25:
                # In elevated VIX, naked longs are buying expensive premium
                for s in strategies:
                    if s.get("type") == "NAKED_LONG":
                        s["score"] = max(0, s.get("score", 0) - 15)
                        s["vol_penalty"] = "elevated_vix_naked"
                # Re-sort
                strategies.sort(key=lambda x: x.get("score", 0), reverse=True)
        except Exception:
            pass

        best = strategies[0]

        # Check for dual-strategy opportunity
        if conviction_score >= 88 and len(strategies) >= 2:
            second = strategies[1]
            if second["score"] >= 70:
                if best["type"] != second["type"]:
                    combined_cost = best["total_cost"] + second["total_cost"]
                    if combined_cost <= max_cost * 1.3:
                        dual = self._create_dual(best, second, symbol)
                        if dual:
                            strategies.insert(0, dual)
                            best = dual

        alts = [
            f"{s['type']}:{s['score']:.0f}"
            for s in strategies[1:4]
        ]
        logger.info(
            f"Strategy: {best['type']} for {symbol} "
            f"(score:{best['score']:.0f} "
            f"EM:${exp_move:.1f}) | Alt: {alts}"
        )

        return best

    def _create_dual(self, primary, secondary, symbol):
        """Combine two strategies into a dual position."""
        total_cost = primary["total_cost"] + secondary["total_cost"]
        all_contracts = list(primary.get("contracts", []))
        all_contracts.extend(secondary.get("contracts", []))

        return {
            "type": "DUAL_STRATEGY",
            "score": (primary["score"] * 0.6 + secondary["score"] * 0.4),
            "direction": primary["direction"],
            "primary": primary,
            "secondary": secondary,
            "contracts": all_contracts,
            "net_debit": primary.get("net_debit", 0) + secondary.get("net_debit", 0),
            "max_loss": primary.get("max_loss", 0),
            "max_profit": primary.get("max_profit", "combined"),
            "total_cost": total_cost,
            "qty": primary.get("qty", 1),
            "description": (
                f"DUAL: {primary['type'].replace('_', ' ')} "
                f"+ {secondary['type'].replace('_', ' ')}"
            ),
        }

    # ─── IV ANALYSIS ───

    def _get_atm_iv(self, chain, direction):
        exp_map = chain.get(
            "callExpDateMap" if direction == "CALL" else "putExpDateMap", {}
        )
        ivs = []
        for ek, strikes in exp_map.items():
            try:
                dte = int(ek.split(":")[1])
            except (IndexError, ValueError):
                continue
            if not (20 <= dte <= 45):
                continue
            for sk, contracts in strikes.items():
                for c in (contracts if isinstance(contracts, list) else [contracts]):
                    delta = abs(c.get("delta", 0))
                    iv = c.get("volatility", 0)
                    if 0.40 <= delta <= 0.60 and iv > 0:
                        ivs.append(iv)
        return sum(ivs) / len(ivs) if ivs else 35

    def _get_iv_skew(self, chain, direction):
        exp_map = chain.get(
            "callExpDateMap" if direction == "CALL" else "putExpDateMap", {}
        )
        atm_iv = []
        otm_iv = []
        for ek, strikes in exp_map.items():
            try:
                dte = int(ek.split(":")[1])
            except (IndexError, ValueError):
                continue
            if not (20 <= dte <= 45):
                continue
            for sk, contracts in strikes.items():
                for c in (contracts if isinstance(contracts, list) else [contracts]):
                    delta = abs(c.get("delta", 0))
                    iv = c.get("volatility", 0)
                    if iv <= 0:
                        continue
                    if 0.40 <= delta <= 0.60:
                        atm_iv.append(iv)
                    elif 0.15 <= delta <= 0.30:
                        otm_iv.append(iv)
        if atm_iv and otm_iv:
            return (sum(otm_iv)/len(otm_iv)) - (sum(atm_iv)/len(atm_iv))
        return 0

    def _get_term_structure(self, chain, direction):
        exp_map = chain.get(
            "callExpDateMap" if direction == "CALL" else "putExpDateMap", {}
        )
        near_iv = []
        far_iv = []
        for ek, strikes in exp_map.items():
            try:
                dte = int(ek.split(":")[1])
            except (IndexError, ValueError):
                continue
            for sk, contracts in strikes.items():
                for c in (contracts if isinstance(contracts, list) else [contracts]):
                    delta = abs(c.get("delta", 0))
                    iv = c.get("volatility", 0)
                    if 0.40 <= delta <= 0.60 and iv > 0:
                        if 7 <= dte <= 21:
                            near_iv.append(iv)
                        elif 30 <= dte <= 55:
                            far_iv.append(iv)
        near = sum(near_iv) / len(near_iv) if near_iv else 0
        far = sum(far_iv) / len(far_iv) if far_iv else 0
        return near - far

    # ─── STRATEGY 1: NAKED LONG ───

    def _score_naked(self, direction, atm_iv, iv_rank, conviction,
                     price, support, resistance, max_cost, chain):
        contract = self._find_best_single(chain, direction, max_cost, price)
        if not contract:
            return None

        score = 50
        if iv_rank < 25:
            score += 25
        elif iv_rank < 40:
            score += 15
        elif iv_rank > 60:
            score -= 20
        elif iv_rank > 50:
            score -= 10

        if conviction >= 90:
            score += 15
        elif conviction >= 80:
            score += 8

        if direction == "CALL":
            rr = (resistance - price) / max(price - support, 0.01)
        else:
            rr = (price - support) / max(resistance - price, 0.01)
        if rr > 3:
            score += 10
        elif rr > 2:
            score += 5

        return {
            "type": "NAKED_LONG",
            "score": min(score, 100),
            "direction": direction,
            "contracts": [contract],
            "net_debit": contract["mid"],
            "max_loss": contract["mid"],
            "max_profit": "unlimited",
            "breakeven": contract["strike"] + contract["mid"] if direction == "CALL" else contract["strike"] - contract["mid"],
            "qty": contract["qty"],
            "total_cost": contract["total_cost"],
            "description": f"Long {contract['strike']} {direction}",
        }

    # ─── STRATEGY 2: DEBIT SPREAD (with expected move targeting) ───

    def _score_debit_spread(self, direction, atm_iv, iv_rank, conviction,
                            price, support, resistance, max_cost, chain,
                            exp_move=0):
        spread = self._find_best_debit_spread(
            chain, direction, price, max_cost, exp_move
        )
        if not spread:
            return None

        score = 50
        if 40 <= iv_rank <= 70:
            score += 20
        elif iv_rank > 70:
            score += 10
        elif iv_rank < 25:
            score -= 10

        if spread["net_debit"] < spread["spread_width"] * 0.5:
            score += 15
        elif spread["net_debit"] < spread["spread_width"] * 0.65:
            score += 8

        profit_ratio = spread["max_profit"] / max(spread["net_debit"], 0.01)
        if profit_ratio > 1.5:
            score += 10
        elif profit_ratio > 1.0:
            score += 5

        if 75 <= conviction <= 95:
            score += 10

        return {
            "type": "DEBIT_SPREAD",
            "score": min(score, 100),
            "direction": direction,
            "contracts": spread["contracts"],
            "net_debit": spread["net_debit"],
            "max_loss": spread["net_debit"],
            "max_profit": spread["max_profit"],
            "breakeven": spread["breakeven"],
            "qty": spread["qty"],
            "total_cost": spread["total_cost"],
            "spread_width": spread["spread_width"],
            "description": spread["description"],
        }

    # ─── STRATEGY 3: CREDIT SPREAD ───

    def _score_credit_spread(self, direction, atm_iv, iv_rank, conviction,
                             price, support, resistance, max_cost, chain):
        spread = self._find_best_credit_spread(
            chain, direction, price, support, resistance, max_cost
        )
        if not spread:
            return None

        score = 50
        if iv_rank > 60:
            score += 25
        elif iv_rank > 45:
            score += 15
        elif iv_rank < 30:
            score -= 15

        if direction == "CALL":
            dist = (price - support) / price
        else:
            dist = (resistance - price) / price
        if dist > 0.05:
            score += 15
        elif dist > 0.03:
            score += 8

        if spread["net_credit"] > spread["spread_width"] * 0.35:
            score += 10
        elif spread["net_credit"] > spread["spread_width"] * 0.25:
            score += 5

        return {
            "type": "CREDIT_SPREAD",
            "score": min(score, 100),
            "direction": direction,
            "contracts": spread["contracts"],
            "net_credit": spread["net_credit"],
            "max_loss": spread["max_loss"],
            "max_profit": spread["net_credit"],
            "breakeven": spread["breakeven"],
            "qty": spread["qty"],
            "total_cost": spread["collateral"],
            "spread_width": spread["spread_width"],
            "description": spread["description"],
        }

    # ─── STRATEGY 4: CALENDAR SPREAD ───

    def _score_calendar(self, direction, atm_iv, iv_rank, term_structure,
                        price, days_to_catalyst, max_cost, chain):
        cal = self._find_best_calendar(chain, direction, price, max_cost)
        if not cal:
            return None

        score = 40
        if term_structure > 5:
            score += 25
        elif term_structure > 2:
            score += 15
        elif term_structure < -2:
            score -= 15

        if 3 <= days_to_catalyst <= 10:
            score += 15
        elif days_to_catalyst < 3:
            score -= 10

        if 40 <= iv_rank <= 70:
            score += 10

        return {
            "type": "CALENDAR_SPREAD",
            "score": min(score, 100),
            "direction": direction,
            "contracts": cal["contracts"],
            "net_debit": cal["net_debit"],
            "max_loss": cal["net_debit"],
            "max_profit": "varies",
            "breakeven": "varies",
            "qty": cal["qty"],
            "total_cost": cal["total_cost"],
            "description": cal["description"],
        }

    # ─── STRATEGY 5: BROKEN WING BUTTERFLY ───

    def _score_bwb(self, direction, atm_iv, iv_rank, conviction,
                   price, support, resistance, max_cost, chain, exp_move):
        """
        BWB: Directional butterfly with asymmetric wings.
        Zero risk on the favorable side, defined risk opposite.
        Can be entered for a credit in high IV.
        """
        bwb = self._find_best_bwb(chain, direction, price, max_cost, exp_move)
        if not bwb:
            return None

        score = 45

        # BWB thrives in high IV
        if iv_rank > 60:
            score += 25
        elif iv_rank > 45:
            score += 15
        elif iv_rank < 30:
            score -= 15

        # Credit entry is a huge bonus
        if bwb.get("net_credit", 0) > 0:
            score += 15

        # Good with moderate conviction
        if 78 <= conviction <= 92:
            score += 10

        # Works well near support/resistance
        if direction == "CALL" and price > support * 1.02:
            score += 5
        elif direction == "PUT" and price < resistance * 0.98:
            score += 5

        return {
            "type": "BROKEN_WING_BUTTERFLY",
            "score": min(score, 100),
            "direction": direction,
            "contracts": bwb["contracts"],
            "net_debit": bwb.get("net_debit", 0),
            "net_credit": bwb.get("net_credit", 0),
            "max_loss": bwb["max_loss"],
            "max_profit": bwb["max_profit"],
            "breakeven": bwb.get("breakeven", 0),
            "qty": bwb["qty"],
            "total_cost": bwb["total_cost"],
            "description": bwb["description"],
        }

    # ──────────────────────────────────────
    # CONTRACT FINDERS
    # ──────────────────────────────────────

    def _find_best_single(self, chain, direction, max_cost, price):
        exp_map = chain.get(
            "callExpDateMap" if direction == "CALL" else "putExpDateMap", {}
        )
        best = None
        best_score = -999

        for ek, strikes in exp_map.items():
            try:
                dte = int(ek.split(":")[1])
            except (IndexError, ValueError):
                continue
            if not (14 <= dte <= 55):
                continue
            for sk, contracts in strikes.items():
                for c in (contracts if isinstance(contracts, list) else [contracts]):
                    delta = abs(c.get("delta", 0))
                    bid = c.get("bid", 0)
                    ask = c.get("ask", 0)
                    oi = c.get("openInterest", 0)
                    vol = c.get("totalVolume", 0)
                    iv = c.get("volatility", 0)

                    if not (0.40 <= delta <= 0.65):
                        continue
                    if bid <= 0 or ask <= 0:
                        continue
                    mid = (bid + ask) / 2
                    spread = (ask - bid) / mid if mid > 0 else 1
                    if spread > 0.15 or oi < 50:
                        continue
                    cost = mid * 100
                    if cost > max_cost or cost < 30:
                        continue

                    score = 0
                    score += (1 - abs(delta - 0.55) * 5) * 25
                    score += (1 - abs(dte - 30) / 30) * 20
                    score += max(0, (0.15 - spread) * 80)
                    if vol >= 500:
                        score += 10

                    if score > best_score:
                        best_score = score
                        qty = min(int(max_cost / cost), 3)  # Max 3 contracts
                        if qty < 1:
                            continue
                        try:
                            strike_val = float(sk)
                        except ValueError:
                            continue
                        best = {
                            "symbol": c.get("symbol", ""),
                            "desc": c.get("description", ""),
                            "strike": strike_val,
                            "dte": dte,
                            "delta": round(delta, 3),
                            "iv": round(iv, 1),
                            "bid": bid, "ask": ask,
                            "mid": round(mid, 2),
                            "spread_pct": round(spread, 4),
                            "qty": qty,
                            "total_cost": round(qty * cost, 2),
                            "oi": oi, "volume": vol,
                            "leg": "LONG",
                        }
        return best

    def _get_options_at_exp(self, chain, opt_type, dte_min, dte_max):
        """Helper to get all options at a given expiration range."""
        exp_map = chain.get(
            "callExpDateMap" if opt_type == "CALL" else "putExpDateMap", {}
        )
        by_exp = {}
        for ek, strikes in exp_map.items():
            try:
                dte = int(ek.split(":")[1])
            except (IndexError, ValueError):
                continue
            if not (dte_min <= dte <= dte_max):
                continue
            opts = []
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
                    opts.append({
                        "symbol": c.get("symbol", ""),
                        "desc": c.get("description", ""),
                        "strike": strike,
                        "delta": abs(c.get("delta", 0)),
                        "bid": bid, "ask": ask,
                        "mid": round((bid+ask)/2, 2),
                        "iv": c.get("volatility", 0),
                        "oi": c.get("openInterest", 0),
                        "dte": dte,
                    })
            if opts:
                opts.sort(key=lambda x: x["strike"])
                by_exp[dte] = opts
        return by_exp

    def _find_best_debit_spread(self, chain, direction, price, max_cost, exp_move=0):
        opt_type = "CALL" if direction == "CALL" else "PUT"
        by_exp = self._get_options_at_exp(chain, opt_type, 21, 50)

        best = None
        best_score = -999

        for dte, options in by_exp.items():
            if len(options) < 2:
                continue

            # Target short strike at expected move
            if direction == "CALL":
                target_short = price + exp_move if exp_move > 0 else price * 1.08
            else:
                target_short = price - exp_move if exp_move > 0 else price * 0.92

            for i in range(len(options)):
                for j in range(i+1, min(i+6, len(options))):
                    if direction == "CALL":
                        long_leg = options[i]
                        short_leg = options[j]
                        if not (0.35 <= long_leg["delta"] <= 0.65):
                            continue
                        if not (0.15 <= short_leg["delta"] <= 0.50):
                            continue
                    else:
                        long_leg = options[j]
                        short_leg = options[i]
                        if not (0.35 <= long_leg["delta"] <= 0.65):
                            continue
                        if not (0.15 <= short_leg["delta"] <= 0.50):
                            continue

                    width = abs(long_leg["strike"] - short_leg["strike"])
                    if width < 1 or width > price * 0.12:
                        continue

                    net_debit = long_leg["ask"] - short_leg["bid"]
                    if net_debit <= 0:
                        continue
                    max_profit = width - net_debit
                    if max_profit <= 0:
                        continue

                    cost_per = net_debit * 100
                    if cost_per > max_cost or cost_per < 20:
                        continue
                    qty = min(int(max_cost / cost_per), 3)  # Max 3 contracts
                    if qty < 1:
                        continue

                    score = 0
                    profit_ratio = max_profit / net_debit
                    score += min(profit_ratio * 20, 40)
                    score += (1 - abs(long_leg["delta"] - 0.50) * 5) * 15
                    score += (1 - abs(dte - 35) / 30) * 15

                    # Bonus for short strike near expected move
                    if exp_move > 0:
                        dist = abs(short_leg["strike"] - target_short) / price
                        if dist < 0.02:
                            score += 15
                        elif dist < 0.05:
                            score += 8

                    if net_debit < width * 0.50:
                        score += 10
                    if long_leg["oi"] >= 200 and short_leg["oi"] >= 200:
                        score += 5

                    if score > best_score:
                        best_score = score
                        if direction == "CALL":
                            be = long_leg["strike"] + net_debit
                            desc = f"Bull Call {long_leg['strike']}/{short_leg['strike']}"
                        else:
                            be = long_leg["strike"] - net_debit
                            desc = f"Bear Put {short_leg['strike']}/{long_leg['strike']}"

                        best = {
                            "contracts": [
                                {**long_leg, "leg": "LONG", "qty": qty},
                                {**short_leg, "leg": "SHORT", "qty": qty},
                            ],
                            "net_debit": round(net_debit, 2),
                            "max_profit": round(max_profit, 2),
                            "spread_width": round(width, 2),
                            "breakeven": round(be, 2),
                            "qty": qty,
                            "total_cost": round(qty * cost_per, 2),
                            "description": desc,
                        }
        return best

    def _find_best_credit_spread(self, chain, direction, price,
                                 support, resistance, max_cost):
        if direction == "CALL":
            exp_map = self._get_options_at_exp(chain, "PUT", 14, 45)
        else:
            exp_map = self._get_options_at_exp(chain, "CALL", 14, 45)

        best = None
        best_score = -999

        for dte, options in exp_map.items():
            if len(options) < 2:
                continue

            for i in range(len(options)):
                for j in range(i+1, min(i+5, len(options))):
                    if direction == "CALL":
                        short_leg = options[j]
                        long_leg = options[i]
                        if not (0.20 <= short_leg["delta"] <= 0.40):
                            continue
                        if short_leg["strike"] > support:
                            continue
                    else:
                        short_leg = options[i]
                        long_leg = options[j]
                        if not (0.20 <= short_leg["delta"] <= 0.40):
                            continue
                        if short_leg["strike"] < resistance:
                            continue

                    width = abs(short_leg["strike"] - long_leg["strike"])
                    if width < 1 or width > price * 0.08:
                        continue

                    net_credit = short_leg["bid"] - long_leg["ask"]
                    if net_credit <= 0.10:
                        continue

                    max_loss = width - net_credit
                    collateral = max_loss * 100
                    if collateral > max_cost or collateral < 20:
                        continue
                    qty = min(int(max_cost / collateral), 3)  # Max 3 contracts
                    if qty < 1:
                        continue

                    score = 0
                    score += min((net_credit / width) * 60, 35)
                    score += (1 - abs(dte - 30) / 25) * 15
                    if direction == "CALL":
                        buffer = (price - short_leg["strike"]) / price
                    else:
                        buffer = (short_leg["strike"] - price) / price
                    if buffer > 0.05:
                        score += 20
                    elif buffer > 0.03:
                        score += 10
                    if short_leg["oi"] >= 200:
                        score += 5

                    if score > best_score:
                        best_score = score
                        if direction == "CALL":
                            be = short_leg["strike"] - net_credit
                            desc = f"Bull Put {long_leg['strike']}/{short_leg['strike']} cr${net_credit:.2f}"
                        else:
                            be = short_leg["strike"] + net_credit
                            desc = f"Bear Call {short_leg['strike']}/{long_leg['strike']} cr${net_credit:.2f}"

                        best = {
                            "contracts": [
                                {**short_leg, "leg": "SHORT", "qty": qty},
                                {**long_leg, "leg": "LONG", "qty": qty},
                            ],
                            "net_credit": round(net_credit, 2),
                            "max_loss": round(max_loss, 2),
                            "spread_width": round(width, 2),
                            "breakeven": round(be, 2),
                            "qty": qty,
                            "collateral": round(qty * collateral, 2),
                            "description": desc,
                        }
        return best

    def _find_best_calendar(self, chain, direction, price, max_cost):
        opt_type = "CALL" if direction == "CALL" else "PUT"
        near = self._get_options_at_exp(chain, opt_type, 10, 25)
        far = self._get_options_at_exp(chain, opt_type, 30, 55)

        if not near or not far:
            return None

        best = None
        best_score = -999

        for nd, near_opts in near.items():
            for fd, far_opts in far.items():
                near_strikes = {o["strike"]: o for o in near_opts}
                far_strikes = {o["strike"]: o for o in far_opts}
                common = set(near_strikes.keys()) & set(far_strikes.keys())

                for strike in common:
                    nc = near_strikes[strike]
                    fc = far_strikes[strike]
                    if not (0.35 <= fc["delta"] <= 0.65):
                        continue

                    net_debit = fc["ask"] - nc["bid"]
                    if net_debit <= 0 or net_debit > max_cost / 100:
                        continue
                    cost = net_debit * 100
                    qty = min(int(max_cost / cost), 3)  # Max 3 contracts
                    if qty < 1:
                        continue

                    score = 0
                    score += (1 - abs(fc["delta"] - 0.50) * 5) * 25
                    iv_diff = nc.get("iv", 0) - fc.get("iv", 0)
                    if iv_diff > 5:
                        score += 25
                    elif iv_diff > 2:
                        score += 15
                    elif iv_diff < 0:
                        score -= 15

                    if score > best_score:
                        best_score = score
                        best = {
                            "contracts": [
                                {**fc, "leg": "LONG", "qty": qty},
                                {**nc, "leg": "SHORT", "qty": qty},
                            ],
                            "net_debit": round(net_debit, 2),
                            "qty": qty,
                            "total_cost": round(qty * cost, 2),
                            "description": f"Calendar {strike} ({nd}d/{fd}d)",
                        }
        return best

    def _find_best_bwb(self, chain, direction, price, max_cost, exp_move):
        """
        Broken Wing Butterfly:
        CALL direction (bullish):
          Buy 1 lower call, Sell 2 middle calls, Buy 1 higher call (wider wing)
        PUT direction (bearish):
          Buy 1 higher put, Sell 2 middle puts, Buy 1 lower put (wider wing)
        """
        opt_type = "CALL" if direction == "CALL" else "PUT"
        by_exp = self._get_options_at_exp(chain, opt_type, 21, 45)

        best = None
        best_score = -999

        for dte, options in by_exp.items():
            if len(options) < 4:
                continue

            # Target body at expected move
            if direction == "CALL":
                target_body = price + exp_move * 0.7 if exp_move > 0 else price * 1.05
            else:
                target_body = price - exp_move * 0.7 if exp_move > 0 else price * 0.95

            for body_idx in range(1, len(options) - 1):
                body = options[body_idx]
                if body["oi"] < 50:
                    continue

                # Find narrow wing (closer to body)
                for narrow_idx in range(max(0, body_idx - 4), body_idx):
                    if direction == "CALL":
                        narrow = options[narrow_idx]  # lower strike
                    else:
                        narrow = options[min(body_idx + 1 + (body_idx - narrow_idx), len(options) - 1)]

                    narrow_width = abs(body["strike"] - narrow["strike"])
                    if narrow_width < 2 or narrow_width > price * 0.06:
                        continue

                    # Find wide wing (further from body, "broken")
                    wide_width = narrow_width * 2  # 2x as wide
                    if direction == "CALL":
                        target_wide_strike = body["strike"] + wide_width
                        wide = None
                        for o in options:
                            if abs(o["strike"] - target_wide_strike) < narrow_width * 0.3:
                                wide = o
                                break
                    else:
                        target_wide_strike = body["strike"] - wide_width
                        wide = None
                        for o in options:
                            if abs(o["strike"] - target_wide_strike) < narrow_width * 0.3:
                                wide = o
                                break

                    if not wide or wide["oi"] < 20:
                        continue

                    actual_wide_width = abs(body["strike"] - wide["strike"])

                    # Calculate BWB pricing
                    if direction == "CALL":
                        # Buy 1 narrow (lower), Sell 2 body, Buy 1 wide (higher)
                        cost = narrow["ask"] - 2 * body["bid"] + wide["ask"]
                    else:
                        # Buy 1 narrow (higher), Sell 2 body, Buy 1 wide (lower)
                        cost = narrow["ask"] - 2 * body["bid"] + wide["ask"]

                    # Credit or small debit?
                    is_credit = cost < 0
                    net = abs(cost)

                    if not is_credit and net * 100 > max_cost:
                        continue

                    # Max profit at body strike
                    max_profit = narrow_width - net if not is_credit else narrow_width + net
                    # Max loss on the broken side
                    max_loss = actual_wide_width - narrow_width
                    if is_credit:
                        max_loss = max_loss - net
                    else:
                        max_loss = max_loss + net

                    if max_loss <= 0 or max_profit <= 0:
                        continue

                    total_cost = max_loss * 100 if not is_credit else max_loss * 100
                    if total_cost > max_cost:
                        continue

                    qty = max(1, int(max_cost / total_cost))

                    # Score
                    score = 0
                    dist_to_target = abs(body["strike"] - target_body) / price
                    if dist_to_target < 0.02:
                        score += 20
                    elif dist_to_target < 0.05:
                        score += 10

                    profit_ratio = max_profit / max(max_loss, 0.01)
                    score += min(profit_ratio * 15, 30)

                    if is_credit:
                        score += 15

                    if body["oi"] >= 500:
                        score += 5

                    if score > best_score:
                        best_score = score
                        if direction == "CALL":
                            desc = f"BWB Call {narrow['strike']}/{body['strike']}x2/{wide['strike']}"
                        else:
                            desc = f"BWB Put {wide['strike']}/{body['strike']}x2/{narrow['strike']}"

                        contracts = [
                            {**narrow, "leg": "LONG", "qty": qty},
                            {**body, "leg": "SHORT", "qty": qty * 2},
                            {**wide, "leg": "LONG", "qty": qty},
                        ]

                        best = {
                            "contracts": contracts,
                            "net_debit": round(net, 2) if not is_credit else 0,
                            "net_credit": round(net, 2) if is_credit else 0,
                            "max_profit": round(max_profit, 2),
                            "max_loss": round(max_loss, 2),
                            "breakeven": round(body["strike"], 2),
                            "qty": qty,
                            "total_cost": round(qty * total_cost, 2),
                            "is_credit": is_credit,
                            "narrow_width": round(narrow_width, 2),
                            "wide_width": round(actual_wide_width, 2),
                            "description": desc,
                        }
        return best