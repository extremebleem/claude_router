# Token Leakage Prevention and Accurate Cache Telemetry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent massive token consumption in long sessions by deduplicating superseded file reads, reporting accurate cache telemetry in SSE streams, and setting a proactive context window cap in launcher.py.

**Architecture:** 
- In `transformer/content.py`, track `Read` tool invocations per file path and replace earlier instances with a lightweight superseded marker.
- In `transformer/streamer.py`, include `cache_read_input_tokens` and full `usage` in the final `message_delta` event.
- In `launcher.py`, tune `CLAUDE_CODE_MAX_CONTEXT_TOKENS` to `200000` to trigger natural autocompact before quotas are exhausted.

**Tech Stack:** Python 3.10+, aiohttp, unittest.

**Spec:** `docs/superpowers/specs/2026-10-13-token-leakage-prevention-design.md`

## Global Constraints
- Pure Python standard library implementation.
- Preserve backward compatibility across all existing unit tests in `tests/`.
- Ensure tool call structure (`tool_use` and `tool_result` IDs) remains valid for Gemini function response matching.

## Review Focus
1. Deduplication must only affect earlier reads of the same file path; the latest read must never be modified.
2. File reads with line limits, offsets, or errors must not be wrongly deduplicated against full reads if the path is missing or differs.
3. Final `message_delta` in SSE streaming must correctly pass `cache_read_input_tokens` to Claude Code CLI without breaking stream termination (`message_stop`).
4. Launcher settings must allow overriding `CLAUDE_CODE_MAX_CONTEXT_TOKENS` via user environment if desired.

---

### Task 1: Accurate Cache Usage Reporting in SSE Streamer

**Files:**
- Modify: `transformer/streamer.py:265-277`
- Test: `tests/test_streamer_cache.py`

**Interfaces:**
- Produces: Updated `parse_google_sse_stream` yielding `message_delta` with complete `usage` dictionary containing `cache_read_input_tokens`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_streamer_cache.py`:
```python
import unittest
import json
from transformer.streamer import parse_google_sse_stream, aggregate_sse_stream_to_json

class TestStreamerCacheTelemetry(unittest.TestCase):
    async def test_cache_telemetry_in_message_delta(self):
        # Fake Google SSE stream where usageMetadata has cache
        chunks = [
            'data: {"response": {"candidates": [{"content": {"parts": [{"text": "Hello"}]}}], "usageMetadata": {"promptTokenCount": 1000, "candidatesTokenCount": 10, "cachedContentTokenCount": 800}}}\n\n'
        ]
        async def fake_stream():
            for c in chunks:
                yield c.encode("utf-8")

        events = []
        async for event in parse_google_sse_stream(fake_stream(), original_model="gemini-3.8-flash-tiered"):
            events.append(event)

        delta_events = [e for e in events if e.get("type") == "message_delta"]
        self.assertTrue(len(delta_events) > 0)
        usage = delta_events[0].get("usage", {})
        self.assertEqual(usage.get("cache_read_input_tokens"), 800)
        self.assertEqual(usage.get("output_tokens"), 10)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests/test_streamer_cache.py`
Expected: FAIL with `AssertionError: None != 800`

- [ ] **Step 3: Implement updated `usage` reporting in `transformer/streamer.py`**

In `parse_google_sse_stream`:
```python
    # Final message_delta and message_stop
    yield {
        "type": "message_delta",
        "delta": {
            "stop_reason": stop_reason,
            "stop_sequence": None
        },
        "usage": {
            "output_tokens": output_tokens,
            "input_tokens": max(0, input_tokens - cache_read_tokens),
            "cache_read_input_tokens": cache_read_tokens,
            "cache_creation_input_tokens": 0
        }
    }
    yield {"type": "message_stop"}
```
Also update `aggregate_sse_stream_to_json` to copy `cache_read_input_tokens`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests/test_streamer_cache.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add transformer/streamer.py tests/test_streamer_cache.py
git commit -m "fix(streamer): report accurate cache_read_input_tokens in final message_delta"
```

---

### Task 2: Superseded Read Tool Result Deduplication

**Files:**
- Modify: `transformer/content.py`
- Test: `tests/test_read_deduplication.py`

**Interfaces:**
- Produces: `deduplicate_superseded_reads(messages: List[Dict[str, Any]]) -> Set[str]` returning set of `tool_use_id`s whose content should be tombstoned.

- [ ] **Step 1: Write the failing test**

Create `tests/test_read_deduplication.py`:
```python
import unittest
from transformer.content import convert_anthropic_to_google

class TestReadDeduplication(unittest.TestCase):
    def test_superseded_reads_collapsed(self):
        # Turn 1: Read foo.cpp
        # Turn 2: Read bar.cpp
        # Turn 3: Read foo.cpp again
        msgs = [
            {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "foo.cpp"}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "1000 lines of foo v1"}]},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "t2", "name": "Read", "input": {"file_path": "bar.cpp"}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t2", "content": "bar content"}]},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "t3", "name": "Read", "input": {"file_path": "foo.cpp"}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t3", "content": "1000 lines of foo v2"}]},
        ]
        req = {"model": "gemini-3.8-flash-tiered", "messages": msgs}
        google_wrapped = convert_anthropic_to_google(req, project_id="test-proj")
        contents = google_wrapped["request"]["contents"]

        # Check turn 1 output: must be superseded marker
        fr1 = contents[2]["parts"][0]["functionResponse"]["response"]["output"]
        self.assertIn("superseded by a later Read of foo.cpp", fr1)

        # Check turn 3 output: must retain actual content
        fr3 = contents[6]["parts"][0]["functionResponse"]["response"]["output"]
        self.assertIn("1000 lines of foo v2", fr3)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests/test_read_deduplication.py`
Expected: FAIL with `AssertionError: 'superseded by a later Read of foo.cpp' not found`

- [ ] **Step 3: Implement read deduplication in `transformer/content.py`**

In `convert_anthropic_to_google`:
Scan messages to map each file path to all its `tool_use_id` instances. Identify all prior IDs that are superseded by a later read of the exact same path. Replace their `tool_result` output with:
`f"[File content omitted: superseded by a later Read of {path}]"`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests/test_read_deduplication.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add transformer/content.py tests/test_read_deduplication.py
git commit -m "feat(context): deduplicate superseded Read tool results across multi-turn history"
```

---

### Task 3: Proactive Context Threshold in Launcher

**Files:**
- Modify: `launcher.py:261-264`
- Test: `tests/test_launcher_config.py`

**Interfaces:**
- Sets `CLAUDE_CODE_MAX_CONTEXT_TOKENS` default to `200000` (or uses `os.environ.get("CLAUDE_CODE_MAX_CONTEXT_TOKENS", "200000")`).

- [ ] **Step 1: Write test to verify launcher settings**

Create `tests/test_launcher_config.py`:
```python
import unittest
import os
import json
from pathlib import Path
from launcher import run_session

class TestLauncherConfig(unittest.TestCase):
    def test_default_context_tokens_is_200k(self):
        # Verify default env var setting in launcher
        from launcher import link_sandbox_directories
        # Read launcher code or test settings generation
        with open("launcher.py", "r", encoding="utf-8") as f:
            code = f.read()
        self.assertIn('"CLAUDE_CODE_MAX_CONTEXT_TOKENS": os.environ.get("CLAUDE_CODE_MAX_CONTEXT_TOKENS", "200000")', code)
```

- [ ] **Step 2: Update `launcher.py`**

Update `CLAUDE_CODE_MAX_CONTEXT_TOKENS` to default to `200000`.

- [ ] **Step 3: Run full test suite**

Run: `python -m unittest discover -s tests`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add launcher.py tests/test_launcher_config.py
git commit -m "chore(launcher): set proactive default context token limit to 200k to prevent TPM exhaustion"
```
