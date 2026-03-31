"""
Gamma Exposure (GEX) Analyzer.
Calculates dealer gamma positioning to identify:
- Pin levels (where price gets "stuck")
- Flip zones (where moves accelerate)
- Optimal strike targets for spreads/butterflies

When dealers are long gamma (positive GEX), they hedge
by selling into rallies and buying dips = stabilizing.
When dealers are short gamma (negative GEX), they hedge
by buying into rallies and selling dips = amplifying.

This is the single most important concept in modern
options market microstructure.
"""

from loguru import logger


class GEXAnalyzer:

    def __init__(self):
        self.cache = {}

    def analyze(self, symbol, chain_data, price):
        """
        Calculate GEX profile for a symbol.
        Returns:
            gex_profile: {
                net_gex: total gamma exposure,
                gex_by_strike: {strike: gex_value},
                max_gex_strike: highest positive GEX (pin level),
                flip_strike: where GEX flips from + to - ,
                regime: "POSITIVE" or "NEGATIVE",
                key_levels: [strike1, strike2, ...],
                suggested_target: best strike for spread short leg,
            }
        """
        if not chain_data or price <= 0:
            return None

        call_map = chain_data.get("callExpDateMap", {})
        put_map = chain_data.get("putExpDateMap", {})

        gex_by_strike = {}
        total_gex = 0

        # Process calls and puts for each strike
        for exp_key, strikes in call_map.items():
            try:
                dte = int(exp_key.split(":")[1])
            except (IndexError, ValueError):
                continue
            if not (1 <= dte <= 60):
                continue

            for sk, contracts in strikes.items():
                try:
                    strike = float(sk)
                except ValueError:
                    continue

                for c in (contracts if isinstance(contracts, list) else [contracts]):
                    oi = c.get("openInterest", 0)
                    gamma = c.get("gamma", 0)

                    if oi <= 0 or gamma <= 0:
                        continue

                    # Call GEX: dealers are short calls = long gamma
                    # GEX = gamma * OI * 100 * spot_price
                    # Positive because dealers buy stock as price rises
                    call_gex = gamma * oi * 100 * price**2 * 0.01 / 1e6

                    if strike not in gex_by_strike:
                        gex_by_strike[strike] = 0
                    gex_by_strike[strike] += call_gex
                    total_gex += call_gex

        for exp_key, strikes in put_map.items():
            try:
                dte = int(exp_key.split(":")[1])
            except (IndexError, ValueError):
                continue
            if not (1 <= dte <= 60):
                continue

            for sk, contracts in strikes.items():
                try:
                    strike = float(sk)
                except ValueError:
                    continue

                for c in (contracts if isinstance(contracts, list) else [contracts]):
                    oi = c.get("openInterest", 0)
                    gamma = c.get("gamma", 0)

                    if oi <= 0 or gamma <= 0:
                        continue

                    # Put GEX: dealers are short puts = short gamma
                    # Negative because dealers sell stock as price falls
                    put_gex = -gamma * oi * 100 * price**2 * 0.01 / 1e6

                    if strike not in gex_by_strike:
                        gex_by_strike[strike] = 0
                    gex_by_strike[strike] += put_gex
                    total_gex += put_gex

        if not gex_by_strike:
            return None

        # Find key levels
        sorted_strikes = sorted(gex_by_strike.items(), key=lambda x: x[0])

        # Max positive GEX = strongest pin level
        max_gex_strike = max(gex_by_strike, key=gex_by_strike.get)
        max_gex_value = gex_by_strike[max_gex_strike]

        # Find GEX flip zone (where it crosses from + to -)
        flip_strike = None
        prev_sign = None
        for strike, gex in sorted_strikes:
            current_sign = 1 if gex >= 0 else -1
            if prev_sign is not None and current_sign != prev_sign:
                # This is where GEX flips
                if abs(strike - price) < price * 0.10:
                    flip_strike = strike
            prev_sign = current_sign

        # Regime: is price above or below the flip zone?
        if total_gex > 0:
            regime = "POSITIVE"  # Stabilizing, mean-reverting
        else:
            regime = "NEGATIVE"  # Amplifying, trend-following

        # Key levels: top 5 strikes by absolute GEX
        key_levels = sorted(
            gex_by_strike.items(),
            key=lambda x: abs(x[1]),
            reverse=True,
        )[:5]

        # Suggested target for spread short leg:
        # In positive GEX, target the max GEX strike (pin level)
        # In negative GEX, target the flip strike (breakout level)
        if regime == "POSITIVE" and max_gex_strike:
            suggested = max_gex_strike
        elif flip_strike:
            suggested = flip_strike
        else:
            suggested = max_gex_strike

        # Calculate call wall and put wall
        call_strikes = {
            s: g for s, g in gex_by_strike.items()
            if g > 0 and s > price
        }
        put_strikes = {
            s: g for s, g in gex_by_strike.items()
            if g < 0 and s < price
        }

        call_wall = max(call_strikes, key=call_strikes.get) if call_strikes else price * 1.05
        put_wall = min(put_strikes, key=lambda x: put_strikes[x]) if put_strikes else price * 0.95

        profile = {
            "net_gex": round(total_gex, 2),
            "regime": regime,
            "max_gex_strike": max_gex_strike,
            "max_gex_value": round(max_gex_value, 2),
            "flip_strike": flip_strike,
            "call_wall": round(call_wall, 2),
            "put_wall": round(put_wall, 2),
            "suggested_target": round(suggested, 2),
            "key_levels": [
                {"strike": round(s, 2), "gex": round(g, 2)}
                for s, g in key_levels
            ],
        }

        self.cache[symbol] = profile
        return profile

    def get_strike_recommendation(self, symbol, direction, price):
        """
        Use GEX to recommend optimal strike placement.
        For CALL spreads: short leg at call wall
        For PUT spreads: short leg at put wall
        For BWB: body at max GEX (pin level)
        """
        profile = self.cache.get(symbol)
        if not profile:
            return None

        if direction == "CALL":
            return {
                "short_target": profile["call_wall"],
                "pin_level": profile["max_gex_strike"],
                "regime": profile["regime"],
                "reason": (
                    f"Call wall at ${profile['call_wall']:.2f}, "
                    f"GEX {profile['regime']}"
                ),
            }
        else:
            return {
                "short_target": profile["put_wall"],
                "pin_level": profile["max_gex_strike"],
                "regime": profile["regime"],
                "reason": (
                    f"Put wall at ${profile['put_wall']:.2f}, "
                    f"GEX {profile['regime']}"
                ),
            }
