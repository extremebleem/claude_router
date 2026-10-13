import json
import time
import asyncio
from typing import Dict, Any, List, Optional, AsyncGenerator
import aiohttp

OPENCODE_ZEN_BASE_URL = "https://opencode.ai/zen/v1"

def anthropic_to_opencode_payload(anthropic_req: Dict[str, Any]) -> Dict[str, Any]:
    """Translates Anthropic Messages API payload to OpenAI-compatible Chat Completions payload for OpenCode."""
    messages: List[Dict[str, Any]] = []

    # Handle system prompt
    system_prompt = anthropic_req.get("system")
    if system_prompt:
        if isinstance(system_prompt, list):
            sys_text = "\n\n".join([item.get("text", "") for item in system_prompt if isinstance(item, dict)])
        else:
            sys_text = str(system_prompt)
        messages.append({"role": "system", "content": sys_text})

    # Handle chat messages
    for msg in anthropic_req.get("messages", []):
        role = msg.get("role", "user")
        content = msg.get("content")

        if isinstance(content, list):
            text_parts = []
            tool_calls = []
            tool_results = []
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
                    elif part.get("type") == "tool_use":
                        tool_calls.append({
                            "id": part.get("id"),
                            "type": "function",
                            "function": {
                                "name": part.get("name"),
                                "arguments": json.dumps(part.get("input", {}))
                            }
                        })
                    elif part.get("type") == "tool_result":
                        tool_results.append(part)

            # If tool results exist, append as tool role messages
            if tool_results:
                for tr in tool_results:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tr.get("tool_use_id"),
                        "content": tr.get("content", "") if isinstance(tr.get("content"), str) else json.dumps(tr.get("content", ""))
                    })
            else:
                formatted_msg: Dict[str, Any] = {"role": role, "content": "\n".join(text_parts)}
                if tool_calls:
                    formatted_msg["tool_calls"] = tool_calls
                messages.append(formatted_msg)
        else:
            messages.append({"role": role, "content": content or ""})

    payload: Dict[str, Any] = {
        "model": anthropic_req.get("model", "space-bunny-free"),
        "messages": messages,
    }

    if "max_tokens" in anthropic_req:
        payload["max_tokens"] = anthropic_req["max_tokens"]
    if "temperature" in anthropic_req:
        payload["temperature"] = anthropic_req["temperature"]
    if "top_p" in anthropic_req:
        payload["top_p"] = anthropic_req["top_p"]

    # Translate tools if present
    tools = anthropic_req.get("tools")
    if tools:
        openai_tools = []
        for t in tools:
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": t.get("name"),
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema", {})
                }
            })
        payload["tools"] = openai_tools

    return payload


class OpenCodeClient:
    def __init__(self, token_manager, base_url: str = OPENCODE_ZEN_BASE_URL):
        self.token_manager = token_manager
        self.base_url = base_url.rstrip("/")

    async def generate_stream(self, anthropic_req: Dict[str, Any], headers: Optional[Dict[str, str]] = None) -> AsyncGenerator[str, None]:
        """Streams responses from OpenCode Zen API and yields SSE formatted chunks in Anthropic Messages style."""
        account = self.token_manager.get_active_account(provider="opencode") if self.token_manager else None
        if not account:
            # Fallback mock/error event
            err_msg = "No active OpenCode account configured. Run 'python main.py login --provider opencode' first."
            yield f"event: error\ndata: {json.dumps({'type': 'error', 'error': {'type': 'authentication_error', 'message': err_msg}})}\n\n"
            return

        token = account.get("access_token")
        payload = anthropic_to_opencode_payload(anthropic_req)
        payload["stream"] = True

        req_headers = {
            "Authorization": f"Bearer {token}",
            "HTTP-Referer": "https://opencode.ai/",
            "X-Title": "opencode",
            "Content-Type": "application/json"
        }

        url = f"{self.base_url}/chat/completions"

        msg_id = f"msg_{int(time.time() * 1000)}"
        model_name = anthropic_req.get("model", "space-bunny-free")

        # Emit initial message_start
        start_event = {
            "type": "message_start",
            "message": {
                "id": msg_id,
                "type": "message",
                "role": "assistant",
                "model": model_name,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 0}
            }
        }
        yield f"event: message_start\ndata: {json.dumps(start_event)}\n\n"
        yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}})}\n\n"

        output_tokens = 0

        timeout = aiohttp.ClientTimeout(total=300)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload, headers=req_headers) as resp:
                if resp.status == 429:
                    email_or_id = account.get("email") or account.get("id") or "opencode"
                    if self.token_manager:
                        self.token_manager.mark_account_cooldown(email_or_id, cooldown_seconds=600.0, provider="opencode")
                    err_text = await resp.text()
                    yield f"event: error\ndata: {json.dumps({'type': 'error', 'error': {'type': 'rate_limit_error', 'message': f'OpenCode 429 rate limit: {err_text}'}})}\n\n"
                    return

                if resp.status != 200:
                    err_text = await resp.text()
                    yield f"event: error\ndata: {json.dumps({'type': 'error', 'error': {'type': 'api_error', 'message': f'OpenCode API error ({resp.status}): {err_text}'}})}\n\n"
                    return

                async for line in resp.content:
                    line_str = line.decode("utf-8").strip()
                    if not line_str.startswith("data: "):
                        continue
                    data_str = line_str[6:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        choices = chunk.get("choices", [])
                        if not choices:
                            continue
                        delta = choices[0].get("delta", {})
                        text_delta = delta.get("content")
                        if text_delta:
                            output_tokens += 1
                            delta_event = {
                                "type": "content_block_delta",
                                "index": 0,
                                "delta": {"type": "text_delta", "text": text_delta}
                            }
                            yield f"event: content_block_delta\ndata: {json.dumps(delta_event)}\n\n"
                    except Exception:
                        continue

        # Emit closing events
        yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': 0})}\n\n"
        yield f"event: message_delta\ndata: {json.dumps({'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'}, 'usage': {'output_tokens': output_tokens}})}\n\n"
        yield f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n"
