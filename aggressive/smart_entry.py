"""
Smart Entry Timing Engine v2.
Fixed option quote field names for Schwab API.
"""

import httpx
from datetime import datetime
from loguru import logger


class SmartEntry:

    VWAP_DISCOUNT = 0.005
    MAX_OPTION_SPREAD_PCT = 0.15
    MIN_VOLUME_CONFIRM = 100

    def __init__(self, schwab_client):
        self.client = schwab_client
        self.entry_attempts = {}

    def should_enter(self, symbol, direction, contract_sym):
        """Returns (should_buy, limit_price, reason)"""

        quote = self._get_quote(symbol)
        if not quote:
            return False, 0, "no_quote"

        price = quote.get("lastPrice", 0)
        bid = quote.get("bidPrice", price)
        ask = quote.get("askPrice", price)
        volume = quote.get("totalVolume", 0)
        avg_vol = quote.get("averageDailyVolume10Day", 1)

        if price <= 0:
            return False, 0, "no_price"

        # Track attempts
        if symbol not in self.entry_attempts:
            self.entry_attempts[symbol] = {
                "first_price": price,
                "high": price,
                "low": price,
                "attempts": 0,
                "start_time": datetime.now(),
            }

        tracker = self.entry_attempts[symbol]
        tracker["attempts"] += 1
        tracker["high"] = max(tracker["high"], price)
        tracker["low"] = min(tracker["low"], price)

        # Get option quote
        opt_quote = self._get_option_quote(contract_sym)
        if not opt_quote:
            # No option quote - after 3 attempts, enter at estimated price
            if tracker["attempts"] >= 3:
                return True, 0, "no_opt_quote_patience"
            return False, 0, "no_option_quote"

        opt_bid = opt_quote.get("bidPrice", opt_quote.get("bid", 0))
        opt_ask = opt_quote.get("askPrice", opt_quote.get("ask", 0))
        opt_mid = (opt_bid + opt_ask) / 2

        # Fallback to mark price
        if opt_mid <= 0:
            opt_mark = opt_quote.get("mark", 0)
            if opt_mark > 0:
                opt_bid = opt_mark * 0.95
                opt_ask = opt_mark * 1.05
                opt_mid = opt_mark

        if opt_mid <= 0:
            if tracker["attempts"] >= 3:
                return True, 0, "zero_price_patience"
            return False, 0, "zero_option_price"

        spread_pct = (opt_ask - opt_bid) / opt_mid if opt_mid > 0 else 1

        # ── ENTRY CONDITIONS ──

        # Condition 1: Option spread must be reasonable
        if spread_pct > self.MAX_OPTION_SPREAD_PCT:
            if tracker["attempts"] >= 5:
                limit = round(opt_mid, 2)
                return True, limit, "spread_wide_patience"
            return False, 0, f"spread_wide_{spread_pct:.0%}"

        # Condition 2: For CALLS, prefer buying on dips
        if direction == "CALL":
            intraday_range = tracker["high"] - tracker["low"]
            if intraday_range > 0:
                position_in_range = (price - tracker["low"]) / intraday_range
            else:
                position_in_range = 0.5

            if position_in_range <= 0.40:
                limit = round(opt_bid + (opt_ask - opt_bid) * 0.3, 2)
                return True, limit, "call_buying_dip"

            pullback_from_high = (tracker["high"] - price) / tracker["high"]
            if pullback_from_high >= 0.003:
                limit = round(opt_bid + (opt_ask - opt_bid) * 0.4, 2)
                return True, limit, "call_pullback"

        # Condition 3: For PUTS, prefer buying on rips
        if direction == "PUT":
            intraday_range = tracker["high"] - tracker["low"]
            if intraday_range > 0:
                position_in_range = (price - tracker["low"]) / intraday_range
            else:
                position_in_range = 0.5

            if position_in_range >= 0.60:
                limit = round(opt_bid + (opt_ask - opt_bid) * 0.3, 2)
                return True, limit, "put_buying_rip"

            bounce_from_low = (price - tracker["low"]) / tracker["low"] if tracker["low"] > 0 else 0
            if bounce_from_low >= 0.003:
                limit = round(opt_bid + (opt_ask - opt_bid) * 0.4, 2)
                return True, limit, "put_bounce"

        # Condition 4: Volume surge
        if avg_vol > 0 and volume > avg_vol * 1.5:
            limit = round(opt_bid + (opt_ask - opt_bid) * 0.5, 2)
            return True, limit, "volume_surge"

        # Condition 5: Patience expired - just enter
        if tracker["attempts"] >= 5:
            limit = round(opt_mid, 2)
            return True, limit, "patience_expired"

        return False, 0, "waiting_for_setup"

    def _get_quote(self, symbol):
        try:
            resp = self.client.get_quote(symbol)
            if resp.status_code == httpx.codes.OK:
                data = resp.json()
                return data.get(symbol, {}).get("quote", {})
        except Exception:
            pass
        return None

    def _get_option_quote(self, symbol):
        try:
            resp = self.client.get_quote(symbol)
            if resp.status_code == httpx.codes.OK:
                data = resp.json()
                return data.get(symbol, {}).get("quote", {})
        except Exception:
            pass
        return None

    def get_optimal_limit(self, contract_sym):
        q = self._get_option_quote(contract_sym)
        if not q:
            return None
        bid = q.get("bidPrice", q.get("bid", 0))
        ask = q.get("askPrice", q.get("ask", 0))
        if bid > 0 and ask > 0:
            return round(bid + (ask - bid) * 0.35, 2)
        mark = q.get("mark", 0)
        if mark > 0:
            return round(mark, 2)
        return None
