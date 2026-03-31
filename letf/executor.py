"""
LETF Executor - Buy/sell leveraged ETF shares on Schwab.
"""
import json
import time
import httpx
from datetime import datetime, date
from loguru import logger

CONFIG_PATH = "config/letf_config.json"
PORTFOLIO_PATH = "config/letf_portfolio.json"


class LETFExecutor:

    def __init__(self, client, live=False, config_path=CONFIG_PATH, portfolio_path=PORTFOLIO_PATH):
        self.client = client
        self.live = live
        self.config_path = config_path
        self.portfolio_path = portfolio_path
        self.config = json.load(open(config_path))
        self.account_hash = self.config["account_hash"]
        self._load_portfolio()

    def _load_portfolio(self):
        try:
            self.portfolio = json.load(open(self.portfolio_path))
        except FileNotFoundError:
            self.portfolio = {
                "equity": self.config["equity"],
                "cash": self.config["equity"],
                "positions": [],
            }
            self._save_portfolio()

    def _save_portfolio(self):
        json.dump(self.portfolio, open(self.portfolio_path, "w"), indent=2)

    def get_real_balance(self):
        """Get real balance from Schwab PCRA."""
        try:
            r = self.client.get_account_numbers()
            accounts = r.json()
            # Find PCRA account
            ah = None
            for a in accounts:
                if a["accountNumber"] == self.config["account_number"]:
                    ah = a["hashValue"]
                    break
            if not ah:
                return None
            r2 = self.client.get_account(ah)
            d = r2.json()
            bal = d.get("securitiesAccount", {}).get("currentBalances", {})
            return {
                "cash": bal.get("cashBalance", 0),
                "equity": bal.get("liquidationValue", 0),
                "available": bal.get("availableFundsNonMarginableTrade", 0),
            }
        except Exception as e:
            logger.error(f"Balance error: {e}")
            return None

    def can_enter(self, cost):
        """Check if we can enter a trade."""
        if self.live:
            bal = self.get_real_balance()
            if not bal:
                return False, "cant_read_balance"
            available = bal["available"] if bal["available"] > 0 else bal["cash"]  # Use cash if available is 0 (settlement)
            equity = bal["equity"]
        else:
            available = self.portfolio["cash"]
            equity = self.portfolio["equity"]

        # 10% max per trade
        if cost > equity * self.config["max_position_pct"]:
            return False, f"too_large_{cost/equity:.0%}"

        # Cash reserve
        min_cash = equity * self.config["cash_reserve_pct"]
        if available - cost < min_cash:
            return False, f"cash_reserve"

        # Max positions
        open_pos = [p for p in self.portfolio["positions"] if p.get("status") == "OPEN"]
        if len(open_pos) >= self.config["max_positions"]:
            return False, "max_positions"

        # Portfolio heat
        total_deployed = sum(p.get("entry_cost", 0) for p in open_pos)
        if total_deployed + cost > equity * self.config["max_portfolio_heat"]:
            return False, "max_heat"

        return True, "approved"

    def buy(self, symbol, qty, price, analysis):
        """Buy shares of a leveraged ETF."""
        cost = qty * price

        can, reason = self.can_enter(cost)
        if not can:
            logger.warning(f"SKIP {symbol}: {reason}")
            return {"status": "REJECTED", "reason": reason}

        if self.live:
            try:
                r = self.client.get_account_numbers()
                accounts = r.json()
                ah = None
                for a in accounts:
                    if a["accountNumber"] == self.config["account_number"]:
                        ah = a["hashValue"]
                        break
                if not ah:
                    return {"status": "ERROR", "reason": "account_not_found"}

                from schwab.orders.equities import equity_buy_limit, equity_buy_market
                # Try limit order first (0.5% above last price)
                limit_price = str(round(price * 1.005, 2))
                order = equity_buy_limit(symbol, qty, limit_price)
                resp = self.client.place_order(ah, order)
                if resp.status_code in (200, 201):
                    logger.info(f"LIVE BUY: {symbol} x{qty} limit @ ${price * 1.005:.2f}")
                else:
                    # Fallback to market order
                    logger.warning(f"Limit rejected for {symbol}, trying market order")
                    order2 = equity_buy_market(symbol, qty)
                    resp2 = self.client.place_order(ah, order2)
                    if resp2.status_code in (200, 201):
                        logger.info(f"LIVE BUY (market): {symbol} x{qty}")
                    else:
                        return {"status": "REJECTED", "code": resp2.status_code}
            except Exception as e:
                return {"status": "ERROR", "reason": str(e)}
        else:
            logger.info(f"PAPER BUY: {symbol} x{qty} @ ${price:.2f} = ${cost:,.2f}")

        # Record position
        position = {
            "symbol": symbol,
            "qty": qty,
            "entry_price": round(price, 2),
            "entry_cost": round(cost, 2),
            "entry_date": date.today().isoformat(),
            "settlement_date": (date.today() + __import__('datetime').timedelta(days=1)).isoformat(),
            "entry_time": datetime.now().isoformat(),
            "status": "OPEN",
            "peak_price": round(price, 2),
            "sector": analysis.get("sector", ""),
            "direction": analysis.get("direction", ""),
            "conviction": analysis.get("score", 0),
            "leverage": analysis.get("leverage", 3),
        }
        self.portfolio["positions"].append(position)
        self.portfolio["cash"] -= cost
        self._save_portfolio()

        return {"status": "FILLED", "cost": cost}

    def sell(self, position, price, reason=""):
        """Sell a position."""
        symbol = position["symbol"]
        qty = position["qty"]
        proceeds = qty * price

        if self.live:
            try:
                r = self.client.get_account_numbers()
                accounts = r.json()
                ah = None
                for a in accounts:
                    if a["accountNumber"] == self.config["account_number"]:
                        ah = a["hashValue"]
                        break
                if not ah:
                    return {"status": "ERROR", "reason": "account_not_found"}

                from schwab.orders.equities import equity_sell_market
                order = equity_sell_market(symbol, qty)
                resp = self.client.place_order(ah, order)
                if resp.status_code in (200, 201):
                    logger.info(f"LIVE SELL: {symbol} x{qty} @ ${price:.2f} ({reason})")
                else:
                    return {"status": "REJECTED", "code": resp.status_code}
            except Exception as e:
                return {"status": "ERROR", "reason": str(e)}
        else:
            logger.info(f"PAPER SELL: {symbol} x{qty} @ ${price:.2f} ({reason})")

        pnl = proceeds - position["entry_cost"]
        position["status"] = "CLOSED"
        position["exit_price"] = round(price, 2)
        position["exit_date"] = date.today().isoformat()
        position["pnl"] = round(pnl, 2)
        position["pnl_pct"] = round(pnl / position["entry_cost"] * 100, 1)
        position["exit_reason"] = reason
        self.portfolio["cash"] += proceeds
        self._save_portfolio()

        logger.info(f"  P&L: ${pnl:+,.2f} ({position['pnl_pct']:+.1f}%)")
        return {"status": "FILLED", "pnl": pnl}

    def sync_equity(self):
        """Sync real equity to config and portfolio at end of day."""
        bal = self.get_real_balance()
        if not bal:
            return
        real_equity = bal["equity"]
        real_cash = bal["cash"]

        # Update config
        config = json.load(open(self.config_path))
        config["equity"] = real_equity
        config["cash"] = real_cash
        json.dump(config, open(self.config_path, "w"), indent=2)

        # Update portfolio
        self.portfolio["equity"] = real_equity
        self._save_portfolio()

        logger.info(f"EQUITY SYNC: ${real_equity:,.2f} (cash: ${real_cash:,.2f})")
        return real_equity

    def check_weekly_drawdown(self):
        """Check if account has dropped 10%+ from recent peak."""
        bal = self.get_real_balance()
        if not bal:
            return False

        equity = bal["equity"]
        config = json.load(open(self.config_path))
        peak = config.get("peak_equity", equity)

        # Update peak
        if equity > peak:
            config["peak_equity"] = equity
            json.dump(config, open(self.config_path, "w"), indent=2)
            return False

        drawdown = (equity - peak) / peak
        if drawdown < -0.10:
            logger.warning(f"WEEKLY DRAWDOWN: {drawdown:.1%} from peak ${peak:,.0f}")
            return True
        return False

    def get_available_cash(self):
        """Get cash available for trading (considering settlements)."""
        if self.live:
            bal = self.get_real_balance()
            if bal:
                return bal["available"]
        # Paper mode: check settlement dates
        today = date.today().isoformat()
        unsettled = 0
        for p in self.portfolio["positions"]:
            if p.get("status") == "CLOSED":
                settle = p.get("settlement_date", "")
                if settle > today:
                    unsettled += p.get("exit_proceeds", 0)
        return self.portfolio["cash"] - unsettled

    def get_summary(self):
        open_pos = [p for p in self.portfolio["positions"] if p["status"] == "OPEN"]
        closed = [p for p in self.portfolio["positions"] if p["status"] == "CLOSED"]
        return {
            "cash": self.portfolio["cash"],
            "open": len(open_pos),
            "deployed": sum(p["entry_cost"] for p in open_pos),
            "closed": len(closed),
            "total_pnl": sum(p.get("pnl", 0) for p in closed),
            "win_rate": sum(1 for p in closed if p.get("pnl", 0) > 0) / max(len(closed), 1) * 100,
        }
