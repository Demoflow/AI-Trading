"""
Schwab authentication - updated for schwab-py 1.5+
"""

import os
import httpx
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

SCHWAB_API_KEY = os.getenv("SCHWAB_API_KEY")
SCHWAB_APP_SECRET = os.getenv("SCHWAB_APP_SECRET")
SCHWAB_CALLBACK_URL = os.getenv(
    "SCHWAB_CALLBACK_URL", "https://127.0.0.1:8182"
)
SCHWAB_TOKEN_PATH = os.getenv(
    "SCHWAB_TOKEN_PATH", "config/schwab_token.json"
)


def _check_token_age():
    """
    Log how many days remain before the Schwab refresh token expires (7-day window).
    Warns at <3 days remaining so the user knows to re-authenticate before the
    next trading day.
    """
    import json as _json
    from pathlib import Path as _Path
    from datetime import datetime as _dt, timezone as _tz

    p = _Path(SCHWAB_TOKEN_PATH)
    if not p.exists():
        return
    try:
        with open(p) as f:
            tok = _json.load(f)
        created_ts = tok.get("creation_timestamp")
        if created_ts is None:
            return
        age_days = (_dt.now(_tz.utc).timestamp() - created_ts) / 86400
        days_left = 7.0 - age_days
        if days_left < 0:
            logger.error(
                f"Schwab refresh token EXPIRED ({age_days:.1f} days old). "
                f"Desktop: python scripts/authenticate_schwab.py | "
                f"VPS/SSH: python scripts/authenticate_manual.py"
            )
        elif days_left < 3:
            logger.warning(
                f"Schwab refresh token expires in {days_left:.1f} day(s). "
                f"Desktop: python scripts/authenticate_schwab.py | "
                f"VPS/SSH: python scripts/authenticate_manual.py"
            )
        else:
            logger.info(
                f"Schwab token age: {age_days:.1f}d | "
                f"{days_left:.1f} day(s) until re-auth required"
            )
    except Exception:
        pass


def get_schwab_client():
    from schwab import auth
    from pathlib import Path

    if not SCHWAB_API_KEY or not SCHWAB_APP_SECRET:
        raise ValueError("Set SCHWAB_API_KEY and APP_SECRET")

    _check_token_age()

    if Path(SCHWAB_TOKEN_PATH).exists():
        try:
            client = auth.client_from_token_file(
                token_path=SCHWAB_TOKEN_PATH,
                api_key=SCHWAB_API_KEY,
                app_secret=SCHWAB_APP_SECRET,
            )
            logger.info("Schwab client loaded from token")
            return client
        except Exception as e:
            if "refresh_token_authentication_error" in str(e):
                logger.error("Schwab refresh token EXPIRED (7-day limit).")
                logger.error("Re-authenticate (desktop): python scripts/authenticate_schwab.py")
                logger.error("Re-authenticate (VPS/SSH): python scripts/authenticate_manual.py")
            else:
                logger.warning(f"Token load failed: {e}")
                logger.warning("Desktop: python scripts/authenticate_schwab.py")
                logger.warning("VPS/SSH: python scripts/authenticate_manual.py")
            raise
    else:
        raise FileNotFoundError(
            "No token file. Run: python scripts/authenticate_schwab.py"
        )


def get_account_hash(client):
    resp = client.get_account_numbers()
    assert resp.status_code == httpx.codes.OK
    accounts = resp.json()
    if not accounts:
        raise ValueError("No linked accounts")
    h = accounts[0]["hashValue"]
    n = accounts[0]["accountNumber"]
    logger.info(f"Using account {n}")
    return h


def get_account_positions(client, account_hash):
    from schwab.client import Client
    resp = client.get_account(
        account_hash,
        fields=[Client.Account.Fields.POSITIONS],
    )
    assert resp.status_code == httpx.codes.OK
    data = resp.json()
    sa = data.get("securitiesAccount", {})
    bal = sa.get("currentBalances", {})
    raw = sa.get("positions", [])
    positions = []
    for p in raw:
        inst = p.get("instrument", {})
        if inst.get("assetType") == "EQUITY":
            positions.append({
                "symbol": inst.get("symbol"),
                "quantity": p.get("longQuantity", 0),
                "market_value": p.get("marketValue", 0),
                "avg_price": p.get("averagePrice", 0),
            })
    return {
        "cash_available": bal.get("availableFunds", 0),
        "equity": bal.get("equity", 0),
        "positions": positions,
    }
