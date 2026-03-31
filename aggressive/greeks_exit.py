"""
Greeks-Based Exit Triggers for Options.
Monitors option-specific data for exit decisions:
- Delta death: option losing directional sensitivity
- IV crush: premium evaporating
- Theta acceleration: time decay exceeding acceptable rate
- Gamma risk: near-expiry gamma explosion
"""
from loguru import logger


class GreeksExitMonitor:

    # Thresholds
    MIN_DELTA = 0.15          # Exit if delta drops below this
    IV_CRUSH_PCT = 0.20       # Exit if IV drops 20%+ from entry
    MAX_THETA_PCT = 0.05      # Exit if daily theta > 5% of position value
    GAMMA_WARNING_DTE = 5     # Warn when DTE < 5 (gamma risk)

    def __init__(self, client):
        self.client = client

    def check_greeks_exit(self, position, option_quote):
        """
        Check if Greeks warrant an exit.
        Returns: (should_exit, reason) or (False, "hold")
        """
        if not option_quote:
            return False, "no_greeks"

        delta = abs(option_quote.get("delta", 0.50))
        theta = abs(option_quote.get("theta", 0))
        gamma = abs(option_quote.get("gamma", 0))
        iv = option_quote.get("volatility", 0)
        dte = option_quote.get("daysToExpiration", 30)
        mid = (option_quote.get("bid", 0) + option_quote.get("ask", 0)) / 2

        entry_iv = position.get("entry_iv", iv)
        stype = position.get("strategy_type", "NAKED_LONG")

        # Only check Greeks exits for long options
        if stype not in ("NAKED_LONG",):
            return False, "spread_skip"

        # 1. DELTA DEATH: option losing directional sensitivity
        if delta < self.MIN_DELTA and dte > 7:
            return True, f"delta_death_{delta:.2f}"

        # 2. IV CRUSH: premium evaporating
        if entry_iv > 0:
            iv_change = (iv - entry_iv) / entry_iv
            if iv_change < -self.IV_CRUSH_PCT:
                return True, f"iv_crush_{iv_change:+.0%}"

        # 3. THETA ACCELERATION: time decay too fast
        if mid > 0 and theta > 0:
            theta_pct = theta / mid
            if theta_pct > self.MAX_THETA_PCT:
                return True, f"theta_accel_{theta_pct:.1%}/day"

        # 4. GAMMA RISK: near expiry, gamma explosion risk
        if dte <= self.GAMMA_WARNING_DTE and delta > 0.40:
            # Near ATM with few days left = high gamma risk
            return True, f"gamma_risk_dte{dte}"

        # 5. SPREAD WIDENING: liquidity drying up
        bid = option_quote.get("bid", 0)
        ask = option_quote.get("ask", 0)
        if bid > 0 and ask > 0:
            spread_pct = (ask - bid) / mid if mid > 0 else 1.0
            if spread_pct > 0.20 and dte < 10:
                return True, f"spread_wide_{spread_pct:.0%}"

        return False, "greeks_ok"

    def get_option_greeks(self, option_symbol):
        """Fetch current Greeks for an option."""
        try:
            r = self.client.get_quote(option_symbol)
            if r.status_code == 200:
                q = r.json().get(option_symbol, {}).get("quote", {})
                return {
                    "delta": q.get("delta", 0),
                    "theta": q.get("theta", 0),
                    "gamma": q.get("gamma", 0),
                    "vega": q.get("vega", 0),
                    "volatility": q.get("volatility", 0),
                    "bid": q.get("bidPrice", q.get("bid", 0)),
                    "ask": q.get("askPrice", q.get("ask", 0)),
                    "daysToExpiration": q.get("daysToExpiration", 30),
                }
        except Exception:
            pass
        return None
