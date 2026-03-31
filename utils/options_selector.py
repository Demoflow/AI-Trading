"""
#13 - Options Contract Selection Logic.
Selects optimal strike, expiration, and quantity.
"""

from loguru import logger


class OptionsSelector:

    TARGET_DELTA_LOW = 0.45
    TARGET_DELTA_HIGH = 0.65
    MIN_DTE = 14
    MAX_DTE = 45
    MAX_SPREAD_PCT = 0.10

    def select_contract(self, chain_data, direction,
                         max_cost, underlying_price):
        """
        Find the best options contract.
        Returns contract details or None.
        """
        if not chain_data:
            return None

        if direction == "CALL":
            exp_map = chain_data.get("callExpDateMap", {})
        else:
            exp_map = chain_data.get("putExpDateMap", {})

        candidates = []

        for exp_key, strikes in exp_map.items():
            try:
                dte = int(exp_key.split(":")[1])
            except (IndexError, ValueError):
                continue

            if not (self.MIN_DTE <= dte <= self.MAX_DTE):
                continue

            for strike_key, contracts in strikes.items():
                for c in (
                    contracts if isinstance(contracts, list)
                    else [contracts]
                ):
                    delta = abs(c.get("delta", 0))
                    bid = c.get("bid", 0)
                    ask = c.get("ask", 0)
                    mid = (bid + ask) / 2
                    volume = c.get("totalVolume", 0)
                    oi = c.get("openInterest", 0)
                    iv = c.get("volatility", 0)

                    if not (
                        self.TARGET_DELTA_LOW
                        <= delta
                        <= self.TARGET_DELTA_HIGH
                    ):
                        continue

                    if bid <= 0 or ask <= 0:
                        continue

                    spread = (ask - bid) / mid if mid > 0 else 1
                    if spread > self.MAX_SPREAD_PCT:
                        continue

                    premium_per = mid * 100
                    if premium_per > max_cost:
                        continue
                    if premium_per < 50:
                        continue

                    contracts_can_buy = int(
                        max_cost / premium_per
                    )
                    if contracts_can_buy < 1:
                        continue

                    score = 0
                    ideal_delta = 0.55
                    score += (
                        1 - abs(delta - ideal_delta) * 5
                    ) * 30
                    ideal_dte = 30
                    score += (
                        1 - abs(dte - ideal_dte) / 30
                    ) * 20
                    score += min(spread * -100 + 10, 20)
                    if volume > 100:
                        score += 15
                    if oi > 500:
                        score += 15

                    try:
                        strike_val = float(strike_key)
                    except ValueError:
                        strike_val = 0

                    candidates.append({
                        "symbol": c.get("symbol", ""),
                        "description": c.get(
                            "description", ""
                        ),
                        "strike": strike_val,
                        "dte": dte,
                        "delta": round(delta, 3),
                        "bid": bid,
                        "ask": ask,
                        "mid": round(mid, 2),
                        "premium_per_contract": round(
                            premium_per, 2
                        ),
                        "spread_pct": round(spread, 4),
                        "volume": volume,
                        "open_interest": oi,
                        "iv": round(iv, 2),
                        "contracts": contracts_can_buy,
                        "total_cost": round(
                            contracts_can_buy * premium_per, 2
                        ),
                        "score": round(score, 1),
                    })

        if not candidates:
            logger.info(
                f"No suitable {direction} contracts found"
            )
            return None

        candidates.sort(
            key=lambda x: x["score"], reverse=True
        )
        best = candidates[0]

        logger.info(
            f"Selected: {best['description']} "
            f"strike ${best['strike']} "
            f"DTE {best['dte']} "
            f"delta {best['delta']} "
            f"${best['mid']:.2f} x{best['contracts']} "
            f"= ${best['total_cost']:.2f}"
        )
        return best
