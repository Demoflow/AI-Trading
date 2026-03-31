"""
Order Fill Confirmation Tracker.
Stores order IDs from place_order() responses.
Checks fill status on each monitoring cycle.
Handles partial fills and stale order cancellation.
"""
import json
import time
from datetime import datetime, timedelta
from loguru import logger

ORDER_TRACKER_PATH = "config/order_tracker.json"
STALE_ORDER_MINUTES = 10  # Cancel unfilled orders after 10 min


class OrderFillTracker:

    def __init__(self, client):
        self.client = client
        self._load()

    def _load(self):
        try:
            self.orders = json.load(open(ORDER_TRACKER_PATH))
        except (FileNotFoundError, json.JSONDecodeError):
            self.orders = {"pending": [], "filled": [], "cancelled": []}

    def _save(self):
        json.dump(self.orders, open(ORDER_TRACKER_PATH, "w"), indent=2)

    def record_submission(self, order_response, symbol, strategy_type, account_hash):
        """Record an order submission for tracking."""
        # Extract order ID from response headers
        order_id = None
        try:
            if hasattr(order_response, 'headers'):
                location = order_response.headers.get("Location", "")
                if location:
                    order_id = location.split("/")[-1]
        except Exception:
            pass

        entry = {
            "order_id": order_id,
            "symbol": symbol,
            "strategy_type": strategy_type,
            "account_hash": account_hash,
            "submitted_at": datetime.now().isoformat(),
            "status": "PENDING",
            "fill_qty": 0,
            "fill_price": 0,
        }
        self.orders["pending"].append(entry)
        self._save()
        logger.info(f"ORDER TRACKED: {symbol} {strategy_type} id={order_id}")
        return order_id

    def check_fills(self, account_hash):
        """
        Check all pending orders for fill status.
        Returns list of newly filled orders.
        """
        newly_filled = []
        still_pending = []

        for order in self.orders.get("pending", []):
            oid = order.get("order_id")
            if not oid:
                # No order ID - can't track, assume filled
                order["status"] = "ASSUMED_FILLED"
                self.orders["filled"].append(order)
                newly_filled.append(order)
                continue

            try:
                resp = self.client.get_order(oid, account_hash)
                if resp.status_code == 200:
                    data = resp.json()
                    status = data.get("status", "UNKNOWN")

                    if status == "FILLED":
                        order["status"] = "FILLED"
                        order["fill_qty"] = data.get("filledQuantity", 0)
                        order["fill_price"] = data.get("price", 0)
                        order["filled_at"] = datetime.now().isoformat()
                        self.orders["filled"].append(order)
                        newly_filled.append(order)
                        logger.info(f"ORDER FILLED: {order['symbol']} qty={order['fill_qty']} "
                                   f"price=${order['fill_price']}")

                    elif status in ("CANCELED", "REJECTED", "EXPIRED"):
                        order["status"] = status
                        self.orders["cancelled"].append(order)
                        logger.warning(f"ORDER {status}: {order['symbol']}")

                    elif status in ("QUEUED", "WORKING", "PENDING_ACTIVATION", "ACCEPTED"):
                        # Check if stale
                        submitted = datetime.fromisoformat(order["submitted_at"])
                        age_minutes = (datetime.now() - submitted).total_seconds() / 60

                        if age_minutes > STALE_ORDER_MINUTES:
                            # Cancel stale order
                            try:
                                self.client.cancel_order(oid, account_hash)
                                order["status"] = "CANCELLED_STALE"
                                self.orders["cancelled"].append(order)
                                logger.warning(f"CANCELLED STALE ORDER: {order['symbol']} "
                                             f"({age_minutes:.0f} min old)")
                            except Exception:
                                still_pending.append(order)
                        else:
                            still_pending.append(order)
                    else:
                        still_pending.append(order)
                else:
                    still_pending.append(order)

            except Exception as e:
                logger.warning(f"Fill check error for {order['symbol']}: {e}")
                still_pending.append(order)

        self.orders["pending"] = still_pending
        self._save()
        return newly_filled

    def get_pending_count(self):
        return len(self.orders.get("pending", []))

    def get_stats(self):
        return {
            "pending": len(self.orders.get("pending", [])),
            "filled": len(self.orders.get("filled", [])),
            "cancelled": len(self.orders.get("cancelled", [])),
        }
