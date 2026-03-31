"""
Pre-Market Gap Scanner.
Runs at 8:00 AM ET before market open.
Checks for overnight gaps on open positions.
"""

import os
import sys
from datetime import datetime
from loguru import logger


class PremarketScanner:

    GAP_WARN = -0.02
    GAP_CRIT = -0.03
    GAP_CATASTROPHIC = -0.05

    def scan(self, tracker, executor, notifier=None):
        """
        Check pre-market prices for all open positions.
        Returns list of gap alerts with recommended actions.
        """
        positions = tracker.get_open()
        if not positions:
            logger.info("No open positions for pre-market check")
            return []

        alerts = []
        for key, pos in positions.items():
            try:
                q = executor.get_current_quote(pos.symbol)
                pm_price = q.get("last") or q.get("bid") or 0
                if pm_price <= 0:
                    continue

                gap_pct = (
                    (pm_price - pos.entry_price)
                    / pos.entry_price
                )

                if pos.direction == "LONG":
                    pnl_pct = gap_pct
                else:
                    pnl_pct = -gap_pct

                alert = {
                    "key": key,
                    "symbol": pos.symbol,
                    "instrument": pos.instrument,
                    "prev_close": pos.entry_price,
                    "premarket": round(pm_price, 2),
                    "gap_pct": round(gap_pct, 4),
                    "pnl_pct": round(pnl_pct, 4),
                    "action": "HOLD",
                }

                if pnl_pct <= self.GAP_CATASTROPHIC:
                    alert["action"] = "SELL_AT_OPEN"
                    alert["priority"] = "CRITICAL"
                    logger.warning(
                        f"CATASTROPHIC GAP: "
                        f"{pos.symbol} {pnl_pct:+.1%}"
                    )
                elif pnl_pct <= self.GAP_CRIT:
                    alert["action"] = "SELL_AT_OPEN"
                    alert["priority"] = "HIGH"
                    logger.warning(
                        f"CRITICAL GAP: "
                        f"{pos.symbol} {pnl_pct:+.1%}"
                    )
                elif pnl_pct <= self.GAP_WARN:
                    alert["action"] = "TIGHTEN_STOP"
                    alert["priority"] = "MEDIUM"
                    logger.info(
                        f"Gap warning: "
                        f"{pos.symbol} {pnl_pct:+.1%}"
                    )
                else:
                    alert["priority"] = "LOW"

                alerts.append(alert)

            except Exception as e:
                logger.warning(
                    f"Pre-market check failed "
                    f"{pos.symbol}: {e}"
                )

        crits = [
            a for a in alerts
            if a["priority"] in ("CRITICAL", "HIGH")
        ]
        if crits and notifier:
            msg = "Pre-market gap alerts:\n"
            for a in crits:
                msg += (
                    f"  {a['symbol']}: {a['gap_pct']:+.1%} "
                    f"-> {a['action']}\n"
                )
            notifier.send(
                "CRITICAL",
                "PRE-MARKET GAP ALERT",
                msg,
            )

        sells = [
            a for a in alerts
            if a["action"] == "SELL_AT_OPEN"
        ]
        logger.info(
            f"Pre-market scan: {len(alerts)} positions, "
            f"{len(crits)} warnings, "
            f"{len(sells)} sell-at-open"
        )
        return alerts

    def execute_gap_exits(self, alerts, tracker,
                          executor, dt_tracker):
        for alert in alerts:
            if alert["action"] != "SELL_AT_OPEN":
                continue
            key = alert["key"]
            pos = tracker.positions.get(key)
            if not pos:
                continue
            pm = alert["premarket"]
            lp = round(pm * 0.998, 2)
            result = executor.submit_order(
                pos.symbol, "SELL",
                int(pos.current_quantity), lp
            )
            if result.get("status") in ("SUBMITTED", "FILLED"):
                is_dt = (
                    pos.entry_date
                    == str(datetime.now().date())
                )
                if is_dt:
                    dt_tracker.record(
                        pos.symbol, "gap_protection"
                    )
                tracker.close_position(
                    key, lp, "premarket_gap_exit"
                )
                logger.info(
                    f"GAP EXIT: {pos.symbol} "
                    f"@ ${lp:.2f}"
                )
