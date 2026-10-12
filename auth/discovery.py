"""
Project discovery and user provisioning for Antigravity Cloud Code API.
Detects Google Cloud projects, subscription tiers, and model quotas.
"""

import urllib.request
import json
import time
from typing import Dict, Any, Optional, Tuple

from config import (
    LOAD_CODE_ASSIST_ENDPOINTS,
    CLOUDCODE_ENDPOINTS,
    CLIENT_METADATA,
    ANTIGRAVITY_HEADERS,
    DEFAULT_FALLBACK_PROJECT_ID,
)


def discover_project_and_tier(access_token: str) -> Dict[str, Any]:
    """
    Calls v1internal:loadCodeAssist to discover the user's tier and associated GCP project.
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        **ANTIGRAVITY_HEADERS,
    }

    body = json.dumps({
        "metadata": CLIENT_METADATA,
        "mode": 1
    }).encode("utf-8")

    last_error = None
    for endpoint in LOAD_CODE_ASSIST_ENDPOINTS:
        url = f"{endpoint}/v1internal:loadCodeAssist"
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read().decode("utf-8"))

                project_id = None
                comp = data.get("cloudaicompanionProject")
                if isinstance(comp, str):
                    project_id = comp
                elif isinstance(comp, dict):
                    project_id = comp.get("id")

                # Parse tier
                tier = "free"
                paid_tier = data.get("paidTier", {})
                current_tier = data.get("currentTier", {})
                tier_id = paid_tier.get("id") or current_tier.get("id") or "free-tier"

                if "ultra" in str(tier_id).lower():
                    tier = "ultra"
                elif "pro" in str(tier_id).lower() or "standard" in str(tier_id).lower():
                    tier = "pro"

                return {
                    "project_id": project_id or DEFAULT_FALLBACK_PROJECT_ID,
                    "tier": tier,
                    "tier_id": tier_id,
                    "raw_response": data
                }
        except Exception as e:
            last_error = e
            continue

    return {
        "project_id": DEFAULT_FALLBACK_PROJECT_ID,
        "tier": "free",
        "tier_id": "free-tier",
        "error": str(last_error) if last_error else None
    }


def onboard_user(access_token: str, tier_id: str = "free-tier", max_attempts: int = 5) -> Optional[str]:
    """
    Onboards a user if they do not yet have an assigned managed project.
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        **ANTIGRAVITY_HEADERS,
    }

    body = json.dumps({
        "tierId": tier_id,
        "metadata": CLIENT_METADATA
    }).encode("utf-8")

    for endpoint in CLOUDCODE_ENDPOINTS:
        url = f"{endpoint}/v1internal:onboardUser"
        for _ in range(max_attempts):
            try:
                req = urllib.request.Request(url, data=body, headers=headers, method="POST")
                with urllib.request.urlopen(req) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    if data.get("done"):
                        proj = data.get("response", {}).get("cloudaicompanionProject", {}).get("id")
                        if proj:
                            return proj
                time.sleep(2)
            except Exception:
                break

    return None


def fetch_available_models(access_token: str, project_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Fetches the list of supported models and remaining quotas.
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        **ANTIGRAVITY_HEADERS,
    }

    body_data = {"project": project_id} if project_id else {}
    body = json.dumps(body_data).encode("utf-8")

    for endpoint in CLOUDCODE_ENDPOINTS:
        url = f"{endpoint}/v1internal:fetchAvailableModels"
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            continue

    return {}
