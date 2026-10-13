import asyncio
import base64
import hashlib
import json
import re
import time
from typing import Dict, Any, List, Optional, AsyncGenerator, Tuple
import aiohttp

from auth.opencode_oauth import OPENCODE_USER_AGENT
from auth.opencode_catalog import get_catalog, output_limit
from backend.proxy_pool import ProxyPool, mask_proxy
from backend.router import is_free_tier_model
from config import (
    OPENCODE_CLIENT_NAME,
    OPENCODE_MERGE_SYSTEM_BLOCKS,
    OPENCODE_PROJECT_ID,
    OPENCODE_ZEN_BASE_URL,
    SAVE_DETAILED_REQUEST_LOGS,
)
from logger import LOGS_DIR, log
from transformer.opencode_ids import message_identifier, session_identifier
from transformer.opencode_tool_alias import (
    client_tool_name,
    free_tier_markers,
    missing_free_tier_tools,
)
from transformer.opencode_prompts import (
    DROP_TOKEN_ACCOUNTING,
    build_opencode_system,
    is_token_accounting,
    strip_token_accounting,
)

DEFAULT_ZEN_MODEL = "space-bunny-free"
REQUEST_TIMEOUT_SECONDS = 300
DEFAULT_COOLDOWN_SECONDS = 600.0

# What the opencode client itself sends when nobody is logged in
# (packages/core/src/plugin/provider/opencode.ts: with no credential the provider is given the literal
# apiKey "public" and every model with a non-zero input cost is switched off). Verified against the
# live gateway on 2026-10-14: `Bearer public` + a streamed request + a non-empty tools array is
# accepted for the free models and refused for the paid ones with AuthError "Missing API key".
PUBLIC_TOKEN = "public"
ANONYMOUS_ACCOUNT = {"id": "public@opencode", "email": "public@opencode", "anonymous": True}

PAID_NEEDS_ACCOUNT_MESSAGE = (
    "This OpenCode model is paid and needs a Console API key: "
    "python cli_auth.py login --provider opencode --api-key <KEY>. Free models work without one."
)
FREE_TIER_REFUSED_MESSAGE = (
    "OpenCode refused the anonymous free tier for this request. The gateway only serves it to a turn "
    "that is streamed, carries tools under opencode's own names, and names a session in opencode's "
    "format - all of which this proxy does, so the client may be too new or too old for it. A Console "
    "API key removes the restriction: python cli_auth.py login --provider opencode --api-key <KEY>"
)

# Zen's per-model output cap is not published, so a Claude Code turn asking for max_tokens: 32000 can
# come back as a 400. Only a complaint that names the token cap triggers the retry below.
_MAX_TOKENS_MENTION = re.compile(r"max[_\s-]?tokens?|max[_\s-]?completion[_\s-]?tokens?", re.IGNORECASE)
_OVER_CAP_MENTION = re.compile(
    r"too (?:large|big|high|long)|exceed|greater than|maximum|at most|limit|supported", re.IGNORECASE
)


def is_output_cap_rejection(detail: str) -> bool:
    """True when an upstream 400 is about the requested output-token cap rather than the payload."""
    text = detail or ""
    return bool(_MAX_TOKENS_MENTION.search(text) and _OVER_CAP_MENTION.search(text))


def model_hint(payload: Dict[str, Any]) -> str:
    return payload.get("model") or DEFAULT_ZEN_MODEL

TOOL_CHOICES = {"auto": "auto", "any": "required", "none": "none"}
STOP_REASONS = {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use", "content_filter": "end_turn"}
EFFORT_VALUES = {"none", "minimal", "low", "medium", "high", "max"}

# Anthropic error type -> HTTP status surfaced to the client.
OPENCODE_ERROR_STATUS = {
    "invalid_request_error": 400,
    "authentication_error": 401,
    "permission_error": 403,
    "not_found_error": 404,
    "request_too_large": 413,
    "rate_limit_error": 429,
    "api_error": 500,
    "overloaded_error": 503,
    "timeout_error": 504,
}
# Upstream Zen HTTP status -> Anthropic error type, so a raw 400/401 from the gateway
# does not reach the client disguised as an opaque 500.
HTTP_STATUS_ERRORS = {
    400: "invalid_request_error",
    401: "authentication_error",
    402: "invalid_request_error",
    403: "permission_error",
    404: "not_found_error",
    413: "request_too_large",
    422: "invalid_request_error",
    429: "rate_limit_error",
    500: "api_error",
    502: "api_error",
    503: "overloaded_error",
    504: "timeout_error",
}

# Claude Code's own context-budget bookkeeping. It is emitted as a system turn between every
# model turn, carries no instruction for the model, and would otherwise inject hundreds of
# noise messages into the Zen context. See DROP_TOKEN_ACCOUNTING in transformer/opencode_prompts.py.


def escape_system_update_text(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def wrap_system_update(text: str) -> str:
    return f"<system-update>\n{escape_system_update_text(text)}\n</system-update>"


def zen_model_id(model: Optional[str]) -> str:
    """Zen receives the bare model id. The `opencode/` prefix is an opencode-side namespace and is
    never sent on the wire (packages/core/src/model.ts splits providerID/modelID)."""
    name = (model or "").strip() or DEFAULT_ZEN_MODEL
    return name.rsplit("/", 1)[-1] if "/" in name else name


def _account_id(account: Dict[str, Any]) -> str:
    return account.get("email") or account.get("id") or "opencode"


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(
            p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
        ).strip()
    return ""


def _tool_result_text(part: Dict[str, Any]) -> str:
    """OpenAI tool messages have no is_error flag, so a failure has to be marked in the text;
    dropping the flag makes a failed call look exactly like a successful one."""
    content = part.get("content", "")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = "\n".join(
            p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
        )
    else:
        text = json.dumps(content or "")
    if part.get("is_error"):
        return f"Error: {text}" if text else "Error"
    return text


def _image_part(block: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    src = block.get("source") or {}
    if src.get("type") == "base64":
        return {"type": "image_url", "image_url": {"url": f"data:{src.get('media_type', 'image/png')};base64,{src.get('data', '')}"}}
    if src.get("type") == "url":
        return {"type": "image_url", "image_url": {"url": src.get("url", "")}}
    return None


def _append_user_message(messages: List[Dict[str, Any]], content: Any) -> None:
    """Appends a user turn, merging into the previous one when both are plain text."""
    previous = messages[-1] if messages else None
    if isinstance(content, str) and previous is not None \
            and previous.get("role") == "user" and isinstance(previous.get("content"), str):
        previous["content"] = f"{previous['content']}\n{content}"
        return
    messages.append({"role": "user", "content": content})


def _reasoning_effort(anthropic_req: Dict[str, Any]) -> Optional[str]:
    """Claude Code asks for reasoning through Anthropic's vocabulary; Zen expects reasoning_effort."""
    output_config = anthropic_req.get("output_config")
    effort = output_config.get("effort") if isinstance(output_config, dict) else None
    if not effort:
        thinking = anthropic_req.get("thinking")
        if isinstance(thinking, dict) and thinking.get("type") == "enabled":
            effort = "high" if (thinking.get("budget_tokens") or 0) >= 16000 else "medium"
    if isinstance(effort, str) and effort.lower() in EFFORT_VALUES:
        return effort.lower()
    return None


def anthropic_to_opencode_payload(anthropic_req: Dict[str, Any], free_tier_markers_on_wire: bool = False) -> Dict[str, Any]:
    """Translates an Anthropic Messages API payload into the OpenAI-compatible Chat Completions
    payload opencode sends to Zen.

    Mirrors the opencode lowering layer (packages/ai/src/protocols/openai-chat.ts :: lowerMessages):
      * only messages[0] may be a system turn; mid-conversation system turns become
        <system-update> user messages (opencode itself never emits a bare mid-array system role).
      * Zen receives the bare model id, without the opencode/ provider prefix.
      * tool schemas carry strict:false, which is what opencode emits for this provider.
      * with free_tier_markers_on_wire two extra tools go out under the names the anonymous free tier
        looks for; the client's own tools are untouched. Pass it only when no credential is in play.
    """
    messages: List[Dict[str, Any]] = []
    raw_model = anthropic_req.get("model") or DEFAULT_ZEN_MODEL
    tool_names = [t.get("name", "") for t in anthropic_req.get("tools") or []]

    # opencode's own system prompt for this model, then the CLI agent prompt with the tools this
    # request carries, then the client's own instructions. Each block is its own system message so
    # the opencode prefix stays byte-identical to what the opencode client sends; set
    # OPENCODE_MERGE_SYSTEM_BLOCKS if a gateway ever refuses more than one leading system turn.
    sys_blocks = build_opencode_system(raw_model, anthropic_req.get("system"), tool_names)
    if OPENCODE_MERGE_SYSTEM_BLOCKS:
        sys_blocks = ["\n\n".join(sys_blocks)]
    for block in sys_blocks:
        messages.append({"role": "system", "content": block})

    # System updates are buffered so they never land between an assistant tool_calls turn and the
    # tool messages answering it, which Chat Completions requires to be adjacent.
    pending_updates: List[str] = []

    def flush_updates():
        if not pending_updates:
            return
        text = "\n".join(pending_updates)
        pending_updates.clear()
        _append_user_message(messages, text)

    for msg in anthropic_req.get("messages", []):
        role = msg.get("role", "user")
        content = msg.get("content")

        # Claude Code interleaves system turns into the message list; they are chronological
        # operator updates, not the head prompt.
        if role == "system":
            text = _message_text(content)
            if text and DROP_TOKEN_ACCOUNTING:
                if is_token_accounting(text):
                    continue
                text = strip_token_accounting(text)
            if text:
                pending_updates.append(wrap_system_update(text))
            continue

        if not isinstance(content, list):
            text = content or ""
            if not text.strip():
                continue
            flush_updates()
            if role == "user":
                _append_user_message(messages, text)
            else:
                messages.append({"role": role, "content": text})
            continue

        text_parts: List[str] = []
        parts: List[Dict[str, Any]] = []
        tool_calls: List[Dict[str, Any]] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            kind = part.get("type")
            if kind == "text":
                text_parts.append(part.get("text", ""))
                parts.append({"type": "text", "text": part.get("text", "")})
            elif kind == "image":
                image = _image_part(part)
                if image:
                    parts.append(image)
            elif kind == "tool_use":
                tool_calls.append({
                    "id": part.get("id"),
                    "type": "function",
                    "function": {"name": part.get("name"), "arguments": json.dumps(part.get("input", {}))},
                })
            elif kind == "tool_result":
                # Tool messages must directly follow the assistant tool_calls turn
                messages.append({
                    "role": "tool",
                    "tool_call_id": part.get("tool_use_id"),
                    "content": _tool_result_text(part),
                })
            # thinking / redacted_thinking blocks are Anthropic-only and are dropped

        if role == "assistant":
            if tool_calls:
                flush_updates()
                messages.append({
                    "role": "assistant",
                    "content": "\n".join(text_parts) or None,
                    "tool_calls": tool_calls,
                })
            elif text_parts:
                flush_updates()
                messages.append({"role": "assistant", "content": "\n".join(text_parts)})
            continue

        if parts:
            flush_updates()
            # Plain text stays a string; images need the multipart form
            has_image = any(p["type"] == "image_url" for p in parts)
            _append_user_message(messages, parts if has_image else "\n".join(text_parts))

    flush_updates()

    payload: Dict[str, Any] = {
        "model": zen_model_id(raw_model),
        "messages": messages,
    }

    if "max_tokens" in anthropic_req:
        # opencode sends max_completion_tokens to Zen (detectMaxTokensField in packages/ai openai-chat.ts)
        payload["max_completion_tokens"] = anthropic_req["max_tokens"]
    if "temperature" in anthropic_req:
        payload["temperature"] = anthropic_req["temperature"]
    if "top_p" in anthropic_req:
        payload["top_p"] = anthropic_req["top_p"]
    if anthropic_req.get("stop_sequences"):
        payload["stop"] = anthropic_req["stop_sequences"]

    # Translate tools if present
    tools = anthropic_req.get("tools")
    if tools:
        # Every tool the client sends is forwarded, schema untouched - the CLI agent only ever offers
        # the tools its harness runs, so dropping "unknown" ones would silently shrink what the model
        # can do. strict:false is what opencode emits for this provider.
        payload["tools"] = [{
            "type": "function",
            "function": {
                "name": t.get("name"),
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {}),
                # detectSupportsStrictMode("opencode", ...) is true upstream, and openai-compatible
                # emits the field either way; models that validate tools reject it when absent.
                "strict": False,
            },
        } for t in tools]
        choice = anthropic_req.get("tool_choice") or {}
        if choice.get("type") == "tool":
            payload["tool_choice"] = {"type": "function", "function": {"name": choice.get("name")}}
        elif choice.get("type") in TOOL_CHOICES:
            payload["tool_choice"] = TOOL_CHOICES[choice["type"]]

    if free_tier_markers_on_wire and payload.get("tools"):
        # The names are added, not substituted: the client's own tools go out exactly as written, so
        # the model keeps the surface Claude Code gave it and the history stays consistent.
        payload["tools"] = payload["tools"] + free_tier_markers(payload["tools"])

    effort = _reasoning_effort(anthropic_req)
    if effort:
        payload["reasoning_effort"] = effort

    return payload


def _session_seed(anthropic_req: Dict[str, Any]) -> str:
    raw = (anthropic_req.get("metadata") or {}).get("user_id")
    if raw:
        # Claude Code packs {"device_id", "account_uuid", "session_id"} into user_id; the session id
        # alone identifies the conversation, so a device reinstall does not change the affinity.
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            parsed = None
        if isinstance(parsed, dict) and parsed.get("session_id"):
            return str(parsed["session_id"])
        return str(raw)
    first = next((m for m in anthropic_req.get("messages", []) if m.get("role") == "user"), {})
    return json.dumps(first.get("content", ""), sort_keys=True)


# The id itself is built by transformer/opencode_ids.py: the gateway only admits an anonymous turn
# whose x-opencode-session carries opencode's own shape (see that module for the port and the shape).
def conversation_session_id(anthropic_req: Dict[str, Any]) -> str:
    """Stable opencode-style session id per conversation.

    It doubles as the affinity key. Upstream that is parentID ?? fork?.sessionID ?? session.id
    (packages/core/src/session/affinity.ts), but the Anthropic payload carries no parent-session
    signal - Claude Code subagents inherit the same metadata.user_id - so it collapses to the id.
    """
    return session_identifier(_session_seed(anthropic_req))


def _sse(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _error(kind: str, message: str) -> str:
    return _sse("error", {"type": "error", "error": {"type": kind, "message": message}})


def _save_payload(req_id: str, payload: Dict[str, Any]) -> None:
    """Writes the outgoing Zen request next to the incoming one. The system blocks and the tool set
    the gateway actually saw are otherwise invisible, which is the only way to check them live."""
    try:
        path = LOGS_DIR / f"req_{req_id}_opencode_payload.json"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        log(f"[Logging Error] {e}", level="ERROR")


def _dummy_signature(msg_id: str, index: int) -> str:
    """Anthropic expects a signature to close a thinking block. Zen does not issue one, so a stable
    opaque value is derived from the message id and lets the client round-trip the block."""
    return base64.b64encode(hashlib.sha256(f"{msg_id}:{index}".encode("utf-8")).digest()).decode("ascii")


def _parse_tool_input(raw: str) -> Dict[str, Any]:
    """A tool call cut off by max_tokens arrives as truncated JSON; a missing input plus a log line
    is recoverable, an unhandled JSONDecodeError would turn the whole turn into a 500."""
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        log(f"[OpenCode] Truncated tool arguments ({len(raw)} bytes), sending empty input instead", level="WARN")
        return {}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


async def _safe_text(response: aiohttp.ClientResponse) -> str:
    try:
        return (await response.text()).strip()
    except Exception:
        return "<unreadable error body>"


async def fold_sse_to_message(chunks: AsyncGenerator[str, None]) -> Tuple[int, Dict[str, Any]]:
    """Folds Anthropic SSE chunks into one Messages API response; returns (http_status, body)."""
    message: Optional[Dict[str, Any]] = None
    blocks: Dict[int, Dict[str, Any]] = {}
    async for chunk in chunks:
        data_line = next((l for l in chunk.splitlines() if l.startswith("data:")), None)
        if data_line is None:
            continue
        try:
            event = json.loads(data_line[5:].strip())
        except ValueError:
            continue
        kind = event.get("type")
        if kind == "error":
            err = event.get("error") or {}
            return OPENCODE_ERROR_STATUS.get(err.get("type"), 500), event
        if kind == "message_start":
            message = event["message"]
            message.setdefault("usage", {"input_tokens": 0, "output_tokens": 0})
        elif kind == "content_block_start":
            blocks[event["index"]] = {**event["content_block"], "_json": ""}
        elif kind == "content_block_delta":
            delta = event["delta"]
            block = blocks.setdefault(event["index"], {"type": "text", "text": "", "_json": ""})
            if delta["type"] == "text_delta":
                block["text"] = block.get("text", "") + delta["text"]
            elif delta["type"] == "thinking_delta":
                block["thinking"] = block.get("thinking", "") + delta["thinking"]
            elif delta["type"] == "signature_delta":
                block["signature"] = delta["signature"]
            elif delta["type"] == "input_json_delta":
                block["_json"] += delta["partial_json"]
        elif kind == "message_delta" and message is not None:
            message.update(event["delta"])
            message.setdefault("usage", {}).update(event.get("usage", {}))
    if message is None:
        return 502, {"type": "error", "error": {"type": "api_error", "message": "OpenCode returned an empty response"}}
    content = []
    for index in sorted(blocks):
        block = blocks[index]
        raw = block.pop("_json")
        if block["type"] == "tool_use":
            block["input"] = _parse_tool_input(raw)
        content.append(block)
    message["content"] = content
    return 200, message


def upstream_error_kind(detail: str) -> Optional[str]:
    """Extracts the gateway's own error type, e.g. FreeTierError, from an error body.

    Zen answers with {"type": "error", "error": {"type": "<Kind>", "message": "..."}} (and a flat
    {"error": {"type": ...}} from some upstream paths), so the type is what tells a policy refusal
    apart from a broken credential.
    """
    if not detail or "{" not in detail:
        return None
    try:
        body = json.loads(detail[detail.index("{"):])
    except ValueError:
        return None
    while isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("type"), str):
            return error["type"]
        if isinstance(body.get("type"), str) and body["type"] not in ("error",):
            return body["type"]
        body = error if isinstance(error, dict) else None
    return None


# Policy refusals: the credential works, the account is simply not allowed to run this model.
# Cooling the account down for these would mute a perfectly good login.
POLICY_ERRORS: Dict[str, Tuple[str, str]] = {
    "FreeTierError": ("permission_error", FREE_TIER_REFUSED_MESSAGE),
    "RegionError": ("permission_error", "This OpenCode model is not available in your region."),
    "ModelDeprecated": ("not_found_error", "This OpenCode model has been deprecated and was removed from Zen."),
}


class OpenCodeClient:
    def __init__(self, token_manager, base_url: str = OPENCODE_ZEN_BASE_URL, http_session=None,
                 proxy_pool: Optional[ProxyPool] = None):
        self.token_manager = token_manager
        # Outbound IPs: a throttled route hands the turn to the next one before an account is cooled down
        self.proxy_pool = proxy_pool or ProxyPool(token_manager)
        self.base_url = base_url.rstrip("/")
        # Reused across requests; a fresh session per turn would pay a full TLS handshake every time.
        self.http_session = http_session
        self._catalog: Optional[Dict[str, Any]] = None

    async def catalog(self, token: Optional[str] = None) -> Dict[str, Any]:
        """Live catalogue for this pool, fetched off the event loop and cached process-wide."""
        if self._catalog is None:
            self._catalog = await asyncio.to_thread(get_catalog, token, OPENCODE_USER_AGENT)
        return self._catalog

    def _candidate_accounts(self, model_name: str) -> List[Dict[str, Any]]:
        """Accounts to try for this model, active one first. Empty when every entry is cooling down
        and no anonymous turn is possible - the caller turns that into the right error.

        With no credential at all the free models still work: the opencode client sends the literal
        token "public" for them, so the same thing is offered here as a synthetic account. A paid
        model gets none, because the gateway answers that with AuthError rather than a free-tier one.
        """
        accounts = []
        if self.token_manager:
            accounts = self.token_manager.get_available_accounts(
                model_name, provider="opencode", allow_cooldown_fallback=False
            )
        if not accounts and is_free_tier_model(model_name):
            return [ANONYMOUS_ACCOUNT]
        return accounts

    def _request_headers(self, token: str, anthropic_req: Dict[str, Any],
                         catalog: Optional[Dict[str, Any]] = None,
                         message_id: Optional[str] = None) -> Dict[str, str]:
        # Same headers opencode adds to every model request (session/model-request.ts), plus
        # whatever the Console handed this account (x-opencode-org-id), which it uses to attribute
        # the request to the workspace.
        # The id is computed once: it is stamped on first use, and a second lookup could in principle
        # land after the stamp cache dropped this conversation and return a different id, which would
        # put two different sessions in the headers of one request.
        session_id = conversation_session_id(anthropic_req)
        headers = {
            "Authorization": f"Bearer {token}",
            "x-opencode-session-id": session_id,
            "x-session-affinity": session_id,
            "X-Session-Id": session_id,
            "User-Agent": OPENCODE_USER_AGENT,
            "x-opencode-project": OPENCODE_PROJECT_ID,
            "x-opencode-session": session_id,
            "x-opencode-client": OPENCODE_CLIENT_NAME,
            "Content-Type": "application/json",
        }
        # The Console forwards this one to the inference service as x-opencode-request-id.
        if message_id:
            headers["x-opencode-request"] = message_id
        headers.update((catalog or {}).get("headers") or {})
        return headers

    def _account_error(self, status: int, detail: str, account: Dict[str, Any],
                       model_name: str) -> Tuple[str, str]:
        """Maps an upstream failure onto an Anthropic error and cools the account down where that
        helps. Cooldowns are per model, so one throttled model does not mute the rest of the pool."""
        kind = HTTP_STATUS_ERRORS.get(status, "api_error")
        ident = _account_id(account)
        policy = POLICY_ERRORS.get(upstream_error_kind(detail) or "")
        if policy:
            # The account is fine; the request is not allowed. Cooling it down would hide the
            # reason behind a rate limit and mute the whole pool for a policy decision.
            return policy[0], f"OpenCode refused {model_name} for {ident}: {policy[1]}"
        if status == 429:
            self.token_manager.mark_account_cooldown(
                ident, cooldown_seconds=DEFAULT_COOLDOWN_SECONDS, model=model_name, provider="opencode"
            )
            return "rate_limit_error", f"OpenCode 429 rate limit for {model_name} on {ident}: {detail}"
        if status in (401, 403):
            # The credential itself is rejected, so it is dead for every model until re-login.
            self.token_manager.mark_account_cooldown(
                ident, cooldown_seconds=DEFAULT_COOLDOWN_SECONDS, model="*", provider="opencode"
            )
            return kind, f"OpenCode rejected the credentials of {ident} (HTTP {status}): {detail}"
        if status == 402:
            # Not a transport problem: the account authenticated but has no funds. Cooldown is
            # deliberately skipped so the message reaches the user instead of being retried.
            return kind, f"OpenCode billing error for {ident}: {detail}"
        return kind, f"OpenCode API error ({status}): {detail}"

    async def generate_stream(self, anthropic_req: Dict[str, Any], headers: Optional[Dict[str, str]] = None,
                              req_id: Optional[str] = None) -> AsyncGenerator[str, None]:
        """Streams responses from OpenCode Zen API and yields SSE formatted chunks in Anthropic
        Messages style, failing over across the account pool when one is throttled or rejected."""
        model_name = zen_model_id(anthropic_req.get("model"))
        accounts = self._candidate_accounts(model_name)
        if not accounts:
            wait = self.token_manager.cooldown_remaining("opencode", model_name) if self.token_manager else 0.0
            if wait > 0:
                yield _error("rate_limit_error",
                             f"All OpenCode accounts are rate limited for {model_name}; retry in {int(wait)}s")
            elif is_free_tier_model(model_name):
                # Only a cooldown can have emptied the pool here; without one the free models run
                # anonymously. Kept distinct so the reason is never guessed at from the outside.
                yield _error("rate_limit_error",
                             f"OpenCode free models are temporarily unavailable for {model_name}; retry shortly")
            else:
                yield _error("permission_error", PAID_NEEDS_ACCOUNT_MESSAGE)
            return

        # The account's own endpoint and org header come from the Console config; the documented
        # public endpoint is the fallback when there is no Console login to ask.
        catalog = await self.catalog(accounts[0].get("access_token") if accounts else None)
        base_url = (catalog.get("base_url") or self.base_url).rstrip("/")
        url = f"{base_url}/chat/completions"
        cap = output_limit(catalog, model_name)
        # opencode's own message id, which also rides along as x-opencode-request.
        msg_id = message_identifier(f"{req_id or 'stream'}:{time.time_ns()}")
        owned_session = self.http_session
        if owned_session is None:
            owned_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS))

        try:
            last_error: Optional[Tuple[str, str]] = None
            for account in accounts:
                # Anonymous turns carry the literal "public" token and never touch the pool; a real
                # account refreshes, which is a blocking HTTP call, so keep it off the event loop.
                if account.get("anonymous"):
                    token = PUBLIC_TOKEN
                else:
                    token, _ = await asyncio.to_thread(self.token_manager.get_token_for_account, account)
                if not token:
                    last_error = ("authentication_error",
                                  f"OpenCode account '{_account_id(account)}' has no usable access token")
                    continue

                anonymous = bool(account.get("anonymous"))
                payload = anthropic_to_opencode_payload(anthropic_req, free_tier_markers_on_wire=anonymous)
                payload["stream"] = True
                payload["stream_options"] = {"include_usage": True}
                # Claude Code always asks for its own max_tokens; the catalogue knows the real cap.
                if cap and payload.get("max_completion_tokens", 0) > cap:
                    payload["max_completion_tokens"] = cap
                if anonymous:
                    # The free tier is only served to a request that advertises the tool names the
                    # opencode client uses. Claude Code sends Bash and Read, so the markers are
                    # copies of them; a request without either is refused, and says so here first.
                    missing = missing_free_tier_tools(payload.get("tools") or [])
                    if missing:
                        log(f"[OpenCode] anonymous turn has no tool to answer {', '.join(missing)}; "
                            "Zen will refuse the free tier", level="WARN")
                if SAVE_DETAILED_REQUEST_LOGS and req_id:
                    _save_payload(req_id, payload)

                request_headers = self._request_headers(token, anthropic_req, catalog, message_id=msg_id)
                response = None
                for route in self.proxy_pool.routes():
                    session, extra = self.proxy_pool.session_for(route, owned_session)
                    try:
                        response = await self._post(session, url, payload, request_headers, **extra)
                    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                        self.proxy_pool.mark_limited(route, failed=True)
                        last_error = ("api_error", f"OpenCode connection error via {mask_proxy(route)}: {e}")
                        response = None
                        continue
                    if response.status == 429 and self.proxy_pool.has_alternative(route):
                        # This IP is out of quota; the same turn goes out through the next one
                        retry_after = response.headers.get("Retry-After", "")
                        self.proxy_pool.mark_limited(route, float(retry_after) if retry_after.isdigit() else None)
                        last_error = ("rate_limit_error", f"OpenCode 429 via {mask_proxy(route)}")
                        response.release()
                        response = None
                        continue
                    if response.status == 200:
                        self.proxy_pool.mark_good(route)
                    break
                if response is None:
                    continue

                if response.status != 200:
                    detail = await _safe_text(response)
                    response.release()
                    last_error = self._account_error(response.status, detail, account, model_name)
                    log(f"[OpenCode] {last_error[1]}", level="WARN")
                    continue

                try:
                    async for chunk in self._relay(response, msg_id, model_name, route_reserved_tools=anonymous):
                        yield chunk
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    yield _error("api_error", f"OpenCode connection error: {e}")
                finally:
                    response.release()
                return

            kind, message = last_error or ("api_error", "OpenCode: no usable account")
            yield _error(kind, message)
        finally:
            if self.http_session is None:
                await owned_session.close()

    async def _post(self, session, url: str, payload: Dict[str, Any],
                    headers: Dict[str, str], **extra) -> aiohttp.ClientResponse:
        """Posts the turn, retrying once without the output cap when the model refuses the cap.

        Claude Code always asks for its own max_tokens (32000), while a free Zen model caps output
        far below that and answers with a 400 naming the limit. Dropping the field lets the model
        use its own default, which is what opencode does when a provider has no advertised cap.
        """
        response = await session.post(url, json=payload, headers=headers, **extra)
        if response.status == 400 and "max_completion_tokens" in payload:
            # Reading the body caches it, so the caller still gets a readable error if we do not retry.
            detail = await _safe_text(response)
            if is_output_cap_rejection(detail):
                response.release()
                log(f"[OpenCode] {model_hint(payload)} refused the requested output cap, "
                    f"retrying without max_completion_tokens", level="WARN")
                trimmed = {k: v for k, v in payload.items() if k != "max_completion_tokens"}
                return await session.post(url, json=trimmed, headers=headers, **extra)
        return response

    async def _relay(self, response: aiohttp.ClientResponse, msg_id: str,
                     model_name: str, route_reserved_tools: bool = False) -> AsyncGenerator[str, None]:
        """Translates Zen's OpenAI chat-completion chunks into Anthropic Messages SSE."""
        yield _sse("message_start", {
            "type": "message_start",
            "message": {
                "id": msg_id, "type": "message", "role": "assistant", "model": model_name,
                "content": [], "stop_reason": None, "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        })

        next_index = 0
        open_block: Optional[int] = None
        open_kind: Optional[str] = None
        thinking_index: Optional[int] = None
        tool_blocks: Dict[int, int] = {}  # upstream tool_call index -> anthropic block index
        stop_reason = "end_turn"
        usage = {"input_tokens": 0, "output_tokens": 0}

        def switch_block(kind: str, content_block: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
            """Events needed to move the stream onto a block of `kind`, closing the previous one.
            Each index is opened exactly once, with the block's real content_block - emitting a
            placeholder start and then the real one for the same index is a malformed stream."""
            nonlocal open_block, open_kind, next_index, thinking_index
            events: List[Dict[str, Any]] = []
            if open_kind == kind:
                return events
            if open_block is not None:
                if open_kind == "thinking":
                    events.append({
                        "type": "content_block_delta", "index": thinking_index,
                        "delta": {"type": "signature_delta", "signature": _dummy_signature(msg_id, thinking_index)},
                    })
                events.append({"type": "content_block_stop", "index": open_block})
            index = next_index
            next_index += 1
            if kind == "thinking":
                thinking_index = index
                content_block = {"type": "thinking", "thinking": ""}
            elif content_block is None:
                content_block = {"type": "text", "text": ""}
            events.append({"type": "content_block_start", "index": index, "content_block": content_block})
            open_block, open_kind = index, kind
            return events

        async for line in response.content:
            line_str = line.decode("utf-8").strip()
            if not line_str.startswith("data:"):
                continue
            data_str = line_str[5:].strip()
            if data_str == "[DONE]":
                break
            try:
                chunk = json.loads(data_str)
            except ValueError:
                continue
            if chunk.get("usage"):
                usage = {
                    "input_tokens": chunk["usage"].get("prompt_tokens", 0),
                    "output_tokens": chunk["usage"].get("completion_tokens", 0),
                }
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}

            reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
            if reasoning:
                for event in switch_block("thinking"):
                    yield _sse(event["type"], event)
                yield _sse("content_block_delta", {
                    "type": "content_block_delta", "index": open_block,
                    "delta": {"type": "thinking_delta", "thinking": reasoning},
                })

            if delta.get("content"):
                for event in switch_block("text"):
                    yield _sse(event["type"], event)
                yield _sse("content_block_delta", {
                    "type": "content_block_delta", "index": open_block,
                    "delta": {"type": "text_delta", "text": delta["content"]},
                })

            for call in delta.get("tool_calls") or []:
                up_index = call.get("index", 0)
                fn = call.get("function") or {}
                if up_index not in tool_blocks:
                    name = fn.get("name", "")
                    if route_reserved_tools:
                        # A call to one of the reserved names is routed to the tool the harness runs,
                        # so a model that ignored the "do not call this" note still gets its work done.
                        name = client_tool_name(name) or name
                    block = {
                        "type": "tool_use", "id": call.get("id") or f"toolu_{msg_id}_{up_index}",
                        "name": name, "input": {},
                    }
                    for event in switch_block(f"tool_use:{up_index}", block):
                        yield _sse(event["type"], event)
                    tool_blocks[up_index] = open_block
                if fn.get("arguments"):
                    yield _sse("content_block_delta", {
                        "type": "content_block_delta", "index": tool_blocks[up_index],
                        "delta": {"type": "input_json_delta", "partial_json": fn["arguments"]},
                    })

            finish = choices[0].get("finish_reason")
            if finish:
                stop_reason = STOP_REASONS.get(finish, "end_turn")

        if open_block is not None:
            if open_kind == "thinking":
                yield _sse("content_block_delta", {
                    "type": "content_block_delta", "index": open_block,
                    "delta": {"type": "signature_delta", "signature": _dummy_signature(msg_id, open_block)},
                })
            yield _sse("content_block_stop", {"type": "content_block_stop", "index": open_block})
        yield _sse("message_delta", {
            "type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None}, "usage": usage,
        })
        yield _sse("message_stop", {"type": "message_stop"})