"""
In-memory caches for Gemini thought signatures and tool names.
Stores thoughtSignature and tool_name by tool_use_id so that subsequent tool_result turns
can restore them even if the client stripped them or pruned history turns.
"""

import time
from typing import Dict, Optional, Tuple, Any

SIGNATURE_CACHE_TTL = 7200  # 2 hours
SKIP_SIGNATURE_SENTINEL = "skip_thought_signature_validator"


class SignatureCache:
    def __init__(self):
        # tool_use_id -> (signature, timestamp)
        self._cache: Dict[str, Tuple[str, float]] = {}

    def set(self, tool_use_id: str, signature: str):
        if not tool_use_id or not signature:
            return
        self._cache[tool_use_id] = (signature, time.time())

    def get(self, tool_use_id: str) -> Optional[str]:
        if not tool_use_id or tool_use_id not in self._cache:
            return None
        sig, ts = self._cache[tool_use_id]
        if time.time() - ts > SIGNATURE_CACHE_TTL:
            del self._cache[tool_use_id]
            return None
        return sig

    def get_or_sentinel(self, tool_use_id: str) -> str:
        sig = self.get(tool_use_id)
        return sig if sig else SKIP_SIGNATURE_SENTINEL

    def clear(self):
        self._cache.clear()


class ToolNameCache:
    """Session-level cache preserving tool_name for tool_use_id across turns."""
    def __init__(self):
        # tool_use_id -> (tool_name, timestamp)
        self._cache: Dict[str, Tuple[str, float]] = {}

    def set(self, tool_use_id: str, tool_name: str):
        if not tool_use_id or not tool_name:
            return
        self._cache[tool_use_id] = (tool_name, time.time())

    def get(self, tool_use_id: str, default: str = "unknown_tool") -> str:
        if not tool_use_id or tool_use_id not in self._cache:
            return default
        name, ts = self._cache[tool_use_id]
        if time.time() - ts > SIGNATURE_CACHE_TTL:
            del self._cache[tool_use_id]
            return default
        return name

    def clear(self):
        self._cache.clear()


class ToolArgsCache:
    """Session-level cache preserving input args for tool_use_id across multi-turn histories."""
    def __init__(self):
        # tool_use_id -> (args_dict, timestamp)
        self._cache: Dict[str, Tuple[Dict[str, Any], float]] = {}

    def set(self, tool_use_id: str, args: Dict[str, Any]):
        if not tool_use_id or not isinstance(args, dict):
            return
        self._cache[tool_use_id] = (args, time.time())

    def get(self, tool_use_id: str) -> Optional[Dict[str, Any]]:
        if not tool_use_id or tool_use_id not in self._cache:
            return None
        args, ts = self._cache[tool_use_id]
        if time.time() - ts > SIGNATURE_CACHE_TTL:
            del self._cache[tool_use_id]
            return None
        return args

    def clear(self):
        self._cache.clear()


signature_cache = SignatureCache()
tool_name_cache = ToolNameCache()
tool_args_cache = ToolArgsCache()
