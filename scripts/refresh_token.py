"""
Schwab token refresh checker.
Run weekly or add to scheduler.
"""

import os
import sys
import json
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )
)

from loguru import logger
from dotenv import load_dotenv

load_dotenv()


def check_token():
    tp = os.getenv("SCHWAB_TOKEN_PATH", "config/schwab_token.json")
    if not Path(tp).exists():
        logger.warning("No token file found. Run initial auth first.")
        return False

    with open(tp) as f:
        token = json.load(f)

    created = token.get("creation_timestamp")
    if not created:
        logger.warning("No creation timestamp in token")
        return False

    try:
        ct = datetime.fromisoformat(str(created))
    except (ValueError, TypeError):
        try:
            ct = datetime.fromtimestamp(float(created))
        except (ValueError, TypeError):
            logger.warning("Cannot parse token timestamp")
            return False

    age = datetime.now() - ct
    days_old = age.days
    expires_in = 7 - days_old

    if expires_in <= 0:
        logger.error(
            f"Token EXPIRED ({days_old} days old). "
            f"Must re-authenticate!"
        )
        return False
    elif expires_in <= 1:
        logger.warning(
            f"Token expires in {expires_in} day(s)! "
            f"Refresh NOW."
        )
        return True
    elif expires_in <= 2:
        logger.warning(
            f"Token expires in {expires_in} days. "
            f"Plan to refresh soon."
        )
        return True
    else:
        logger.info(
            f"Token OK: {days_old} days old, "
            f"{expires_in} days until expiry."
        )
        return True


def refresh():
    logger.info("Attempting token refresh...")
    try:
        from data.broker.schwab_auth import get_schwab_client
        client = get_schwab_client()
        logger.info("Token refreshed successfully!")
        return True
    except Exception as e:
        logger.error(f"Refresh failed: {e}")
        logger.info(
            "You may need to re-authenticate manually."
        )
        return False


if __name__ == "__main__":
    valid = check_token()
    if not valid:
        logger.info("Attempting manual refresh...")
        refresh()
