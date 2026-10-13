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

from config import OPENCODE_PROBE_MODEL, OPENCODE_ZEN_BASE_URL

# Same flow as packages/core/src/plugin/provider/opencode.ts in github.com/anomalyco/opencode
OPENCODE_CONSOLE_URL = "https://opencode.ai/console"
OPENCODE_CLIENT_ID = "opencode-cli"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
# What the installed client actually puts on the wire (captured from opencode 1.18.31):
#   opencode/1.18.31 ai-sdk/provider-utils/4.0.23 runtime/bun/1.3.14
# The AI SDK appends its own half to the `opencode/<version>` the client supplies. The gateway does
# not gate on this (verified against both this string and a nonsense value), but matching it keeps the
# request indistinguishable from the app's, so every part is overridable instead of hardcoded.
OPENCODE_VERSION = os.environ.get("OPENCODE_CLIENT_VERSION", "1.18.31")
OPENCODE_SDK_VERSION = os.environ.get("OPENCODE_AI_SDK_VERSION", "4.0.23")
OPENCODE_RUNTIME = os.environ.get("OPENCODE_RUNTIME", f"bun/{os.environ.get('OPENCODE_BUN_VERSION', '1.3.14')}")
OPENCODE_USER_AGENT = (
    f"opencode/{OPENCODE_VERSION} ai-sdk/provider-utils/{OPENCODE_SDK_VERSION} "
    f"runtime/{OPENCODE_RUNTIME}"
)
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


def api_key_account(api_key: str) -> Dict[str, Any]:
    """Account entry for a Console API key. The key is the credential the docs issue for
    programmatic access (https://opencode.ai/docs/zen) and the one free-tier models are served to."""
    key_id = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:8]
    return {"id": f"opencode_key_{key_id}@opencode.ai", "email": f"opencode_key_{key_id}@opencode.ai",
            "provider": "opencode", "access_token": api_key}


def store_api_key(token_manager, api_key: str) -> Dict[str, Any]:
    """Verifies the key against the public model list before storing it, so a typo fails here
    rather than as a 401 on the next conversation turn."""
    account = api_key_account(api_key)
    request = urllib.request.Request(
        f"{OPENCODE_ZEN_BASE_URL}/models",
        headers={"Accept": "application/json", "User-Agent": OPENCODE_USER_AGENT,
                 "Authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(f"model list returned HTTP {response.status}")
    token_manager.add_or_update_account(account)
    return account


def probe_access(access_token: str, model: Optional[str] = None, base_url: Optional[str] = None) -> Dict[str, Any]:
    """Sends the smallest possible real request and reports what Zen says.

    This is the only way to tell the three failure modes apart - bad key, no funds, free-tier gate -
    because they all arrive as one HTTP status with a type the client has to interpret.
    """
    model = model or OPENCODE_PROBE_MODEL
    url = f"{(base_url or OPENCODE_ZEN_BASE_URL).rstrip('/')}/chat/completions"
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": "ping"}],
                       "max_completion_tokens": 8, "stream": False}).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json", "User-Agent": OPENCODE_USER_AGENT,
        "Authorization": f"Bearer {access_token}",
    })
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return {"ok": True, "status": response.status, "model": model, "message": ""}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "model": model, "message": e.read().decode("utf-8", "replace")[:400]}
    except Exception as e:
        return {"ok": False, "status": None, "model": model, "message": repr(e)}


async def login_opencode_flow(token_manager, api_key: Optional[str] = None) -> bool:
    """Stores a Console API key, or runs the interactive device flow when no key is given."""
    if api_key:
        try:
            account = store_api_key(token_manager, api_key.strip())
        except Exception as e:
            print(f"OpenCode API key rejected: {e}")
            return False
        print(f"Successfully added OpenCode API key account '{account['email']}' to pool!")
        return True

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
        account = api_key_account(api_key)

    account.update({"id": account["email"], "provider": "opencode"})
    token_manager.add_or_update_account(account)
    print(f"Successfully added OpenCode account '{account['email']}' to pool!")
    return True
