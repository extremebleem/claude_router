"""
Unit tests for transformer modules:
- schema sanitization
- request conversion
- thought signature cache
- SSE streaming generator
"""

import sys
import os
import json
import asyncio
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from transformer.schema import sanitize_schema
from transformer.signatures import SignatureCache, SKIP_SIGNATURE_SENTINEL
from transformer.content import convert_anthropic_to_google
from transformer.streamer import parse_google_sse_stream, aggregate_sse_stream_to_json


class TestTransformer(unittest.TestCase):

    def test_sanitize_schema(self):
        input_schema = {
            "type": "object",
            "$schema": "http://json-schema.org/draft-07/schema#",
            "additionalProperties": False,
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to file"
                },
                "line_count": {
                    "type": "integer"
                }
            },
            "required": ["file_path", "unknown_field"]
        }

        sanitized = sanitize_schema(input_schema)

        # Check type is uppercase
        self.assertEqual(sanitized["type"], "OBJECT")
        self.assertEqual(sanitized["properties"]["file_path"]["type"], "STRING")
        self.assertEqual(sanitized["properties"]["line_count"]["type"], "INTEGER")

        # Check unsupported fields are removed
        self.assertNotIn("$schema", sanitized)
        self.assertNotIn("additionalProperties", sanitized)

        # Check required fields are filtered to only declared properties
        self.assertEqual(sanitized["required"], ["file_path"])

    def test_signature_cache(self):
        cache = SignatureCache()
        self.assertEqual(cache.get_or_sentinel("missing_tool"), SKIP_SIGNATURE_SENTINEL)

        cache.set("tool_123", "test_signature_abc")
        self.assertEqual(cache.get("tool_123"), "test_signature_abc")
        self.assertEqual(cache.get_or_sentinel("tool_123"), "test_signature_abc")

    def test_convert_anthropic_to_google(self):
        anthropic_req = {
            "model": "claude-opus-4-6-thinking",
            "messages": [
                {"role": "user", "content": "Hello!"},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "Let me think...", "signature": "sig123"},
                        {"type": "text", "text": "I will read the file."},
                        {"type": "tool_use", "id": "t1", "name": "read_file", "input": {"path": "main.py"}}
                    ]
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "t1", "content": "print('hello')"}
                    ]
                }
            ],
            "system": "You are a helpful assistant.",
            "tools": [
                {
                    "name": "read_file",
                    "description": "Reads a file",
                    "input_schema": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"]
                    }
                }
            ]
        }

        google_wrapped = convert_anthropic_to_google(anthropic_req, project_id="test-proj")
        self.assertEqual(google_wrapped["project"], "test-proj")
        self.assertEqual(google_wrapped["model"], "claude-opus-4-6-thinking")

        req = google_wrapped["request"]
        self.assertIn("contents", req)
        self.assertEqual(len(req["contents"]), 3)

        # User message
        self.assertEqual(req["contents"][0]["role"], "user")
        self.assertEqual(req["contents"][0]["parts"][0]["text"], "Hello!")

        # Model message with thinking, text, functionCall
        self.assertEqual(req["contents"][1]["role"], "model")
        parts = req["contents"][1]["parts"]
        self.assertTrue(parts[0].get("thought"))
        self.assertEqual(parts[0].get("thoughtSignature"), "sig123")
        self.assertEqual(parts[1].get("text"), "I will read the file.")
        self.assertEqual(parts[2]["functionCall"]["name"], "read_file")

        # Tool result mapped to functionResponse
        self.assertEqual(req["contents"][2]["role"], "user")
        self.assertEqual(req["contents"][2]["parts"][0]["functionResponse"]["name"], "read_file")

        # Tools declarations
        self.assertIn("tools", req)
        decls = req["tools"][0]["functionDeclarations"]
        self.assertEqual(len(decls), 1)
        self.assertEqual(decls[0]["name"], "read_file")
        self.assertEqual(decls[0]["parameters"]["type"], "OBJECT")

        # System prompt checks
        sys_parts = req["systemInstruction"]["parts"]
        self.assertTrue(len(sys_parts) >= 2)
        # Should safely include user directives while retaining Antigravity identity
        full_sys_text = " ".join([p["text"] for p in sys_parts])
        self.assertIn("Antigravity", full_sys_text)
        self.assertIn("You are a helpful assistant.", full_sys_text)
        self.assertIn("Antigravity", full_sys_text)

    def test_model_specific_prompts(self):
        from transformer.prompts import build_system_instruction

        # Gemini 3.8 Flash
        flash_parts = build_system_instruction("gemini-3.8-flash-tiered")
        flash_text = " ".join(p["text"] for p in flash_parts)
        self.assertIn("Gemini Flash Agentic", flash_text)

        # Gemini 3.1 Pro
        pro_parts = build_system_instruction("gemini-3.1-pro-high")
        pro_text = " ".join(p["text"] for p in pro_parts)
        self.assertIn("Gemini Pro High-Reasoning", pro_text)

        # Thinking
        think_parts = build_system_instruction("gemini-2.5-flash-thinking")
        think_text = " ".join(p["text"] for p in think_parts)
        self.assertIn("Thinking & Deliberation", think_text)

        # Claude
        claude_parts = build_system_instruction("claude-sonnet-4-6")
        claude_text = " ".join(p["text"] for p in claude_parts)
        self.assertIn("Claude Cloud Code", claude_text)

    def test_turn_merging_and_system_role(self):
        # Claude Code sends user followed by system (environment reminders)
        anthropic_req = {
            "model": "gemini-3.8-flash-tiered",
            "messages": [
                {"role": "user", "content": "What is 2+2?"},
                {"role": "system", "content": "Environment: Windows 11"},
            ]
        }
        res = convert_anthropic_to_google(anthropic_req, "proj-123")
        contents = res["request"]["contents"]
        # System turn must be merged into user, not become a model turn
        self.assertEqual(len(contents), 1)
        self.assertEqual(contents[0]["role"], "user")
        self.assertEqual(len(contents[0]["parts"]), 2)
        self.assertEqual(contents[0]["parts"][0]["text"], "What is 2+2?")
        self.assertEqual(contents[0]["parts"][1]["text"], "Environment: Windows 11")

        # Test assistant prefill at the end
        anthropic_req_prefill = {
            "model": "gemini-3.8-flash-tiered",
            "messages": [
                {"role": "user", "content": "Hi"},
                {"role": "assistant", "content": "{"}
            ]
        }
        res_prefill = convert_anthropic_to_google(anthropic_req_prefill, "proj-123")
        contents_prefill = res_prefill["request"]["contents"]
        # Must end with user turn
        self.assertEqual(contents_prefill[-1]["role"], "user")

    def test_schema_advanced_features(self):
        # 1. const -> enum
        const_schema = {"type": "string", "const": "fixed_value"}
        sanitized_const = sanitize_schema(const_schema)
        self.assertEqual(sanitized_const.get("enum"), ["fixed_value"])
        self.assertNotIn("const", sanitized_const)

        # 2. Multi-type array flattening
        multi_type_schema = {"type": ["string", "null"]}
        sanitized_multi = sanitize_schema(multi_type_schema)
        self.assertEqual(sanitized_multi["type"], "STRING")
        self.assertTrue(sanitized_multi.get("nullable"))

        # 3. Constraints preserved in description
        constraint_schema = {
            "type": "string",
            "description": "User username",
            "minLength": 3,
            "maxLength": 30,
            "pattern": "^[a-z0-9_-]+$"
        }
        sanitized_c = sanitize_schema(constraint_schema)
        self.assertIn("minLength: 3", sanitized_c.get("description", ""))
        self.assertIn("maxLength: 30", sanitized_c["description"])
        self.assertIn("pattern: ^[a-z0-9_-]+$", sanitized_c["description"])

        # 4. Phase 6: Array without items gets STRING fallback
        array_no_items = {"type": "array"}
        sanitized_arr = sanitize_schema(array_no_items)
        self.assertEqual(sanitized_arr["type"], "ARRAY")
        self.assertIn("items", sanitized_arr)
        self.assertEqual(sanitized_arr["items"]["type"], "STRING")

        # 5. Phase 6: Array with prefixItems gets first item as items
        array_prefix = {"type": "array", "prefixItems": [{"type": "integer"}]}
        sanitized_pref = sanitize_schema(array_prefix)
        self.assertEqual(sanitized_pref["items"]["type"], "INTEGER")

        # 6. Phase 7: Non-object stripping properties and required
        non_obj = {
            "type": "string",
            "properties": {"bad": {"type": "string"}},
            "required": ["bad"]
        }
        sanitized_non_obj = sanitize_schema(non_obj)
        self.assertEqual(sanitized_non_obj["type"], "STRING")
        self.assertNotIn("properties", sanitized_non_obj)
        self.assertNotIn("required", sanitized_non_obj)

    def test_multimodal_support(self):
        anthropic_req = {
            "model": "gemini-3.8-flash-tiered",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Analyze this image and PDF:"},
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": "iVBORw0KGgoAAAANSUhEUg=="
                            }
                        },
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": "JVBERi0xLjQK..."
                            }
                        }
                    ]
                }
            ]
        }
        res = convert_anthropic_to_google(anthropic_req, "proj-123")
        parts = res["request"]["contents"][0]["parts"]
        self.assertEqual(len(parts), 3)
        self.assertEqual(parts[0]["text"], "Analyze this image and PDF:")
        self.assertEqual(parts[1]["inlineData"]["mimeType"], "image/png")
        self.assertEqual(parts[1]["inlineData"]["data"], "iVBORw0KGgoAAAANSUhEUg==")
        self.assertEqual(parts[2]["inlineData"]["mimeType"], "application/pdf")
        self.assertEqual(parts[2]["inlineData"]["data"], "JVBERi0xLjQK...")

    def test_thinking_and_function_calling_configs(self):
        # 1. Claude model with thinking
        claude_req = {
            "model": "claude-opus-4-6-thinking",
            "thinking": {"type": "enabled", "budget_tokens": 4096},
            "messages": [{"role": "user", "content": "Solve math"}],
            "tools": [{"name": "calc", "input_schema": {"type": "object"}}]
        }
        res_claude = convert_anthropic_to_google(claude_req, "proj-123")
        gen_config = res_claude["request"]["generationConfig"]
        tool_config = res_claude["request"]["toolConfig"]

        self.assertEqual(gen_config["thinkingConfig"]["thinking_budget"], 4096)
        self.assertGreaterEqual(gen_config["maxOutputTokens"], 4096 + 8192)
        self.assertEqual(tool_config["functionCallingConfig"]["mode"], "VALIDATED")

        # 2. Gemini model with thinking - should NOT use VALIDATED tool mode
        gemini_req = {
            "model": "gemini-3.8-flash-tiered",
            "thinking": {"type": "enabled", "budget_tokens": 2048},
            "messages": [{"role": "user", "content": "Hello"}],
            "tools": [{"name": "calc", "input_schema": {"type": "object"}}]
        }
        res_gemini = convert_anthropic_to_google(gemini_req, "proj-123")
        gen_gemini = res_gemini["request"]["generationConfig"]
        self.assertTrue(gen_gemini["thinkingConfig"]["includeThoughts"])
        self.assertEqual(gen_gemini["thinkingConfig"]["thinkingBudget"], 2048)
        self.assertNotIn("toolConfig", res_gemini["request"])

    def test_core_coding_guidelines_contain_error_safeguards(self):
        from transformer.prompts import CORE_CODING_GUIDELINES
        # Safeguard 1: Edit precision and indentation matching
        self.assertIn("For Edit tool calls", CORE_CODING_GUIDELINES)
        self.assertIn("match indentation", CORE_CODING_GUIDELINES)

        # Safeguard 2: Unchanged file re-read avoidance
        self.assertIn("Do not re-read unchanged files", CORE_CODING_GUIDELINES)

        # Safeguard 3: Windows command line safety against ENAMETOOLONG
        self.assertIn("ENAMETOOLONG", CORE_CODING_GUIDELINES)


class TestStreamer(unittest.IsolatedAsyncioTestCase):

    async def test_parse_google_sse_stream(self):
        # Fake Google SSE chunks
        mock_chunks = [
            b'data: {"response": {"usageMetadata": {"promptTokenCount": 20}, "candidates": [{"content": {"parts": [{"thought": true, "text": "Thinking deeply..."}]}}]}}\n\n',
            b'data: {"response": {"candidates": [{"content": {"parts": [{"text": "Hello, "}]}}]}}\n\n',
            b'data: {"response": {"candidates": [{"content": {"parts": [{"text": "world!"}]}}], "usageMetadata": {"candidatesTokenCount": 15}}}\n\n',
        ]

        async def _mock_stream():
            for c in mock_chunks:
                yield c

        events = []
        async for ev in parse_google_sse_stream(_mock_stream(), "claude-opus-4-6-thinking"):
            events.append(ev)

        event_types = [e["type"] for e in events]
        self.assertIn("message_start", event_types)
        self.assertIn("content_block_start", event_types)
        self.assertIn("content_block_delta", event_types)
        self.assertIn("content_block_stop", event_types)
        self.assertIn("message_delta", event_types)
        self.assertIn("message_stop", event_types)

        # Check aggregated non-streaming output
        async def _mock_stream_agg():
            for c in mock_chunks:
                yield c

        agg = await aggregate_sse_stream_to_json(_mock_stream_agg(), "claude-opus-4-6-thinking")
        self.assertEqual(agg["type"], "message")
        self.assertEqual(agg["role"], "assistant")
        texts = [b["text"] for b in agg["content"] if b.get("type") == "text"]
        self.assertEqual("".join(texts), "Hello, world!")

    async def test_grounding_metadata_sources(self):
        # Fake Google SSE chunks with grounding metadata
        mock_chunks = [
            b'data: {"response": {"candidates": [{"content": {"parts": [{"text": "Found recent news."}]}, "groundingMetadata": {"groundingChunks": [{"web": {"uri": "https://example.com/news", "title": "Example News"}}]}}]}}\n\n',
        ]

        async def _mock_stream():
            for c in mock_chunks:
                yield c

        agg = await aggregate_sse_stream_to_json(_mock_stream(), "gemini-3.8-search")
        texts = [b["text"] for b in agg["content"] if b.get("type") == "text"]
        full_text = "".join(texts)
        self.assertIn("Found recent news.", full_text)
        self.assertIn("https://example.com/news", full_text)
        self.assertIn("Example News", full_text)


class TestClassifierStub(unittest.TestCase):
    def test_classifier_detection(self):
        from server import ProxyApp
        from auth.token_manager import TokenManager
        from aiohttp.test_utils import make_mocked_request

        app = ProxyApp(TokenManager())

        # 1. Stop sequences: </block>
        req = make_mocked_request("POST", "/v1/messages")
        body_block = {"messages": [{"role": "user", "content": "test"}], "stop_sequences": ["</block>"]}
        self.assertTrue(app.is_classifier_request(req, body_block))

        # 2. Stop sequences: </severity>
        body_sev = {"messages": [{"role": "user", "content": "test"}], "stop_sequences": ["</severity>"]}
        self.assertTrue(app.is_classifier_request(req, body_sev))

        # 3. querySource: auto_mode
        body_qm = {"messages": [{"role": "user", "content": "test"}], "querySource": "auto_mode"}
        self.assertTrue(app.is_classifier_request(req, body_qm))

        # 4. Safeguards field
        body_sg = {"messages": [{"role": "user", "content": "test"}], "safeguards": {}}
        self.assertTrue(app.is_classifier_request(req, body_sg))

        # 5. Normal user query -> False
        body_normal = {"messages": [{"role": "user", "content": "Hello world"}]}
        self.assertFalse(app.is_classifier_request(req, body_normal))

    def test_tool_name_cache_preservation(self):
        from transformer.signatures import tool_name_cache

        # 1. Assistant calls tool
        anthropic_req_1 = {
            "model": "gemini-3.8-flash-tiered",
            "messages": [
                {"role": "user", "content": "Run tests"},
                {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "call_abc999", "name": "run_test_suite", "input": {}}]
                }
            ]
        }
        convert_anthropic_to_google(anthropic_req_1, "proj-123")
        self.assertEqual(tool_name_cache.get("call_abc999"), "run_test_suite")

        # 2. In turn 2, client sends tool_result where earlier assistant turn was compressed/omitted
        anthropic_req_2 = {
            "model": "gemini-3.8-flash-tiered",
            "messages": [
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_abc999", "content": "All passed"}]}
            ]
        }
        res_2 = convert_anthropic_to_google(anthropic_req_2, "proj-123")
        fn_resp = res_2["request"]["contents"][0]["parts"][0]["functionResponse"]
        # Must resolve to 'run_test_suite' via tool_name_cache instead of 'unknown_tool'
        self.assertEqual(fn_resp["name"], "run_test_suite")

    def test_token_estimator(self):
        from transformer.tokenizer import estimate_string_tokens, estimate_request_tokens

        self.assertGreater(estimate_string_tokens("def hello_world(): return 42"), 5)
        # Multilingual / Non-Latin token estimation test
        self.assertGreater(estimate_string_tokens("Hello world! Testing tokenizer functionality."), 5)

        req_body = {
            "system": "You are Antigravity.",
            "messages": [
                {"role": "user", "content": "Write quicksort in Python."}
            ],
            "tools": [
                {"name": "bash", "description": "Run shell command", "input_schema": {"type": "object"}}
            ]
        }
        count = estimate_request_tokens(req_body)
        self.assertGreater(count, 20)

    def test_stable_session_id_prompt_caching(self):
        from transformer.content import derive_session_id

        # Turn 1: user initiates task
        msgs_turn_1 = [
            {"role": "user", "content": "Start coding the proxy architecture."}
        ]
        s1 = derive_session_id(msgs_turn_1, account_id="user@example.com", system="You are an expert.")

        # Turn 2: assistant answered, user provides tool response
        msgs_turn_2 = msgs_turn_1 + [
            {"role": "assistant", "content": "I will examine the codebase."},
            {"role": "user", "content": "File contents here..."}
        ]
        s2 = derive_session_id(msgs_turn_2, account_id="user@example.com", system="You are an expert.")

        # Session ID must be completely identical across turns to guarantee Cloud Code prefix cache hits
        self.assertEqual(s1, s2)
        self.assertTrue(s1.startswith("session-"))

    def test_web_search_handling(self):
        # 1. Anthropic native web search tool (type: "web_search_20250101")
        req_anthropic_search = {
            "model": "gemini-3.8-flash-tiered",
            "messages": [{"role": "user", "content": "Search for news"}],
            "tools": [
                {"type": "web_search_20250101", "name": "web_search"}
            ]
        }
        res = convert_anthropic_to_google(req_anthropic_search, "proj-123")
        tools = res["request"].get("tools", [])
        self.assertEqual(len(tools), 1)
        self.assertIn("functionDeclarations", tools[0])
        self.assertEqual(tools[0]["functionDeclarations"][0]["name"], "web_search")

        # 2. Standalone Google Search grounding flag
        req_google_search = {
            "model": "gemini-3.8-flash-tiered",
            "enable_google_search": True,
            "messages": [{"role": "user", "content": "Search online"}],
            "tools": [{"name": "Bash", "input_schema": {"type": "object"}}]
        }
        res_gs = convert_anthropic_to_google(req_google_search, "proj-123")
        gs_tools = res_gs["request"].get("tools", [])
        self.assertEqual(len(gs_tools), 2)
        self.assertIn("functionDeclarations", gs_tools[0])
        self.assertIn("googleSearch", gs_tools[1])

        # 3. WebSearch tool result enricher on empty search output
        from transformer.search import enrich_empty_search_result
        from unittest.mock import patch
        empty_search_out = 'Web search results for query: "python"\n\n\nREMINDER: You MUST include the sources above in your response to the user using markdown hyperlinks.'
        with patch("transformer.search.perform_web_search", return_value=[{"title": "Python Programming", "url": "https://www.python.org", "snippet": "Official site"}]):
            enriched = enrich_empty_search_result("WebSearch", empty_search_out)
            self.assertIn("python", enriched.lower())
            self.assertIn("http", enriched)

    def test_muse_spark_prompt_directives(self):
        from transformer.prompts import build_system_instruction
        parts = build_system_instruction("muse-spark-1.3", user_system="Work on code.")
        combined_text = " ".join(p["text"] for p in parts)
        self.assertIn("Muse Spark", combined_text)
        self.assertIn("Operating Guidelines", combined_text)

    def test_state_classifier_stub_detection(self):
        from server import ProxyApp
        from auth.token_manager import TokenManager
        from aiohttp.test_utils import make_mocked_request

        app = ProxyApp(TokenManager())
        req = make_mocked_request("POST", "/v1/messages")

        # 1. State classifier via querySource
        body_qs = {"messages": [{"role": "user", "content": "state check"}], "querySource": "auto_mode_state"}
        self.assertTrue(app.is_state_classifier_request(req, body_qs))

        # 2. State classifier via prompt text
        body_text = {
            "messages": [{"role": "user", "content": "test"}],
            "system": "A user kicked off a Claude Code agent to do a coding task and walked away. Read the tail..."
        }
        self.assertTrue(app.is_state_classifier_request(req, body_text))

        # 3. Normal request -> False
        body_norm = {"messages": [{"role": "user", "content": "write code"}], "system": "You are helpful."}
        self.assertFalse(app.is_state_classifier_request(req, body_norm))

    def test_system_prompt_sanitization_and_auto_mode(self):
        from transformer.prompts import extract_user_system_prompt, build_system_instruction

        raw_system = [
            {"type": "text", "text": "x-anthropic-billing-header: cc_version=2.1.296.418; cc_entrypoint=cli;"},
            {"type": "text", "text": "You are Claude Code, Anthropic's official CLI for Claude."},
            {"type": "text", "text": (
                "You are an agent working with the user.\n\n"
                "# Harness\n- Tools run behind permissions.\n\n"
                "# Environment\n- The most recent Claude models are the Claude 5 family.\n\n"
                "# Background Session\nThis session runs as a background job."
            )}
        ]

        cleaned = extract_user_system_prompt(raw_system)

        # 1. Billing headers must be completely stripped
        self.assertNotIn("x-anthropic-billing-header", cleaned.lower())
        self.assertNotIn("cc_version", cleaned.lower())

        # 2. Block 1 identity must be stripped
        self.assertNotIn("you are claude code", cleaned.lower())
        self.assertNotIn("anthropic's official cli", cleaned.lower())

        # 3. Harness preserved
        self.assertIn("# Harness", cleaned)

        # 4. Environment updated (Claude 5 hallucination removed)
        self.assertNotIn("claude 5", cleaned.lower())
        self.assertIn("Antigravity", cleaned)

        # 5. Auto mode native tools policy injected
        self.assertIn("Autonomous Execution Policy", cleaned)
        self.assertIn("Read", cleaned)
        self.assertIn("Edit", cleaned)
        self.assertIn("Write", cleaned)

        # 6. Base prompt uses Antigravity identity
        parts = build_system_instruction("gemini-3.8-flash-tiered", user_system=raw_system)
        full_sys = " ".join(p["text"] for p in parts)
        self.assertIn("Antigravity", full_sys)
        self.assertNotIn("Claude Code", full_sys)


if __name__ == "__main__":
    unittest.main()
