"""
Scalper Executor v2.
Supports: long options, credit spreads, iron condors.
Portfolio: config/paper_scalp.json
"""

import os
import json
from datetime import datetime, date
from loguru import logger

BUY_SLIPPAGE = 0.03   # Pay 3% above mid when buying
SELL_SLIPPAGE = 0.015  # Receive 1.5% below mid when selling (tighter for premium)


class ScalperExecutor:

    def __init__(self, equity=25000):
        self.equity = equity
        self.portfolio = self._load()

    def _load(self):
        path = "config/paper_scalp.json"
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
        return {
            "equity": self.equity,
            "cash": self.equity,
            "positions": [],
            "history": [],
            "daily_stats": {},
        }

    def _save(self):
        os.makedirs("config", exist_ok=True)
        with open("config/paper_scalp.json", "w") as f:
            json.dump(self.portfolio, f, indent=2, default=str)

    def open_position(self, signal, contract, max_cost):
        """Open a directional long option position."""
        if not contract:
            return {"status": "REJECTED", "reason": "no_contract"}
        mid = contract.get("mid", 0)
        qty = contract.get("qty", 1)
        fill = round(mid * (1 + BUY_SLIPPAGE), 2)
        cost = fill * qty * 100
        if cost > self.portfolio["cash"]:
            qty = int(self.portfolio["cash"] / (fill * 100))
            if qty < 1:
                return {"status": "REJECTED", "reason": "no_cash"}
            cost = fill * qty * 100

        self.portfolio["cash"] -= cost
        pos = {
            "id": len(self.portfolio["positions"]) + len(self.portfolio["history"]) + 1,
            "symbol": signal["symbol"],
            "direction": signal["direction"],
            "signal_type": signal["type"],
            "structure": "LONG_OPTION",
            "confidence": signal["confidence"],
            "contract": contract.get("symbol", ""),
            "strike": contract.get("strike", 0),
            "delta": contract.get("delta", 0),
            "gamma": contract.get("gamma", 0),
            "theta": contract.get("theta", 0),
            "entry_price": fill,
            "entry_cost": round(cost, 2),
            "qty": qty,
            "entry_time": datetime.now().isoformat(),
            "entry_underlying": signal["price"],
            "status": "OPEN",
            "peak_value": cost,
            "reason": signal.get("reason", ""),
        }
        self.portfolio["positions"].append(pos)
        self._save()
        logger.info(
            f"SCALP OPEN: {signal['direction']} {signal['symbol']} "
            f"${contract['strike']} {signal['type']} "
            f"d={contract.get('delta',0)} g={contract.get('gamma',0)} "
            f"@ ${fill} x{qty} = ${cost:,.2f}"
        )
        return {"status": "FILLED", "cost": cost, "position": pos}

    def open_credit_position(self, signal, spread):
        """Open a credit spread position."""
        if not spread:
            return {"status": "REJECTED"}

        credit = spread["credit"]
        qty = spread["qty"]
        collateral = spread["collateral"]
        credit_received = round(qty * credit * (1 - SELL_SLIPPAGE) * 100, 2)

        if collateral > self.portfolio["cash"]:
            return {"status": "REJECTED", "reason": "no_cash"}

        self.portfolio["cash"] -= collateral

        pos = {
            "id": len(self.portfolio["positions"]) + len(self.portfolio["history"]) + 1,
            "symbol": signal["symbol"],
            "direction": signal["direction"],
            "signal_type": signal["type"],
            "structure": "CREDIT_SPREAD",
            "confidence": signal["confidence"],
            "contract": spread["short"]["symbol"],
            "contract_long": spread["long"]["symbol"],
            "strike_short": spread["short"]["strike"],
            "strike_long": spread["long"]["strike"],
            "entry_cost": round(collateral, 2),
            "credit_received": credit_received,
            "qty": qty,
            "entry_time": datetime.now().isoformat(),
            "entry_underlying": signal["price"],
            "status": "OPEN",
            "peak_value": collateral,
            "reason": signal.get("reason", ""),
        }
        self.portfolio["positions"].append(pos)
        self.portfolio["cash"] += credit_received
        self._save()
        logger.info(
            f"CREDIT SPREAD: {signal['symbol']} "
            f"${spread['short']['strike']}/"
            f"${spread['long']['strike']} "
            f"cr=${credit} x{qty} "
            f"collateral=${collateral:,.2f}"
        )
        return {"status": "FILLED", "collateral": collateral}

    def close_position(self, pos_id, current_option_value):
        for pos in self.portfolio["positions"]:
            if pos.get("id") == pos_id and pos["status"] == "OPEN":
                qty = pos["qty"]
                fill = round(current_option_value * (1 - SELL_SLIPPAGE), 2)

                if pos.get("structure") == "CREDIT_SPREAD":
                    # Buy back the spread
                    close_cost = fill * qty * 100
                    credit = pos.get("credit_received", 0)
                    pnl = credit - close_cost
                    self.portfolio["cash"] += pos["entry_cost"]  # return collateral
                    self.portfolio["cash"] -= close_cost
                else:
                    proceeds = fill * qty * 100
                    pnl = proceeds - pos["entry_cost"]
                    self.portfolio["cash"] += proceeds

                pnl_pct = pnl / max(pos["entry_cost"], 1)
                pos["status"] = "CLOSED"
                pos["exit_price"] = fill
                pos["exit_time"] = datetime.now().isoformat()
                pos["pnl"] = round(pnl, 2)
                pos["pnl_pct"] = round(pnl_pct * 100, 1)

                self.portfolio["history"].append(pos)
                self.portfolio["positions"] = [
                    p for p in self.portfolio["positions"]
                    if p.get("id") != pos_id
                ]

                today = date.today().isoformat()
                if today not in self.portfolio["daily_stats"]:
                    self.portfolio["daily_stats"][today] = {
                        "trades": 0, "wins": 0, "losses": 0, "pnl": 0,
                    }
                s = self.portfolio["daily_stats"][today]
                s["trades"] += 1
                s["pnl"] += pnl
                if pnl > 0:
                    s["wins"] += 1
                else:
                    s["losses"] += 1
                self._save()

                r = "WIN" if pnl > 0 else "LOSS"
                et = datetime.fromisoformat(pos["entry_time"])
                mins = (datetime.now() - et).total_seconds() / 60
                logger.info(
                    f"SCALP {r}: {pos['direction']} {pos['symbol']} "
                    f"{pos.get('structure','?')} "
                    f"${pnl:+,.2f} ({pnl_pct:+.0%}) {mins:.1f}min"
                )
                return {"status": "CLOSED", "pnl": pnl}
        return {"status": "NOT_FOUND"}

    def expire_stale_positions(self):
        """Expire any 0DTE positions from previous days."""
        today = date.today().isoformat()
        expired = []
        for pos in self.portfolio["positions"]:
            if pos["status"] != "OPEN":
                continue
            entry_date = pos.get("entry_time", "")[:10]
            if entry_date and entry_date < today:
                # Position from a previous day — expired worthless for buys,
                # max profit for premium sells
                structure = pos.get("structure", "LONG_OPTION")
                if structure in ("NAKED_PUT", "NAKED_CALL", "CREDIT_SPREAD", "STRADDLE", "STRANGLE"):
                    # Premium sell expired OTM = keep premium (best case)
                    # But we need to check if it expired ITM
                    pnl = pos.get("credit_received", 0)  # Best case: keep all premium
                    self.portfolio["cash"] += pos["entry_cost"]  # Return collateral
                    logger.info(f"EXPIRED (premium sell): {pos['symbol']} {structure} +${pnl:,.2f} (kept premium)")
                else:
                    # Long option expired worthless
                    pnl = -pos["entry_cost"]
                    logger.info(f"EXPIRED WORTHLESS: {pos['symbol']} {structure} -${pos['entry_cost']:,.2f}")

                pos["status"] = "CLOSED"
                pos["exit_price"] = 0
                pos["exit_time"] = f"{entry_date}T16:00:00"
                pos["pnl"] = round(pnl, 2)
                pos["pnl_pct"] = round(pnl / max(pos["entry_cost"], 1) * 100, 1)
                pos["exit_reason"] = "EXPIRED_0DTE"
                self.portfolio["history"].append(pos)
                expired.append(pos["id"])

                # Update daily stats for the entry date
                if entry_date not in self.portfolio["daily_stats"]:
                    self.portfolio["daily_stats"][entry_date] = {
                        "trades": 0, "wins": 0, "losses": 0, "pnl": 0
                    }
                s = self.portfolio["daily_stats"][entry_date]
                s["trades"] += 1
                s["pnl"] += pnl
                if pnl > 0:
                    s["wins"] += 1
                else:
                    s["losses"] += 1

        if expired:
            self.portfolio["positions"] = [
                p for p in self.portfolio["positions"] if p.get("id") not in expired
            ]
            self._save()
            logger.info(f"Expired {len(expired)} stale positions from previous days")
        return len(expired)

    def get_open_positions(self):
        return [p for p in self.portfolio["positions"] if p["status"] == "OPEN"]


    def open_naked_position(self, signal, contract):
        """Open a naked put or call (selling premium)."""
        if not contract:
            return {"status": "REJECTED"}
        premium = contract.get("premium", 0)
        collateral = contract.get("collateral", 0)
        qty = contract.get("qty", 1)

        if collateral > self.portfolio["cash"]:
            return {"status": "REJECTED", "reason": "no_cash"}

        self.portfolio["cash"] -= collateral
        credit = round(premium * (1 - SELL_SLIPPAGE), 2)
        self.portfolio["cash"] += credit

        pos = {
            "id": len(self.portfolio["positions"]) + len(self.portfolio["history"]) + 1,
            "symbol": signal["symbol"],
            "direction": signal["direction"],
            "signal_type": signal["type"],
            "structure": signal["structure"],
            "confidence": signal["confidence"],
            "contract": contract.get("symbol", ""),
            "strike": contract.get("strike", 0),
            "delta": contract.get("delta", 0),
            "entry_cost": round(collateral, 2),
            "credit_received": credit,
            "qty": qty,
            "entry_time": datetime.now().isoformat(),
            "entry_underlying": signal["price"],
            "status": "OPEN",
            "peak_value": collateral,
            "reason": signal.get("reason", ""),
        }
        self.portfolio["positions"].append(pos)
        self._save()
        logger.info(
            f"NAKED {signal['type']}: {signal['symbol']} "
            f"${contract['strike']} cr=${credit:,.2f} "
            f"collateral=${collateral:,.2f}"
        )
        return {"status": "FILLED", "collateral": collateral}

    def open_straddle_position(self, signal, straddle):
        """Open a short straddle or strangle."""
        if not straddle:
            return {"status": "REJECTED"}
        premium = straddle.get("premium", 0)
        collateral = straddle.get("collateral", 0)
        qty = straddle.get("qty", 1)

        if collateral > self.portfolio["cash"]:
            return {"status": "REJECTED", "reason": "no_cash"}

        self.portfolio["cash"] -= collateral
        credit = round(premium * (1 - SELL_SLIPPAGE), 2)
        self.portfolio["cash"] += credit

        stype = straddle.get("type", "STRADDLE")
        pos = {
            "id": len(self.portfolio["positions"]) + len(self.portfolio["history"]) + 1,
            "symbol": signal["symbol"],
            "direction": "NEUTRAL",
            "signal_type": signal["type"],
            "structure": stype,
            "confidence": signal["confidence"],
            "contract": straddle.get("call", {}).get("symbol", ""),
            "contract_put": straddle.get("put", {}).get("symbol", ""),
            "strike": straddle.get("strike", straddle.get("call_strike", 0)),
            "entry_cost": round(collateral, 2),
            "credit_received": credit,
            "qty": qty,
            "entry_time": datetime.now().isoformat(),
            "entry_underlying": signal["price"],
            "status": "OPEN",
            "peak_value": collateral,
            "reason": signal.get("reason", ""),
        }
        self.portfolio["positions"].append(pos)
        self._save()
        logger.info(
            f"{stype}: {signal['symbol']} "
            f"cr=${credit:,.2f} collateral=${collateral:,.2f}"
        )
        return {"status": "FILLED", "collateral": collateral}

    def open_ratio_position(self, signal, ratio):
        """Open a ratio spread."""
        if not ratio:
            return {"status": "REJECTED"}
        collateral = ratio.get("collateral", 0)
        if collateral > self.portfolio["cash"]:
            return {"status": "REJECTED", "reason": "no_cash"}

        net_cr = ratio.get("net_credit", 0) * 100
        net_db = ratio.get("net_debit", 0) * 100
        self.portfolio["cash"] -= collateral
        if net_cr > 0:
            self.portfolio["cash"] += round(net_cr * (1 - SELL_SLIPPAGE), 2)
        else:
            self.portfolio["cash"] -= round(net_db * (1 + BUY_SLIPPAGE), 2)

        pos = {
            "id": len(self.portfolio["positions"]) + len(self.portfolio["history"]) + 1,
            "symbol": signal["symbol"],
            "direction": signal["direction"],
            "signal_type": "RATIO_SPREAD",
            "structure": "RATIO_SPREAD",
            "confidence": signal["confidence"],
            "contract": ratio["buy"]["symbol"],
            "contract_sell": ratio["sell"]["symbol"],
            "entry_cost": round(collateral, 2),
            "credit_received": round(net_cr, 2) if net_cr > 0 else 0,
            "qty": 1,
            "entry_time": datetime.now().isoformat(),
            "entry_underlying": signal["price"],
            "status": "OPEN",
            "peak_value": collateral,
            "reason": signal.get("reason", ""),
        }
        self.portfolio["positions"].append(pos)
        self._save()
        logger.info(
            f"RATIO: {signal['symbol']} {signal['direction']} "
            f"buy ${ratio['buy']['strike']} / sell 2x ${ratio['sell']['strike']} "
            f"collateral=${collateral:,.2f}"
        )
        return {"status": "FILLED", "collateral": collateral}

    def get_summary(self):
        today = date.today().isoformat()
        ds = self.portfolio["daily_stats"].get(
            today, {"trades": 0, "wins": 0, "losses": 0, "pnl": 0}
        )
        h = self.portfolio["history"]
        wins = [t for t in h if t.get("pnl", 0) > 0]
        return {
            "cash": round(self.portfolio["cash"], 2),
            "open_positions": len(self.get_open_positions()),
            "today_trades": ds["trades"],
            "today_pnl": round(ds["pnl"], 2),
            "today_wins": ds["wins"],
            "today_losses": ds["losses"],
            "total_trades": len(h),
            "total_pnl": round(sum(t.get("pnl", 0) for t in h), 2),
            "win_rate": round(len(wins) / max(len(h), 1), 2),
        }
