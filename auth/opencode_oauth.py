import asyncio
import hashlib
import json
import os
import time
import urllib.parse
import urllib.request
import webbrowser
from typing import Dict, Any, Optional
import aiohttp

from config import OPENCODE_CLIENT_NAME

# Same flow as packages/core/src/plugin/provider/opencode.ts in github.com/anomalyco/opencode
OPENCODE_CONSOLE_URL = "https://opencode.ai/console"
OPENCODE_CLIENT_ID = "opencode-cli"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
# App.useragent() (packages/core/src/app.ts) is `opencode/<channel>/<version>/<name>`;
# the Console reads it to identify the client. Both are build-time defines upstream,
# so they are overridable here instead of hardcoded.
OPENCODE_CHANNEL = os.environ.get("OPENCODE_CHANNEL", "stable")
OPENCODE_VERSION = os.environ.get("OPENCODE_CLIENT_VERSION", "2.0.28")
OPENCODE_USER_AGENT = f"opencode/{OPENCODE_CHANNEL}/{OPENCODE_VERSION}/{OPENCODE_CLIENT_NAME}"
HEADERS = {"Accept": "application/json", "User-Agent": OPENCODE_USER_AGENT}


async def request_device_code() -> Dict[str, Any]:
    """Initiates device authorization grant for OpenCode Console."""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{OPENCODE_CONSOLE_URL}/auth/device/code",
            json={"client_id": OPENCODE_CLIENT_ID, "supports_org_scope": True},
            headers=HEADERS,
        ) as resp:
            if resp.status != 200:
                raise RuntimeError(f"device code request failed (HTTP {resp.status}): {await resp.text()}")
            return await resp.json()


async def poll_device_token(device_code: str, interval: int = 5, expires_in: int = 600) -> Optional[Dict[str, Any]]:
    """Polls token endpoint until the user approves, denies, or the code expires."""
    deadline = time.monotonic() + expires_in
    last_problem = None
    # A hung request must not stall the loop: the default aiohttp timeout is 5 minutes
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
        while time.monotonic() < deadline:
            await asyncio.sleep(interval)
            try:
                async with session.post(
                    f"{OPENCODE_CONSOLE_URL}/auth/device/token",
                    json={"grant_type": DEVICE_GRANT, "device_code": device_code, "client_id": OPENCODE_CLIENT_ID},
                    headers=HEADERS,
                ) as resp:
                    status, body = resp.status, await resp.text()
                data = json.loads(body)
                if not isinstance(data, dict):
                    raise ValueError("not a JSON object")
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
                # transient error: retry on the next tick, but show it once instead of hiding it
                problem = repr(e)
                if problem != last_problem:
                    print(f"  poll problem, retrying: {problem}")
                    last_problem = problem
                continue
            if "access_token" in data:
                return data
            error = data.get("error")
            if error is None and last_problem != f"http{status}":
                print(f"  unexpected token response (HTTP {status}): {body[:200]}")
                last_problem = f"http{status}"
            if error == "slow_down":
                interval += 5
            elif error not in (None, "authorization_pending"):
                print(f"Device authorization failed: {error}")
                return None
    return None


async def fetch_opencode_user(access_token: str) -> Dict[str, Any]:
    """Returns {id, email} of the Console account; the token response carries no email."""
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
        async with session.get(
            f"{OPENCODE_CONSOLE_URL}/api/user",
            headers={**HEADERS, "Authorization": f"Bearer {access_token}"},
        ) as resp:
            if resp.status != 200:
                raise RuntimeError(f"user info request failed (HTTP {resp.status}): {await resp.text()}")
            return await resp.json()


def refresh_opencode_token(refresh_token: str) -> Dict[str, Any]:
    """Refreshes an OpenCode Console access token. HTTP 400 invalid_grant means a new login is needed."""
    body = json.dumps({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": OPENCODE_CLIENT_ID,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{OPENCODE_CONSOLE_URL}/auth/device/token",
        data=body,
        method="POST",
        headers={**HEADERS, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    payload["expires_at"] = time.time() + payload["expires_in"]
    return payload


async def login_opencode_flow(token_manager) -> bool:
    """Runs interactive login flow for an OpenCode account and stores it in TokenManager."""
    print("Initiating OpenCode device authorization flow...")
    account = None
    try:
        info = await request_device_code()
        # The Console returns a relative URI such as /console/device?user_code=...
        uri = urllib.parse.urljoin(OPENCODE_CONSOLE_URL + "/", info["verification_uri_complete"])
        if urllib.parse.urlparse(uri).scheme not in ("http", "https"):
            raise RuntimeError(f"unexpected verification URL: {uri}")

        print("\n" + "=" * 60)
        print("OPENCODE LOGIN")
        print(f"1. Open verification URL: {uri}")
        print(f"2. Confirm code: {info['user_code']}")
        print("=" * 60 + "\n")
        try:
            webbrowser.open(uri)
        except Exception:
            pass

        print("Waiting for browser authorization...")
        token_data = await poll_device_token(
            info["device_code"], interval=info.get("interval", 5), expires_in=info.get("expires_in", 600)
        )
        if token_data:
            print("Authorized, fetching account info...")
            user = await fetch_opencode_user(token_data["access_token"])
            account = {
                "email": user["email"],
                "access_token": token_data["access_token"],
                "refresh_token": token_data["refresh_token"],
                "expires_at": time.time() + token_data["expires_in"],
            }
        else:
            print("Polling timeout or authorization rejected.")
    except Exception as e:
        print(f"OpenCode device authorization error: {e}")

    if not account:
        print("Fallback: you can paste an OpenCode Zen API key instead.")
        try:
            api_key = input("Enter OpenCode API key (or press Enter to cancel): ").strip()
        except EOFError:
            api_key = ""
        if not api_key:
            return False
        # Stable id: adding the same key again updates the entry instead of duplicating it
        key_id = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:8]
        account = {"email": f"opencode_key_{key_id}@opencode.ai", "access_token": api_key}

    account.update({"id": account["email"], "provider": "opencode"})
    token_manager.add_or_update_account(account)
    print(f"Successfully added OpenCode account '{account['email']}' to pool!")
    return True
