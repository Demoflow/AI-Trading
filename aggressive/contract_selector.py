"""
Contract Selector - Optimized.
Pre-filters strikes near ATM for speed.
Better scoring with IV rank consideration.
"""

from loguru import logger


class ContractSelector:
    MIN_SPREAD_WIDTH = 5  # Minimum $5 wide spreads

    TARGET_DELTA_LOW = 0.40
    TARGET_DELTA_HIGH = 0.65
    IDEAL_DELTA = 0.55
    MIN_DTE = 14
    MAX_DTE = 55
    MAX_SPREAD_PCT = 0.15
    MIN_OI = 50
    MIN_VOLUME = 20

    def select(self, chain_data, direction, max_cost, underlying_price):
        if not chain_data or underlying_price <= 0:
            return None

        if direction == "CALL":
            exp_map = chain_data.get("callExpDateMap", {})
        else:
            exp_map = chain_data.get("putExpDateMap", {})

        # Pre-filter: only strikes within 15% of underlying
        strike_low = underlying_price * 0.85
        strike_high = underlying_price * 1.15

        candidates = []

        for exp_key, strikes in exp_map.items():
            try:
                dte = int(exp_key.split(":")[1])
            except (IndexError, ValueError):
                continue
            if not (self.MIN_DTE <= dte <= self.MAX_DTE):
                continue

            for sk, contracts in strikes.items():
                try:
                    strike_val = float(sk)
                except ValueError:
                    continue

                # Pre-filter by strike range
                if not (strike_low <= strike_val <= strike_high):
                    continue

                for c in (
                    contracts if isinstance(contracts, list)
                    else [contracts]
                ):
                    delta = abs(c.get("delta", 0))
                    bid = c.get("bid", 0)
                    ask = c.get("ask", 0)
                    vol = c.get("totalVolume", 0)
                    oi = c.get("openInterest", 0)
                    iv = c.get("volatility", 0)
                    gamma = c.get("gamma", 0)
                    theta = c.get("theta", 0)

                    if not (self.TARGET_DELTA_LOW <= delta <= self.TARGET_DELTA_HIGH):
                        continue
                    if bid <= 0 or ask <= 0:
                        continue

                    mid = (bid + ask) / 2
                    spread = (ask - bid) / mid if mid > 0 else 1
                    if spread > self.MAX_SPREAD_PCT:
                        continue

                    cost_per = mid * 100
                    if cost_per > max_cost or cost_per < 30:
                        continue
                    if oi < self.MIN_OI:
                        continue

                    qty = int(max_cost / cost_per)
                    if qty < 1:
                        continue

                    score = 0
                    score += (1 - abs(delta - self.IDEAL_DELTA) * 5) * 25
                    ideal_dte = 30
                    score += (1 - abs(dte - ideal_dte) / 30) * 20
                    score += max(0, (0.15 - spread) * 80) * 0.15
                    if vol >= 500:
                        score += 15
                    elif vol >= 100:
                        score += 8
                    if oi >= 1000:
                        score += 10
                    elif oi >= 500:
                        score += 5
                    # Prefer lower IV
                    if iv < 35:
                        score += 10
                    elif iv < 50:
                        score += 5
                    elif iv > 80:
                        score -= 10

                    candidates.append({
                        "symbol": c.get("symbol", ""),
                        "desc": c.get("description", ""),
                        "strike": strike_val,
                        "dte": dte,
                        "delta": round(delta, 3),
                        "gamma": round(gamma, 5),
                        "theta": round(theta, 3),
                        "iv": round(iv, 1),
                        "bid": bid,
                        "ask": ask,
                        "mid": round(mid, 2),
                        "spread_pct": round(spread, 4),
                        "cost_per": round(cost_per, 2),
                        "volume": vol,
                        "oi": oi,
                        "qty": qty,
                        "total_cost": round(qty * cost_per, 2),
                        "score": round(score, 1),
                    })

        if not candidates:
            return None

        candidates.sort(
            key=lambda x: x["score"], reverse=True
        )
        best = candidates[0]

        logger.info(
            f"Selected: {best['desc']} "
            f"${best['strike']} {best['dte']}DTE "
            f"d={best['delta']} ${best['mid']} "
            f"x{best['qty']}=${best['total_cost']}"
        )
        return best