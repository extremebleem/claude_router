# Claude Multi-Router (Architecture & Design Specification)

**Date:** 2026-10-13  
**Status:** Approved  
**Topic:** Evolution from claude-gemini-proxy to claude-multi-router with multi-provider routing (Google Antigravity & OpenCode Zen/Space Bunny), account pools, and seamless in-session switching via Claude Code `/model`.

---

## 1. Context & Motivation
Previously, the proxy (`claude-gemini-proxy`) was tailored specifically to Google CloudCode/Antigravity and Gemini API. With the emergence of other high-context and free models like **Space Bunny Free** on OpenCode Zen, the architecture is being generalized into **Claude Multi-Router**.

### Key Requirements
1. **Dynamic Model Routing (`/model` in Claude Code):**
   - The user switches models directly in Claude Code (`/model space-bunny-free` vs `/model gemini-3.8-flash-tiered`).
   - The launcher asks no interactive questions on startup.
   - The proxy transparently routes requests based on the selected model prefix/name.
2. **Provider Separation:**
   - Rename `CloudCodeClient` -> `AntigravityClient` (`backend/antigravity.py`).
   - Introduce `OpenCodeClient` (`backend/opencode.py`) supporting OpenCode Zen API (`https://opencode.ai/zen/v1`).
   - Unify dispatching in `backend/router.py`.
3. **Multi-Account & Multi-Provider Auth (`auth/`):**
   - Account store supports multiple providers (`google` and `opencode`).
   - Multi-account rotation with independent 429 rate limit cooldowns per provider.
   - OpenCode device OAuth flow (`python main.py login --provider opencode` / `login-opencode`).

---

## 2. Architecture & Components

```
                          ┌─────────────────────────┐
                          │   Claude Code CLI       │
                          │   (Anthropic API)       │
                          └───────────┬─────────────┘
                                      │  HTTP /v1/messages
                                      ▼
                          ┌─────────────────────────┐
                          │     MultiRouterApp      │
                          │      (server.py)        │
                          └───────────┬─────────────┘
                                      │
               ┌──────────────────────┴──────────────────────┐
               ▼                                             ▼
     [ Model = gemini-* ]                         [ Model = space-bunny* / opencode/* ]
               │                                             │
               ▼                                             ▼
   ┌───────────────────────┐                     ┌───────────────────────┐
   │   AntigravityClient   │                     │    OpenCodeClient     │
   │ (Google CloudCode API)│                     │  (OpenCode Zen API)   │
   └───────────┬───────────┘                     └───────────┬───────────┘
               │                                             │
               ▼                                             ▼
    [ Google Account Pool ]                      [ OpenCode Account Pool ]
```

---

## 3. Detailed Component Design

### 3.1. `backend/router.py`
A unified interface `BaseBackendClient` with methods:
- `generate_stream(request_data, headers)` -> AsyncGenerator of SSE chunks (Anthropic format).
- `generate_json(request_data, headers)` -> Dict (Anthropic JSON response).

Router dispatches:
- `gemini-*`, `claude-*` (defaulting to Antigravity if configured) -> `AntigravityClient` or `GeminiApiClient`.
- `space-bunny-free`, `space-bunny*`, `opencode/*` -> `OpenCodeClient`.

### 3.2. `backend/antigravity.py`
- Renamed from `backend/cloudcode.py`.
- Class renamed to `AntigravityClient`.
- Preserves all Google Cloud Code Assist RPC streaming, tool schema transformations, and identity scrubbing.

### 3.3. `backend/opencode.py` (`OpenCodeClient`)
- Base URL: `https://opencode.ai/zen/v1` (or `https://opencode.ai/zen/go/v1`).
- Protocol: OpenAI-compatible Chat Completions (`/chat/completions`).
- Headers:
  - `Authorization: Bearer <OPENCODE_ACCESS_TOKEN>`
  - `HTTP-Referer: https://opencode.ai/`
  - `X-Title: opencode`
- Payload Translation:
  - Translates Anthropic messages & tools schema into OpenAI Chat Completions payload.
  - Translates incoming OpenAI-compatible streaming chunks / SSE back to Anthropic SSE (`message_start`, `content_block_delta`, `message_delta`, `message_stop`).

### 3.4. Multi-Provider Token & Account Manager (`auth/token_manager.py`)
- Schema update in `~/.claude-gemini-proxy/accounts.json` (or `~/.claude-multi-router/accounts.json` with fallback):
  ```json
  [
    {
      "id": "user@gmail.com",
      "provider": "google",
      "access_token": "...",
      "refresh_token": "...",
      "expiry": 123456789,
      "cooldown_until": 0
    },
    {
      "id": "opencode-user@domain.com",
      "provider": "opencode",
      "access_token": "...",
      "refresh_token": "...",
      "expiry": 123456789,
      "cooldown_until": 0
    }
  ]
  ```
- Methods:
  - `get_active_account(provider: str)` -> returns available non-cooled account for given provider.
  - `mark_account_cooldown(account_id: str, provider: str, duration: float)`.
  - `refresh_token(account_id: str)`.

### 3.5. OpenCode Device OAuth Flow (`auth/opencode_oauth.py`)
- Implements device authorization flow:
  1. Calls OpenCode device endpoint to acquire `{ device_code, user_code, verification_uri, interval, expires_in }`.
  2. Prints code to console and opens browser to `verification_uri`.
  3. Polls token endpoint with `device_code` every `interval` seconds until user confirms.
  4. Stores token into `TokenManager` under `provider: "opencode"`.

---

## 4. Model Catalog & CLI Integration
- `GET /v1/models` in `server.py` returns models from both providers:
  - Google: `gemini-2.5-pro`, `gemini-3.1-pro-high`, `gemini-3.8-flash-tiered`, etc.
  - OpenCode: `space-bunny-free` (and optional OpenCode free models).
- Launcher (`launcher.py`):
  - Starts proxy server silently.
  - Launches Claude Code with no prompts.
  - In-session model switching (`/model space-bunny-free` or `/model gemini-3.8-flash-tiered`) routes immediately to the proper backend.

---

## 5. Verification & Testing Strategy
1. **Unit Tests:**
   - Account isolation: Google requests select only Google accounts; OpenCode requests select only OpenCode accounts.
   - Provider router: Correct routing for model names (`gemini-*` vs `space-bunny-free`).
   - Payload translator: Anthropic Messages <-> OpenAI Chat Completions for OpenCode SSE.
2. **End-to-End Test:**
   - Verification of `/v1/models` returning both model suites.
   - Mocking streaming responses from OpenCodeClient and verifying Claude Code client receives valid Anthropic SSE events.
