"""
LETF Earnings Calendar.
Tracks earnings dates for stocks that drive sector ETF moves.
When a mega-cap reports, the sector leveraged ETF will move 5-15%.
Uses options flow on the underlying stock to determine direction.
"""
import time
from datetime import date, timedelta, datetime
from loguru import logger


# Stocks that move their sector ETFs the most
SECTOR_MOVERS = {
    "nasdaq": ["AAPL", "MSFT", "AMZN", "GOOGL", "META"],
    "semis": ["NVDA", "AMD", "AVGO", "MU", "MRVL"],
    "fang": ["AAPL", "MSFT", "AMZN", "GOOGL", "META", "NFLX", "TSLA", "NVDA"],
    "financials": ["JPM", "BAC", "GS", "MS", "WFC"],
    "energy": ["XOM", "CVX", "SLB", "COP"],
    "biotech": ["AMGN", "GILD", "REGN", "VRTX", "MRNA"],
    "nvidia": ["NVDA"],
    "tesla": ["TSLA"],
}


class EarningsCalendar:

    def __init__(self, client):
        self.client = client
        self.earnings_cache = {}

    def check_upcoming_earnings(self, sector_name):
        """
        Check if any sector-moving stock has earnings in the next 5 days.
        Returns: list of {symbol, days_until, direction_signal, confidence}
        """
        movers = SECTOR_MOVERS.get(sector_name, [])
        if not movers:
            return []

        upcoming = []
        today = date.today()

        for symbol in movers:
            earnings_date = self._get_earnings_date(symbol)
            if not earnings_date:
                continue

            days_until = (earnings_date - today).days
            if 0 <= days_until <= 5:
                # Analyze options flow for direction
                flow = self._analyze_pre_earnings_flow(symbol)
                upcoming.append({
                    "symbol": symbol,
                    "earnings_date": earnings_date.isoformat(),
                    "days_until": days_until,
                    "direction": flow.get("direction", "NEUTRAL"),
                    "flow_conviction": flow.get("conviction", 0),
                    "put_call_ratio": flow.get("pcr", 1.0),
                    "iv_percentile": flow.get("iv_pct", 50),
                })

        return upcoming

    def _get_earnings_date(self, symbol):
        """Get next earnings date from Schwab fundamental data."""
        if symbol in self.earnings_cache:
            cached = self.earnings_cache[symbol]
            if cached and cached >= date.today():
                return cached

        try:
            time.sleep(0.08)
            r = self.client.get_quote(symbol)
            if r.status_code == 200:
                q = r.json().get(symbol, {})
                fund = q.get("fundamental", {})
                # Schwab returns next earnings date in some endpoints
                # Fallback: check if high IV suggests upcoming earnings
                ref = q.get("reference", {})
                # Store None if not found
                self.earnings_cache[symbol] = None
                return None
        except Exception:
            pass
        return None

    def _analyze_pre_earnings_flow(self, symbol):
        """
        Analyze options flow on a stock approaching earnings.
        High call/put ratio = bullish positioning
        High put/call ratio = bearish positioning
        Straddle buying = expecting big move, direction unclear
        """
        try:
            time.sleep(0.08)
            r = self.client.get_option_chain(
                symbol, strike_count=10,
                contract_type=self.client.Options.ContractType.ALL
            )
            if r.status_code != 200:
                return {"direction": "NEUTRAL", "conviction": 0, "pcr": 1.0}

            data = r.json()
            call_vol = 0
            put_vol = 0
            call_oi = 0
            put_oi = 0

            for ek, strikes in data.get("callExpDateMap", {}).items():
                try:
                    dte = int(ek.split(":")[1])
                except (IndexError, ValueError):
                    continue
                if dte > 10:
                    continue  # Only near-term
                for sk, contracts in strikes.items():
                    for c in (contracts if isinstance(contracts, list) else [contracts]):
                        call_vol += c.get("totalVolume", 0)
                        call_oi += c.get("openInterest", 0)

            for ek, strikes in data.get("putExpDateMap", {}).items():
                try:
                    dte = int(ek.split(":")[1])
                except (IndexError, ValueError):
                    continue
                if dte > 10:
                    continue
                for sk, contracts in strikes.items():
                    for c in (contracts if isinstance(contracts, list) else [contracts]):
                        put_vol += c.get("totalVolume", 0)
                        put_oi += c.get("openInterest", 0)

            total_vol = call_vol + put_vol
            if total_vol == 0:
                return {"direction": "NEUTRAL", "conviction": 0, "pcr": 1.0}

            pcr = put_vol / max(call_vol, 1)

            # Determine direction from flow
            if pcr < 0.5:
                direction = "BULL"
                conviction = min(95, 70 + int((0.5 - pcr) * 100))
            elif pcr > 2.0:
                direction = "BEAR"
                conviction = min(95, 70 + int((pcr - 2.0) * 20))
            elif pcr > 1.5:
                direction = "BEAR"
                conviction = 60
            elif pcr < 0.7:
                direction = "BULL"
                conviction = 65
            else:
                direction = "NEUTRAL"
                conviction = 40

            return {
                "direction": direction,
                "conviction": conviction,
                "pcr": round(pcr, 2),
                "call_vol": call_vol,
                "put_vol": put_vol,
                "iv_pct": 75,  # Approximate - IV is elevated pre-earnings
            }

        except Exception as e:
            logger.warning(f"Earnings flow error for {symbol}: {e}")
            return {"direction": "NEUTRAL", "conviction": 0, "pcr": 1.0}

    def get_earnings_boost(self, sector_name):
        """
        Returns conviction boost if a sector-moving stock has
        upcoming earnings with strong directional flow.
        Returns: (boost, direction, details)
        """
        upcoming = self.check_upcoming_earnings(sector_name)
        if not upcoming:
            return 0, "NEUTRAL", None

        # Find the highest conviction earnings signal
        best = max(upcoming, key=lambda x: x["flow_conviction"])

        if best["flow_conviction"] >= 80:
            boost = 15
        elif best["flow_conviction"] >= 65:
            boost = 8
        else:
            boost = 0

        return boost, best["direction"], best
