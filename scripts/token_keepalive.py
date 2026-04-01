"""
Token Keep-Alive.
Makes one API call to keep the refresh token rolling.
Run daily via scheduler or cron.
"""

import os
import sys
import json
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger
from dotenv import load_dotenv

load_dotenv()


def keepalive():
    tp = os.getenv("SCHWAB_TOKEN_PATH", "config/schwab_token.json")
    if not Path(tp).exists():
        logger.warning("No token file")
        return False

    try:
        with open(tp) as f:
            token = json.load(f)
        created = token.get("creation_timestamp", 0)
        try:
            ct = datetime.fromtimestamp(float(created))
            age_days = (datetime.now() - ct).days
        except Exception:
            age_days = -1

        from data.broker.schwab_auth import get_schwab_client
        client = get_schwab_client()
        import httpx
        resp = client.get_account_numbers()
        if resp.status_code == httpx.codes.OK:
            logger.info(
                f"Token alive (age: {age_days}d). "
                f"Refresh successful."
            )
            return True
        else:
            logger.warning(f"API returned {resp.status_code}")
            return False
    except Exception as e:
        if "refresh_token_authentication_error" in str(e):
            logger.critical("Schwab refresh token EXPIRED (7-day limit). Must re-authenticate!")
            logger.critical("Run: python scripts/authenticate_schwab.py")
        else:
            logger.error(f"Keepalive failed: {e}")
            logger.error("Re-authenticate: python scripts/authenticate_schwab.py")
        return False


if __name__ == "__main__":
    keepalive()
