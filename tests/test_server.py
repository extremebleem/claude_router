"""
Integration test for server.py HTTP endpoints using aiohttp test client.
"""

import sys
import os
import unittest
from unittest.mock import AsyncMock, patch, MagicMock
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop
from aiohttp import web

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import ProxyApp
from auth.token_manager import TokenManager
from auth.opencode_catalog import static_snapshot

_catalog_patcher = None


def setUpModule():
    """Keeps the suite offline: /v1/models refreshes the Zen catalogue, which is network IO."""
    global _catalog_patcher
    _catalog_patcher = patch("server.get_catalog", side_effect=lambda token=None, ua="": static_snapshot())
    _catalog_patcher.start()


def tearDownModule():
    _catalog_patcher.stop()


class TestServerEndpoints(AioHTTPTestCase):

    async def get_application(self):
        token_manager = TokenManager()
        # Initialize proxy app with a test session auth token
        self.proxy = ProxyApp(token_manager, session_auth_token="secret_sandbox_token")
        return self.proxy.app

    @unittest_run_loop
    async def test_health_endpoint(self):
        resp = await self.client.get("/health")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data["status"], "healthy")
        self.assertTrue(data["sandbox_secured"])

    @unittest_run_loop
    async def test_models_endpoint_auth(self):
        # 1. Without token -> 401
        resp = await self.client.get("/v1/models")
        self.assertEqual(resp.status, 401)

        # 2. With valid Bearer token -> 200
        headers = {"Authorization": "Bearer secret_sandbox_token"}
        resp = await self.client.get("/v1/models", headers=headers)
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data["object"], "list")
        self.assertFalse(data["has_more"])
        self.assertTrue(len(data["data"]) > 0)
        first_m = data["data"][0]
        self.assertEqual(first_m["type"], "model")
        self.assertIn("display_name", first_m)
        self.assertIn("created_at", first_m)
        model_ids = [m["id"] for m in data["data"]]
        self.assertIn("gemini-3.8-flash-tiered", model_ids)

        # 3. Model detail endpoint /v1/models/{model_id}
        resp_detail = await self.client.get("/v1/models/gemini-3.8", headers=headers)
        self.assertEqual(resp_detail.status, 200)
        detail_data = await resp_detail.json()
        self.assertEqual(detail_data["id"], "gemini-3.8")
        self.assertEqual(detail_data["type"], "model")

    @unittest_run_loop
    async def test_zen_catalogue_drives_the_advertised_models(self):
        """A model the snapshot has never heard of still has to reach the client, with its limits."""
        headers = {"Authorization": "Bearer secret_sandbox_token"}
        catalog = {"base_url": "https://zen.test", "headers": {}, "source": "console", "models": {
            "brand-new-free": {"name": "Brand New Free", "free": True, "output": 16384, "context": 262144},
            "hidden-model": {"name": "Hidden", "free": False, "disabled": True},
        }}
        with patch("server.peek_catalog", return_value=catalog):
            resp = await self.client.get("/v1/models", headers=headers)
        models = {m["id"]: m for m in (await resp.json())["data"]}
        self.assertIn("opencode/brand-new-free", models)
        self.assertNotIn("opencode/hidden-model", models)
        self.assertIn("free", models["opencode/brand-new-free"]["description"])
        self.assertIn("16K max output", models["opencode/brand-new-free"]["description"])


    @unittest_run_loop
    async def test_zen_models_are_listed_when_google_discovery_fails(self):
        """A Zen-only account has no Google token; the catalogue must still offer the free models."""
        headers = {"Authorization": "Bearer secret_sandbox_token"}
        with patch("server.fetch_available_models", side_effect=RuntimeError("no cloud code access")):
            self.proxy._models_cache = None
            resp = await self.client.get("/v1/models", headers=headers)
        self.assertEqual(resp.status, 200)
        model_ids = [m["id"] for m in (await resp.json())["data"]]
        self.assertIn("space-bunny-free", model_ids)
        self.assertIn("opencode/nemotron-3-ultra-free", model_ids)

    @unittest_run_loop
    async def test_count_tokens(self):
        headers = {"Authorization": "Bearer secret_sandbox_token"}
        payload = {"messages": [{"role": "user", "content": "Hello world from test!"}]}
        resp = await self.client.post("/v1/messages/count_tokens", json=payload, headers=headers)
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertIn("input_tokens", data)

    @unittest_run_loop
    async def test_silent_event_logging(self):
        resp = await self.client.post("/api/event_logging/batch", json={"events": []})
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data["status"], "ok")

        resp = await self.client.post("/", json={})
        self.assertEqual(resp.status, 200)

    @unittest_run_loop
    async def test_clear_signature_cache(self):
        # Without auth token -> 401
        resp_unauth = await self.client.post("/test/clear-signature-cache")
        self.assertEqual(resp_unauth.status, 401)

        # With valid auth token -> 200
        headers = {"Authorization": "Bearer secret_sandbox_token"}
        resp = await self.client.post("/test/clear-signature-cache", headers=headers)
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["success"])

    @unittest_run_loop
    async def test_account_limits_json(self):
        resp = await self.client.get("/account-limits")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertIn("totalAccounts", data)
        self.assertIn("models", data)
        self.assertIn("accounts", data)

    @unittest_run_loop
    async def test_account_limits_table(self):
        resp = await self.client.get("/account-limits?format=table")
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertIn("Account Limits", text)
        self.assertIn("Account", text)
        self.assertIn("Model", text)

    @unittest_run_loop
    async def test_accounts_endpoints(self):
        # 1. Test GET /accounts (public)
        resp = await self.client.get("/accounts")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertIn("accounts", data)
        self.assertIn("total", data)

        # 2. Test POST /accounts/switch without auth token -> 401
        resp_unauth = await self.client.post("/accounts/switch", json={"email": "nonexistent@example.com"})
        self.assertEqual(resp_unauth.status, 401)

        headers = {"Authorization": "Bearer secret_sandbox_token"}

        # 3. Test POST /accounts/switch with invalid email and auth -> 404
        resp_err = await self.client.post("/accounts/switch", json={"email": "nonexistent@example.com"}, headers=headers)
        self.assertEqual(resp_err.status, 404)

        # 4. Test POST /accounts/switch without email -> 400
        resp_bad = await self.client.post("/accounts/switch", json={}, headers=headers)
        self.assertEqual(resp_bad.status, 400)

    @unittest_run_loop
    async def test_dashboard_and_stats(self):
        # 1. Test GET /dashboard
        resp = await self.client.get("/dashboard")
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertIn("Claude-Gemini Dashboard", text)

        # 2. Test GET /api/stats
        resp_stats = await self.client.get("/api/stats")
        self.assertEqual(resp_stats.status, 200)
        data_stats = await resp_stats.json()
        self.assertIn("total_requests", data_stats)
        self.assertIn("active_account", data_stats)

        # 3. Test GET /api/events
        resp_events = await self.client.get("/api/events")
        self.assertEqual(resp_events.status, 200)
        data_events = await resp_events.json()
        self.assertIn("events", data_events)

    @unittest_run_loop
    async def test_refresh_token(self):
        # Without auth -> 401
        resp_unauth = await self.client.post("/refresh-token")
        self.assertEqual(resp_unauth.status, 401)

        # With auth -> 200 or 400
        headers = {"Authorization": "Bearer secret_sandbox_token"}
        resp = await self.client.post("/refresh-token", headers=headers)
        self.assertIn(resp.status, [200, 400])
        data = await resp.json()
        self.assertIn("status", data)

    @unittest_run_loop
    async def test_csrf_protection(self):
        # Cross-site browser requests are rejected with 403
        headers = {"Sec-Fetch-Site": "cross-site"}
        resp = await self.client.get("/health", headers=headers)
        self.assertEqual(resp.status, 403)

        # External origins rejected with 403
        headers_ext = {"Origin": "https://malicious-website.com"}
        resp_ext = await self.client.get("/health", headers=headers_ext)
        self.assertEqual(resp_ext.status, 403)

    @unittest_run_loop
    async def test_classifier_dangerous_command_guardrail(self):
        headers = {"Authorization": "Bearer secret_sandbox_token"}

        # 1. Safe command in classifier request -> approved (<block>no</block>)
        safe_body = {
            "querySource": "auto_mode",
            "messages": [{"role": "user", "content": "Execute git status in working directory"}]
        }
        resp_safe = await self.client.post("/v1/messages", json=safe_body, headers=headers)
        self.assertEqual(resp_safe.status, 200)
        data_safe = await resp_safe.json()
        self.assertIn("<block>no</block>", data_safe["content"][0]["text"])

        # 2. Destructive command in classifier request -> blocked (<block>yes</block>)
        danger_body = {
            "querySource": "auto_mode",
            "messages": [{"role": "user", "content": "Execute rm -rf / to wipe files"}]
        }
        resp_danger = await self.client.post("/v1/messages", json=danger_body, headers=headers)
        self.assertEqual(resp_danger.status, 200)
        data_danger = await resp_danger.json()
        self.assertIn("<block>yes</block>", data_danger["content"][0]["text"])
        self.assertIn("Blocked dangerous system command", data_danger["content"][0]["text"])

        # 3. Realistic Claude Code payload with 'rm -rf /' in SYSTEM prompt, but SAFE command in transcript
        realistic_safe_body = {
            "stop_sequences": ["</block>"],
            "system": "You are a classifier. Block dangerous actions like `rm -rf /` or `rm -rf ~`. Err on the side of blocking.",
            "messages": [
                {
                    "role": "user",
                    "content": "<transcript>\n{\"Bash\": \"git status\"}\n{\"Bash\": \"python cli_auth.py pool\"}\n</transcript>\nErr on the side of blocking."
                }
            ]
        }
        resp_real_safe = await self.client.post("/v1/messages", json=realistic_safe_body, headers=headers)
        self.assertEqual(resp_real_safe.status, 200)
        data_real_safe = await resp_real_safe.json()
        self.assertIn("<block>no</block>", data_real_safe["content"][0]["text"])

        # 4. Realistic Claude Code payload with Fetch command
        realistic_fetch_body = {
            "stop_sequences": ["</block>"],
            "system": "Examples of bad commands: rm -rf /, format C:",
            "messages": [
                {
                    "role": "user",
                    "content": "<transcript>\n{\"Fetch\": \"http://127.0.0.1:62289/account-limits\"}\n</transcript>\nJudge action."
                }
            ]
        }
        resp_fetch = await self.client.post("/v1/messages", json=realistic_fetch_body, headers=headers)
        self.assertEqual(resp_fetch.status, 200)
        data_fetch = await resp_fetch.json()
        self.assertIn("<block>no</block>", data_fetch["content"][0]["text"])

        # 5. Realistic Claude Code payload with ACTUAL destructive command inside transcript
        realistic_danger_body = {
            "stop_sequences": ["</block>"],
            "system": "You are a safety filter.",
            "messages": [
                {
                    "role": "user",
                    "content": "<transcript>\n{\"Bash\": \"python cli_auth.py\"}\n{\"meta\": {\"gitStatus\": \"clean\"}}\n{\"Bash\": \"rm -rf /\"}\n</transcript>\n"
                }
            ]
        }
        resp_real_danger = await self.client.post("/v1/messages", json=realistic_danger_body, headers=headers)
        self.assertEqual(resp_real_danger.status, 200)
        data_real_danger = await resp_real_danger.json()
        self.assertIn("<block>yes</block>", data_real_danger["content"][0]["text"])
        self.assertIn("Blocked dangerous system command: rm -rf /", data_real_danger["content"][0]["text"])

        # 6. History isolation: dangerous command mentioned in previous assistant response
        # should NOT trigger guardrail block when current candidate action is safe
        chat_with_assistant_history_body = {
            "stop_sequences": ["</block>"],
            "messages": [
                {
                    "role": "user",
                    "content": "How do I wipe everything in Linux?"
                },
                {
                    "role": "assistant",
                    "content": "You would use rm -rf / but that is dangerous."
                },
                {
                    "role": "user",
                    "content": "<transcript>\n{\"Bash\": \"ls -la\"}\n</transcript>\nJudge action."
                }
            ]
        }
        resp_history = await self.client.post("/v1/messages", json=chat_with_assistant_history_body, headers=headers)
        self.assertEqual(resp_history.status, 200)
        data_history = await resp_history.json()
        self.assertIn("<block>no</block>", data_history["content"][0]["text"])

    @unittest_run_loop
    async def test_classifier_request_detection_isolation(self):
        # Verify that normal chat messages mentioning classifier words in history
        # are NOT falsely recognized as classifier requests unless current request parameters match
        headers = {"Authorization": "Bearer secret_sandbox_token"}

        chat_body = {
            "messages": [
                {"role": "user", "content": "Tell me about <transcript> and how classifier works with </block>"}
            ]
        }
        # In test mode without upstream Google credentials, normal message will attempt get_backend_stream
        # which returns error or 500, but NOT classifier stub 200 with <block>no</block>
        req = web.Request
        is_clf = self.proxy.is_classifier_request(req, chat_body)
        self.assertFalse(is_clf)

        # But with querySource or stop_sequences, it IS a classifier request
        clf_body1 = {"querySource": "auto_mode", "messages": [{"role": "user", "content": "hi"}]}
        self.assertTrue(self.proxy.is_classifier_request(req, clf_body1))

        clf_body2 = {"stop_sequences": ["</block>"], "messages": [{"role": "user", "content": "hi"}]}
        self.assertTrue(self.proxy.is_classifier_request(req, clf_body2))

        clf_body3 = {"max_tokens": 50, "messages": [{"role": "user", "content": "hi"}]}
        mock_req = type("MockReq", (), {"headers": {"x-claude-code-request-class": "auxiliary"}})()
        self.assertTrue(self.proxy.is_classifier_request(mock_req, clf_body3))

    @unittest_run_loop
    async def test_streaming_client_abrupt_disconnect(self):
        # Test graceful handling when client disconnects during classifier stub streaming
        headers = {"Authorization": "Bearer secret_sandbox_token"}
        body = {
            "querySource": "auto_mode",
            "stream": True,
            "messages": [{"role": "user", "content": "git status"}]
        }
        # Connect and close immediately
        resp = await self.client.post("/v1/messages", json=body, headers=headers)
        self.assertEqual(resp.status, 200)
        # In aiohttp client response, close() is synchronous
        resp.close()

    @unittest_run_loop
    async def test_upstream_streaming_disconnect_resilience(self):
        # Verify handle_messages catches ConnectionResetError during upstream streaming
        # and cleanly closes upstream_resp without raising
        mock_upstream_resp = MagicMock()
        mock_upstream_resp.close = MagicMock()

        # Generate sample SSE chunk
        async def mock_content_iter():
            yield b'data: {"candidates": [{"content": {"parts": [{"text": "Hello"}]}}]}\n\n'

        mock_upstream_resp.content = mock_content_iter()

        with patch.object(self.proxy, "get_backend_stream", new=AsyncMock(return_value=(mock_upstream_resp, "gemini-3.8-flash-tiered"))):
            # Directly test handle_messages with mock request and simulated ConnectionResetError in prepare
            mock_request = MagicMock(spec=web.Request)
            mock_request.json = AsyncMock(return_value={
                "stream": True,
                "messages": [{"role": "user", "content": "Hello world"}]
            })
            mock_request.headers = {}

            with patch("aiohttp.web.StreamResponse.prepare", side_effect=ConnectionResetError("Simulated client disconnect")):
                resp = await self.proxy.handle_messages(mock_request)
                self.assertIsInstance(resp, web.StreamResponse)
                mock_upstream_resp.close.assert_called_once()

    @unittest_run_loop
    async def test_search_only_turn_is_answered_without_calling_the_model(self):
        """Zen's free tier refuses a turn with no bash/read, so the search is run here instead.

        The point of the test is that generate_stream is never reached: forwarding it is what
        produced the 403 FreeTierError.
        """
        body = {
            "model": "space-bunny-free",
            "messages": [{"role": "user", "content": "what is the STE100 style guide"}],
            "tools": [{"name": "WebSearch", "description": "Searches the web.",
                       "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}}}],
        }
        results = [{"title": "STE100", "url": "https://example.org/ste100", "snippet": "plain language"}]
        headers = {"Authorization": "Bearer secret_sandbox_token"}
        with patch("server.perform_web_search", return_value=results) as search, \
                patch.object(self.proxy.opencode_client, "generate_stream") as upstream:
            resp = await self.client.post("/v1/messages", json=body, headers=headers)
            self.assertEqual(resp.status, 200)
            data = await resp.json()
            self.assertIn("STE100", data["content"][0]["text"])
            self.assertIn("https://example.org/ste100", data["content"][0]["text"])
            search.assert_called_once()
            self.assertEqual(search.call_args[0][0], "what is the STE100 style guide")
            upstream.assert_not_called()

    @unittest_run_loop
    async def test_a_gemini_search_turn_reaches_its_own_grounding(self):
        """The intercept is scoped to Zen: Gemini -search carries the googleSearch tool itself."""
        body = {
            "model": "gemini-3.8-search",
            "messages": [{"role": "user", "content": "what is the STE100 style guide"}],
            "tools": [{"name": "WebSearch", "description": "Searches the web.",
                       "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}}}],
        }
        headers = {"Authorization": "Bearer secret_sandbox_token"}
        with patch("server.perform_web_search") as search, \
                patch.object(self.proxy, "get_backend_stream", new=AsyncMock()) as upstream:
            upstream.return_value = (MagicMock(), "gemini-3.8-flash-tiered")
            await self.client.post("/v1/messages", json=body, headers=headers)
            search.assert_not_called()
            upstream.assert_called_once()

    @unittest_run_loop
    async def test_the_search_query_comes_from_the_tool_call(self):
        """Once the model has called the tool the query is in its tool_use input, not the prompt."""
        body = {
            "model": "space-bunny-free",
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "research STE100 for the project"}]},
                {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "WebSearch",
                                                   "input": {"query": "STE100 style guide rules"}}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "old"}]},
            ],
            "tools": [{"name": "WebSearch", "description": "Searches the web.",
                       "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}}}],
        }
        headers = {"Authorization": "Bearer secret_sandbox_token"}
        with patch("server.perform_web_search", return_value=[]) as search, \
                patch.object(self.proxy.opencode_client, "generate_stream") as upstream:
            await self.client.post("/v1/messages", json=body, headers=headers)
            search.assert_called_once()
            self.assertEqual(search.call_args[0][0], "STE100 style guide rules")
            upstream.assert_not_called()

    def test_the_search_query_handles_every_content_shape(self):
        """The list-content branch is the one that broke once; each shape has its own answer."""
        query_of = ProxyApp._search_query_from
        # plain string
        self.assertEqual(query_of({"messages": [{"role": "user", "content": "plain prompt"}]}),
                         "plain prompt")
        # list of text blocks: only text blocks count, never a tool_result's payload
        self.assertEqual(query_of({"messages": [{"role": "user", "content": [
            {"type": "tool_result", "content": "stale output"},
            {"type": "text", "text": "the real question"}]}]}), "the real question")
        # a tool_use query wins over the prompt it was made from
        self.assertEqual(query_of({"messages": [
            {"role": "user", "content": [{"type": "text", "text": "research STE100"}]},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "t1",
                                               "input": {"query": "STE100 rules"}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "old"}]}]}),
            "STE100 rules")
        # nothing to search for
        self.assertEqual(query_of({"messages": []}), "")
        # a pasted block is bounded, not sent whole as the query
        self.assertLessEqual(len(query_of({"messages": [{"role": "user", "content": "x" * 5000}]})),
                             500)

    @unittest_run_loop
    async def test_an_auxiliary_call_is_answered_by_its_own_session_model(self):
        """Two sessions in flight: each one's titles come from its own model, not the last speaker."""
        headers = {"Authorization": "Bearer secret_sandbox_token"}
        tools = [{"name": "Bash", "description": "Runs a command.",
                  "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}}}]

        async def answered(*_args, **_kwargs):
            yield ('event: message_start\ndata: {"type": "message_start", "message": {"id": "m1", '
                   '"type": "message", "role": "assistant", "model": "m", "content": [], '
                   '"stop_reason": null, "stop_sequence": null, '
                   '"usage": {"input_tokens": 1, "output_tokens": 0}}}\n\n')
            yield ('event: message_delta\ndata: {"type": "message_delta", "delta": '
                   '{"stop_reason": "end_turn", "stop_sequence": null}, "usage": {"output_tokens": 1}}\n\n')
            yield 'event: message_stop\ndata: {"type": "message_stop"}\n\n'

        seen = []

        async def record(body, *_args, **_kwargs):
            seen.append(body["model"])
            async for chunk in answered():
                yield chunk

        with patch.object(self.proxy.opencode_client, "generate_stream", side_effect=record):
            for session, model in (("sess-a", "space-bunny-free"), ("sess-b", "gemini-3.8-flash-tiered")):
                await self.client.post("/v1/messages", json={
                    "model": model, "messages": [{"role": "user", "content": "hi"}], "tools": tools,
                    "stream": False}, headers={**headers, "x-claude-code-session-id": session})
            # Session A asks for a title on the small model; it must be answered by session A's model.
            await self.client.post("/v1/messages", headers={
                **headers, "x-claude-code-session-id": "sess-a",
                "x-claude-code-request-class": "auxiliary"}, json={
                "model": "claude-sonnet-5", "messages": [{"role": "user", "content": "title please"}]})

        self.assertEqual(seen[-1], "space-bunny-free")

    @unittest_run_loop
    async def test_a_turn_with_real_tools_is_still_forwarded(self):
        """The intercept is search-only: a turn carrying Bash must reach the model as before."""
        body = {
            "model": "space-bunny-free",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"name": "WebSearch", "description": "Searches the web.",
                       "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}}},
                      {"name": "Bash", "description": "Runs a command.",
                       "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}}}],
        }

        async def answered(*_args, **_kwargs):
            yield ('event: message_start\ndata: {"type": "message_start", "message": {"id": "m1", '
                   '"type": "message", "role": "assistant", "model": "space-bunny-free", '
                   '"content": [], "stop_reason": null, "stop_sequence": null, '
                   '"usage": {"input_tokens": 1, "output_tokens": 0}}}\n\n')
            yield ('event: content_block_start\ndata: {"type": "content_block_start", "index": 0, '
                   '"content_block": {"type": "text", "text": ""}}\n\n')
            yield ('event: content_block_delta\ndata: {"type": "content_block_delta", "index": 0, '
                   '"delta": {"type": "text_delta", "text": "hi"}}\n\n')
            yield 'event: content_block_stop\ndata: {"type": "content_block_stop", "index": 0}\n\n'
            yield ('event: message_delta\ndata: {"type": "message_delta", "delta": '
                   '{"stop_reason": "end_turn", "stop_sequence": null}, '
                   '"usage": {"output_tokens": 1}}\n\n')
            yield 'event: message_stop\ndata: {"type": "message_stop"}\n\n'

        with patch("server.perform_web_search") as search, \
                patch.object(self.proxy.opencode_client, "generate_stream",
                             side_effect=answered) as upstream:
            resp = await self.client.post("/v1/messages", json=body,
                                          headers={"Authorization": "Bearer secret_sandbox_token"})
            self.assertEqual(resp.status, 200)
            search.assert_not_called()
            upstream.assert_called_once()

    @unittest_run_loop
    async def test_opencode_model_relays_anthropic_sse(self):
        import json
        events = [
            {"type": "message_start", "message": {"id": "m1", "type": "message", "role": "assistant",
                                                  "model": "space-bunny-free", "content": [],
                                                  "usage": {"input_tokens": 1, "output_tokens": 0}}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "При"}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "вет"}},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 2}},
        ]

        def fake_stream(body, headers=None, req_id=None):
            async def gen():
                for e in events:
                    yield f"event: {e['type']}\ndata: {json.dumps(e)}\n\n"
            return gen()

        headers = {"Authorization": "Bearer secret_sandbox_token"}
        body = {"model": "space-bunny-free", "messages": [{"role": "user", "content": "привет"}]}
        with patch.object(self.proxy.opencode_client, "generate_stream", side_effect=fake_stream):
            resp = await self.client.post("/v1/messages", json={**body, "stream": True}, headers=headers)
            self.assertEqual(resp.status, 200)
            self.assertIn('"text": "\\u0432\\u0435\\u0442"', await resp.text())

            resp = await self.client.post("/v1/messages", json=body, headers=headers)
            self.assertEqual(resp.status, 200)
            data = await resp.json()
            self.assertEqual(data["content"], [{"type": "text", "text": "Привет"}])
            self.assertEqual(data["stop_reason"], "end_turn")


if __name__ == "__main__":
    unittest.main()
