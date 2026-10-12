"""
Token estimation utilities for Claude-Gemini Proxy.
Provides calibrated BPE token counts for Anthropic Messages requests
without external binary dependencies.
"""

import re
from typing import Any, List, Union, Dict


def estimate_string_tokens(text: str) -> int:
    """
    Estimates token count for arbitrary text, calibrating for code, punctuation,
    ASCII words, and multi-byte UTF-8 scripts (Cyrillic, CJK).
    """
    if not text:
        return 0

    # 1. Non-ASCII characters (e.g. Cyrillic, Chinese, Emoji)
    # Cyrillic averages ~1.3-1.8 tokens per word or ~0.7-1.1 tokens per 2 characters.
    non_ascii = len(re.findall(r'[^\x00-\x7F]', text))

    # 2. Split words / punctuation in ASCII
    ascii_tokens = len(re.findall(r'\b[A-Za-z0-9_]+\b|[^\s\w]', text))

    # Multi-byte compensation: ~1.2 tokens per non-ascii char in BPE
    non_ascii_tokens = int(non_ascii * 0.9)

    return max(1, ascii_tokens + non_ascii_tokens)


def estimate_request_tokens(body: Dict[str, Any]) -> int:
    """
    Calculates calibrated input token count for an Anthropic Messages request,
    including system instructions, messages (text/tools/images), and tool declarations.
    """
    total = 3  # Base prompt framing overhead

    # 1. System prompt
    sys_val = body.get("system")
    if isinstance(sys_val, str):
        total += estimate_string_tokens(sys_val)
    elif isinstance(sys_val, list):
        for block in sys_val:
            if isinstance(block, str):
                total += estimate_string_tokens(block)
            elif isinstance(block, dict) and "text" in block:
                total += estimate_string_tokens(block.get("text", ""))

    # 2. Messages
    for msg in body.get("messages", []):
        total += 4  # Turn framing overhead
        content = msg.get("content")
        if isinstance(content, str):
            total += estimate_string_tokens(content)
        elif isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                p_type = part.get("type")
                if p_type == "text":
                    total += estimate_string_tokens(part.get("text", ""))
                elif p_type == "thinking":
                    total += estimate_string_tokens(part.get("thinking", ""))
                elif p_type == "tool_use":
                    total += 10 + estimate_string_tokens(str(part.get("input", {})))
                elif p_type == "tool_result":
                    total += 6 + estimate_string_tokens(str(part.get("content", "")))
                elif p_type == "image":
                    total += 256  # Standard vision tile token approximation
                elif p_type == "document":
                    total += 512

    # 3. Tool schemas
    for t in body.get("tools", []):
        if isinstance(t, dict):
            total += 12  # Schema definition overhead
            total += estimate_string_tokens(t.get("name", ""))
            total += estimate_string_tokens(t.get("description", ""))
            total += estimate_string_tokens(str(t.get("input_schema", {})))

    return max(1, total)
