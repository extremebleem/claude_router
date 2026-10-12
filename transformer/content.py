"""
Content and Request Converter.
Transforms Anthropic Messages API requests into Google Cloud Code / Gemini format
with stable prompt caching, session hashing, and multi-turn tool preserving.
"""

import uuid
import hashlib
import json
import re
from typing import Dict, Any, List, Optional, Tuple, Union

from transformer.schema import sanitize_schema
from transformer.signatures import signature_cache, tool_name_cache, tool_args_cache, SKIP_SIGNATURE_SENTINEL
from transformer.prompts import build_system_instruction, ANTIGRAVITY_BASE_PROMPT
from transformer.search import enrich_empty_search_result
from transformer.streamer import estimate_request_tokens

ANTIGRAVITY_SYSTEM_INSTRUCTION = (
    "You are Antigravity, a powerful agentic AI coding assistant designed by "
    "the Google Deepmind team working on Advanced Agentic Coding. You are pair "
    "programming with a USER to solve their coding task. The task may require "
    "creating a new codebase, modifying or debugging an existing codebase, "
    "or simply answering a question.**Absolute paths only****Proactiveness**"
)

# Identity strings that trigger false 429 RESOURCE_EXHAUSTED in Cloud Code
IDENTITY_SCRUBS = [
    ("Nous Research", "the assistant team"),
    ("Hermes Agent", "the assistant"),
    ("Hermes", "the assistant"),
]


def scrub_identity(text: str) -> str:
    """Removes vendor identities and telemetry headers that trip Cloud Code's strict 429 filters."""
    if not isinstance(text, str):
        return text
    # Strip Anthropic billing header line that causes Google Cloud Code 429 RESOURCE_EXHAUSTED
    text = re.sub(r"x-anthropic-billing-header[^\n]*\n?", "", text, flags=re.IGNORECASE)
    for term, replacement in IDENTITY_SCRUBS:
        text = text.replace(term, replacement)
    return text


def truncate_tool_output(
    output_text: str,
    max_lines: int = 600,
    max_chars: int = 120000,
    head_lines: int = 250,
    tail_lines: int = 250
) -> str:
    """Truncates oversized tool outputs preserving head and tail to optimize context window."""
    if not isinstance(output_text, str):
        return output_text

    lines = output_text.splitlines()
    num_lines = len(lines)
    char_len = len(output_text)

    # 1. Truncate by lines if excessive and head+tail < total lines
    if num_lines > max_lines:
        if head_lines + tail_lines < num_lines:
            head = "\n".join(lines[:head_lines])
            tail = "\n".join(lines[-tail_lines:]) if tail_lines > 0 else ""
            omitted_lines = num_lines - head_lines - tail_lines
            omitted_chars = max(0, char_len - len(head) - len(tail))
            return (
                f"{head}\n\n"
                f"[... Omitted {omitted_lines} lines (~{omitted_chars} chars) by proxy to optimize context window ...]\n\n"
                f"{tail}"
            )

    # 2. Truncate by character budget if output is still excessively long
    if char_len > max_chars:
        if max_chars < 200:
            half = max(10, max_chars // 2)
            head_part = output_text[:half]
            tail_part = output_text[-half:] if half > 0 else ""
            omitted = max(0, char_len - len(head_part) - len(tail_part))
            return f"{head_part}\n[... Omitted ~{omitted} chars ...]\n{tail_part}"

        half = (max_chars - 200) // 2
        omitted_chars = max(0, char_len - 2 * half)
        return (
            f"{output_text[:half]}\n\n"
            f"[... Omitted ~{omitted_chars} chars by proxy to optimize context window ...]\n\n"
            f"{output_text[-half:]}"
        )

    return output_text


def collapse_historic_tool_output(
    output_text: str,
    max_lines: int = 15,
    max_chars: int = 1500
) -> str:
    """
    Collapses deep historic tool outputs into a lightweight summary.
    Preserves tool execution awareness while freeing hundreds of thousands of tokens
    from old turns to prevent 429 RESOURCE_EXHAUSTED errors.
    """
    if not isinstance(output_text, str):
        output_text = str(output_text)

    char_len = len(output_text)
    lines = output_text.splitlines()
    num_lines = len(lines)

    if num_lines <= max_lines and char_len <= max_chars:
        return output_text

    # Keep brief sample of head
    head = "\n".join(lines[:5])
    return (
        f"{head}\n\n"
        f"[... Prior tool output collapsed ({num_lines} lines, {char_len} chars) to preserve context window ...]"
    )


def calculate_thinking_budget(
    model: str,
    total_prompt_tokens: int,
    user_budget: Optional[int] = None
) -> int:
    """
    Calculates token-aware dynamic thinking budget based on prompt token count.
    Scales down for larger context windows to prevent token waste and latency explosion.
    Respects explicit user_budget=0 to allow disabling thinking.
    """
    # 0. Explicit disable
    if user_budget == 0:
        return 0

    m = model.lower()
    # 1. Base budget
    if user_budget is not None and user_budget > 0:
        budget = user_budget
    elif "claude" in m:
        budget = 32000
    elif "pro" in m:
        budget = 16000
    else:
        budget = 2048

    is_flash = "claude" not in m and "pro" not in m

    # 2. Context scaling
    if total_prompt_tokens > 300_000:
        clamp_max = 1024 if is_flash else 4096
        budget = min(budget, clamp_max)
    elif total_prompt_tokens > 100_000:
        clamp_max = 2048 if is_flash else 8192
        budget = min(budget, clamp_max)

    # 3. Model maximum ceiling
    max_ceiling = 24576 if "2.5" in m else 64000
    budget = min(budget, max_ceiling)

    # 4. Minimum floor (respects explicit user_budget=0 disable)
    return max(budget, 1024)


def derive_session_id(
    messages: List[Dict[str, Any]],
    account_id: str = "",
    system: Optional[Union[str, List[Any]]] = None,
    tools: Optional[List[Dict[str, Any]]] = None
) -> str:
    """
    Derives a stable, deterministic session ID for Cloud Code's X-Machine-Session-Id
    and sessionId prefix caching.
    Uses anchor points:
    1. System prompt text / cache_control
    2. Initial user message / session start
    3. Account email
    This guarantees that as a conversation grows across multi-turn interactions,
    the session prefix remains 100% stable, maximizing Google Cloud Code cache hits.
    """
    anchor_parts: List[str] = [account_id]

    # Check for cache_control marker in messages or system
    has_explicit_cache = False

    if system:
        if isinstance(system, str):
            anchor_parts.append(f"sys:{hashlib.md5(system.encode('utf-8')).hexdigest()[:8]}")
        elif isinstance(system, list):
            for b in system:
                if isinstance(b, dict):
                    if b.get("cache_control"):
                        has_explicit_cache = True
                    if "text" in b:
                        anchor_parts.append(f"sys:{hashlib.md5(b['text'].encode('utf-8')).hexdigest()[:8]}")
                        break

    first_user_content = ""
    for msg in messages:
        if msg.get("role") == "user":
            c = msg.get("content")
            if isinstance(c, str):
                first_user_content = c
            elif isinstance(c, list):
                for part in c:
                    if isinstance(part, dict):
                        if part.get("cache_control"):
                            has_explicit_cache = True
                        if part.get("type") == "text":
                            first_user_content = part.get("text", "")
                            break
            if first_user_content:
                break

    if first_user_content:
        # Normalize whitespace and take first 300 chars as permanent conversation anchor
        norm_text = " ".join(first_user_content.split())[:300]
        anchor_parts.append(norm_text)
    else:
        anchor_parts.append("default-session")

    raw_seed = "|".join(anchor_parts)
    hash_digest = hashlib.sha256(raw_seed.encode("utf-8")).hexdigest()[:16]
    return f"session-{hash_digest}"


def convert_anthropic_to_google(
    anthropic_req: Dict[str, Any],
    project_id: str,
    account_email: str = ""
) -> Dict[str, Any]:
    """
    Main converter: Anthropic request dict -> Google Cloud Code wrapped payload.
    """
    model = anthropic_req.get("model", "claude-opus-4-6-thinking")
    messages = anthropic_req.get("messages", [])
    system = anthropic_req.get("system")
    tools = anthropic_req.get("tools")
    max_tokens = anthropic_req.get("max_tokens", 4096)
    temperature = anthropic_req.get("temperature", 1.0)
    top_p = anthropic_req.get("top_p")
    top_k = anthropic_req.get("top_k")

    google_contents: List[Dict[str, Any]] = []

    # Map of tool_use_id -> tool_name (for matching tool_results to functionResponse)
    tool_name_map: Dict[str, str] = {}
    tool_args_map: Dict[str, Dict[str, Any]] = {}

    # Total messages in request
    total_msgs = len(messages)
    # Active recent window: preserve full output for the most recent 40 messages
    # Messages older than this threshold have their bulky tool_results safely collapsed
    recent_window_threshold = max(0, total_msgs - 40)

    for msg_idx, msg in enumerate(messages):
        is_historic_turn = (msg_idx < recent_window_threshold)
        role = msg.get("role")
        content = msg.get("content")

        # Map role: ONLY assistant becomes model. user and system become user.
        google_role = "model" if role == "assistant" else "user"
        parts: List[Dict[str, Any]] = []

        if isinstance(content, str):
            parts.append({"text": content})

        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                b_type = block.get("type")

                # 1. Plain text block
                if b_type == "text":
                    parts.append({"text": block.get("text", "")})

                # 2. Thinking / Reasoning block
                elif b_type == "thinking":
                    thought_text = block.get("thinking", "")
                    sig = block.get("signature") or SKIP_SIGNATURE_SENTINEL
                    parts.append({
                        "thought": True,
                        "text": thought_text,
                        "thoughtSignature": sig
                    })

                # 3. Image block (base64 or URL)
                elif b_type == "image":
                    src = block.get("source", {})
                    if src.get("type") == "base64":
                        parts.append({
                            "inlineData": {
                                "mimeType": src.get("media_type", "image/png"),
                                "data": src.get("data", "")
                            }
                        })
                    elif src.get("type") == "url":
                        parts.append({
                            "fileData": {
                                "mimeType": src.get("media_type", "image/jpeg"),
                                "fileUri": src.get("url", "")
                            }
                        })

                # 4. Document block (e.g. PDF base64 or URL)
                elif b_type == "document":
                    src = block.get("source", {})
                    if src.get("type") == "base64":
                        parts.append({
                            "inlineData": {
                                "mimeType": src.get("media_type", "application/pdf"),
                                "data": src.get("data", "")
                            }
                        })
                    elif src.get("type") == "url":
                        parts.append({
                            "fileData": {
                                "mimeType": src.get("media_type", "application/pdf"),
                                "fileUri": src.get("url", "")
                            }
                        })

                # 5. Tool use block (from assistant)
                elif b_type == "tool_use":
                    tool_id = block.get("id", "")
                    tool_name = block.get("name", "")
                    tool_args = block.get("input", {})
                    if tool_id and tool_name:
                        tool_name_map[tool_id] = tool_name
                        tool_name_cache.set(tool_id, tool_name)
                        if isinstance(tool_args, dict):
                            tool_args_map[tool_id] = tool_args
                            tool_args_cache.set(tool_id, tool_args)

                    # Restore thought signature if cached
                    cached_sig = signature_cache.get(tool_id)

                    fc_part: Dict[str, Any] = {
                        "functionCall": {
                            "name": tool_name,
                            "args": tool_args
                        }
                    }
                    if cached_sig:
                        fc_part["thoughtSignature"] = cached_sig
                    parts.append(fc_part)

                # 6. Tool result block (from user)
                elif b_type == "tool_result":
                    tool_id = block.get("tool_use_id", "")
                    tool_name = tool_name_map.get(tool_id) or tool_name_cache.get(tool_id, "unknown_tool")
                    tool_input_args = tool_args_map.get(tool_id) or tool_args_cache.get(tool_id)
                    raw_res = block.get("content", "")
                    extracted_images = []

                    if isinstance(raw_res, list):
                        text_items = []
                        for item in raw_res:
                            if not isinstance(item, dict):
                                continue
                            if item.get("type") == "text":
                                text_items.append(item.get("text", ""))
                            elif item.get("type") == "image":
                                src = item.get("source", {})
                                if src.get("type") == "base64":
                                    extracted_images.append({
                                        "inlineData": {
                                            "mimeType": src.get("media_type", "image/png"),
                                            "data": src.get("data", "")
                                        }
                                    })
                        raw_output = "\n".join(text_items)
                        enriched_output = enrich_empty_search_result(tool_name, raw_output, input_args=tool_input_args)
                        if is_historic_turn:
                            final_output = collapse_historic_tool_output(enriched_output)
                        else:
                            final_output = truncate_tool_output(enriched_output)
                        res_obj = {"output": final_output}
                    elif isinstance(raw_res, dict):
                        res_obj = raw_res
                    else:
                        raw_output = str(raw_res)
                        enriched_output = enrich_empty_search_result(tool_name, raw_output, input_args=tool_input_args)
                        if is_historic_turn:
                            final_output = collapse_historic_tool_output(enriched_output)
                        else:
                            final_output = truncate_tool_output(enriched_output)
                        res_obj = {"output": final_output}

                    parts.append({
                        "functionResponse": {
                            "name": tool_name,
                            "response": res_obj
                        }
                    })
                    for img_part in extracted_images:
                        parts.append(img_part)

        if parts:
            # Merge consecutive turns of the same role (required by Gemini API)
            if google_contents and google_contents[-1]["role"] == google_role:
                google_contents[-1]["parts"].extend(parts)
            else:
                google_contents.append({"role": google_role, "parts": parts})

    # Validate turn structure required by Gemini API:
    # 1. Must not be empty
    if not google_contents:
        google_contents.append({"role": "user", "parts": [{"text": "Hello"}]})

    # 2. Must start with a 'user' turn
    if google_contents[0]["role"] != "user":
        google_contents.insert(0, {"role": "user", "parts": [{"text": "Hello"}]})

    # 3. Must end with a 'user' turn (Gemini fails with 400 if ending with model)
    if google_contents[-1]["role"] != "user":
        google_contents.append({"role": "user", "parts": [{"text": "Continue"}]})

    # Prepare system instructions
    # Merges Antigravity model identity with client system directives (CLAUDE.md, rules)
    system_parts = build_system_instruction(model, user_system=system)

    # Build generation config
    thinking_param = anthropic_req.get("thinking")
    is_thinking = "thinking" in model.lower() or bool(thinking_param)
    gen_config: Dict[str, Any] = {
        "maxOutputTokens": max(max_tokens, 16384) if is_thinking else max_tokens,
        "temperature": temperature,
    }
    if top_p is not None:
        gen_config["topP"] = top_p
    if top_k is not None:
        gen_config["topK"] = top_k

    # Configure thinking budget if requested or if using a thinking model
    if is_thinking:
        total_prompt_tokens = estimate_request_tokens(anthropic_req)
        user_budget = None
        if isinstance(thinking_param, dict) and "budget_tokens" in thinking_param:
            user_budget = thinking_param["budget_tokens"]

        scaled_budget = calculate_thinking_budget(model, total_prompt_tokens, user_budget=user_budget)

        if "claude" in model.lower():
            # Claude Cloud Code thinking format (snake_case)
            gen_config["thinkingConfig"] = {
                "include_thoughts": True,
                "thinking_budget": scaled_budget
            }
            # Claude requires maxOutputTokens > thinking_budget
            gen_config["maxOutputTokens"] = max(gen_config.get("maxOutputTokens", 16384), scaled_budget + 8192)
            if tools:
                system_parts.append({
                    "text": "Interleaved thinking is enabled. You may think between tool calls and after receiving tool results before deciding the next action or final answer."
                })
        else:
            # Gemini thinking format (camelCase)
            gen_config["thinkingConfig"] = {
                "includeThoughts": True,
                "thinkingBudget": scaled_budget
            }

    # Prepare tools
    google_tools = []
    has_native_google_search = bool(anthropic_req.get("enable_google_search"))

    if tools and isinstance(tools, list):
        func_decls = []
        for t in tools:
            if not isinstance(t, dict):
                continue

            # Detect Anthropic native web search tool (e.g. type: "web_search_20250101")
            t_type = t.get("type", "")
            if "web_search" in str(t_type).lower():
                name = t.get("name") or "WebSearch"
                desc = t.get("description") or "Search the web for current information and external sources."
                schema = t.get("input_schema") or {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "The search query to look up"}
                    },
                    "required": ["query"]
                }
                func_decls.append({
                    "name": name,
                    "description": desc,
                    "parameters": sanitize_schema(schema)
                })
                continue

            name = t.get("name")
            desc = t.get("description", "")
            schema = t.get("input_schema", {})
            if name:
                func_decls.append({
                    "name": name,
                    "description": desc,
                    "parameters": sanitize_schema(schema)
                })
        if func_decls:
            google_tools.append({"functionDeclarations": func_decls})

    if has_native_google_search:
        google_tools.append({"googleSearch": {}})

    google_request: Dict[str, Any] = {
        "contents": google_contents,
        "systemInstruction": {"parts": system_parts},
        "generationConfig": gen_config,
        "sessionId": derive_session_id(messages, account_email, system=system, tools=tools)
    }

    if google_tools:
        google_request["tools"] = google_tools
        # Only Claude models use VALIDATED mode in Cloud Code API (Gemini models use default AUTO)
        if "claude" in model.lower():
            google_request["toolConfig"] = {
                "functionCallingConfig": {"mode": "VALIDATED"}
            }

    return {
        "project": project_id,
        "model": model,
        "request": google_request,
        "userAgent": "antigravity",
        "requestType": "agent",
        "requestId": f"agent-{uuid.uuid4()}"
    }
