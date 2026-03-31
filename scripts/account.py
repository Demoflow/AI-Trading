"""
Account management CLI.
Usage:
  python scripts/account.py deposit 2000
  python scripts/account.py withdraw 500
  python scripts/account.py summary
  python scripts/account.py history
"""

import os
import sys

sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )
)

from loguru import logger
from dotenv import load_dotenv

load_dotenv()


def main():
    from utils.account_manager import AccountManager
    am = AccountManager()

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python scripts/account.py deposit 2000")
        print("  python scripts/account.py withdraw 500")
        print("  python scripts/account.py summary")
        print("  python scripts/account.py history")
        return

    cmd = sys.argv[1].lower()

    if cmd == "deposit":
        if len(sys.argv) < 3:
            print("Specify amount: python scripts/account.py deposit 2000")
            return
        amount = float(sys.argv[2])
        print(f"Depositing ${amount:,.2f}...")
        am.deposit(amount)
        eq = float(os.getenv("ACCOUNT_EQUITY", "8000"))
        am.print_summary(eq + amount)

    elif cmd == "withdraw":
        if len(sys.argv) < 3:
            print("Specify amount: python scripts/account.py withdraw 500")
            return
        amount = float(sys.argv[2])
        eq = float(os.getenv("ACCOUNT_EQUITY", "8000"))
        if amount > eq:
            print(f"Cannot withdraw ${amount:,.2f} from ${eq:,.2f}")
            return
        print(f"Withdrawing ${amount:,.2f}...")
        am.withdraw(amount)
        am.print_summary(eq - amount)

    elif cmd == "summary":
        eq = float(os.getenv("ACCOUNT_EQUITY", "8000"))
        am.print_summary(eq)

    elif cmd == "history":
        for t in am.transactions:
            print(
                f"  {t['date']} {t['type']:10s} "
                f"${t['amount']:>10,.2f}"
            )
        if not am.transactions:
            print("  No transactions recorded.")

    else:
        print(f"Unknown command: {cmd}")


if __name__ == "__main__":
    main()
