"""
Exit Engine - Updated.
#5: Targets based on stop distance (1.5x, 2.5x, 4x)
#6: Smart time stop (trail if profitable, close if not)
"""

from datetime import date, time
from loguru import logger


class ExitEngine:

    def __init__(self, tracker, dt_tracker):
        self.tracker = tracker
        self.dt_tracker = dt_tracker

    def evaluate_all(self, prices, spy_change=0,
                     premarket=False):
        exits = []
        positions = self.tracker.get_open()
        is_fri = date.today().weekday() == 4

        for key, pos in positions.items():
            sym = pos.symbol
            price = prices.get(sym)
            if price is None:
                continue
            pos.update_high_low(price, price)

            if premarket:
                gap = pos.unrealized_pnl_pct(price)
                if gap <= -0.03:
                    exits.append(self._build(
                        key, pos, price,
                        pos.current_quantity,
                        "gap_protection",
                    ))
                    continue

            ex = self._check_stop(key, pos, price)
            if ex:
                exits.append(ex)
                continue

            if pos.instrument == "ETF" and is_fri:
                exits.append(self._build(
                    key, pos, price,
                    pos.current_quantity,
                    "friday_etf_close",
                ))
                continue

            # Smart time stop (#6)
            ts = self._check_time_stop(key, pos, price)
            if ts:
                exits.append(ts)
                continue

            if spy_change <= -0.02 and pos.instrument == "STOCK":
                rq = int(pos.current_quantity * 0.5)
                if rq > 0:
                    exits.append(self._build(
                        key, pos, price, rq,
                        "spy_breaker",
                    ))
                continue

            tp = self._check_tp(key, pos, price)
            if tp:
                exits.append(tp)
                continue

            trl = self._check_trail(key, pos, price)
            if trl:
                exits.append(trl)

        return exits

    def _check_stop(self, key, pos, price):
        if pos.direction == "LONG" and price <= pos.stop_loss:
            return self._build(
                key, pos, price,
                pos.current_quantity, "hard_stop",
            )
        if pos.instrument in ("CALL", "PUT"):
            lp = abs(pos.unrealized_pnl_pct(price))
            if lp >= 0.35:
                return self._build(
                    key, pos, price,
                    pos.current_quantity, "options_stop",
                )
        return None

    def _check_time_stop(self, key, pos, price):
        """
        Smart time stop:
        - If losing after max days: close
        - If profitable after max days: tighten trail
        """
        days = pos.days_held()
        max_days = getattr(pos, "max_hold_days", 7)

        if days < max_days:
            return None

        pnl_pct = pos.unrealized_pnl_pct(price)

        if pnl_pct <= 0:
            return self._build(
                key, pos, price,
                pos.current_quantity,
                "time_stop_losing",
            )
        elif pnl_pct < 0.02:
            return self._build(
                key, pos, price,
                pos.current_quantity,
                "time_stop_marginal",
            )
        else:
            atr = getattr(pos, "atr", price * 0.02)
            tight_stop = price - (1.0 * atr)
            if pos.trailing_stop is None:
                pos.trailing_stop = round(tight_stop, 2)
            elif tight_stop > pos.trailing_stop:
                pos.trailing_stop = round(tight_stop, 2)
            return None

    def _check_tp(self, key, pos, price):
        if pos.instrument == "STOCK":
            if (pos.scale_stage == 0
                    and pos.direction == "LONG"
                    and price >= pos.target_1):
                qty = max(
                    1, int(pos.original_quantity / 3)
                )
                pos.scale_stage = 1
                pos.stop_loss = pos.entry_price
                return self._build(
                    key, pos, price, qty, "target_1",
                )
            if (pos.scale_stage == 1
                    and pos.direction == "LONG"
                    and price >= pos.target_2):
                qty = max(
                    1, int(pos.original_quantity / 3)
                )
                pos.scale_stage = 2
                atr = getattr(
                    pos, "atr", pos.entry_price * 0.02
                )
                pos.trailing_stop = round(
                    price - (1.5 * atr), 2
                )
                return self._build(
                    key, pos, price, qty, "target_2",
                )
        elif pos.instrument in ("CALL", "PUT"):
            gp = pos.unrealized_pnl_pct(price)
            if pos.scale_stage == 0 and gp >= 0.50:
                qty = max(
                    1, int(pos.original_quantity / 2)
                )
                pos.scale_stage = 1
                pos.stop_loss = pos.entry_price
                return self._build(
                    key, pos, price, qty,
                    "options_tp_50pct",
                )
        elif pos.instrument == "ETF":
            ep = pos.entry_price
            gp = (price - ep) / ep if ep > 0 else 0
            if pos.scale_stage == 0 and gp >= 0.08:
                qty = int(pos.current_quantity / 2)
                if qty > 0:
                    pos.scale_stage = 1
                    pos.stop_loss = round(ep * 1.02, 2)
                    return self._build(
                        key, pos, price, qty,
                        "etf_tp_8pct",
                    )
            if pos.scale_stage == 1 and gp >= 0.15:
                return self._build(
                    key, pos, price,
                    pos.current_quantity,
                    "etf_tp_15pct",
                )
        return None

    def _check_trail(self, key, pos, price):
        if pos.trailing_stop and pos.direction == "LONG":
            if price <= pos.trailing_stop:
                return self._build(
                    key, pos, price,
                    pos.current_quantity,
                    "trailing_stop",
                )
            if pos.scale_stage >= 2:
                atr = getattr(
                    pos, "atr", pos.entry_price * 0.02
                )
                nt = price - (1.5 * atr)
                if nt > pos.trailing_stop:
                    pos.trailing_stop = round(nt, 2)
        if pos.instrument in ("CALL", "PUT"):
            if pos.scale_stage >= 1:
                gp = pos.unrealized_pnl_pct(price)
                if gp < 0.20:
                    return self._build(
                        key, pos, price,
                        pos.current_quantity,
                        "options_trail_stop",
                    )
        return None

    def _build(self, key, pos, price, qty, reason):
        qty = min(qty, pos.current_quantity)
        if qty <= 0:
            return None
        is_dt = (
            pos.entry_date == date.today().isoformat()
        )
        lp = round(price * 0.997, 2)
        return {
            "type": f"{pos.instrument}_SELL",
            "position_key": key,
            "symbol": pos.symbol,
            "quantity": int(qty),
            "limit_price": lp,
            "reason": reason,
            "is_day_trade": is_dt,
            "unrealized_pnl": round(
                pos.unrealized_pnl(price), 2
            ),
            "unrealized_pnl_pct": round(
                pos.unrealized_pnl_pct(price), 4
            ),
        }
