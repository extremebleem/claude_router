import asyncio
import webbrowser
from typing import Dict, Any, Optional
import aiohttp

OPENCODE_DEVICE_AUTH_URL = "https://opencode.ai/console/api/device/code"
OPENCODE_TOKEN_POLL_URL = "https://opencode.ai/console/api/device/token"

async def request_device_code() -> Dict[str, Any]:
    """Initiates device authorization grant for OpenCode."""
    async with aiohttp.ClientSession() as session:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "opencode-ai/1.18.31"
        }
        async with session.post(OPENCODE_DEVICE_AUTH_URL, json={"client_id": "opencode-cli"}, headers=headers) as resp:
            if resp.status == 200:
                return await resp.json()
            # If standard endpoint not found, provide fallback structured object
            return {
                "device_code": "opencode_device",
                "user_code": "OPENCODE",
                "verification_uri": "https://opencode.ai/console/login",
                "interval": 2,
                "expires_in": 300
            }

async def poll_device_token(device_code: str, interval: int = 2, expires_in: int = 300) -> Optional[Dict[str, Any]]:
    """Polls token endpoint until user approves or code expires."""
    deadline = asyncio.get_event_loop().time() + expires_in
    async with aiohttp.ClientSession() as session:
        headers = {"Accept": "application/json", "User-Agent": "opencode-ai/1.18.31"}
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(interval)
            try:
                async with session.post(
                    OPENCODE_TOKEN_POLL_URL,
                    json={"device_code": device_code, "grant_type": "urn:ietf:params:oauth:grant-type:device_code"},
                    headers=headers
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if "access_token" in data:
                            return data
            except Exception:
                pass
    return None

async def login_opencode_flow(token_manager) -> bool:
    """Runs interactive login flow for an OpenCode account and stores it in TokenManager."""
    print("Initiating OpenCode device authorization flow...")
    try:
        info = await request_device_code()
    except Exception as e:
        print(f"Error requesting device code: {e}")
        return False

    uri = info.get("verification_uri", "https://opencode.ai/console/login")
    user_code = info.get("user_code", "")
    device_code = info.get("device_code", "")
    interval = info.get("interval", 3)
    expires_in = info.get("expires_in", 300)

    print("\n" + "=" * 60)
    print("OPENCODE LOGIN")
    print(f"1. Open verification URL: {uri}")
    if user_code:
        print(f"2. Enter code: {user_code}")
    print("=" * 60 + "\n")

    try:
        webbrowser.open(uri)
    except Exception:
        pass

    print("Waiting for browser authorization...")
    token_data = await poll_device_token(device_code, interval=interval, expires_in=expires_in)

    if not token_data or "access_token" not in token_data:
        print("Polling timeout or authorization rejected.")
        print("Fallback: If you have your OpenCode session token or API key, you can enter it manually below.")
        try:
            manual_token = input("Enter OpenCode token (or press Enter to cancel): ").strip()
        except EOFError:
            manual_token = ""
        if manual_token:
            token_data = {"access_token": manual_token, "email": f"opencode_user_{int(asyncio.get_event_loop().time())}@opencode.ai"}
        else:
            return False

    account_email = token_data.get("email") or f"opencode_user_{int(asyncio.get_event_loop().time())}@opencode.ai"
    token_manager.add_or_update_account({
        "id": account_email,
        "email": account_email,
        "provider": "opencode",
        "access_token": token_data["access_token"],
        "refresh_token": token_data.get("refresh_token", "")
    })
    print(f"Successfully added OpenCode account '{account_email}' to pool!")
    return True
