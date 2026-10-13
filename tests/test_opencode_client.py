import json
import unittest
from unittest.mock import MagicMock, patch

import aiohttp
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from auth.opencode_oauth import OPENCODE_USER_AGENT
from backend.opencode import (
    OpenCodeClient,
    anthropic_to_opencode_payload,
    conversation_session_id,
    fold_sse_to_message,
    is_token_accounting,
    zen_model_id,
)
from server import ProxyApp
from transformer.opencode_prompts import TOOL_GUIDANCE_PLACEHOLDER, build_opencode_system


def sse_lines(chunks):
    """Renders OpenAI chat-completion chunks the way Zen streams them."""
    return [f"data: {json.dumps(c)}\n".encode() for c in chunks] + [b"data: [DONE]\n"]


class FakeResponse:
    def __init__(self, status=200, lines=None, body=""):
        self.status = status
        self._lines = lines or []
        self._body = body
        self.released = False

    async def text(self):
        if self.released:
            raise RuntimeError("body is no longer available once released")
        return self._body

    def release(self):
        self.released = True

    @property
    def content(self):
        async def iterator():
            for line in self._lines:
                yield line
        return iterator()


class FakeSession:
    """Stands in for aiohttp.ClientSession: post() is awaited and close() releases the pool."""

    def __init__(self, responses=()):
        self._responses = list(responses)
        self.sent = []
        self.closed = False

    async def post(self, url, json=None, headers=None):
        self.sent.append((url, json, headers))
        nxt = self._responses.pop(0) if self._responses else FakeResponse()
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    async def close(self):
        self.closed = True


def make_token_manager(accounts=(), cooldown=0.0):
    tm = MagicMock()
    tm.get_available_accounts.return_value = list(accounts)
    tm.cooldown_remaining.return_value = cooldown
    tm.get_token_for_account.side_effect = lambda account, **kw: (account.get("access_token", "tok"), None)
    return tm


class TestPayloadTranslation(unittest.TestCase):
    def test_payload_translation(self):
        anthropic_req = {
            "model": "space-bunny-free",
            "messages": [{"role": "user", "content": "Hello"}],
            "system": "You are a helpful assistant.",
            "max_tokens": 1000,
        }
        oc_req = anthropic_to_opencode_payload(anthropic_req)
        self.assertEqual(oc_req["model"], "space-bunny-free")
        self.assertEqual(oc_req["messages"][0]["role"], "system")
        sys_text = oc_req["messages"][0]["content"]
        self.assertTrue(sys_text.startswith("You are an AI agent running in OpenCode"))
        self.assertTrue(sys_text.endswith("You are a helpful assistant."))
        self.assertNotIn(TOOL_GUIDANCE_PLACEHOLDER, sys_text)
        self.assertEqual(oc_req["messages"][1]["role"], "user")
        self.assertEqual(oc_req["messages"][1]["content"], "Hello")
        self.assertEqual(oc_req["max_completion_tokens"], 1000)
        self.assertNotIn("max_tokens", oc_req)

    def test_tool_choice_mapping(self):
        base = {"messages": [], "tools": [{"name": "ls"}]}
        self.assertEqual(anthropic_to_opencode_payload({**base, "tool_choice": {"type": "any"}})["tool_choice"], "required")
        self.assertEqual(
            anthropic_to_opencode_payload({**base, "tool_choice": {"type": "tool", "name": "ls"}})["tool_choice"],
            {"type": "function", "function": {"name": "ls"}})
        self.assertNotIn("tool_choice", anthropic_to_opencode_payload(base))

    def test_tool_result_with_text_and_image(self):
        req = {"model": "m", "messages": [
            {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "ls", "input": {}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": "ok"}]},
                {"type": "text", "text": "next"},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AA=="}},
            ]},
        ]}
        msgs = anthropic_to_opencode_payload(req)["messages"][1:]  # [0] is the opencode system prompt
        self.assertIsNone(msgs[0]["content"])
        self.assertEqual(msgs[1], {"role": "tool", "tool_call_id": "t1", "content": "ok"})
        self.assertEqual(msgs[2]["content"][0], {"type": "text", "text": "next"})
        self.assertEqual(msgs[2]["content"][1]["image_url"]["url"], "data:image/png;base64,AA==")

    def test_system_prompt_per_model_and_tools(self):
        claude_code = [
            {"type": "text", "text": "x-anthropic-billing-header: cc_version=1;"},
            {"type": "text", "text": "You are Claude Code, Anthropic's official CLI for Claude."},
            {"type": "text", "text": "Project rules from CLAUDE.md. Run via Claude Code."},
        ]
        tools = [{"name": "Bash"}, {"name": "Edit"}]
        base = anthropic_to_opencode_payload({"model": "opencode/big-pickle", "system": claude_code, "tools": tools, "messages": []})
        sys_text = base["messages"][0]["content"]
        self.assertIn("Do not chain shell commands", sys_text)
        self.assertIn("`old_string`", sys_text)
        self.assertNotIn("use the write tool", sys_text.lower())
        self.assertNotIn("billing", sys_text)
        self.assertNotIn("Claude Code", sys_text)
        self.assertTrue(sys_text.endswith("Project rules from CLAUDE.md. Run via OpenCode."))

        kimi = anthropic_to_opencode_payload({"model": "opencode/kimi-k2", "messages": []})["messages"][0]["content"]
        self.assertTrue(kimi.startswith("You are opencode, an interactive general AI agent")
                        or "OpenCode" in kimi[:200])

        claude = anthropic_to_opencode_payload({"model": "claude-x", "messages": []})["messages"][0]["content"]
        self.assertIn("# Code comments", claude)
        self.assertTrue(claude.startswith("You are an AI agent running in OpenCode"))

    def test_every_override_prompt_template_carries_the_guidance_placeholder(self):
        """Without the placeholder render_prompt is a no-op and shell/write/edit guidance vanishes."""
        for model in ("opencode/kimi-k2", "opencode/trinity", "opencode/muse-spark", "opencode/gpt-astra"):
            prompt = build_opencode_system(model, None, ["Bash", "Edit"])
            self.assertNotIn(TOOL_GUIDANCE_PLACEHOLDER, prompt, model)
            self.assertIn("Do not chain shell commands", prompt, model)
            self.assertIn("`old_string`", prompt, model)

    def test_session_id_is_stable_per_conversation(self):
        a = {"messages": [{"role": "user", "content": "hi"}]}
        b = {"messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]}
        c = {"messages": [{"role": "user", "content": "other"}]}
        self.assertEqual(conversation_session_id(a), conversation_session_id(b))
        self.assertNotEqual(conversation_session_id(a), conversation_session_id(c))
        self.assertRegex(conversation_session_id(a), r"^ses_[0-9a-f]{64}$")

    def test_session_id_follows_the_claude_code_session_not_the_device(self):
        def request(device, session):
            return {"metadata": {"user_id": json.dumps(
                {"device_id": device, "account_uuid": "", "session_id": session})}, "messages": []}

        same = request("device-a", "s-1")
        reinstalled = request("device-b", "s-1")
        other = request("device-a", "s-2")
        self.assertEqual(conversation_session_id(same), conversation_session_id(reinstalled))
        self.assertNotEqual(conversation_session_id(same), conversation_session_id(other))


class TestZenModelId(unittest.TestCase):
    def test_prefix_is_stripped(self):
        # opencode splits providerID/modelID and sends only the bare id on the wire.
        self.assertEqual(zen_model_id("opencode/nemotron-3-ultra-free"), "nemotron-3-ultra-free")
        self.assertEqual(zen_model_id("space-bunny-free"), "space-bunny-free")
        self.assertEqual(zen_model_id(None), "space-bunny-free")

    def test_payload_sends_bare_id_but_prompt_sees_the_full_name(self):
        payload = anthropic_to_opencode_payload({"model": "opencode/kimi-k2", "messages": []})
        self.assertEqual(payload["model"], "kimi-k2")
        self.assertTrue(payload["messages"][0]["content"].startswith("You are OpenCode, an interactive general AI agent"))


class TestSystemTurnLowering(unittest.TestCase):
    def _payload(self, messages):
        return anthropic_to_opencode_payload({"model": "m", "messages": messages})["messages"][1:]

    def test_mid_conversation_system_turn_becomes_a_system_update_user_message(self):
        msgs = self._payload([
            {"role": "user", "content": "hi"},
            {"role": "system", "content": "switch to plan mode"},
        ])
        # Merged into the preceding user turn, which is what opencode's lowering does too.
        self.assertEqual([m["role"] for m in msgs], ["user"])
        self.assertEqual(msgs[-1]["content"], "hi\n<system-update>\nswitch to plan mode\n</system-update>")

    def test_system_update_after_a_tool_turn_starts_a_new_user_turn(self):
        msgs = self._payload([
            {"role": "assistant", "content": "done"},
            {"role": "system", "content": "switch to plan mode"},
        ])
        self.assertEqual([m["role"] for m in msgs], ["assistant", "user"])
        self.assertEqual(msgs[-1]["content"], "<system-update>\nswitch to plan mode\n</system-update>")

    def test_system_update_text_is_xml_escaped(self):
        msgs = self._payload([
            {"role": "user", "content": "hi"},
            {"role": "system", "content": "a < b & c > d"},
        ])
        self.assertIn("a &lt; b &amp; c &gt; d", msgs[-1]["content"])

    def test_only_the_head_turn_is_a_system_role(self):
        msgs = self._payload([
            {"role": "system", "content": "first"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "yo"},
            {"role": "system", "content": "later"},
        ])
        roles = [m["role"] for m in msgs]
        self.assertNotIn("system", roles)
        self.assertEqual(roles[0], "user")

    def test_token_accounting_noise_is_dropped(self):
        for noise in (
            "<total_tokens>14976683 tokens left</total_tokens>",
            "<total_tokens>14976615 tokens left</total_tokens>\n\n<total_tokens>15000000 tokens left</total_tokens>",
            # The word "tokens" and the trailing "left" are both optional in the wild; a regex that
            # misses one spelling turns every bookkeeping turn back into a noise message.
            "<total_tokens>690000 left</total_tokens>",
            "<total_tokens>14976683</total_tokens>",
            "<total_tokens> 14,976,683 tokens remaining </total_tokens>",
        ):
            self.assertTrue(is_token_accounting(noise))
            self.assertFalse(is_token_accounting("Please refactor the parser"))
            msgs = self._payload([{"role": "user", "content": "hi"}, {"role": "system", "content": noise}])
            self.assertEqual([m["role"] for m in msgs], ["user"])

    def test_token_count_mentioned_in_a_real_instruction_is_not_bookkeeping(self):
        instruction = "Print <total_tokens>100 tokens left</total_tokens> as a marker in your output."
        self.assertFalse(is_token_accounting(instruction))
        msgs = self._payload([{"role": "user", "content": "hi"}, {"role": "system", "content": instruction}])
        self.assertIn("Print &lt;total_tokens&gt;100 tokens left&lt;/total_tokens&gt; as a marker", msgs[-1]["content"])

    def test_real_system_update_survives_next_to_token_noise(self):
        msgs = self._payload([
            {"role": "user", "content": "hi"},
            {"role": "system", "content": "<total_tokens>5 tokens left</total_tokens>"},
            {"role": "system", "content": "<system-reminder>be careful</system-reminder>"},
        ])
        self.assertIn("be careful", msgs[-1]["content"])
        self.assertNotIn("total_tokens", msgs[-1]["content"])

    def test_bookkeeping_is_stripped_from_an_update_that_has_real_content(self):
        mixed = ("give a recommendation, not an exhaustive survey\n\n"
                 "<total_tokens>15000000 tokens left</total_tokens>")
        msgs = self._payload([
            {"role": "user", "content": "hi"},
            {"role": "system", "content": mixed},
        ])
        self.assertIn("not an exhaustive survey", msgs[-1]["content"])
        self.assertNotIn("total_tokens", msgs[-1]["content"])

    def test_bookkeeping_is_stripped_from_the_head_system_prompt_too(self):
        """Claude Code also appends the budget counter to its main system block."""
        head = [{"type": "text", "text":
                 "You are an agent working with the user.\n\n<total_tokens>15000000 tokens left</total_tokens>"}]
        prompt = anthropic_to_opencode_payload({"model": "m", "system": head, "messages": []})["messages"][0]["content"]
        self.assertIn("working with the user", prompt)
        self.assertNotIn("total_tokens", prompt)

    def test_system_update_never_splits_an_assistant_tool_call_from_its_results(self):
        msgs = self._payload([
            {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]},
            {"role": "system", "content": "budget note"},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]},
        ])
        roles = [m["role"] for m in msgs]
        self.assertEqual(roles, ["assistant", "tool", "user"])
        self.assertIn("budget note", msgs[-1]["content"])


class TestToolAndEffortFields(unittest.TestCase):
    def test_tool_result_error_flag_is_preserved(self):
        msgs = anthropic_to_opencode_payload({"model": "m", "messages": [
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "is_error": True, "content": "no such file"}]},
        ]})["messages"][1:]
        self.assertEqual(msgs[0]["content"], "Error: no such file")

    def test_successful_tool_result_is_not_marked(self):
        msgs = anthropic_to_opencode_payload({"model": "m", "messages": [
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "fine"}]},
        ]})["messages"][1:]
        self.assertEqual(msgs[0]["content"], "fine")

    def test_tools_carry_strict_false(self):
        payload = anthropic_to_opencode_payload({
            "model": "m", "messages": [], "tools": [{"name": "Bash", "input_schema": {"type": "object"}}]})
        self.assertIs(payload["tools"][0]["function"]["strict"], False)

    def test_reasoning_effort_comes_from_output_config(self):
        payload = anthropic_to_opencode_payload({
            "model": "m", "messages": [], "output_config": {"effort": "high"}})
        self.assertEqual(payload["reasoning_effort"], "high")

    def test_reasoning_effort_derived_from_thinking_budget(self):
        payload = anthropic_to_opencode_payload({
            "model": "m", "messages": [], "thinking": {"type": "enabled", "budget_tokens": 32000}})
        self.assertEqual(payload["reasoning_effort"], "high")

    def test_absent_effort_is_not_invented(self):
        payload = anthropic_to_opencode_payload({"model": "m", "messages": []})
        self.assertNotIn("reasoning_effort", payload)


class TestFoldSseToMessage(unittest.IsolatedAsyncioTestCase):
    async def _fold(self, events):
        async def gen():
            for name, event in events:
                yield f"event: {name}\ndata: {json.dumps(event)}\n\n"
        return await fold_sse_to_message(gen())

    async def test_truncated_tool_arguments_do_not_crash(self):
        """A call cut off by max_tokens is recoverable; an unhandled JSONDecodeError is a 500."""
        status, message = await self._fold([
            ("message_start", {"type": "message_start", "message": {
                "id": "m", "type": "message", "role": "assistant", "model": "m", "content": [],
                "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 0}}}),
            ("content_block_start", {"type": "content_block_start", "index": 0,
                                     "content_block": {"type": "tool_use", "id": "t", "name": "B", "input": {}}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                     "delta": {"type": "input_json_delta", "partial_json": '{"a": 1'}}),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "max_tokens"},
                               "usage": {"output_tokens": 9}}),
        ])
        self.assertEqual(status, 200)
        self.assertEqual(message["stop_reason"], "max_tokens")
        self.assertEqual(message["content"][0]["input"], {})

    async def test_thinking_block_is_reconstructed(self):
        status, message = await self._fold([
            ("message_start", {"type": "message_start", "message": {
                "id": "m", "type": "message", "role": "assistant", "model": "m", "content": [],
                "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 0}}}),
            ("content_block_start", {"type": "content_block_start", "index": 0,
                                     "content_block": {"type": "thinking", "thinking": ""}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                     "delta": {"type": "thinking_delta", "thinking": "hmm"}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                     "delta": {"type": "signature_delta", "signature": "sig"}}),
        ])
        self.assertEqual(status, 200)
        self.assertEqual(message["content"][0], {"type": "thinking", "thinking": "hmm", "signature": "sig"})

    async def test_error_event_maps_to_a_status(self):
        async def gen():
            yield ('event: error\ndata: '
                   + json.dumps({"type": "error", "error": {"type": "permission_error", "message": "no"}})
                   + "\n\n")
        status, body = await fold_sse_to_message(gen())
        self.assertEqual(status, 403)


class TestOpenCodeStream(unittest.IsolatedAsyncioTestCase):
    async def test_tool_call_stream_and_fold(self):
        lines = [
            {"choices": [{"delta": {"content": "Hi"}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "ls", "arguments": "{\"p\":"}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "1}"}}]}, "finish_reason": "tool_calls"}]},
            {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 3}},
        ]
        session = FakeSession([FakeResponse(200, sse_lines(lines))])
        client = OpenCodeClient(make_token_manager([{"email": "a@b"}]), http_session=session)
        req = {"model": "m", "messages": [{"role": "user", "content": "x"}]}
        status, msg = await fold_sse_to_message(client.generate_stream(req))
        self.assertEqual(status, 200)
        self.assertEqual(msg["stop_reason"], "tool_use")
        self.assertEqual(msg["content"][0], {"type": "text", "text": "Hi"})
        self.assertEqual(msg["content"][1], {"type": "tool_use", "id": "c1", "name": "ls", "input": {"p": 1}})
        self.assertEqual(msg["usage"], {"input_tokens": 7, "output_tokens": 3})
        _, body, headers = session.sent[0]
        self.assertTrue(body["stream_options"]["include_usage"])
        self.assertEqual(headers["x-opencode-client"], "opencode")
        self.assertRegex(headers["User-Agent"], r"^opencode/[^/]+/[^/]+/[^/]+$")
        self.assertTrue(headers["User-Agent"].startswith("opencode/"))

    async def test_every_block_is_opened_once_with_its_real_type(self):
        """A placeholder text start followed by a tool_use start on the same index is a malformed
        stream; folding hides it because the second start overwrites the first."""
        lines = [
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "c1", "function": {"name": "Bash", "arguments": "{}"}},
                {"index": 1, "id": "c2", "function": {"name": "Read", "arguments": "{}"}},
            ]}}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ]
        session = FakeSession([FakeResponse(200, sse_lines(lines))])
        client = OpenCodeClient(make_token_manager([{"email": "a@b"}]), http_session=session)
        events = [json.loads(c.split("data: ", 1)[1])
                  async for c in client.generate_stream({"model": "m", "messages": []})]
        starts = [e for e in events if e["type"] == "content_block_start"]
        self.assertEqual([(e["index"], e["content_block"]["type"]) for e in starts],
                         [(0, "tool_use"), (1, "tool_use")])
        self.assertEqual([e["index"] for e in events if e["type"] == "content_block_stop"], [0, 1])
        self.assertEqual(events[-2]["delta"]["stop_reason"], "tool_use")

    async def test_output_cap_refusal_is_retried_without_max_completion_tokens(self):
        """Claude Code always asks for 32000; a free model's own cap must not fail the whole turn."""
        session = FakeSession([
            FakeResponse(400, body='{"error":{"message":"max_tokens is too large: 32000 > 8192"}}'),
            FakeResponse(200, sse_lines([{"choices": [{"delta": {"content": "ok"}}], "finish_reason": "stop"}])),
        ])
        client = OpenCodeClient(make_token_manager([{"email": "a@b"}]), http_session=session)
        status, message = await fold_sse_to_message(
            client.generate_stream({"model": "m", "messages": [], "max_tokens": 32000}))
        self.assertEqual(status, 200)
        self.assertEqual(message["content"][0]["text"], "ok")
        self.assertEqual(session.sent[0][1]["max_completion_tokens"], 32000)
        self.assertNotIn("max_completion_tokens", session.sent[1][1])

    async def test_unrelated_400_is_not_retried(self):
        session = FakeSession([FakeResponse(400, body='{"error":{"message":"unknown tool: WebFetch"}}')])
        client = OpenCodeClient(make_token_manager([{"email": "a@b"}]), http_session=session)
        status, body = await fold_sse_to_message(
            client.generate_stream({"model": "m", "messages": [], "max_tokens": 32000}))
        self.assertEqual(status, 400)
        self.assertIn("unknown tool", body["error"]["message"])
        self.assertEqual(len(session.sent), 1)

    async def test_reasoning_content_becomes_a_thinking_block(self):
        lines = [
            {"choices": [{"delta": {"reasoning_content": "let me think"}}]},
            {"choices": [{"delta": {"content": "answer"}}], "finish_reason": "stop"},
        ]
        session = FakeSession([FakeResponse(200, sse_lines(lines))])
        client = OpenCodeClient(make_token_manager([{"email": "a@b"}]), http_session=session)
        events = [chunk async for chunk in client.generate_stream({"model": "m", "messages": []})]
        blob = "".join(events)
        self.assertIn('"type": "thinking"', blob)
        self.assertIn('"type": "thinking_delta"', blob)
        self.assertIn('"type": "signature_delta"', blob)

    async def test_connection_error_becomes_error_event(self):
        session = FakeSession([aiohttp.ClientConnectionError("down")])
        client = OpenCodeClient(make_token_manager([{"email": "a@b"}]), http_session=session)
        status, body = await fold_sse_to_message(client.generate_stream({"model": "m", "messages": []}))
        self.assertEqual(status, 500)
        self.assertIn("connection error", body["error"]["message"])

    async def test_upstream_401_maps_to_authentication_error(self):
        session = FakeSession([FakeResponse(401, body="token expired")])
        client = OpenCodeClient(make_token_manager([{"email": "a@b"}]), http_session=session)
        status, body = await fold_sse_to_message(client.generate_stream({"model": "m", "messages": []}))
        self.assertEqual(status, 401)
        self.assertEqual(body["error"]["type"], "authentication_error")

    async def test_upstream_400_is_not_disguised_as_a_500(self):
        session = FakeSession([FakeResponse(400, body="unknown model")])
        client = OpenCodeClient(make_token_manager([{"email": "a@b"}]), http_session=session)
        status, body = await fold_sse_to_message(client.generate_stream({"model": "m", "messages": []}))
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["type"], "invalid_request_error")

    async def test_billing_error_surfaces_the_reason_and_is_not_retried(self):
        """402 means the account authenticated but has no funds; a retry storm would not help."""
        session = FakeSession([FakeResponse(402, body="Insufficient account funds")])
        tm = make_token_manager([{"email": "a@b"}])
        client = OpenCodeClient(tm, http_session=session)
        status, body = await fold_sse_to_message(client.generate_stream({"model": "m", "messages": []}))
        self.assertEqual(status, 400)
        self.assertIn("Insufficient account funds", body["error"]["message"])
        tm.mark_account_cooldown.assert_not_called()
        self.assertEqual(len(session.sent), 1)

    async def test_free_tier_gate_is_reported_as_a_permission_error(self):
        session = FakeSession([FakeResponse(403, body='{"type":"FreeTierError"}')])
        tm = make_token_manager([{"email": "a@b"}])
        client = OpenCodeClient(tm, http_session=session)
        status, body = await fold_sse_to_message(client.generate_stream({"model": "m", "messages": []}))
        self.assertEqual(status, 403)
        self.assertEqual(body["error"]["type"], "permission_error")
        self.assertIn("FreeTierError", body["error"]["message"])

    async def test_rate_limit_fails_over_to_the_next_account(self):
        session = FakeSession([
            FakeResponse(429, body="slow down"),
            FakeResponse(200, sse_lines([{"choices": [{"delta": {"content": "ok"}}], "finish_reason": "stop"}])),
        ])
        tm = make_token_manager([{"email": "a@b"}, {"email": "c@d"}])
        client = OpenCodeClient(tm, http_session=session)
        status, message = await fold_sse_to_message(client.generate_stream({"model": "m", "messages": []}))
        self.assertEqual(status, 200)
        self.assertEqual(message["content"][0]["text"], "ok")
        self.assertEqual(len(session.sent), 2)
        self.assertEqual(session.sent[1][2]["Authorization"], "Bearer tok")
        # Cooldown is per model so one throttled model does not mute the whole pool.
        tm.mark_account_cooldown.assert_called_once()
        self.assertEqual(tm.mark_account_cooldown.call_args.kwargs["model"], "m")

    async def test_rejected_credentials_cool_the_account_down_for_every_model(self):
        session = FakeSession([FakeResponse(403, body="forbidden")])
        tm = make_token_manager([{"email": "a@b"}])
        client = OpenCodeClient(tm, http_session=session)
        status, _ = await fold_sse_to_message(client.generate_stream({"model": "m", "messages": []}))
        self.assertEqual(status, 403)
        self.assertEqual(tm.mark_account_cooldown.call_args.kwargs["model"], "*")

    async def test_all_accounts_cooling_reports_a_rate_limit_not_an_auth_error(self):
        tm = make_token_manager([], cooldown=120.0)
        client = OpenCodeClient(tm, http_session=FakeSession())
        status, body = await fold_sse_to_message(client.generate_stream({"model": "m", "messages": []}))
        self.assertEqual(status, 429)
        self.assertEqual(body["error"]["type"], "rate_limit_error")
        self.assertIn("retry in", body["error"]["message"])

    async def test_empty_pool_reports_an_auth_error(self):
        tm = make_token_manager([], cooldown=0.0)
        client = OpenCodeClient(tm, http_session=FakeSession())
        status, body = await fold_sse_to_message(client.generate_stream({"model": "m", "messages": []}))
        self.assertEqual(status, 401)
        self.assertIn("cli_auth.py login --provider opencode", body["error"]["message"])

    async def test_owned_session_is_closed_after_the_stream(self):
        session = FakeSession([FakeResponse(200, sse_lines([{"choices": [{"delta": {"content": "x"}}]}]))])
        with patch("backend.opencode.aiohttp.ClientSession", lambda *a, **k: session):
            client = OpenCodeClient(make_token_manager([{"email": "a@b"}]))
            await fold_sse_to_message(client.generate_stream({"model": "m", "messages": []}))
        self.assertTrue(session.closed)

    async def test_shared_session_is_never_closed_by_the_client(self):
        session = FakeSession([FakeResponse(200, sse_lines([{"choices": [{"delta": {"content": "x"}}]}]))])
        client = OpenCodeClient(make_token_manager([{"email": "a@b"}]), http_session=session)
        await fold_sse_to_message(client.generate_stream({"model": "m", "messages": []}))
        self.assertFalse(session.closed)


class TestOpenCodeRoutingIntegration(AioHTTPTestCase):
    """End-to-end through /v1/messages: routing to Zen and the HTTP status of a failed turn."""

    async def get_application(self):
        self.proxy = ProxyApp(MagicMock(), session_auth_token="t")
        return self.proxy.app

    HEADERS = {"Authorization": "Bearer t"}

    def _stub_client(self, *chunks):
        async def generate_stream(body, headers=None):
            for chunk in chunks:
                yield chunk

        return patch.object(self.proxy.opencode_client, "generate_stream", generate_stream)

    @unittest_run_loop
    async def test_leading_error_event_becomes_a_real_http_status(self):
        """A blanket 200 would tell the client a dead account produced a healthy stream."""
        err = ('event: error\ndata: '
               + json.dumps({"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}})
               + "\n\n")
        with self._stub_client(err):
            resp = await self.client.post(
                "/v1/messages", json={"model": "opencode/big-pickle", "stream": True, "messages": [{"role": "user", "content": "hi"}]},
                headers=self.HEADERS)
        self.assertEqual(resp.status, 429)
        self.assertEqual((await resp.json())["error"]["type"], "rate_limit_error")

    @unittest_run_loop
    async def test_non_streaming_fold_survives_a_truncated_tool_call(self):
        chunks = [
            'event: message_start\ndata: ' + json.dumps({"type": "message_start", "message": {
                "id": "m", "type": "message", "role": "assistant", "model": "m", "content": [],
                "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 0}}}) + "\n\n",
            'event: content_block_start\ndata: ' + json.dumps({
                "type": "content_block_start", "index": 0,
                "content_block": {"type": "tool_use", "id": "t", "name": "Bash", "input": {}}}) + "\n\n",
            'event: content_block_delta\ndata: ' + json.dumps({
                "type": "content_block_delta", "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": '{"command": "ls -'}}) + "\n\n",
            'event: message_stop\ndata: ' + json.dumps({"type": "message_stop"}) + "\n\n",
        ]
        with self._stub_client(*chunks):
            resp = await self.client.post(
                "/v1/messages", json={"model": "opencode/big-pickle", "stream": False, "messages": [{"role": "user", "content": "hi"}]},
                headers=self.HEADERS)
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertEqual(body["content"][0]["type"], "tool_use")
        self.assertEqual(body["content"][0]["input"], {})


if __name__ == "__main__":
    unittest.main()