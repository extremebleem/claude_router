"""
Async Client for Google Cloud Code Internal API.
Sends wrapped requests to v1internal:streamGenerateContent with automatic multi-account
failover, token refresh, and endpoint fallback.
"""

import aiohttp
import asyncio
import json
import re
from pathlib import Path
from typing import AsyncGenerator, Dict, Any, Tuple, Optional, List

from config import (
    CLOUDCODE_ENDPOINTS,
    ANTIGRAVITY_HEADERS,
    DEFAULT_FALLBACK_PROJECT_ID,
    SAVE_DETAILED_REQUEST_LOGS,
)
from auth.token_manager import TokenManager
from transformer.content import convert_anthropic_to_google
from logger import log

LOGS_DIR = Path(__file__).parent.parent / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)


def parse_retry_delay_from_error(error_text: str, default_seconds: float = 300.0) -> float:
    """Attempts to extract reset delay from Google API error response."""
    # Look for "reset in 120s" or "retry after: 60" or ISO timestamps
    match = re.search(r"(\d+(?:\.\d+)?)\s*s(?:ec|econds)?", error_text, re.IGNORECASE)
    if match:
        try:
            return max(30.0, float(match.group(1)))
        except ValueError:
            pass
    return default_seconds


class AntigravityClient:
    def __init__(self, token_manager: TokenManager, project_id: Optional[str] = None):
        self.token_manager = token_manager
        self.project_id = project_id

    # Backward compatibility alias
    async def generate_stream(self, *args, **kwargs):
        return await self.generate_content_stream(*args, **kwargs)

    async def _send_to_account_endpoints(
        self,
        session: aiohttp.ClientSession,
        account: Dict[str, Any],
        anthropic_request: Dict[str, Any],
        model: str,
        req_id: Optional[str] = None
    ) -> Tuple[Optional[aiohttp.ClientResponse], Optional[int], Optional[str]]:
        """
        Attempts sending the request to Cloud Code endpoints for a specific account.
        Returns: (response, status_code, error_text)
        If status_code is 200, response is returned.
        """
        email = account.get("email", "unknown")
        access_token, project_id = self.token_manager.get_token_for_account(account)
        if not access_token:
            return None, 401, f"Failed to acquire token for {email}"

        effective_project_id = project_id or DEFAULT_FALLBACK_PROJECT_ID
        if not project_id:
            log(f"[CloudCode] [{email}] Warning: GCP project_id not found, using fallback '{effective_project_id}'.", level="WARN")

        payload = convert_anthropic_to_google(
            anthropic_request,
            project_id=effective_project_id,
            account_email=email
        )

        if req_id and SAVE_DETAILED_REQUEST_LOGS:
            try:
                p_file = LOGS_DIR / f"req_{req_id}_google_payload.json"
                with open(p_file, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2, ensure_ascii=False)
            except Exception as e:
                log(f"[Logging Error] {e}", level="ERROR")

        headers = {
            "Authorization": f"Bearer {access_token}",
            **ANTIGRAVITY_HEADERS,
        }
        session_id = payload.get("request", {}).get("sessionId")
        if session_id:
            headers["X-Machine-Session-Id"] = session_id
        if "claude" in model.lower() and "thinking" in model.lower():
            headers["anthropic-beta"] = "interleaved-thinking-2025-05-14"

        last_status = None
        last_err_text = None
        # Keep the FIRST failure separately. CLOUDCODE_ENDPOINTS is tried in order
        # (daily first, prod second) and prod often answers 429 even when the real
        # fault is a 400 from daily. Reporting only the last error hides the cause.
        first_status = None
        first_err_text = None

        for endpoint in CLOUDCODE_ENDPOINTS:
            url = f"{endpoint}/v1internal:streamGenerateContent?alt=sse"
            try:
                log(f"[CloudCode] [{email}] Dispatching request {req_id or ''} to {endpoint} (model: {model})...")
                resp = await session.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=180, sock_read=90)
                )

                if resp.status == 200:
                    log(f"[CloudCode] [{email}] ✓ HTTP 200 OK from {endpoint}")
                    if req_id and SAVE_DETAILED_REQUEST_LOGS:
                        try:
                            with open(LOGS_DIR / f"req_{req_id}_google_success.json", "w", encoding="utf-8") as f:
                                json.dump({"status": 200, "endpoint": endpoint, "model": model, "account": email, "headers": dict(resp.headers)}, f, indent=2)
                        except Exception:
                            pass
                    return resp, 200, None

                # 401: Refresh token and retry once
                if resp.status == 401:
                    err_text = await resp.text()
                    resp.close()
                    log(f"[CloudCode] [{email}] Received 401 Unauthenticated. Force refreshing token...", level="WARN")
                    new_token, _ = self.token_manager.get_token_for_account(account, force_refresh=True)
                    if new_token and new_token != access_token:
                        headers["Authorization"] = f"Bearer {new_token}"
                        retry_resp = await session.post(url, headers=headers, json=payload)
                        if retry_resp.status == 200:
                            log(f"[CloudCode] [{email}] ✓ Retry after 401 succeeded (HTTP 200)")
                            return retry_resp, 200, None
                        else:
                            retry_err = await retry_resp.text()
                            retry_resp.close()
                            return None, retry_resp.status, retry_err
                    return None, 401, err_text

                # 429: Rate Limit / Quota Exhaustion
                if resp.status == 429:
                    err_text = await resp.text()
                    resp.close()
                    log(f"[CloudCode] [{email}] ✗ HTTP 429 from {endpoint}: {err_text}", level="WARN")
                    if first_status is None:
                        first_status, first_err_text = 429, err_text
                    # A genuine quota error on the first endpoint is the real cause.
                    # Otherwise report the earlier, more specific failure instead of
                    # letting this generic 429 mask it.
                    if first_status != 429:
                        return None, first_status, first_err_text
                    return None, 429, err_text

                # 403: Forbidden / Permission Denied
                if resp.status == 403:
                    err_text = await resp.text()
                    resp.close()
                    log(f"[CloudCode] [{email}] ✗ HTTP 403 from {endpoint}: {err_text}", level="ERROR")
                    if first_status is None:
                        first_status, first_err_text = 403, err_text
                    last_status = 403
                    last_err_text = err_text
                    continue

                err_text = await resp.text()
                resp.close()
                log(f"[CloudCode] [{email}] ✗ HTTP {resp.status} from {endpoint}: {err_text}", level="ERROR")
                if first_status is None:
                    first_status, first_err_text = resp.status, err_text
                last_status = resp.status
                last_err_text = err_text

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                log(f"[CloudCode] [{email}] Connection exception to {endpoint}: {e}", level="WARN")
                if first_status is None:
                    first_status, first_err_text = None, str(e)
                last_err_text = str(e)
                continue

        return None, first_status or last_status, first_err_text or last_err_text

    async def generate_content_stream(
        self,
        session: aiohttp.ClientSession,
        anthropic_request: Dict[str, Any],
        req_id: Optional[str] = None
    ) -> Tuple[aiohttp.ClientResponse, str]:
        """
        Sends request to Google Cloud Code with automatic multi-account rotation on 429.
        """
        model = anthropic_request.get("model", "claude-opus-4-6-thinking")

        # 1. Retrieve all non-exhausted accounts for this model
        available_accounts = self.token_manager.get_available_accounts(model)
        if not available_accounts:
            # Check if there are any accounts at all
            all_accs = self.token_manager.get_accounts()
            if not all_accs:
                raise RuntimeError(
                    "No authorization token found. Run 'python cli_auth.py login' before starting."
                )
            # All accounts are currently in cooldown
            exhausted_info = self.token_manager.get_exhausted_status()
            raise RuntimeError(
                f"RESOURCE_EXHAUSTED (429): All available accounts ({len(all_accs)}) exhausted quota for model {model}. "
                f"Cooldown status: {exhausted_info}"
            )

        log(f"[Multi-Account] Accounts available in pool for {model}: {len(available_accounts)}")

        last_error_message = None

        for idx, account in enumerate(available_accounts):
            email = account.get("email", f"account_{idx}")
            resp, status, err_text = await self._send_to_account_endpoints(
                session=session,
                account=account,
                anthropic_request=anthropic_request,
                model=model,
                req_id=req_id
            )

            if status == 200 and resp is not None:
                # Successfully received response
                return resp, model

            if status == 429:
                cooldown = parse_retry_delay_from_error(err_text or "", default_seconds=300.0)
                self.token_manager.mark_account_exhausted(email, model, cooldown_seconds=cooldown)
                last_error_message = f"RESOURCE_EXHAUSTED (429) on {email}: {err_text}"
                log(f"[Multi-Account] Account {email} exhausted limit for {model}. Switching to next account...", level="WARN")
                continue

            if status == 401:
                last_error_message = f"401 Authentication error on {email}: {err_text}"
                continue

            last_error_message = f"HTTP {status} error on {email}: {err_text}"

        raise RuntimeError(
            last_error_message or f"RESOURCE_EXHAUSTED (429): All accounts in pool ({len(available_accounts)}) temporarily exhausted quota."
        )


# Alias for backward compatibility
CloudCodeClient = AntigravityClient

