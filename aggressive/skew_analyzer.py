"""
Put/Call Skew Analyzer.
Measures implied volatility skew to detect institutional fear/greed.
Negative skew (puts expensive) = market pricing downside.
Positive skew (calls expensive) = market pricing upside.
"""
from loguru import logger


class SkewAnalyzer:

    def analyze(self, chain_data, price):
        """
        Returns skew info:
        - skew_pct: negative = puts expensive, positive = calls expensive
        - direction_bias: CALL, PUT, or NEUTRAL based on skew
        - confidence_boost: +5 to +15 if trade aligns with skew
        """
        if not chain_data:
            return {"skew_pct": 0, "direction_bias": "NEUTRAL", "confidence_boost": 0}

        call_map = chain_data.get("callExpDateMap", {})
        put_map = chain_data.get("putExpDateMap", {})

        atm_call_iv = None
        atm_put_iv = None

        # Find ATM options (closest to current price) in 20-40 DTE range
        for ek, strikes in call_map.items():
            try:
                dte = int(ek.split(":")[1])
            except (IndexError, ValueError):
                continue
            if not (20 <= dte <= 40):
                continue
            best_dist = 999
            for sk, contracts in strikes.items():
                try:
                    strike = float(sk)
                except ValueError:
                    continue
                dist = abs(strike - price)
                if dist < best_dist:
                    best_dist = dist
                    for c in (contracts if isinstance(contracts, list) else [contracts]):
                        iv = c.get("volatility", 0)
                        if iv > 0:
                            atm_call_iv = iv
            break  # Use first valid expiration

        for ek, strikes in put_map.items():
            try:
                dte = int(ek.split(":")[1])
            except (IndexError, ValueError):
                continue
            if not (20 <= dte <= 40):
                continue
            best_dist = 999
            for sk, contracts in strikes.items():
                try:
                    strike = float(sk)
                except ValueError:
                    continue
                dist = abs(strike - price)
                if dist < best_dist:
                    best_dist = dist
                    for c in (contracts if isinstance(contracts, list) else [contracts]):
                        iv = c.get("volatility", 0)
                        if iv > 0:
                            atm_put_iv = iv
            break

        if not atm_call_iv or not atm_put_iv:
            return {"skew_pct": 0, "direction_bias": "NEUTRAL", "confidence_boost": 0}

        # Skew = (put_iv - call_iv) / avg_iv * 100
        avg_iv = (atm_call_iv + atm_put_iv) / 2
        skew_pct = ((atm_put_iv - atm_call_iv) / avg_iv) * 100

        if skew_pct > 10:
            # Puts expensive - fear, favor puts
            bias = "PUT"
            boost = min(15, int(skew_pct))
        elif skew_pct < -10:
            # Calls expensive - greed, favor calls
            bias = "CALL"
            boost = min(15, int(abs(skew_pct)))
        else:
            bias = "NEUTRAL"
            boost = 0

        return {
            "skew_pct": round(skew_pct, 1),
            "direction_bias": bias,
            "confidence_boost": boost,
        }
