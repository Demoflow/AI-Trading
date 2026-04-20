"""
Schwab Manual Authentication (headless / SSH-compatible).

Use this script on the VPS or any machine without a browser.
It uses schwab-py's manual flow: you open a URL on your phone/laptop,
log in, then paste the redirect URL back here.

Usage:
    python scripts/authenticate_manual.py
"""

import os
import sys
import json
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )
)

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")


def authenticate():
    from schwab import auth
    import httpx

    api_key = os.getenv("SCHWAB_API_KEY")
    app_secret = os.getenv("SCHWAB_APP_SECRET")
    callback_url = os.getenv(
        "SCHWAB_CALLBACK_URL",
        "https://127.0.0.1:8182",
    )
    token_path = os.getenv(
        "SCHWAB_TOKEN_PATH",
        "config/schwab_token.json",
    )

    if not api_key or not app_secret:
        print("ERROR: Set SCHWAB_API_KEY and SCHWAB_APP_SECRET in .env")
        return

    print("=" * 60)
    print("  SCHWAB MANUAL AUTHENTICATION (headless)")
    print("=" * 60)
    print()
    print("This flow works over SSH - no browser needed on this machine.")
    print()
    print("Steps:")
    print("  1. The script will print a URL below.")
    print("  2. Open that URL in a browser on your phone or laptop.")
    print("  3. Log in to Schwab and authorize the app.")
    print("  4. You will be redirected to a URL that may not load -")
    print("     that is normal. Copy the FULL URL from the address bar.")
    print("  5. Paste it here when prompted.")
    print()

    os.makedirs(os.path.dirname(token_path) or "config", exist_ok=True)

    try:
        client = auth.client_from_manual_flow(
            api_key=api_key,
            app_secret=app_secret,
            callback_url=callback_url,
            token_path=token_path,
        )

        print()
        print("=" * 60)
        print("  Authentication successful!")
        print(f"  Token saved to: {token_path}")

        # Show token expiry info
        try:
            with open(token_path) as f:
                tok = json.load(f)
            created = tok.get("creation_timestamp")
            if created:
                created_dt = datetime.fromtimestamp(created, tz=timezone.utc)
                expires_dt = datetime.fromtimestamp(created + 7 * 86400, tz=timezone.utc)
                print(f"  Token created:  {created_dt.strftime('%Y-%m-%d %H:%M UTC')}")
                print(f"  Token expires:  {expires_dt.strftime('%Y-%m-%d %H:%M UTC')}")
                print(f"  Re-auth needed before: {expires_dt.strftime('%A %B %d')}")
        except Exception:
            pass

        print("=" * 60)
        print()

        # Verify by fetching accounts
        resp = client.get_account_numbers()
        if resp.status_code == httpx.codes.OK:
            accounts = resp.json()
            print(f"Verified: {len(accounts)} account(s) accessible")
            for acc in accounts:
                num = acc["accountNumber"]
                h = acc["hashValue"]
                print(f"  Account: {num}")
                print(f"  Hash:    {h}")
            if accounts:
                h = accounts[0]["hashValue"]
                print()
                print("Make sure your .env contains:")
                print(f"  SCHWAB_ACCOUNT_HASH={h}")
        else:
            print(f"Account verification returned status: {resp.status_code}")

    except Exception as e:
        print(f"\nError: {e}")
        print()
        print("Common issues:")
        print("  - Make sure you pasted the FULL redirect URL")
        print("  - The URL should start with your callback URL")
        print("  - Your Schwab app credentials may have expired")


if __name__ == "__main__":
    authenticate()
