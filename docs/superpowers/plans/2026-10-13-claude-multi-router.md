# Claude Multi-Router Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform the single-provider `claude-gemini-proxy` into `claude-multi-router`, with support for Google Antigravity and OpenCode Zen/Space Bunny Free models, dynamic `/model` switching without launcher prompts, and multi-provider account rotation.

**Architecture:** Split and rename `CloudCodeClient` into `AntigravityClient`, introduce an OpenAI-compatible `OpenCodeClient` for OpenCode Zen, dispatch requests through `backend/router.py`, and generalize `auth/token_manager.py` to support multiple account providers with cooldown tracking.

**Tech Stack:** Python 3.10+, aiohttp, pytest, asyncio.

**Spec:** `docs/superpowers/specs/2026-10-13-claude-multi-router-design.md`

## Global Constraints
- Python 3.10+ async I/O.
- Backward compatibility: Existing `~/.claude-gemini-proxy/` accounts automatically migrate or continue to function under `provider="google"`.
- All network payload sizes supported up to 50 MiB.
- No interactive prompts during launcher execution.

## Review Focus
- Handling 429 rate limit from OpenCode Zen (`Free usage exceeded, subscribe to Go`) by setting provider-specific cooldown without touching Google accounts.
- Preserving tool calls and SSE formatting for both Anthropic <-> Google and Anthropic <-> OpenCode.
- Request routing when an unknown model is requested defaults safely to AntigravityClient.
- Correct isolation of token refreshing between Google OAuth2 and OpenCode Device Auth tokens.
- Graceful error when OpenCode is requested but no OpenCode accounts are logged in.

---

### Task 1: Rename `CloudCodeClient` to `AntigravityClient`

**Files:**
- Rename / Create: `backend/antigravity.py` (from `backend/cloudcode.py`)
- Modify: `backend/__init__.py`
- Modify: `server.py`
- Modify: `tests/test_server.py` (or existing tests)
- Test: `tests/test_antigravity.py`

**Interfaces:**
- Consumes: Google OAuth access tokens, Google Cloud Code Assist endpoints.
- Produces: `AntigravityClient(token_manager, project_id)` with `generate_stream(...)`.

- [ ] **Step 1: Write test for AntigravityClient imports and instantiation**

```python
import pytest
from backend.antigravity import AntigravityClient

def test_antigravity_client_instantiation():
    client = AntigravityClient(token_manager=None, project_id="test-proj")
    assert client.project_id == "test-proj"
    assert hasattr(client, "generate_stream")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_antigravity.py -v`
Expected: FAIL (ModuleNotFoundError or AttributeError)

- [ ] **Step 3: Rename `backend/cloudcode.py` to `backend/antigravity.py` and update class name**

Update `class CloudCodeClient` -> `class AntigravityClient` in `backend/antigravity.py`, and update exports in `backend/__init__.py`. Keep backwards alias `CloudCodeClient = AntigravityClient`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_antigravity.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/antigravity.py backend/__init__.py tests/test_antigravity.py
git commit -m "refactor(backend): rename CloudCodeClient to AntigravityClient"
```

---

### Task 2: Multi-Provider Support in `TokenManager`

**Files:**
- Modify: `auth/token_manager.py`
- Test: `tests/test_token_manager_multi_provider.py`

**Interfaces:**
- Consumes: Account dictionaries with `"provider"` (`"google"` | `"opencode"`).
- Produces: `get_active_account(provider="google")`, `mark_account_cooldown(account_id, provider, duration)`, `list_accounts(provider=None)`.

- [ ] **Step 1: Write failing test for multi-provider account retrieval and cooldown isolation**

```python
import pytest
import time
from auth.token_manager import TokenManager

def test_token_manager_provider_isolation(tmp_path):
    storage = tmp_path / "accounts.json"
    tm = TokenManager(storage_path=str(storage))
    tm.add_account({"id": "g1@gmail.com", "provider": "google", "access_token": "g_tok"})
    tm.add_account({"id": "oc1@opencode.ai", "provider": "opencode", "access_token": "oc_tok"})

    assert tm.get_active_account(provider="google")["id"] == "g1@gmail.com"
    assert tm.get_active_account(provider="opencode")["id"] == "oc1@opencode.ai"

    # Cooldown opencode account
    tm.mark_account_cooldown("oc1@opencode.ai", duration=60, provider="opencode")
    assert tm.get_active_account(provider="opencode") is None
    # Google account must still be active
    assert tm.get_active_account(provider="google")["id"] == "g1@gmail.com"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_token_manager_multi_provider.py -v`
Expected: FAIL

- [ ] **Step 3: Implement provider filtering and default migration in `TokenManager`**

Ensure all existing accounts without `provider` default to `"google"`. Add `provider` parameter to `get_active_account` and cooldown management.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_token_manager_multi_provider.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add auth/token_manager.py tests/test_token_manager_multi_provider.py
git commit -m "feat(auth): add multi-provider support to TokenManager"
```

---

### Task 3: Implement `OpenCodeClient` (OpenCode Zen / Space Bunny API)

**Files:**
- Create: `backend/opencode.py`
- Test: `tests/test_opencode_client.py`

**Interfaces:**
- Consumes: Anthropic `/v1/messages` JSON payload, OpenCode tokens from `TokenManager`.
- Produces: `OpenCodeClient.generate_stream(request_data, headers)` producing Anthropic SSE events.

- [ ] **Step 1: Write failing test for Anthropic to OpenAI payload translation and SSE chunk parsing**

```python
import pytest
from backend.opencode import OpenCodeClient, anthropic_to_opencode_payload

def test_payload_translation():
    anthropic_req = {
        "model": "space-bunny-free",
        "messages": [{"role": "user", "content": "Hello"}],
        "system": "You are an assistant.",
        "max_tokens": 1000
    }
    oc_req = anthropic_to_opencode_payload(anthropic_req)
    assert oc_req["model"] == "space-bunny-free"
    assert oc_req["messages"][0]["role"] == "system"
    assert oc_req["messages"][1]["content"] == "Hello"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_opencode_client.py -v`
Expected: FAIL

- [ ] **Step 3: Implement payload translation and `OpenCodeClient` streaming**

Implement `OpenCodeClient` with endpoint `https://opencode.ai/zen/v1/chat/completions`, headers `HTTP-Referer: https://opencode.ai/` and `X-Title: opencode`, and OpenAI -> Anthropic SSE translator.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_opencode_client.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/opencode.py tests/test_opencode_client.py
git commit -m "feat(backend): implement OpenCodeClient for Zen models"
```

---

### Task 4: Unified Multi-Router & Server Dispatch

**Files:**
- Create: `backend/router.py`
- Modify: `server.py`
- Modify: `main.py`
- Test: `tests/test_router.py`

**Interfaces:**
- Consumes: Incoming HTTP request from Claude Code (`/v1/messages`, `/v1/models`).
- Produces: Routes to `AntigravityClient` or `OpenCodeClient` based on model name.

- [ ] **Step 1: Write test for routing dispatch**

```python
import pytest
from backend.router import ProviderRouter

def test_provider_router_selection():
    router = ProviderRouter(antigravity_client="anti", opencode_client="oc")
    assert router.resolve_client("gemini-3.8-flash-tiered") == "anti"
    assert router.resolve_client("space-bunny-free") == "oc"
    assert router.resolve_client("opencode/nemotron-3-ultra-free") == "oc"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_router.py -v`
Expected: FAIL

- [ ] **Step 3: Implement `ProviderRouter` and integrate into `server.py`**

Connect router to `/v1/messages` and update `/v1/models` to return both Google models and OpenCode models (`space-bunny-free`, etc.).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_router.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/router.py server.py tests/test_router.py
git commit -m "feat(router): implement unified ProviderRouter dispatching"
```

---

### Task 5: OpenCode Device OAuth Login Flow

**Files:**
- Create: `auth/opencode_oauth.py`
- Modify: `main.py`
- Test: `tests/test_opencode_oauth.py`

**Interfaces:**
- Consumes: CLI invocation `python main.py login --provider opencode`.
- Produces: Polled OAuth access token stored in `TokenManager`.

- [ ] **Step 1: Write unit test mocking device auth poll flow**

```python
import pytest
from unittest.mock import patch, AsyncMock
from auth.opencode_oauth import request_device_code, poll_device_token

@pytest.mark.asyncio
async def test_device_auth_flow():
    with patch("aiohttp.ClientSession.post") as mock_post:
        mock_post.return_value.__aenter__.return_value.json = AsyncMock(return_value={
            "device_code": "dev123",
            "user_code": "USER-456",
            "verification_uri": "https://opencode.ai/console",
            "interval": 1,
            "expires_in": 300
        })
        info = await request_device_code()
        assert info["user_code"] == "USER-456"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_opencode_oauth.py -v`
Expected: FAIL

- [ ] **Step 3: Implement device auth request & polling in `auth/opencode_oauth.py` and CLI command in `main.py`**

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_opencode_oauth.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add auth/opencode_oauth.py main.py tests/test_opencode_oauth.py
git commit -m "feat(auth): implement OpenCode device oauth login"
```

---

### Task 6: Launcher & Seamless Switching Verification

**Files:**
- Modify: `launcher.py`
- Test: `tests/test_e2e_switching.py`

**Interfaces:**
- Consumes: Claude Code CLI invocations without prompts.
- Produces: Seamless switching between `gemini-3.8-flash-tiered` and `space-bunny-free`.

- [ ] **Step 1: Write E2E test verifying `/v1/models` and in-flight routing**
- [ ] **Step 2: Verify launcher starts cleanly with no interactive prompts**
- [ ] **Step 3: Run full test suite: `pytest`**
- [ ] **Step 4: Commit**

```bash
git add launcher.py tests/test_e2e_switching.py
git commit -m "feat(launcher): enable seamless multi-provider switching"
```
