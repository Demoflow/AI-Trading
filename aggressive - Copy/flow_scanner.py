"""
Flow Scanner v6 - Institutional Edge.
New capabilities:
1. Sweep detection (aggressive vs passive orders)
2. Trade side classification (bought at ask vs sold at bid)
3. Multi-leg detection (hedges vs directional bets)
4. Rate-limited API calls
5. Auth failure detection
"""

import time
import httpx
from datetime import datetime
from loguru import logger


class FlowScanner:

    MIN_PREMIUM = 10000
    API_DELAY = 0.5

    def __init__(self, schwab_client):
        self.client = schwab_client
        self._auth_ok = True

    def scan_universe(self, symbols):
        logger.info(f"Scanning flow for {len(symbols)} symbols")
        results = []
        errors = 0
        auth_errors = 0

        for i, sym in enumerate(symbols):
            if i > 0 and i % 25 == 0:
                logger.info(
                    f"  Progress: {i}/{len(symbols)} "
                    f"({len(results)} signals, {errors} errors)"
                )
            try:
                flow = self.analyze_flow(sym)
                if flow and flow["signal_strength"] >= 1:
                    results.append(flow)
            except Exception as e:
                err_str = str(e).lower()
                if any(k in err_str for k in ["token", "auth", "401", "refresh"]):
                    auth_errors += 1
                    if auth_errors >= 3:
                        logger.error(
                            "AUTH FAILED - Run: python scripts/authenticate_schwab.py"
                        )
                        self._auth_ok = False
                        return []
                else:
                    errors += 1
            time.sleep(self.API_DELAY)

        results.sort(key=lambda x: x["signal_strength"], reverse=True)
        logger.info(
            f"Flow scan complete: {len(results)} signals "
            f"({errors} errors, {auth_errors} auth)"
        )
        return results

    def analyze_flow(self, symbol):
        try:
            from schwab.client import Client
            resp = self.client.get_option_chain(
                symbol,
                contract_type=Client.Options.ContractType.ALL,
                strike_count=30,
                include_underlying_quote=True,
                strategy=Client.Options.Strategy.SINGLE,
            )
            if resp.status_code == 401:
                raise Exception("401 auth error")
            if resp.status_code != httpx.codes.OK:
                return None
            chain = resp.json()
        except Exception as e:
            raise e

        price = chain.get("underlyingPrice", 0)
        if price <= 0:
            return None

        call_map = chain.get("callExpDateMap", {})
        put_map = chain.get("putExpDateMap", {})

        call_vol = 0
        put_vol = 0
        hot_strikes = []
        opening_trades = 0
        closing_trades = 0

        # NEW: Sweep detection metrics
        sweep_signals = 0
        aggressive_buys = 0
        aggressive_sells = 0

        # NEW: Track per-expiration flow for multi-leg detection
        exp_call_vol = {}
        exp_put_vol = {}

        for opt_type, exp_map in [("CALL", call_map), ("PUT", put_map)]:
            for ek, strikes in exp_map.items():
                try:
                    dte = int(ek.split(":")[1])
                except (IndexError, ValueError):
                    continue
                if not (7 <= dte <= 60):
                    continue

                exp_key = dte
                if exp_key not in exp_call_vol:
                    exp_call_vol[exp_key] = 0
                if exp_key not in exp_put_vol:
                    exp_put_vol[exp_key] = 0

                for sk, contracts in strikes.items():
                    for c in (contracts if isinstance(contracts, list) else [contracts]):
                        vol = c.get("totalVolume", 0)
                        oi = c.get("openInterest", 0)
                        bid = c.get("bid", 0)
                        ask = c.get("ask", 0)
                        last = c.get("last", 0)
                        mid = (bid + ask) / 2
                        delta = abs(c.get("delta", 0))
                        iv = c.get("volatility", 0)

                        if opt_type == "CALL":
                            call_vol += vol
                            exp_call_vol[exp_key] = exp_call_vol.get(exp_key, 0) + vol
                        else:
                            put_vol += vol
                            exp_put_vol[exp_key] = exp_put_vol.get(exp_key, 0) + vol

                        # ── TRADE SIDE DETECTION ──
                        # If last trade is near ask = buyer initiated (aggressive buy)
                        # If last trade is near bid = seller initiated (aggressive sell)
                        if bid > 0 and ask > 0 and last > 0 and vol > 50:
                            spread = ask - bid
                            if spread > 0:
                                position_in_spread = (last - bid) / spread
                                if position_in_spread > 0.7:
                                    # Traded near ask = aggressive BUYER
                                    aggressive_buys += vol
                                elif position_in_spread < 0.3:
                                    # Traded near bid = aggressive SELLER
                                    aggressive_sells += vol

                        # Opening vs closing
                        if vol > 0 and oi > 0:
                            ratio = vol / oi
                            if ratio > 1.0:
                                opening_trades += vol
                            else:
                                closing_trades += vol

                            # ── SWEEP DETECTION ──
                            # Sweep characteristics:
                            # 1. Volume >> OI (new positions)
                            # 2. Traded at or above ask (urgency)
                            # 3. High volume in single contract
                            is_sweep = False
                            if ratio >= 3.0 and vol >= 500:
                                is_sweep = True
                            elif ratio >= 2.0 and vol >= 1000:
                                is_sweep = True
                            elif vol >= 2000 and last >= ask * 0.98:
                                is_sweep = True

                            if is_sweep:
                                sweep_signals += 1

                            if ratio >= 1.5 and vol >= 200:
                                premium = vol * mid * 100
                                if premium >= self.MIN_PREMIUM:
                                    is_opening = ratio > 1.5

                                    # Determine if bought or sold
                                    if bid > 0 and ask > 0 and last > 0:
                                        spread_w = ask - bid
                                        if spread_w > 0:
                                            pos = (last - bid) / spread_w
                                            trade_side = "BOUGHT" if pos > 0.6 else ("SOLD" if pos < 0.4 else "MID")
                                        else:
                                            trade_side = "UNKNOWN"
                                    else:
                                        trade_side = "UNKNOWN"

                                    hot_strikes.append({
                                        "type": opt_type,
                                        "strike": float(sk),
                                        "dte": dte,
                                        "volume": vol,
                                        "oi": oi,
                                        "vol_oi": round(ratio, 1),
                                        "mid": round(mid, 2),
                                        "delta": round(delta, 3),
                                        "iv": round(iv, 1),
                                        "premium": round(premium, 0),
                                        "likely_opening": is_opening,
                                        "is_sweep": is_sweep,
                                        "trade_side": trade_side,
                                    })

        if call_vol + put_vol == 0:
            return None

        cp_ratio = call_vol / max(put_vol, 1)
        opening_pct = opening_trades / max(opening_trades + closing_trades, 1)

        # ── MULTI-LEG DETECTION ──
        # If both calls and puts have high volume at same expiration,
        # likely a spread/hedge, not directional
        mixed_flow = False
        for exp in set(exp_call_vol.keys()) & set(exp_put_vol.keys()):
            cv = exp_call_vol.get(exp, 0)
            pv = exp_put_vol.get(exp, 0)
            if cv > 0 and pv > 0:
                ratio_at_exp = min(cv, pv) / max(cv, pv)
                if ratio_at_exp > 0.6:
                    # Nearly equal call and put volume = likely hedge/spread
                    mixed_flow = True

        # ── SIGNAL STRENGTH CALCULATION ──
        strength = 0
        direction = "NEUTRAL"

        # Directional bias from volume
        if cp_ratio > 2.5:
            strength += 2
            direction = "BULLISH"
        elif cp_ratio > 1.5:
            strength += 1
            direction = "BULLISH"
        elif cp_ratio < 0.4:
            strength += 2
            direction = "BEARISH"
        elif cp_ratio < 0.67:
            strength += 1
            direction = "BEARISH"

        # NEW: Aggressive buy/sell confirmation
        total_aggressive = aggressive_buys + aggressive_sells
        if total_aggressive > 0:
            buy_pct = aggressive_buys / total_aggressive
            if direction == "BULLISH" and buy_pct > 0.65:
                strength += 1  # Calls being BOUGHT aggressively
            elif direction == "BEARISH" and buy_pct < 0.35:
                strength += 1  # Puts being BOUGHT aggressively
            elif direction == "BULLISH" and buy_pct < 0.35:
                strength -= 1  # Calls being SOLD = bearish signal

        # Hot strikes bonus
        if hot_strikes:
            strength += 1
            top = max(hot_strikes, key=lambda x: x["premium"])
            if top["premium"] >= 50000:
                strength += 1
            if top["premium"] >= 200000:
                strength += 1
            if top.get("likely_opening"):
                strength += 1

        # NEW: Sweep bonus (highest conviction signal)
        if sweep_signals >= 3:
            strength += 2
        elif sweep_signals >= 1:
            strength += 1

        # Opening position bonus
        if opening_pct > 0.7:
            strength += 1

        # NEW: Mixed flow penalty (likely hedges)
        if mixed_flow:
            strength -= 1

        total_prem = sum(h["premium"] for h in hot_strikes)

        # Count sweeps and trade sides in hot strikes
        sweep_count = sum(1 for h in hot_strikes if h.get("is_sweep"))
        bought_count = sum(1 for h in hot_strikes if h.get("trade_side") == "BOUGHT")
        sold_count = sum(1 for h in hot_strikes if h.get("trade_side") == "SOLD")

        return {
            "symbol": symbol,
            "price": round(price, 2),
            "direction": direction,
            "signal_strength": max(0, strength),
            "call_volume": call_vol,
            "put_volume": put_vol,
            "cp_ratio": round(cp_ratio, 2),
            "total_premium": round(total_prem, 0),
            "opening_pct": round(opening_pct * 100, 1),
            "sweep_count": sweep_count,
            "aggressive_buy_pct": round(
                aggressive_buys / max(total_aggressive, 1) * 100, 1
            ),
            "bought_count": bought_count,
            "sold_count": sold_count,
            "mixed_flow": mixed_flow,
            "hot_strikes": sorted(
                hot_strikes, key=lambda x: x["premium"], reverse=True
            )[:5],
            "chain_data": chain,
            "timestamp": datetime.utcnow().isoformat(),
        }
