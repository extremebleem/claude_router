"""
SSE Streamer and Response Converter.
Parses Google Cloud Code SSE chunks and converts them into Anthropic SSE events
(message_start, content_block_start, content_block_delta, message_delta, message_stop).
"""

import json
import uuid
from typing import AsyncGenerator, Dict, Any, Optional, List

from transformer.signatures import signature_cache
from logger import log


async def parse_google_sse_stream(
    response_stream,
    original_model: str
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Asynchronously yields Anthropic-format event dictionaries from a Google SSE stream.
    """
    message_id = f"msg_{uuid.uuid4().hex[:16]}"
    has_emitted_start = False
    block_index = 0
    current_block_type: Optional[str] = None  # "thinking", "text", "tool_use"
    current_tool_id: Optional[str] = None
    current_thinking_signature = ""
    input_tokens = 0
    output_tokens = 0
    cache_read_tokens = 0
    stop_reason = "end_turn"
    grounding_metadata: Optional[Dict[str, Any]] = None

    buffer = ""

    read_stream = response_stream.iter_any() if hasattr(response_stream, "iter_any") else response_stream
    async for chunk in read_stream:
        text = chunk.decode("utf-8", errors="replace")
        buffer += text

        lines = buffer.split("\n")
        buffer = lines.pop()  # Keep incomplete line in buffer

        for line in lines:
            line = line.strip()
            if not line.startswith("data:"):
                continue

            json_str = line[5:].strip()
            if not json_str:
                continue

            try:
                data = json.loads(json_str)
            except Exception:
                continue

            inner_resp = data.get("response", data)

            # 1. Update token usage metadata
            usage = inner_resp.get("usageMetadata", {})
            if usage:
                input_tokens = usage.get("promptTokenCount", input_tokens)
                output_tokens = usage.get("candidatesTokenCount", output_tokens)
                cache_read_tokens = usage.get("cachedContentTokenCount", cache_read_tokens)

            candidates = inner_resp.get("candidates", [])
            if not candidates:
                continue

            first_cand = candidates[0]
            if "groundingMetadata" in first_cand:
                grounding_metadata = first_cand["groundingMetadata"]
            finish_reason = first_cand.get("finishReason")
            if finish_reason == "MAX_TOKENS":
                stop_reason = "max_tokens"
            elif finish_reason == "STOP":
                stop_reason = "end_turn"

            content = first_cand.get("content", {})
            parts = content.get("parts", [])

            # Emit message_start on the first batch of parts
            if not has_emitted_start and parts:
                has_emitted_start = True
                yield {
                    "type": "message_start",
                    "message": {
                        "id": message_id,
                        "type": "message",
                        "role": "assistant",
                        "content": [],
                        "model": original_model,
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {
                            "input_tokens": max(0, input_tokens - cache_read_tokens),
                            "output_tokens": 0,
                            "cache_read_input_tokens": cache_read_tokens,
                            "cache_creation_input_tokens": 0,
                        }
                    }
                }

            for part in parts:
                # A. Thinking / Reasoning Block
                if part.get("thought") is True:
                    thought_text = part.get("text", "")
                    sig = part.get("thoughtSignature", "")
                    if sig and len(sig) >= 50:
                        current_thinking_signature = sig

                    if current_block_type != "thinking":
                        if current_block_type is not None:
                            yield {"type": "content_block_stop", "index": block_index}
                            block_index += 1
                        current_block_type = "thinking"
                        current_thinking_signature = ""
                        yield {
                            "type": "content_block_start",
                            "index": block_index,
                            "content_block": {"type": "thinking", "thinking": ""}
                        }

                    if thought_text:
                        yield {
                            "type": "content_block_delta",
                            "index": block_index,
                            "delta": {"type": "thinking_delta", "thinking": thought_text}
                        }

                # B. Function / Tool Call Block
                elif "functionCall" in part:
                    fc = part["functionCall"]
                    fn_name = fc.get("name", "")
                    fn_args = fc.get("args", {})
                    fn_id = f"toolu_{uuid.uuid4().hex[:12]}"

                    # If thought signature is present on tool call, cache it
                    tool_sig = part.get("thoughtSignature")
                    if tool_sig:
                        signature_cache.set(fn_id, tool_sig)

                    stop_reason = "tool_use"

                    if current_block_type == "thinking" and current_thinking_signature:
                        yield {
                            "type": "content_block_delta",
                            "index": block_index,
                            "delta": {"type": "signature_delta", "signature": current_thinking_signature}
                        }
                        current_thinking_signature = ""

                    if current_block_type is not None:
                        yield {"type": "content_block_stop", "index": block_index}
                        block_index += 1

                    current_block_type = "tool_use"
                    current_tool_id = fn_id

                    yield {
                        "type": "content_block_start",
                        "index": block_index,
                        "content_block": {
                            "type": "tool_use",
                            "id": fn_id,
                            "name": fn_name,
                            "input": {}
                        }
                    }

                    # Stream arguments as partial JSON delta
                    args_json = json.dumps(fn_args, ensure_ascii=False)
                    yield {
                        "type": "content_block_delta",
                        "index": block_index,
                        "delta": {
                            "type": "input_json_delta",
                            "partial_json": args_json
                        }
                    }

                # C. Regular Text Block
                elif "text" in part:
                    text_delta = part.get("text", "")
                    if not text_delta:
                        continue

                    if current_block_type != "text":
                        if current_block_type == "thinking" and current_thinking_signature:
                            yield {
                                "type": "content_block_delta",
                                "index": block_index,
                                "delta": {"type": "signature_delta", "signature": current_thinking_signature}
                            }
                            current_thinking_signature = ""

                        if current_block_type is not None:
                            yield {"type": "content_block_stop", "index": block_index}
                            block_index += 1
                        current_block_type = "text"
                        yield {
                            "type": "content_block_start",
                            "index": block_index,
                            "content_block": {"type": "text", "text": ""}
                        }

                    yield {
                        "type": "content_block_delta",
                        "index": block_index,
                        "delta": {"type": "text_delta", "text": text_delta}
                    }

    # Close any currently open block
    if current_block_type == "thinking" and current_thinking_signature:
        yield {
            "type": "content_block_delta",
            "index": block_index,
            "delta": {"type": "signature_delta", "signature": current_thinking_signature}
        }
        current_thinking_signature = ""

    # Append grounding sources (if Gemini used native Google Search)
    if grounding_metadata:
        grounding_chunks = grounding_metadata.get("groundingChunks", [])
        sources = []
        for chunk in grounding_chunks:
            web_info = chunk.get("web", {})
            uri = web_info.get("uri")
            title = web_info.get("title") or uri
            if uri and uri not in [s[1] for s in sources]:
                sources.append((title, uri))
        if sources:
            source_lines = ["\n\n**Sources:**"]
            for title, uri in sources:
                source_lines.append(f"- [{title}]({uri})")
            grounding_text = "\n".join(source_lines)

            if current_block_type == "text":
                yield {
                    "type": "content_block_delta",
                    "index": block_index,
                    "delta": {"type": "text_delta", "text": grounding_text}
                }
            else:
                if current_block_type is not None:
                    yield {"type": "content_block_stop", "index": block_index}
                    block_index += 1
                current_block_type = "text"
                yield {
                    "type": "content_block_start",
                    "index": block_index,
                    "content_block": {"type": "text", "text": ""}
                }
                yield {
                    "type": "content_block_delta",
                    "index": block_index,
                    "delta": {"type": "text_delta", "text": grounding_text}
                }

    if current_block_type is not None:
        yield {"type": "content_block_stop", "index": block_index}

    # Final message_delta and message_stop
    yield {
        "type": "message_delta",
        "delta": {
            "stop_reason": stop_reason,
            "stop_sequence": None
        },
        "usage": {
            "output_tokens": output_tokens
        }
    }
    yield {"type": "message_stop"}


async def aggregate_sse_stream_to_json(
    response_stream,
    original_model: str
) -> Dict[str, Any]:
    """
    For non-streaming requests: consumes the generator and builds a complete Anthropic response.
    """
    content_blocks: List[Dict[str, Any]] = []
    message_id = ""
    stop_reason = "end_turn"
    usage = {"input_tokens": 0, "output_tokens": 0}

    async for event in parse_google_sse_stream(response_stream, original_model):
        e_type = event.get("type")
        if e_type == "message_start":
            msg = event.get("message", {})
            message_id = msg.get("id", "")
            usage["input_tokens"] = msg.get("usage", {}).get("input_tokens", 0)

        elif e_type == "content_block_start":
            cb = dict(event.get("content_block", {}))
            content_blocks.append(cb)

        elif e_type == "content_block_delta":
            idx = event.get("index", 0)
            delta = event.get("delta", {})
            if idx < len(content_blocks):
                b = content_blocks[idx]
                if delta.get("type") == "text_delta":
                    b["text"] = b.get("text", "") + delta.get("text", "")
                elif delta.get("type") == "thinking_delta":
                    b["thinking"] = b.get("thinking", "") + delta.get("thinking", "")
                elif delta.get("type") == "signature_delta":
                    b["signature"] = delta.get("signature", "")
                elif delta.get("type") == "input_json_delta":
                    # Input delta will be parsed at the end if string
                    b["_raw_json"] = b.get("_raw_json", "") + delta.get("partial_json", "")

        elif e_type == "message_delta":
            delta = event.get("delta", {})
            stop_reason = delta.get("stop_reason", stop_reason)
            usage["output_tokens"] = event.get("usage", {}).get("output_tokens", 0)

    # Finalize tool_use inputs from collected JSON
    for b in content_blocks:
        if b.get("type") == "tool_use" and "_raw_json" in b:
            raw_val = b["_raw_json"]
            try:
                b["input"] = json.loads(raw_val) if raw_val else {}
            except Exception as e:
                log(f"[Streamer Warning] Ошибка парсинга JSON аргументов инструмента {b.get('name')}: {e} (raw: {raw_val})", level="WARN")
                b["input"] = {}
            del b["_raw_json"]

    return {
        "id": message_id,
        "type": "message",
        "role": "assistant",
        "content": content_blocks,
        "model": original_model,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": usage
    }
