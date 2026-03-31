"""
IV Rank Analyzer.
Compares current IV to 52-week range.
Blocks entries when IV is too expensive.
"""

import httpx
from loguru import logger


class IVAnalyzer:

    MAX_IV_RANK = 70  # Don't buy when IV above 70th percentile
    CHEAP_IV_RANK = 30  # IV is cheap, increase size

    def __init__(self, schwab_client):
        self.client = schwab_client
        self.cache = {}

    def get_iv_rank(self, symbol):
        """Get IV rank (0-100) for a symbol."""
        if symbol in self.cache:
            return self.cache[symbol]

        try:
            from schwab.client import Client
            resp = self.client.get_option_chain(
                symbol,
                contract_type=Client.Options.ContractType.ALL,
                strike_count=5,
                include_underlying_quote=True,
                strategy=Client.Options.Strategy.SINGLE,
            )
            if resp.status_code != httpx.codes.OK:
                return 50

            chain = resp.json()
            volatility = chain.get("volatility", 0)

            # Get underlying stats for IV context
            underlying = chain.get("underlying", {})
            hi52 = underlying.get("fiftyTwoWeekHigh", 0)
            lo52 = underlying.get("fiftyTwoWeekLow", 0)
            price = chain.get("underlyingPrice", 0)

            # Estimate IV rank from chain data
            # Use the ATM call IV as current IV
            current_iv = volatility
            call_map = chain.get("callExpDateMap", {})

            atm_ivs = []
            for exp_key, strikes in call_map.items():
                try:
                    dte = int(exp_key.split(":")[1])
                except (IndexError, ValueError):
                    continue
                if not (20 <= dte <= 45):
                    continue
                for sk, contracts in strikes.items():
                    for c in (contracts if isinstance(contracts, list) else [contracts]):
                        delta = abs(c.get("delta", 0))
                        iv = c.get("volatility", 0)
                        if 0.40 <= delta <= 0.60 and iv > 0:
                            atm_ivs.append(iv)

            if not atm_ivs:
                self.cache[symbol] = 50
                return 50

            current_iv = sum(atm_ivs) / len(atm_ivs)

            # Estimate IV rank using price range as proxy
            # Stocks near 52w high tend to have lower IV
            # Stocks that dropped hard tend to have higher IV
            if hi52 > 0 and lo52 > 0 and price > 0:
                price_rank = (price - lo52) / (hi52 - lo52) * 100
                # IV tends inversely with price rank
                iv_rank = max(0, min(100, 100 - price_rank + (current_iv - 30)))
            else:
                iv_rank = min(100, current_iv)

            iv_rank = max(0, min(100, iv_rank))
            self.cache[symbol] = round(iv_rank, 1)
            return round(iv_rank, 1)

        except Exception as e:
            logger.debug(f"IV rank error {symbol}: {e}")
            return 50

    def should_trade(self, symbol):
        """Returns (ok, iv_rank, reason)"""
        rank = self.get_iv_rank(symbol)
        if rank > self.MAX_IV_RANK:
            return False, rank, f"IV rank {rank:.0f}% too high"
        return True, rank, "ok"

    def get_size_modifier(self, symbol):
        """Adjust position size based on IV."""
        rank = self.get_iv_rank(symbol)
        if rank <= self.CHEAP_IV_RANK:
            return 1.15  # 15% bigger when IV cheap
        elif rank >= 60:
            return 0.85  # 15% smaller when IV expensive
        return 1.0
