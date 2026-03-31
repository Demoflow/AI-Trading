"""
Schwab authentication using login flow.
Opens browser, you log in, token is saved.
"""

import os
import sys

sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )
)

from dotenv import load_dotenv

load_dotenv()


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
        print("ERROR: Set SCHWAB_API_KEY and "
              "SCHWAB_APP_SECRET in .env")
        return

    print("=" * 50)
    print("SCHWAB AUTHENTICATION")
    print("=" * 50)
    print()
    print("A browser window will open.")
    print("Log into your Schwab account.")
    print("Authorize the app when prompted.")
    print("You may see a security warning about")
    print("the certificate - click Advanced and")
    print("proceed anyway. This is normal.")
    print()

    os.makedirs("config", exist_ok=True)

    try:
        client = auth.client_from_login_flow(
            api_key=api_key,
            app_secret=app_secret,
            callback_url=callback_url,
            token_path=token_path,
        )
        print()
        print("Authentication successful!")
        print(f"Token saved to: {token_path}")

        resp = client.get_account_numbers()
        if resp.status_code == httpx.codes.OK:
            accounts = resp.json()
            print(f"Found {len(accounts)} account(s):")
            for acc in accounts:
                num = acc["accountNumber"]
                h = acc["hashValue"]
                print(f"  Account: {num}")
                print(f"  Hash: {h}")
            if accounts:
                h = accounts[0]["hashValue"]
                print()
                print("Add this to your .env file:")
                print(f"SCHWAB_ACCOUNT_HASH={h}")
        else:
            print(f"Account check: {resp.status_code}")

    except Exception as e:
        print(f"\nError: {e}")
        print()
        print("If that failed, try manual flow:")
        print("Run: python scripts/authenticate_manual.py")


if __name__ == "__main__":
    authenticate()
