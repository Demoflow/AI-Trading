"""
GTC Bracket Stop Order Manager.
Places stop-loss orders at the broker level on every entry.
These execute at Schwab even if our monitoring script crashes.
"""
import time
from loguru import logger


class BracketStopManager:

    # Stop loss percentages by strategy type
    STOP_PCT = {
        "NAKED_LONG": 0.40,       # -40% stop on naked longs
        "DEBIT_SPREAD": 0.50,     # -50% stop on debit spreads
        "RISK_REVERSAL": 0.50,
        "DIAGONAL_SPREAD": 0.40,
        "RATIO_BACKSPREAD": 0.60,
    }

    def __init__(self, client):
        self.client = client

    def place_stop(self, option_symbol, qty, entry_price, strategy_type, account_hash):
        """
        Place a GTC stop-loss order at the broker level.
        For naked longs: sell_to_close at stop price.
        """
        stop_pct = self.STOP_PCT.get(strategy_type, 0.40)
        stop_price = round(entry_price * (1 - stop_pct), 2)

        if stop_price <= 0.05:
            stop_price = 0.05  # Minimum stop

        try:
            from schwab.orders.options import option_sell_to_close_limit
            order = option_sell_to_close_limit(
                option_symbol, qty, str(stop_price)
            )
            # Modify to GTC duration
            order_dict = order
            # The schwab-py library builds order objects - we need to set GTC
            # For now, use the OrderBuilder approach
            from schwab.orders.generic import OrderBuilder
            from schwab.orders.common import Duration, Session, OrderType, OrderStrategyType, OptionInstruction

            stop_order = (OrderBuilder()
                .set_order_type(OrderType.STOP_LIMIT)
                .set_price(str(stop_price))
                .set_stop_price(str(stop_price))
                .set_session(Session.NORMAL)
                .set_duration(Duration.GOOD_TILL_CANCEL)
                .set_order_strategy_type(OrderStrategyType.SINGLE)
                .add_option_leg(OptionInstruction.SELL_TO_CLOSE, option_symbol, qty)
                .build())

            resp = self.client.place_order(account_hash, stop_order)
            if resp.status_code in (200, 201):
                # Extract order ID for tracking
                order_id = None
                try:
                    location = resp.headers.get("Location", "")
                    if location:
                        order_id = location.split("/")[-1]
                except Exception:
                    pass
                logger.info(f"GTC STOP PLACED: {option_symbol} stop=${stop_price} "
                           f"({stop_pct:.0%} below entry ${entry_price})")
                return {"status": "PLACED", "stop_price": stop_price, "order_id": order_id}
            else:
                logger.warning(f"GTC STOP FAILED: {option_symbol} status={resp.status_code}")
                return {"status": "FAILED", "code": resp.status_code}

        except Exception as e:
            logger.warning(f"GTC STOP ERROR: {option_symbol} - {e}")
            return {"status": "ERROR", "reason": str(e)}

    def cancel_stop(self, order_id, account_hash):
        """Cancel a GTC stop order (e.g., when position is closed normally)."""
        try:
            resp = self.client.cancel_order(order_id, account_hash)
            if resp.status_code in (200, 201):
                logger.info(f"GTC STOP CANCELLED: order {order_id}")
                return True
        except Exception as e:
            logger.warning(f"Cancel stop error: {e}")
        return False
