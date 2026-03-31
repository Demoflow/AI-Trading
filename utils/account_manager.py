"""
Account Manager - Handles deposits, withdrawals,
and equity tracking with correct peak adjustment.
"""

import json
from datetime import datetime, date
from pathlib import Path
from loguru import logger


class AccountManager:

    LOG_PATH = "config/account_transactions.json"
    PEAK_PATH = "config/peak_equity.json"

    def __init__(self):
        self.transactions = self._load()

    def _load(self):
        if Path(self.LOG_PATH).exists():
            with open(self.LOG_PATH) as f:
                return json.load(f)
        return []

    def _save(self):
        Path(self.LOG_PATH).parent.mkdir(
            parents=True, exist_ok=True
        )
        with open(self.LOG_PATH, "w") as f:
            json.dump(
                self.transactions, f,
                indent=2, default=str,
            )

    def deposit(self, amount):
        """
        Record a deposit. Adjusts peak equity upward
        so deposits don't count as trading profit,
        and don't trigger false drawdown recovery.
        """
        if amount <= 0:
            logger.warning("Deposit must be positive")
            return

        self.transactions.append({
            "type": "DEPOSIT",
            "amount": round(amount, 2),
            "date": date.today().isoformat(),
            "timestamp": datetime.utcnow().isoformat(),
        })
        self._save()

        # Adjust peak upward by deposit amount
        self._adjust_peak(amount)

        # Update .env
        self._update_env_equity(amount)

        logger.info(
            f"DEPOSIT: +${amount:,.2f} recorded. "
            f"Peak and equity adjusted."
        )

    def withdraw(self, amount):
        """
        Record a withdrawal. Adjusts peak equity downward
        so withdrawals don't trigger false drawdown alerts.
        """
        if amount <= 0:
            logger.warning("Withdrawal must be positive")
            return

        self.transactions.append({
            "type": "WITHDRAWAL",
            "amount": round(amount, 2),
            "date": date.today().isoformat(),
            "timestamp": datetime.utcnow().isoformat(),
        })
        self._save()

        # Adjust peak downward by withdrawal amount
        self._adjust_peak(-amount)

        # Update .env
        self._update_env_equity(-amount)

        logger.info(
            f"WITHDRAWAL: -${amount:,.2f} recorded. "
            f"Peak and equity adjusted."
        )

    def _adjust_peak(self, delta):
        peak = 0
        if Path(self.PEAK_PATH).exists():
            with open(self.PEAK_PATH) as f:
                peak = json.load(f).get("peak", 0)

        new_peak = peak + delta
        with open(self.PEAK_PATH, "w") as f:
            json.dump({"peak": new_peak}, f)

        logger.info(
            f"Peak adjusted: ${peak:,.2f} -> "
            f"${new_peak:,.2f}"
        )

    def _update_env_equity(self, delta):
        env_path = ".env"
        if not Path(env_path).exists():
            return

        with open(env_path) as f:
            lines = f.readlines()

        new_lines = []
        found = False
        for line in lines:
            if line.startswith("ACCOUNT_EQUITY="):
                old_val = float(
                    line.split("=")[1].strip()
                )
                new_val = old_val + delta
                new_lines.append(
                    f"ACCOUNT_EQUITY={new_val:.2f}\n"
                )
                found = True
                logger.info(
                    f"Equity updated: "
                    f"${old_val:,.2f} -> ${new_val:,.2f}"
                )
            else:
                new_lines.append(line)

        if not found:
            new_lines.append(
                f"ACCOUNT_EQUITY={delta:.2f}\n"
            )

        with open(env_path, "w") as f:
            f.writelines(new_lines)

    def get_total_deposited(self):
        return sum(
            t["amount"] for t in self.transactions
            if t["type"] == "DEPOSIT"
        )

    def get_total_withdrawn(self):
        return sum(
            t["amount"] for t in self.transactions
            if t["type"] == "WITHDRAWAL"
        )

    def get_net_deposits(self):
        return self.get_total_deposited() - self.get_total_withdrawn()

    def get_trading_pnl(self, current_equity):
        """
        True trading P&L = current equity minus
        all net deposits. This separates trading
        performance from cash flow.
        """
        starting = 0
        if self.transactions:
            first = self.transactions[0]
            if first["type"] == "DEPOSIT":
                starting = first["amount"]

        net_added = self.get_net_deposits() - starting
        pnl = current_equity - starting - net_added
        return round(pnl, 2)

    def get_summary(self, current_equity):
        net_dep = self.get_net_deposits()
        trading_pnl = self.get_trading_pnl(current_equity)
        if net_dep > 0:
            roi = trading_pnl / net_dep
        else:
            roi = 0
        return {
            "current_equity": round(current_equity, 2),
            "total_deposited": round(
                self.get_total_deposited(), 2
            ),
            "total_withdrawn": round(
                self.get_total_withdrawn(), 2
            ),
            "net_deposits": round(net_dep, 2),
            "trading_pnl": trading_pnl,
            "roi": round(roi, 4),
            "transaction_count": len(self.transactions),
        }

    def print_summary(self, current_equity):
        s = self.get_summary(current_equity)
        logger.info("=" * 45)
        logger.info("ACCOUNT SUMMARY")
        logger.info("=" * 45)
        logger.info(
            f"  Current equity:  ${s['current_equity']:>10,.2f}"
        )
        logger.info(
            f"  Total deposited: ${s['total_deposited']:>10,.2f}"
        )
        logger.info(
            f"  Total withdrawn: ${s['total_withdrawn']:>10,.2f}"
        )
        logger.info(
            f"  Net deposits:    ${s['net_deposits']:>10,.2f}"
        )
        logger.info(
            f"  Trading P&L:     ${s['trading_pnl']:>+10,.2f}"
        )
        logger.info(
            f"  ROI:             {s['roi']:>+10.1%}"
        )
        logger.info("=" * 45)
