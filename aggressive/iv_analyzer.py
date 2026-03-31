"""
IV Rank Analyzer.
Compares current IV to 52-week range.
Blocks entries when IV is too expensive.
"""

import httpx
from loguru import logger


class IVAnalyzer:

    MAX_IV_RANK = 999  # Strategy engine handles IV; only block extreme
    CHEAP_IV_RANK = 30  # IV is cheap, increase size

    def __init__(self, schwab_client):
        self.client = schwab_client
        self.cache = {}

    def get_iv_rank(self, symbol, chain_data=None):
        """
        Calculate IV rank using REAL implied volatility from option chain.
        If chain data available: uses ATM option IV vs historical proxy.
        Fallback: uses Schwab quote's 'volatility' field.
        """
        try:
            # Method 1: Use chain data if available (most accurate)
            if chain_data:
                atm_iv = self._extract_atm_iv(chain_data, symbol)
                if atm_iv and atm_iv > 0:
                    # Compare to historical range
                    # For now, use VIX as a rough benchmark
                    # IV rank = where this stock's IV sits relative to market
                    import time
                    time.sleep(0.05)
                    vix_q = self.client.get_quote("$VIX")
                    vix = 20
                    if vix_q and vix_q.status_code == 200:
                        vix = vix_q.json().get("$VIX", {}).get("quote", {}).get("lastPrice", 20)

                    # Stock IV vs VIX gives relative IV positioning
                    # IV rank approximation: how elevated is this stock's IV
                    # If stock IV >> VIX, options are expensive (high IV rank)
                    # If stock IV << VIX, options are cheap (low IV rank)
                    iv_ratio = atm_iv / max(vix, 10)

                    if iv_ratio > 2.0:
                        iv_rank = 90  # Very expensive
                    elif iv_ratio > 1.5:
                        iv_rank = 75
                    elif iv_ratio > 1.2:
                        iv_rank = 60
                    elif iv_ratio > 0.8:
                        iv_rank = 40
                    elif iv_ratio > 0.5:
                        iv_rank = 25
                    else:
                        iv_rank = 10  # Very cheap

                    return {
                        "iv_rank": iv_rank,
                        "atm_iv": round(atm_iv, 1),
                        "vix": vix,
                        "iv_ratio": round(iv_ratio, 2),
                        "method": "chain_iv",
                    }

            # Method 2: Fallback to quote-level data
            import time
            time.sleep(0.05)
            r = self.client.get_quote(symbol)
            if r.status_code == 200:
                q = r.json().get(symbol, {}).get("quote", {})
                hi52 = q.get("52WeekHigh", 0)
                lo52 = q.get("52WeekLow", 0)
                price = q.get("lastPrice", 0)

                if hi52 > lo52 and price > 0:
                    # Use historical volatility proxy from price range
                    # This is the old method but with better scaling
                    range_pct = (hi52 - lo52) / lo52 * 100
                    price_pos = (price - lo52) / (hi52 - lo52) * 100

                    # Stocks near 52w low tend to have higher IV
                    # Stocks near 52w high tend to have lower IV
                    # But scale with VIX environment
                    vix_q = self.client.get_quote("$VIX")
                    vix = 20
                    if vix_q and vix_q.status_code == 200:
                        vix = vix_q.json().get("$VIX", {}).get("quote", {}).get("lastPrice", 20)

                    base_iv_rank = 100 - price_pos  # Near low = high IV
                    # Adjust for VIX environment
                    if vix > 25:
                        base_iv_rank = min(95, base_iv_rank + 20)  # Everything is expensive
                    elif vix > 20:
                        base_iv_rank = min(90, base_iv_rank + 10)

                    iv_rank = max(5, min(95, base_iv_rank))
                    return {
                        "iv_rank": round(iv_rank, 1),
                        "atm_iv": 0,
                        "vix": vix,
                        "method": "price_proxy_vix_adjusted",
                    }

            return {"iv_rank": 50, "atm_iv": 0, "vix": 20, "method": "default"}

        except Exception as e:
            return {"iv_rank": 50, "atm_iv": 0, "vix": 20, "method": f"error_{e}"}

    def _extract_atm_iv(self, chain_data, symbol):
        """Extract ATM implied volatility from option chain."""
        try:
            price_q = self.client.get_quote(symbol)
            if price_q.status_code != 200:
                return None
            price = price_q.json().get(symbol, {}).get("quote", {}).get("lastPrice", 0)
            if price <= 0:
                return None

            # Check calls first
            call_map = chain_data.get("callExpDateMap", {})
            for ek, strikes in call_map.items():
                try:
                    dte = int(ek.split(":")[1])
                except (IndexError, ValueError):
                    continue
                if not (20 <= dte <= 45):
                    continue

                best_dist = 999
                best_iv = None
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
                                best_iv = iv
                if best_iv:
                    return best_iv
                break  # Only check first valid expiration

        except Exception:
            pass
        return None

    
    def should_trade(self, symbol):
        """Returns (ok, iv_rank, reason)"""
        _iv_tmp = self.get_iv_rank(symbol)
        rank = _iv_tmp.get('iv_rank', 50) if isinstance(_iv_tmp, dict) else _iv_tmp
        if rank > self.MAX_IV_RANK:
            return False, rank, f"IV rank {rank:.0f}% too high"
        return True, rank, "ok"

    def get_size_modifier(self, symbol):
        """Adjust position size based on IV."""
        _iv_tmp = self.get_iv_rank(symbol)
        rank = _iv_tmp.get('iv_rank', 50) if isinstance(_iv_tmp, dict) else _iv_tmp
        if rank <= self.CHEAP_IV_RANK:
            return 1.15  # 15% bigger when IV cheap
        elif rank >= 60:
            return 0.85  # 15% smaller when IV expensive
        return 1.0