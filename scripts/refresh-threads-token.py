#!/usr/bin/env python3
"""Auto-refresh Threads long-lived access token.

Meta Threads API token lifecycle:
- Short-lived token (1 hour) -> obtained via OAuth login
- Long-lived token (60 days) -> obtained via th_exchange_token
- Long-lived tokens CANNOT be refreshed automatically once expired
- Must re-authorize via OAuth when expired

Usage:
  python3 refresh-threads-token.py              # Check status
  python3 refresh-threads-token.py --manual <SHORT_TOKEN>  # Exchange new token
"""

import httpx
import re
import sys
import json
from datetime import datetime

ENV_FILE = "/home/ubuntu/threads-agent/.env"


def load_env():
    creds = {}
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                creds[k] = v
    return creds


def save_token(new_token):
    with open(ENV_FILE) as f:
        content = f.read()
    content = re.sub(r'THREADS_ACCESS_TOKEN=\S+', 'THREADS_ACCESS_TOKEN=' + new_token, content)
    with open(ENV_FILE, 'w') as f:
        f.write(content)
    print(f"[OK] Token updated in {ENV_FILE}")


def check_token(token):
    r = httpx.get(
        "https://graph.threads.net/v1.0/me",
        params={"fields": "id,username", "access_token": token},
        timeout=15
    )
    if r.status_code == 200:
        return {"valid": True, "user": r.json()}
    error = r.json().get("error", {})
    return {"valid": False, "error": error.get("message", "?"), "code": error.get("code", 0)}


def exchange_short_lived(short_token, app_id, app_secret):
    r = httpx.get("https://graph.threads.net/v1.0/oauth/access_token", params={
        "grant_type": "th_exchange_token",
        "client_id": app_id,
        "client_secret": app_secret,
        "access_token": short_token,
    }, timeout=15)
    if r.status_code == 200:
        data = r.json()
        t = data.get("access_token")
        exp = data.get("expires_in", 0)
        if t:
            return {"success": True, "token": t, "expires_days": exp // 86400}
    error = r.json().get("error", {})
    return {"success": False, "error": error.get("message", "?")}


if __name__ == "__main__":
    creds = load_env()
    token = creds.get("THREADS_ACCESS_TOKEN", "")
    app_id = creds.get("THREADS_APP_ID", "")
    app_secret = creds.get("THREADS_APP_SECRET", "")

    if "--manual" in sys.argv:
        idx = sys.argv.index("--manual")
        if idx + 1 >= len(sys.argv):
            print("Usage: --manual <SHORT_LIVED_TOKEN>")
            sys.exit(1)
        short_token = sys.argv[idx + 1]
        print("[*] Exchanging short-lived token for long-lived...")
        result = exchange_short_lived(short_token, app_id, app_secret)
        if result["success"]:
            save_token(result["token"])
            verify = check_token(result["token"])
            if verify["valid"]:
                print(f"[OK] Verified. User: {verify['user']}. Expires in {result['expires_days']} days.")
            else:
                print(f"[WARN] Saved but verify failed: {verify['error']}")
        else:
            print(f"[ERR] Exchange failed: {result['error']}")
            sys.exit(1)
        sys.exit(0)

    # Default: check status
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] Threads Token Status")
    if not token:
        print("[ERR] No THREADS_ACCESS_TOKEN")
        sys.exit(1)
    print(f"Token length: {len(token)} chars")
    result = check_token(token)
    if result["valid"]:
        u = result["user"]
        print(f"VALID  user={u.get('username')} id={u.get('id')}")
    else:
        print(f"EXPIRED  error={result['error']}  code={result['code']}")
        print()
        print("Fix:")
        print("1. https://developers.facebook.com/tools/explorer/")
        print(f"2. App: {app_id}")
        print("3. Generate token with: threads_basic, threads_content_publish, threads_manage_insights")
        print("4. python3 refresh-threads-token.py --manual <TOKEN>")
        sys.exit(1)
