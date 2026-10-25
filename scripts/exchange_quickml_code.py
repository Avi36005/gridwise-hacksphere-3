"""One-time: trade a Zoho self-client grant code (QUICKML_AUTH_CODE in .env) for a refresh token.

The refresh token replaces the code in .env; nothing secret is printed.
"""
import os
from pathlib import Path
import sys

import requests

ENV = Path(__file__).resolve().parents[1] / ".env"


def main():
    lines = ENV.read_text().splitlines()
    values = dict(line.split("=", 1) for line in lines if "=" in line and not line.startswith("#"))
    missing = [k for k in ["QUICKML_CLIENT_ID", "QUICKML_CLIENT_SECRET", "QUICKML_AUTH_CODE"] if not values.get(k, "").strip()]
    if missing:
        print("Missing in .env: " + ", ".join(missing), file=sys.stderr)
        return 1
    accounts = os.getenv("ZOHO_ACCOUNTS_URL", values.get("ZOHO_ACCOUNTS_URL", "https://accounts.zoho.in"))
    reply = requests.post(f"{accounts}/oauth/v2/token", timeout=(5, 20), data={
        "grant_type": "authorization_code", "client_id": values["QUICKML_CLIENT_ID"].strip(),
        "client_secret": values["QUICKML_CLIENT_SECRET"].strip(), "code": values["QUICKML_AUTH_CODE"].strip()})
    body = reply.json()
    if "refresh_token" not in body:
        print(f"Exchange failed: {body.get('error', reply.status_code)} (codes expire after their time window; generate a new one).", file=sys.stderr)
        return 1
    kept = [line for line in lines if not line.startswith(("QUICKML_AUTH_CODE=", "QUICKML_REFRESH_TOKEN="))]
    ENV.write_text("\n".join(kept + [f"QUICKML_REFRESH_TOKEN={body['refresh_token']}"]) + "\n")
    print(f"Refresh token stored in .env (scope: {body.get('scope', 'unknown')}; access tokens last {body.get('expires_in', '?')} s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
