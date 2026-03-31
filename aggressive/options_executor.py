"""
Options Executor v2.1 - Multi-Leg Support (FIXED).
Handles all strategy types:
- Naked long calls/puts
- Debit spreads (bull call, bear put)
- Credit spreads (bull put, bear call)
- Calendar spreads
Paper and live modes with slippage modeling.

FIXES in v2.1:
- Removed dead-code fake debit spread block that returned before real OrderBuilder
- Live exits now use dynamic account hash (same as entries)
- Credit spread live execution added
"""

import os
import json
import shutil
import httpx
from datetime import datetime, date

BUY_SLIPPAGE = 0.03
SELL_SLIPPAGE = 0.015
from loguru import logger
from dotenv import load_dotenv

load_dotenv()

SLIPPAGE = 0.025


class OptionsExecutor:

    def __init__(self, schwab_client=None, account_hash=None, paper_mode=True):
        self.client = schwab_client
        self.account_hash = account_hash
        self.paper_mode = paper_mode

        if paper_mode:
            logger.info("EXECUTOR: Paper mode (Level 3 enabled) (multi-leg)")
            self.paper_positions = self._load_paper()
            self._backup()
        else:
            logger.info("EXECUTOR: LIVE mode (multi-leg)")

    def _get_account_hash(self):
        """Always fetch dynamic account hash from Schwab API."""
        try:
            r0 = self.client.get_account_numbers()
            accounts = r0.json()
            for a in accounts:
                if a["accountNumber"] == "28135437":
                    return a["hashValue"]
            return accounts[-1]["hashValue"]
        except Exception as e:
            logger.error(f"Failed to get account hash: {e}")
            return self.account_hash  # fallback to init value

    def _load_paper(self):
        p = "config/paper_options.json"
        if os.path.exists(p):
            with open(p) as f:
                return json.load(f)
        return {
            "cash": float(os.getenv("ACCOUNT_EQUITY", "8000")),
            "positions": [],
        }

    def _save_paper(self):
        with open("config/paper_options.json", "w") as f:
            json.dump(self.paper_positions, f, indent=2, default=str)

    def _backup(self):
        src = "config/paper_options.json"
        if os.path.exists(src):
            bdir = "config/backups"
            os.makedirs(bdir, exist_ok=True)
            dst = f"{bdir}/paper_{date.today().isoformat()}.json"
            if not os.path.exists(dst):
                shutil.copy2(src, dst)

    def execute_strategy(self, trade):
        """Execute any strategy type from the scanner output."""
        strategy = trade.get("strategy", {})
        stype = strategy.get("type", "NAKED_LONG")
        sym = trade["symbol"]
        direction = trade["direction"]

        if stype == "NAKED_LONG":
            return self._execute_naked(trade)
        elif stype == "DEBIT_SPREAD":
            return self._execute_debit_spread(trade)
        elif stype == "CREDIT_SPREAD":
            return self._execute_credit_spread(trade)
        elif stype == "CALENDAR_SPREAD":
            return self._execute_calendar(trade)
        else:
            logger.warning(f"Unknown strategy: {stype}")
            return {"status": "REJECTED", "reason": "unknown_strategy"}

    def _execute_naked(self, trade):
        """Execute a single-leg long option."""
        s = trade["strategy"]
        contracts = s.get("contracts", [])
        if not contracts:
            return {"status": "REJECTED", "reason": "no_contracts"}

        c = contracts[0]
        csym = c.get("symbol", "")
        qty = c.get("qty", 1)
        mid = c.get("mid", 0)
        limit = round(mid * (1 + BUY_SLIPPAGE), 2)
        cost = qty * limit * 100

        if self.paper_mode:
            return self._paper_open(
                trade, "NAKED_LONG", cost,
                [{
                    "symbol": csym,
                    "leg": "LONG",
                    "strike": c.get("strike", 0),
                    "qty": qty,
                    "price": limit,
                    "delta": c.get("delta", 0),
                }]
            )

        # LIVE naked long
        try:
            from schwab.orders.options import option_buy_to_open_limit
            order = option_buy_to_open_limit(csym, qty, str(limit))
            ah = self._get_account_hash()
            resp = self.client.place_order(ah, order)
            if resp.status_code in (httpx.codes.CREATED, httpx.codes.OK):
                logger.info(f"LIVE NAKED: {trade['symbol']} x{qty} @ ${limit}")
                return {"status": "SUBMITTED", "cost": cost}
            return {"status": "REJECTED", "code": resp.status_code}
        except Exception as e:
            return {"status": "ERROR", "reason": str(e)}

    def _execute_debit_spread(self, trade):
        """Execute a vertical debit spread (2 legs)."""
        s = trade["strategy"]
        contracts = s.get("contracts", [])
        if len(contracts) < 2:
            return {"status": "REJECTED", "reason": "need_2_legs"}

        long_leg = next((c for c in contracts if c["leg"] == "LONG"), None)
        short_leg = next((c for c in contracts if c["leg"] == "SHORT"), None)
        if not long_leg or not short_leg:
            return {"status": "REJECTED", "reason": "missing_legs"}

        net_debit = s.get("net_debit", 0)
        qty = s.get("qty", 1)
        limit = round(net_debit * (1 + BUY_SLIPPAGE), 2)
        cost = qty * limit * 100

        if self.paper_mode:
            return self._paper_open(
                trade, "DEBIT_SPREAD", cost,
                [
                    {
                        "symbol": long_leg.get("symbol", ""),
                        "leg": "LONG",
                        "strike": long_leg.get("strike", 0),
                        "qty": qty,
                        "price": long_leg.get("mid", 0),
                        "delta": long_leg.get("delta", 0),
                    },
                    {
                        "symbol": short_leg.get("symbol", ""),
                        "leg": "SHORT",
                        "strike": short_leg.get("strike", 0),
                        "qty": qty,
                        "price": short_leg.get("mid", 0),
                        "delta": short_leg.get("delta", 0),
                    },
                ]
            )

        # LIVE debit spread via OrderBuilder
        try:
            from schwab.orders.generic import OrderBuilder
            from schwab.orders.common import Duration, Session, OrderType, OrderStrategyType, OptionInstruction
            order = (OrderBuilder()
                .set_order_type(OrderType.NET_DEBIT)
                .set_session(Session.NORMAL)
                .set_duration(Duration.DAY)
                .set_price(str(limit))
                .set_order_strategy_type(OrderStrategyType.SINGLE)
                .add_option_leg(OptionInstruction.BUY_TO_OPEN, long_leg["symbol"], qty)
                .add_option_leg(OptionInstruction.SELL_TO_OPEN, short_leg["symbol"], qty)
                .build())
            ah = self._get_account_hash()
            resp = self.client.place_order(ah, order)
            if resp.status_code in (httpx.codes.CREATED, httpx.codes.OK):
                logger.info(f"LIVE SPREAD: {trade['symbol']} x{qty} net ${limit}")
                return {"status": "SUBMITTED", "cost": cost}
            return {"status": "REJECTED", "code": resp.status_code}
        except Exception as e:
            return {"status": "ERROR", "reason": str(e)}

    def _execute_credit_spread(self, trade):
        """Execute a credit spread (collect premium)."""
        s = trade["strategy"]
        contracts = s.get("contracts", [])
        if len(contracts) < 2:
            return {"status": "REJECTED", "reason": "need_2_legs"}

        net_credit = s.get("net_credit", 0)
        qty = s.get("qty", 1)
        collateral = s.get("collateral", s.get("total_cost", 0))
        credit_received = qty * net_credit * (1 - SELL_SLIPPAGE) * 100

        if self.paper_mode:
            short_leg = next((c for c in contracts if c["leg"] == "SHORT"), None)
            long_leg = next((c for c in contracts if c["leg"] == "LONG"), None)
            return self._paper_open(
                trade, "CREDIT_SPREAD", collateral,
                [
                    {
                        "symbol": short_leg.get("symbol", "") if short_leg else "",
                        "leg": "SHORT",
                        "strike": short_leg.get("strike", 0) if short_leg else 0,
                        "qty": qty,
                        "price": short_leg.get("mid", 0) if short_leg else 0,
                        "delta": short_leg.get("delta", 0) if short_leg else 0,
                    },
                    {
                        "symbol": long_leg.get("symbol", "") if long_leg else "",
                        "leg": "LONG",
                        "strike": long_leg.get("strike", 0) if long_leg else 0,
                        "qty": qty,
                        "price": long_leg.get("mid", 0) if long_leg else 0,
                        "delta": long_leg.get("delta", 0) if long_leg else 0,
                    },
                ],
                credit=round(credit_received, 2),
            )

        # LIVE credit spread via OrderBuilder
        try:
            from schwab.orders.generic import OrderBuilder
            from schwab.orders.common import Duration, Session, OrderType, OrderStrategyType, OptionInstruction
            short_leg = next((c for c in contracts if c["leg"] == "SHORT"), None)
            long_leg = next((c for c in contracts if c["leg"] == "LONG"), None)
            limit = round(net_credit * (1 - SELL_SLIPPAGE), 2)
            order = (OrderBuilder()
                .set_order_type(OrderType.NET_CREDIT)
                .set_session(Session.NORMAL)
                .set_duration(Duration.DAY)
                .set_price(str(limit))
                .set_order_strategy_type(OrderStrategyType.SINGLE)
                .add_option_leg(OptionInstruction.SELL_TO_OPEN, short_leg["symbol"], qty)
                .add_option_leg(OptionInstruction.BUY_TO_OPEN, long_leg["symbol"], qty)
                .build())
            ah = self._get_account_hash()
            resp = self.client.place_order(ah, order)
            if resp.status_code in (httpx.codes.CREATED, httpx.codes.OK):
                logger.info(f"LIVE CREDIT: {trade['symbol']} x{qty} credit ${limit}")
                return {"status": "SUBMITTED", "credit": credit_received}
            return {"status": "REJECTED", "code": resp.status_code}
        except Exception as e:
            return {"status": "ERROR", "reason": str(e)}

    def _execute_calendar(self, trade):
        """Execute a calendar spread."""
        s = trade["strategy"]
        contracts = s.get("contracts", [])
        if len(contracts) < 2:
            return {"status": "REJECTED", "reason": "need_2_legs"}

        net_debit = s.get("net_debit", 0)
        qty = s.get("qty", 1)
        limit = round(net_debit * (1 + BUY_SLIPPAGE), 2)
        cost = qty * limit * 100

        if self.paper_mode:
            long_leg = next((c for c in contracts if c["leg"] == "LONG"), None)
            short_leg = next((c for c in contracts if c["leg"] == "SHORT"), None)
            return self._paper_open(
                trade, "CALENDAR_SPREAD", cost,
                [
                    {
                        "symbol": long_leg.get("symbol", "") if long_leg else "",
                        "leg": "LONG",
                        "strike": long_leg.get("strike", 0) if long_leg else 0,
                        "qty": qty,
                        "price": long_leg.get("mid", 0) if long_leg else 0,
                        "delta": long_leg.get("delta", 0) if long_leg else 0,
                        "dte": long_leg.get("dte", 0) if long_leg else 0,
                    },
                    {
                        "symbol": short_leg.get("symbol", "") if short_leg else "",
                        "leg": "SHORT",
                        "strike": short_leg.get("strike", 0) if short_leg else 0,
                        "qty": qty,
                        "price": short_leg.get("mid", 0) if short_leg else 0,
                        "delta": short_leg.get("delta", 0) if short_leg else 0,
                        "dte": short_leg.get("dte", 0) if short_leg else 0,
                    },
                ]
            )

        # LIVE calendar spread via OrderBuilder
        try:
            from schwab.orders.generic import OrderBuilder
            from schwab.orders.common import Duration, Session, OrderType, OrderStrategyType, OptionInstruction
            long_leg = next((c for c in contracts if c["leg"] == "LONG"), None)
            short_leg = next((c for c in contracts if c["leg"] == "SHORT"), None)
            order = (OrderBuilder()
                .set_order_type(OrderType.NET_DEBIT)
                .set_session(Session.NORMAL)
                .set_duration(Duration.DAY)
                .set_price(str(limit))
                .set_order_strategy_type(OrderStrategyType.SINGLE)
                .add_option_leg(OptionInstruction.BUY_TO_OPEN, long_leg["symbol"], qty)
                .add_option_leg(OptionInstruction.SELL_TO_OPEN, short_leg["symbol"], qty)
                .build())
            ah = self._get_account_hash()
            resp = self.client.place_order(ah, order)
            if resp.status_code in (httpx.codes.CREATED, httpx.codes.OK):
                logger.info(f"LIVE CALENDAR: {trade['symbol']} x{qty} net ${limit}")
                return {"status": "SUBMITTED", "cost": cost}
            return {"status": "REJECTED", "code": resp.status_code}
        except Exception as e:
            return {"status": "ERROR", "reason": str(e)}

    def _paper_open(self, trade, stype, cost, legs, credit=0):
        """Open a paper position for any strategy type."""
        if cost > self.paper_positions["cash"] and stype != "CREDIT_SPREAD":
            return {"status": "REJECTED", "reason": "insufficient_cash"}

        if stype == "CREDIT_SPREAD":
            # Credit spreads: deduct collateral, add credit
            self.paper_positions["cash"] -= cost
            self.paper_positions["cash"] += credit
        else:
            self.paper_positions["cash"] -= cost

        s = trade.get("strategy", {})
        pos = {
            "underlying": trade["symbol"],
            "direction": trade["direction"],
            "strategy_type": stype,
            "conviction": trade.get("conviction", ""),
            "composite": trade.get("composite", 0),
            "legs": legs,
            "entry_cost": round(cost, 2),
            "entry_date": date.today().isoformat(),
            "entry_net": round(s.get("net_debit", s.get("net_credit", 0)), 2),
            "max_profit": s.get("max_profit", "unlimited"),
            "max_loss": s.get("max_loss", cost / 100),
            "spread_width": s.get("spread_width", 0),
            "breakeven": s.get("breakeven", 0),
            "description": s.get("description", stype),
            "credit_received": credit,
            "status": "OPEN",
            "t1_hit": False,
            "highest_val": 0,
            "adds": 0,
        }
        self.paper_positions["positions"].append(pos)
        self._save_paper()

        logger.info(
            f"PAPER {stype}: {trade['direction']} "
            f"{trade['symbol']} | {s.get('description', '')} "
            f"| Cost: ${cost:,.2f}"
        )
        return {"status": "FILLED", "paper": True, "cost": cost}

    def close_position(self, pos, exit_value_per_contract):
        """Close any position type (paper mode)."""
        if pos.get("status") != "OPEN":
            return {"status": "SKIPPED"}

        stype = pos.get("strategy_type", "NAKED_LONG")
        qty = pos["legs"][0]["qty"] if pos.get("legs") else 1
        entry_cost = pos.get("entry_cost", 0)

        exit_price = round(exit_value_per_contract * (1 - SELL_SLIPPAGE), 2)

        if stype == "CREDIT_SPREAD":
            credit = pos.get("credit_received", 0)
            close_cost = qty * exit_price * 100
            pnl = credit - close_cost
            self.paper_positions["cash"] += pos["entry_cost"]  # return collateral
            self.paper_positions["cash"] += pnl
        else:
            proceeds = qty * exit_price * 100
            pnl = proceeds - entry_cost
            self.paper_positions["cash"] += proceeds

        pos["status"] = "CLOSED"
        pos["exit_date"] = date.today().isoformat()
        pos["exit_value"] = exit_price
        pos["pnl"] = round(pnl, 2)
        pos["pnl_pct"] = round(pnl / max(entry_cost, 1) * 100, 1)
        self._save_paper()

        result = "WIN" if pnl > 0 else "LOSS"
        logger.info(
            f"CLOSED {result}: {pos['underlying']} "
            f"{stype} ${pnl:+,.2f} ({pos['pnl_pct']:+.1f}%)"
        )
        return {"status": "FILLED", "pnl": pnl}

    def close_position_live(self, pos):
        """Close a live position on Schwab."""
        stype = pos.get("strategy_type", "NAKED_LONG")
        legs = pos.get("legs", [])
        ah = self._get_account_hash()  # FIXED: dynamic hash

        if stype == "NAKED_LONG":
            if not legs:
                return {"status": "REJECTED", "reason": "no_legs"}
            leg = legs[0]
            csym = leg.get("symbol", "")
            qty = leg.get("qty", 1)
            try:
                from schwab.orders.options import option_sell_to_close_market
                order = option_sell_to_close_market(csym, qty)
                resp = self.client.place_order(ah, order)
                if resp.status_code in (httpx.codes.CREATED, httpx.codes.OK):
                    logger.info(f"LIVE CLOSE: {pos.get('underlying','?')} {csym} x{qty}")
                    return {"status": "FILLED"}
                return {"status": "REJECTED", "code": resp.status_code}
            except Exception as e:
                logger.error(f"Live close error: {e}")
                return {"status": "ERROR", "reason": str(e)}

        elif stype in ("DEBIT_SPREAD", "CALENDAR_SPREAD"):
            if len(legs) < 2:
                return {"status": "REJECTED", "reason": "need_2_legs"}
            long_leg = next((l for l in legs if l["leg"] == "LONG"), None)
            short_leg = next((l for l in legs if l["leg"] == "SHORT"), None)
            if not long_leg or not short_leg:
                return {"status": "REJECTED", "reason": "missing_legs"}
            try:
                from schwab.orders.generic import OrderBuilder
                from schwab.orders.common import Duration, Session, OrderType
                order = (OrderBuilder()
                    .set_order_type(OrderType.MARKET)
                    .set_session(Session.NORMAL)
                    .set_duration(Duration.DAY)
                    .add_option_leg(OptionInstruction.SELL_TO_CLOSE, long_leg["symbol"], long_leg.get("qty", 1))
                    .add_option_leg(OptionInstruction.BUY_TO_CLOSE, short_leg["symbol"], short_leg.get("qty", 1))
                    .build())
                resp = self.client.place_order(ah, order)
                if resp.status_code in (httpx.codes.CREATED, httpx.codes.OK):
                    logger.info(f"LIVE CLOSE SPREAD: {pos.get('underlying','?')}")
                    return {"status": "FILLED"}
                return {"status": "REJECTED", "code": resp.status_code}
            except Exception as e:
                logger.error(f"Live spread close error: {e}")
                return {"status": "ERROR", "reason": str(e)}

        elif stype == "CREDIT_SPREAD":
            if len(legs) < 2:
                return {"status": "REJECTED", "reason": "need_2_legs"}
            short_leg = next((l for l in legs if l["leg"] == "SHORT"), None)
            long_leg = next((l for l in legs if l["leg"] == "LONG"), None)
            if not short_leg or not long_leg:
                return {"status": "REJECTED", "reason": "missing_legs"}
            try:
                from schwab.orders.generic import OrderBuilder
                from schwab.orders.common import Duration, Session, OrderType
                order = (OrderBuilder()
                    .set_order_type(OrderType.MARKET)
                    .set_session(Session.NORMAL)
                    .set_duration(Duration.DAY)
                    .add_option_leg(OptionInstruction.BUY_TO_CLOSE, short_leg["symbol"], short_leg.get("qty", 1))
                    .add_option_leg(OptionInstruction.SELL_TO_CLOSE, long_leg["symbol"], long_leg.get("qty", 1))
                    .build())
                resp = self.client.place_order(ah, order)
                if resp.status_code in (httpx.codes.CREATED, httpx.codes.OK):
                    logger.info(f"LIVE CLOSE CREDIT: {pos.get('underlying','?')}")
                    return {"status": "FILLED"}
                return {"status": "REJECTED", "code": resp.status_code}
            except Exception as e:
                logger.error(f"Live credit close error: {e}")
                return {"status": "ERROR", "reason": str(e)}

        else:
            logger.warning(f"Unknown close type: {stype}")
            return {"status": "REJECTED", "reason": f"unknown_{stype}"}

    def get_live_positions(self):
        """Get open positions from Schwab account."""
        try:
            ah = self._get_account_hash()
            from schwab.client import Client
            resp = self.client.get_account(ah, fields=[Client.Account.Fields.POSITIONS])
            if resp.status_code != httpx.codes.OK:
                return []
            data = resp.json()
            positions = data.get("securitiesAccount", {}).get("positions", [])
            result = []
            for p in positions:
                inst = p.get("instrument", {})
                if inst.get("assetType") != "OPTION":
                    continue
                result.append({
                    "symbol": inst.get("symbol", ""),
                    "underlying": inst.get("underlyingSymbol", ""),
                    "qty": int(p.get("longQuantity", 0) - p.get("shortQuantity", 0)),
                    "avg_price": p.get("averagePrice", 0),
                    "market_value": p.get("marketValue", 0),
                    "day_pnl": p.get("currentDayProfitLoss", 0),
                    "pnl_pct": p.get("currentDayProfitLossPercentage", 0),
                    "asset_type": "OPTION",
                })
            return result
        except Exception as e:
            logger.error(f"Get positions error: {e}")
            return []

    def get_live_summary(self):
        """Get account summary from Schwab."""
        try:
            ah = self._get_account_hash()
            from schwab.client import Client
            resp = self.client.get_account(ah, fields=[Client.Account.Fields.POSITIONS])
            if resp.status_code != httpx.codes.OK:
                return None
            data = resp.json()
            acct = data.get("securitiesAccount", {})
            bal = acct.get("currentBalances", {})
            positions = acct.get("positions", [])
            option_positions = [p for p in positions if p.get("instrument", {}).get("assetType") == "OPTION"]
            return {
                "cash": bal.get("cashBalance", 0),
                "equity": bal.get("liquidationValue", 0),
                "open_positions": len(option_positions),
                "total_pnl": sum(p.get("currentDayProfitLoss", 0) for p in option_positions),
            }
        except Exception as e:
            logger.error(f"Get summary error: {e}")
            return None

    def get_summary(self):
        if not self.paper_mode:
            return {}
        pp = self.paper_positions
        op = [p for p in pp["positions"] if p["status"] == "OPEN"]
        cl = [p for p in pp["positions"] if p["status"] == "CLOSED"]
        pnl = sum(p.get("pnl", 0) for p in cl)
        dep = sum(p["entry_cost"] for p in op)
        return {
            "cash": round(pp["cash"], 2),
            "deployed": round(dep, 2),
            "open_positions": len(op),
            "closed_trades": len(cl),
            "total_pnl": round(pnl, 2),
            "positions": op,
        }