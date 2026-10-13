"""Live OpenCode Zen catalogue: which models exist, which are free, and what each one allows.

The hardcoded list in backend/router.py went stale within days of being written - `union-alpha`
disappeared, `space-bunny-free` stayed, and several free models were added. Two public sources
describe the truth:

  * GET {base}/models            - the documented model list, no Console login needed.
  * GET {console}/api/v2/config  - what the signed-in account is actually served: its endpoint,
                                   its org header, and per-model cost/limit/disabled flags.

Both are fetched through blocking HTTP and cached, so callers should use this from a worker thread.
Everything degrades to the static snapshot instead of failing: a stale model list is annoying,
but a request that cannot be dispatched is worse.
"""

import json
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from config import OPENCODE_CONSOLE_URL, OPENCODE_ZEN_BASE_URL
from logger import log

CACHE_TTL_SECONDS = 900
HTTP_TIMEOUT_SECONDS = 20

# Snapshot of what the Console served on 2026-10-14, used until (or unless) the live call succeeds.
STATIC_BASE_URL = OPENCODE_ZEN_BASE_URL
STATIC_HEADERS: Dict[str, str] = {}
STATIC_MODELS: Dict[str, Dict[str, Any]] = {
    "space-bunny-free": {"name": "Space Bunny Free", "free": True, "output": 32000, "context": 200000},
    "big-pickle": {"name": "Big Pickle", "free": True, "output": 32000, "context": 200000},
    "cat-jam-free": {"name": "Cat Jam Free", "free": True, "output": 131072, "context": 262144},
    "longcat-2.5-preview-free": {"name": "LongCat 2.5 Preview Free", "free": True, "output": 131072, "context": 1000000},
    "mimo-v2.6-flash-free": {"name": "MiMo-V2.6-Flash Free", "free": True, "output": 32000, "context": 200000},
    "mimo-v2.5-free": {"name": "MiMo-V2.5 Free", "free": True, "output": 32000, "context": 200000},
    "ling-3.1-flash-free": {"name": "Ling 3.1 Flash Free", "free": True, "output": 32768, "context": 262144},
    "ling-3.0-flash-fin-free": {"name": "Ling 3.0 Flash Fin Free", "free": True, "output": 32768, "context": 262144},
    "nemotron-3-ultra-free": {"name": "Nemotron 3 Ultra Free", "free": True, "output": 128000, "context": 1000000},
    "nemotron-3.5-lightning-free": {"name": "Nemotron 3.5 Lightning Free", "free": True, "output": 262144, "context": 262144},
    "step-5-preview-free": {"name": "Step 5 Preview Free", "free": True, "output": 32768, "context": 262144},
}

_lock = threading.Lock()
_cache: Optional[Dict[str, Any]] = None


def static_snapshot() -> Dict[str, Any]:
    return {
        "base_url": STATIC_BASE_URL,
        "headers": dict(STATIC_HEADERS),
        "models": {k: dict(v) for k, v in STATIC_MODELS.items()},
        "source": "static",
    }


def _get_json(url: str, token: Optional[str], user_agent: str) -> Optional[Any]:
    request = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": user_agent,
        **({"Authorization": f"Bearer {token}"} if token else {}),
    })
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def _is_free(model: Dict[str, Any]) -> bool:
    costs = model.get("cost") or []
    return bool(costs) and all((cost.get("input") or 0) == 0 and (cost.get("output") or 0) == 0 for cost in costs)


def _from_console_config(config: Dict[str, Any], user_agent: str) -> Optional[Dict[str, Any]]:
    """The signed-in account's view: its own endpoint, org header, and the full model table."""
    provider = (config.get("providers") or {}).get("opencode") or {}
    models = provider.get("models") or {}
    if not models:
        return None
    catalog: Dict[str, Any] = {
        "base_url": (provider.get("settings") or {}).get("baseURL") or STATIC_BASE_URL,
        # The Console hands the client its org id; requests without it are not attributed.
        "headers": dict(provider.get("headers") or STATIC_HEADERS),
        "models": {},
        "source": "console",
    }
    for model_id, model in models.items():
        limit = model.get("limit") or {}
        catalog["models"][model_id] = {
            "name": model.get("name") or model_id,
            "free": _is_free(model),
            "output": limit.get("output") or 0,
            "context": limit.get("context") or 0,
            "disabled": bool(model.get("disabled")),
        }
    return catalog


def _from_model_list(payload: Dict[str, Any], user_agent: str) -> Dict[str, Any]:
    ids = [m.get("id") for m in payload.get("data") or [] if m.get("id")]
    # Merge into the snapshot rather than replacing it: /models carries no limits or pricing, so a
    # live-only catalogue would lose the free flags and output caps the Console config provides.
    models = {k: dict(v) for k, v in STATIC_MODELS.items()}
    for model_id in ids:
        models.setdefault(model_id, {"name": model_id, "free": model_id.endswith("-free") or model_id == "big-pickle",
                                     "output": 0, "context": 0, "disabled": False})
    return {"base_url": STATIC_BASE_URL, "headers": dict(STATIC_HEADERS), "models": models, "source": "models"}


def _fetch(user_agent: str, token: Optional[str]) -> Dict[str, Any]:
    base_url = STATIC_BASE_URL
    headers: Dict[str, str] = {}
    models: Dict[str, Any] = {}
    source = "static"

    if token:
        try:
            console = _get_json(f"{OPENCODE_CONSOLE_URL}/api/v2/config", token, user_agent)
            found = _from_console_config(console, user_agent) if isinstance(console, dict) else None
            if found:
                base_url, headers, models, source = (
                    found["base_url"], found["headers"], found["models"], found["source"])
        except (urllib.error.URLError, OSError, ValueError) as e:
            log(f"[OpenCodeCatalog] Console config unavailable: {e}", level="DEBUG")

    if not models:
        # No Console login, or its config failed: the public list still tells us what exists.
        try:
            listing = _get_json(f"{STATIC_BASE_URL}/models", token, user_agent)
            if isinstance(listing, dict) and listing.get("data"):
                found = _from_model_list(listing, user_agent)
                models, source = found["models"], found["source"]
        except (urllib.error.URLError, OSError, ValueError) as e:
            log(f"[OpenCodeCatalog] Model list unavailable: {e}", level="DEBUG")

    if not models:
        return static_snapshot()
    return {"base_url": base_url, "headers": headers, "models": models, "source": source}


def get_catalog(token: Optional[str] = None, user_agent: str = "opencode") -> Dict[str, Any]:
    """Current catalogue for `token`. Cached per token for CACHE_TTL_SECONDS, static on any failure."""
    global _cache
    key = (token or "")[-16:]
    with _lock:
        if _cache and _cache["key"] == key and time.time() - _cache["at"] < CACHE_TTL_SECONDS:
            return dict(_cache["catalog"], cached=True)
    catalog = _fetch(user_agent, token)
    with _lock:
        _cache = {"key": key, "at": time.time(), "catalog": catalog}
    return dict(catalog, cached=False)


def invalidate() -> None:
    global _cache
    with _lock:
        _cache = None


def peek_catalog() -> Dict[str, Any]:
    """Cached catalogue without ever doing IO. Callers on the hot path (routing) use this, so a
    cold process falls back to the static snapshot instead of blocking on the network."""
    with _lock:
        return dict(_cache["catalog"]) if _cache else static_snapshot()


def routable_models(catalog: Dict[str, Any]) -> List[str]:
    """Model ids worth advertising: everything the account can actually reach."""
    return [model_id for model_id, info in catalog.get("models", {}).items() if not info.get("disabled")]


def free_models(catalog: Dict[str, Any]) -> List[str]:
    return [model_id for model_id, info in catalog.get("models", {}).items()
            if info.get("free") and not info.get("disabled")]


def output_limit(catalog: Dict[str, Any], model_id: str) -> int:
    return int((catalog.get("models", {}).get(model_id) or {}).get("output") or 0)