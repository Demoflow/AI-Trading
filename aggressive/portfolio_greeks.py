"""
Portfolio Greeks Aggregator.
Calculates net delta, vega, theta, gamma across all positions.
Warns when portfolio-level exposure exceeds thresholds.
"""
import time
from loguru import logger


class PortfolioGreeks:

    # Thresholds
    MAX_NET_DELTA = 500      # Max net delta exposure (equivalent to 500 shares)
    MAX_NET_VEGA = 1000      # Max vega exposure ($1000 per 1% IV move)
    MAX_THETA_DAILY = 200    # Max daily theta decay ($200/day)

    def __init__(self, client):
        self.client = client

    def _get_option_greeks(self, symbol):
        """Fetch Greeks for a single option."""
        try:
            time.sleep(0.05)
            r = self.client.get_quote(symbol)
            if r.status_code == 200:
                q = r.json().get(symbol, {}).get("quote", {})
                return {
                    "delta": q.get("delta", 0),
                    "gamma": q.get("gamma", 0),
                    "theta": q.get("theta", 0),
                    "vega": q.get("vega", 0),
                    "iv": q.get("volatility", 0),
                }
        except Exception:
            pass
        return None

    def calculate(self, positions):
        """
        Calculate portfolio-level Greeks from all open positions.
        positions: list of {symbol, qty, direction, strategy_type}
        Returns: portfolio Greeks summary
        """
        net_delta = 0
        net_gamma = 0
        net_theta = 0
        net_vega = 0
        position_greeks = []

        for pos in positions:
            sym = pos.get("symbol", "")
            qty = pos.get("qty", 1)
            direction = pos.get("direction", "LONG")

            greeks = self._get_option_greeks(sym)
            if not greeks:
                continue

            # Multiply by quantity and contract multiplier
            multiplier = qty * 100
            if direction in ("SHORT", "SELL"):
                multiplier = -multiplier

            pos_delta = greeks["delta"] * multiplier
            pos_gamma = greeks["gamma"] * multiplier
            pos_theta = greeks["theta"] * multiplier
            pos_vega = greeks["vega"] * multiplier

            net_delta += pos_delta
            net_gamma += pos_gamma
            net_theta += pos_theta
            net_vega += pos_vega

            position_greeks.append({
                "symbol": sym,
                "delta": round(pos_delta, 1),
                "gamma": round(pos_gamma, 2),
                "theta": round(pos_theta, 2),
                "vega": round(pos_vega, 2),
            })

        return {
            "net_delta": round(net_delta, 1),
            "net_gamma": round(net_gamma, 2),
            "net_theta": round(net_theta, 2),
            "net_vega": round(net_vega, 2),
            "positions": position_greeks,
            "warnings": self._check_warnings(net_delta, net_vega, net_theta),
        }

    def _check_warnings(self, delta, vega, theta):
        """Check if portfolio Greeks exceed thresholds."""
        warnings = []

        if abs(delta) > self.MAX_NET_DELTA:
            warnings.append(f"HIGH_DELTA: net delta {delta:+.0f} exceeds ±{self.MAX_NET_DELTA}")

        if abs(vega) > self.MAX_NET_VEGA:
            warnings.append(f"HIGH_VEGA: net vega {vega:+.0f} — portfolio loses ${abs(vega):.0f} per 1% IV drop")

        if abs(theta) > self.MAX_THETA_DAILY:
            warnings.append(f"HIGH_THETA: losing ${abs(theta):.0f}/day to time decay")

        return warnings

    def log_summary(self, positions):
        """Calculate and log portfolio Greeks."""
        result = self.calculate(positions)

        logger.info(f"PORTFOLIO GREEKS: "
                    f"delta={result['net_delta']:+.0f} "
                    f"gamma={result['net_gamma']:+.1f} "
                    f"theta={result['net_theta']:+.1f}/day "
                    f"vega={result['net_vega']:+.1f}")

        for w in result["warnings"]:
            logger.warning(f"  GREEKS WARNING: {w}")

        return result
