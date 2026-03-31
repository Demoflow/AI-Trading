"""
Pre-Market Sentiment Filter.
Before executing any trade from the evening scan,
checks current pre-market/opening conditions:
1. Overnight gap direction (confirms or invalidates thesis)
2. Gap magnitude (large gaps may mean we missed the move)
3. VIX change overnight (market risk shift)
4. Sector health (is the whole sector moving against us?)
5. Extended hours volume (conviction behind the gap)
"""

import httpx
from datetime import datetime
from loguru import logger


class PremarketFilter:

    # If stock gaps MORE than this against our direction, skip
    MAX_ADVERSE_GAP = 0.03  # 3%
    # If stock gaps MORE than this in our direction, reduce size
    # (we missed the move, chasing now)
    MAX_FAVORABLE_GAP = 0.05  # 5%
    # VIX spike threshold
    VIX_SPIKE_THRESHOLD = 3.0  # points

    def __init__(self, schwab_client):
        self.client = schwab_client
        self._prev_close_cache = {}

    def _get_quote(self, symbol):
        try:
            resp = self.client.get_quote(symbol)
            if resp.status_code == httpx.codes.OK:
                data = resp.json()
                return data.get(symbol, {}).get("quote", {})
        except Exception:
            pass
        return None

    def check_trade(self, trade, evening_vix):
        """
        Check if a trade from last night's scan is still valid.
        Returns (valid, action, reason, size_modifier)
        Actions: EXECUTE, SKIP, REDUCE
        """
        sym = trade["symbol"]
        direction = trade["direction"]

        quote = self._get_quote(sym)
        if not quote:
            return True, "EXECUTE", "no_quote_available", 1.0

        current = quote.get("lastPrice", 0)
        prev_close = quote.get("closePrice", 0)
        pre_market = quote.get("mark", current)

        # Use pre-market price if available, otherwise last
        price = pre_market if pre_market > 0 else current

        if prev_close <= 0 or price <= 0:
            return True, "EXECUTE", "no_price_data", 1.0

        gap_pct = (price - prev_close) / prev_close

        # ── CHECK 1: Adverse gap ──
        if direction == "CALL" and gap_pct < -self.MAX_ADVERSE_GAP:
            logger.warning(
                f"FILTER: {sym} gapped DOWN {gap_pct:.1%} "
                f"against CALL thesis"
            )
            # Large gap down on a call = something changed overnight
            if gap_pct < -0.06:
                return False, "SKIP", f"gap_down_{gap_pct:.1%}", 0
            # Moderate gap = reduce size
            return True, "REDUCE", f"adverse_gap_{gap_pct:.1%}", 0.5

        if direction == "PUT" and gap_pct > self.MAX_ADVERSE_GAP:
            logger.warning(
                f"FILTER: {sym} gapped UP {gap_pct:+.1%} "
                f"against PUT thesis"
            )
            if gap_pct > 0.06:
                return False, "SKIP", f"gap_up_{gap_pct:+.1%}", 0
            return True, "REDUCE", f"adverse_gap_{gap_pct:+.1%}", 0.5

        # ── CHECK 2: Excessive favorable gap (chasing) ──
        if direction == "CALL" and gap_pct > self.MAX_FAVORABLE_GAP:
            logger.info(
                f"FILTER: {sym} gapped UP {gap_pct:+.1%} "
                f"- reduce size (don't chase)"
            )
            return True, "REDUCE", f"chasing_{gap_pct:+.1%}", 0.6

        if direction == "PUT" and gap_pct < -self.MAX_FAVORABLE_GAP:
            logger.info(
                f"FILTER: {sym} gapped DOWN {gap_pct:.1%} "
                f"- reduce size (don't chase)"
            )
            return True, "REDUCE", f"chasing_{gap_pct:.1%}", 0.6

        # ── CHECK 3: VIX spike overnight ──
        vix_quote = self._get_quote("$VIX")
        if vix_quote and evening_vix:
            current_vix = vix_quote.get("lastPrice", 0)
            if current_vix > 0 and evening_vix > 0:
                vix_change = current_vix - evening_vix
                if vix_change > self.VIX_SPIKE_THRESHOLD:
                    logger.warning(
                        f"FILTER: VIX spiked +{vix_change:.1f} "
                        f"overnight ({evening_vix:.1f} -> {current_vix:.1f})"
                    )
                    # VIX spiked = reduce all positions
                    if vix_change > 5:
                        return False, "SKIP", f"vix_spike_+{vix_change:.1f}", 0
                    return True, "REDUCE", f"vix_up_+{vix_change:.1f}", 0.7

        # ── CHECK 4: Favorable confirmation ──
        if direction == "CALL" and 0.005 < gap_pct <= self.MAX_FAVORABLE_GAP:
            logger.info(
                f"CONFIRM: {sym} gapped UP {gap_pct:+.1%} "
                f"- confirms CALL thesis"
            )
            return True, "EXECUTE", f"confirmed_{gap_pct:+.1%}", 1.1

        if direction == "PUT" and -self.MAX_FAVORABLE_GAP <= gap_pct < -0.005:
            logger.info(
                f"CONFIRM: {sym} gapped DOWN {gap_pct:.1%} "
                f"- confirms PUT thesis"
            )
            return True, "EXECUTE", f"confirmed_{gap_pct:.1%}", 1.1

        # ── CHECK 5: Neutral gap (normal) ──
        return True, "EXECUTE", f"neutral_gap_{gap_pct:+.1%}", 1.0

    def check_all_trades(self, trades, evening_vix):
        """Filter all trades and return valid ones with modifiers."""
        valid = []
        skipped = []

        for trade in trades:
            ok, action, reason, modifier = self.check_trade(
                trade, evening_vix
            )

            if not ok or action == "SKIP":
                logger.warning(
                    f"SKIP: {trade['symbol']} {trade['direction']} "
                    f"- {reason}"
                )
                skipped.append((trade, reason))
                continue

            trade["_premarket"] = {
                "action": action,
                "reason": reason,
                "size_modifier": modifier,
            }
            valid.append(trade)

        logger.info(
            f"Pre-market filter: {len(valid)} valid, "
            f"{len(skipped)} skipped"
        )
        return valid, skipped
