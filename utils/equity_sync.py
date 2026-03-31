"""
#20 - Equity Sync with Schwab.
Pulls real balance on startup instead of hardcoded.
"""

import os
from loguru import logger
from dotenv import load_dotenv

load_dotenv()


class EquitySync:

    def get_real_equity(self, schwab_client=None):
        """
        Try to get real equity from Schwab.
        Falls back to env variable.
        """
        if schwab_client:
            try:
                from data.broker.schwab_auth import (
                    get_account_hash,
                    get_account_positions,
                )
                ah = os.getenv("SCHWAB_ACCOUNT_HASH")
                if not ah:
                    ah = get_account_hash(schwab_client)
                data = get_account_positions(
                    schwab_client, ah
                )
                real_eq = data.get("equity", 0)
                if real_eq > 0:
                    logger.info(
                        f"Schwab equity: ${real_eq:,.2f}"
                    )
                    return real_eq
            except Exception as e:
                logger.warning(
                    f"Could not fetch Schwab equity: {e}"
                )

        eq = float(os.getenv("ACCOUNT_EQUITY", "8000"))
        logger.info(f"Using env equity: ${eq:,.2f}")
        return eq
