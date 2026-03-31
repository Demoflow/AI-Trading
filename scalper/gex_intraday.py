"""
Intraday GEX Analyzer for 0DTE Scalping.
Calculates dealer gamma exposure for SPY/QQQ
from live option chain data.
Determines: positive GEX (sell premium) vs
negative GEX (buy direction).
"""

import httpx
from loguru import logger


class IntradayGEX:

    def __init__(self, schwab_client):
        self.client = schwab_client
        self.cache = {}

    def analyze(self, symbol):
        """
        Calculate real-time GEX for SPY or QQQ.
        Returns gex_profile dict.
        """
        try:
            from schwab.client import Client
            resp = self.client.get_option_chain(
                symbol,
                contract_type=Client.Options.ContractType.ALL,
                strike_count=20,
                include_underlying_quote=True,
                strategy=Client.Options.Strategy.SINGLE,
            )
            if resp.status_code != httpx.codes.OK:
                return None
            chain = resp.json()
        except Exception:
            return None

        price = chain.get("underlyingPrice", 0)
        if price <= 0:
            return None

        call_map = chain.get("callExpDateMap", {})
        put_map = chain.get("putExpDateMap", {})

        gex_by_strike = {}
        total_gex = 0
        zero_dte_gex = 0

        for exp_key, strikes in call_map.items():
            try:
                dte = int(exp_key.split(":")[1])
            except (IndexError, ValueError):
                continue
            if dte > 5:
                continue

            for sk, contracts in strikes.items():
                for c in (contracts if isinstance(contracts, list) else [contracts]):
                    oi = c.get("openInterest", 0)
                    gamma = c.get("gamma", 0)
                    if oi <= 0 or gamma <= 0:
                        continue
                    call_gex = gamma * oi * 100 * price / 1e6
                    try:
                        strike = float(sk)
                    except ValueError:
                        continue
                    if strike not in gex_by_strike:
                        gex_by_strike[strike] = 0
                    gex_by_strike[strike] += call_gex
                    total_gex += call_gex
                    if dte <= 1:
                        zero_dte_gex += call_gex

        for exp_key, strikes in put_map.items():
            try:
                dte = int(exp_key.split(":")[1])
            except (IndexError, ValueError):
                continue
            if dte > 5:
                continue

            for sk, contracts in strikes.items():
                for c in (contracts if isinstance(contracts, list) else [contracts]):
                    oi = c.get("openInterest", 0)
                    gamma = c.get("gamma", 0)
                    if oi <= 0 or gamma <= 0:
                        continue
                    put_gex = -gamma * oi * 100 * price / 1e6
                    try:
                        strike = float(sk)
                    except ValueError:
                        continue
                    if strike not in gex_by_strike:
                        gex_by_strike[strike] = 0
                    gex_by_strike[strike] += put_gex
                    total_gex += put_gex
                    if dte <= 1:
                        zero_dte_gex += put_gex

        if not gex_by_strike:
            return None

        # Find key levels
        max_gex_strike = max(gex_by_strike, key=gex_by_strike.get)
        min_gex_strike = min(gex_by_strike, key=gex_by_strike.get)

        # Call wall = highest positive GEX above price
        call_wall = None
        put_wall = None
        for s in sorted(gex_by_strike.keys()):
            if s > price and gex_by_strike[s] > 0:
                if call_wall is None or gex_by_strike[s] > gex_by_strike.get(call_wall, 0):
                    call_wall = s
            if s < price and gex_by_strike[s] < 0:
                if put_wall is None or gex_by_strike[s] < gex_by_strike.get(put_wall, 0):
                    put_wall = s

        # GEX flip zone
        flip_strike = None
        sorted_s = sorted(gex_by_strike.items(), key=lambda x: x[0])
        prev_sign = None
        for strike, gex in sorted_s:
            cur_sign = 1 if gex >= 0 else -1
            if prev_sign is not None and cur_sign != prev_sign:
                if abs(strike - price) < price * 0.03:
                    flip_strike = strike
            prev_sign = cur_sign

        # Regime
        if total_gex > 0:
            regime = "POSITIVE"
        else:
            regime = "NEGATIVE"

        # Pin level = highest absolute GEX near current price
        near_strikes = {
            s: abs(g) for s, g in gex_by_strike.items()
            if abs(s - price) < price * 0.02
        }
        pin_level = max(near_strikes, key=near_strikes.get) if near_strikes else price

        # Highest OI strike for end-of-day pin detection
        max_oi_strike = max_gex_strike  # Proxy

        profile = {
            "net_gex": round(total_gex, 2),
            "zero_dte_gex": round(zero_dte_gex, 2),
            "regime": regime,
            "pin_level": round(pin_level, 2),
            "call_wall": round(call_wall, 2) if call_wall else round(price * 1.005, 2),
            "put_wall": round(put_wall, 2) if put_wall else round(price * 0.995, 2),
            "flip_strike": round(flip_strike, 2) if flip_strike else None,
            "max_oi_strike": round(max_oi_strike, 2),
            "strategy_bias": "SELL_PREMIUM" if regime == "POSITIVE" else "BUY_DIRECTION",
        }

        self.cache[symbol] = profile
        return profile
