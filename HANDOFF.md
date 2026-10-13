# Handoff Documentation: Claude-Gemini Proxy

## 1. Project Overview & Objectives
**Claude-Gemini Proxy** (`C:\AI\ClaudeGemini`) is an Anthropic Messages API-compatible local reverse proxy server built on Python `aiohttp`. It enables **Claude Code CLI** (and third-party GUI clients) to use Google's state-of-the-art models (including `gemini-3.8-flash-tiered`, `gemini-3.1-pro-high`, `claude-opus-4-6-thinking`, and `claude-sonnet-4-6`) through Google Cloud Code API (Antigravity backend) and Google AI Studio.

---

## 2. Project Architecture & File Structure

```
claude-gemini-proxy\
├── launcher.py               # Isolated sandbox launcher for Claude Code CLI (ephemeral port, token isolation)
├── server.py                 # Core aiohttp server implementing /v1/messages, /v1/models, /limits, /health
├── logger.py                 # Centralized quiet logger; writes to logs/proxy.log without polluting stdout
├── config.py                 # Headers, endpoints (Antigravity + OpenCode Zen), debug switches
├── cli_auth.py               # CLI tool: quotas, account pool, Google and OpenCode logins
│
├── auth/
│   ├── token_manager.py      # Multi-provider account pool, per-provider active account, refresh, cooldowns
│   ├── oauth.py              # Interactive Google OAuth login flow
│   ├── opencode_oauth.py     # OpenCode Console device-code login and token refresh
│   ├── discovery.py          # Fetches available models and quota metadata from Cloud Code
│   └── local_detector.py     # Picks up a local Antigravity session when nothing is stored
│
├── backend/
│   ├── antigravity.py        # Cloud Code client (session hashing, multi-account 429 failover)
│   ├── opencode.py           # OpenCode Zen client (Anthropic <-> OpenAI-chat translation, account failover)
│   ├── router.py             # ProviderRouter: maps a model id to antigravity or opencode
│   └── gemini_api.py         # Google AI Studio Gemini API client fallback
│
├── transformer/
│   ├── content.py            # Anthropic -> Google request conversion (messages, roles, merging)
│   ├── schema.py             # Protobuf schema sanitizer for Cloud Code's strict validation
│   ├── prompts.py            # Antigravity system-prompt construction and identity scrubbing
│   ├── opencode_prompts.py   # opencode system-prompt selection/rendering per model
│   ├── signatures.py         # Thought-signature / tool-name / tool-args caches
│   ├── streamer.py           # Google SSE <-> Anthropic SSE streaming conversion
│   ├── search.py             # Web search fallback + Google search grounding formatting
│   ├── tokenizer.py          # BPE token estimation for /v1/messages/count_tokens
│   └── embeddings.py         # Embeddings handling
│
├── prompts/                  # System prompt templates (see prompts/README.md)
│   └── opencode/             # System prompts ported from the opencode app
│
├── logs/                     # Detailed request logs (req_*.json) and proxy.log
├── dashboard.html            # Local web dashboard
└── tests/
```

---

## 3. How to Launch & Use

### Primary Command: Claude Code CLI
```bash
python launcher.py cli
```
- Automatically allocates an available ephemeral port (e.g. `49360`).
- Generates a secure random 32-character session token.
- Creates an isolated sandbox environment at `~/.claude-sandbox` (configured with `modelPicker`, custom base URL, and onboarding flags).
- Suppresses proxy log messages from the console so Claude's terminal interface (Ink/curses TUI) remains clean.
- All proxy requests and errors are written to `logs/proxy.log`.

### Override Default Model
```bash
python launcher.py cli --model gemini-3.1-pro-high
```

### Run Claude Desktop
```bash
python launcher.py desktop
```

### Verbose Mode (Show Proxy Debug Logs in Terminal)
```bash
python launcher.py cli -v
```

### Open Web Dashboard on Launch
```bash
python launcher.py cli --dashboard
```
Opens `http://127.0.0.1:<port>/dashboard` directly in your default browser.

### Non-Interactive / Piped Query
```bash
python launcher.py cli -- -p "Write quicksort function in Python"
```

### Standalone Proxy Mode (Port 8080)
```bash
python launcher.py proxy
```

### Inspect Model Quotas, Accounts & Pool
```bash
python cli_auth.py pool     # Overview of all accounts in pool, cooldowns, and fallbacks
python cli_auth.py list     # List stored accounts
python cli_auth.py add      # Add an additional Google account to the pool via OAuth
python cli_auth.py switch <email>  # Set active account
python cli_auth.py remove <email>  # Remove account from pool
python cli_auth.py set-key <key>   # Set Google AI Studio Gemini API key for zero-downtime fallback
```
Or while proxy is running:
- `GET http://127.0.0.1:<port>/account-limits?format=table`
- `GET http://127.0.0.1:<port>/accounts`
- `POST http://127.0.0.1:<port>/accounts/switch` (JSON: `{"email": "..."}`)

---

## 4. Supported Models & Behavior

| Model ID in Proxy | Cloud Code Backend Model | Role / Behaves As | Description |
|---|---|---|---|
| `gemini-3.8-flash-tiered` | `gemini-3.8-flash-tiered` | `sonnet` | **Default.** Fast, high quota, thinking budget 2048 |
| `gemini-3.1-pro-high` | `gemini-3.1-pro-high` | `opus` | High reasoning budget (8192), deep coding tasks |
| `gemini-3.5-flash-low` | `gemini-3.5-flash-low` | `haiku` | Low latency, fast triage |
| `claude-opus-4-6-thinking`| `claude-opus-4-6-thinking`| `opus` | Cloud Code Anthropic Opus with interleaved thinking |
| `claude-sonnet-4-6` | `claude-sonnet-4-6` | `sonnet` | Cloud Code Anthropic Sonnet 4.6 |
| `gemini-2.5-pro` | `gemini-2.5-pro` | `opus` | Gemini 2.5 Pro reasoning |
| `gemini-2.5-flash` | `gemini-2.5-flash` | `haiku` | Gemini 2.5 Flash |

### OpenCode Zen models (second provider)

Routed to Zen instead of Cloud Code by `backend/router.py`. Advertised ids carry an `opencode/`
prefix because that is what the client sends; Zen only ever receives the bare id.

| Model ID in Proxy | Zen model id | Description |
|---|---|---|
| `space-bunny-free` | `space-bunny-free` | Default Zen model; the only one addressable without a prefix |
| `opencode/big-pickle` | `big-pickle` | Zen |
| `opencode/ling-3.0-flash-fin-free` | `ling-3.0-flash-fin-free` | Zen |
| `opencode/mimo-v2.5-free` | `mimo-v2.5-free` | Zen |
| `opencode/nemotron-3-ultra-free` | `nemotron-3-ultra-free` | Zen |
| `opencode/nemotron-3.5-lightning-free` | `nemotron-3.5-lightning-free` | Zen |
| `opencode/union-alpha` | `union-alpha` | Zen |

Authentication is a Console device-code login (`python cli_auth.py login --provider opencode`),
stored in the same `accounts.json` pool as the Google accounts but under a separate active pointer,
so switching providers never disturbs the other.

**Translation notes** (all mirrored from `anomalyco/opencode`, `packages/ai/src/protocols/openai-chat.ts`):
- Only `messages[0]` may carry `role: "system"`. Claude Code interleaves system turns *between*
  every model turn; those become `<system-update>` user messages with XML-escaped text, merged into
  the preceding user turn when there is one. A bare mid-array `system` role is invalid for Chat
  Completions and also breaks prefix caching.
- Claude Code's own context-budget bookkeeping (`<total_tokens>…</total_tokens>`) is dropped:
  it carries no instruction and would inject hundreds of noise messages. Real system updates
  (`<system-reminder>`, `# Environment`) are kept, with the bookkeeping stripped from them.
  Toggle via `DROP_TOKEN_ACCOUNTING` in `backend/opencode.py`.
- Tools are sent with `strict: false`, `max_tokens` becomes `max_completion_tokens`,
  `stream_options.include_usage` is always on, and `output_config.effort` becomes `reasoning_effort`.
- `tool_result.is_error` has no equivalent field in Chat Completions, so it is marked in the text.

---

## 5. Critical Technical Problems Solved

1. **429 / 400 Bad Request from Cloud Code API:**
   - **Root Cause:** Cloud Code strictly validates JSON Schema against Google Protobuf definitions. Standard JSON schemas from Claude Code contain fields like `type: ["string", "null"]`, `oneOf`, `anyOf`, `title`, or empty object declarations `properties: {}`.
   - **Resolution:** Implemented recursive 7-phase schema sanitizer in `transformer/schema.py`, wired through `transformer/content.py`:
     - Normalized union types (`["string", "null"]` -> `string`).
     - Pruned non-protobuf keywords (`title`, `default`, `oneOf`, `anyOf`, `$schema`).
     - Added fallback properties for empty schemas.
     - Merged consecutive turns from the same role.

2. **SSE Streaming Freezes / Buffered Output:**
   - **Root Cause:** Lack of chunk draining and thinking signature block emissions in Google SSE chunks.
   - **Resolution:**
     - Added `await response.drain()` and `await response.write_eof()`.
     - Switched to `resp.content.iter_any()`.
     - Guaranteed exact thinking block lifecycle: `thinking_delta` -> closing `signature_delta` -> `content_block_stop`.
     - Reduced default Flash thinking budget to 2048 to avoid thinking delays.

3. **Terminal UI Clutter in Claude Code:**
   - **Root Cause:** Background server thread printed directly to `sys.stdout`, corrupting Claude Code's Ink TUI.
   - **Resolution:** Created [`logger.py`](file:///C:/AI/ClaudeGemini/logger.py). All logging routes to [`logs/proxy.log`](file:///C:/AI/ClaudeGemini/logs/proxy.log), suppressing stdout during interactive sessions.

4. **Multi-Session Isolation:**
   - Isolated in `~/.claude-sandbox` with dynamic ephemeral port and session auth token (`ANTHROPIC_AUTH_TOKEN`), preventing interference with any system-wide Claude installations.

5. **Auto Mode Classifier Billing Notice & Zero-Latency Stub:**
   - **Notice Suppression:** Set `CLAUDE_CODE_AUTO_MODE_SERVER=0` and injected `autoModeClassifierBillingNoticeAcknowledgedAt` into `.claude.json` to permanently eliminate the gateway compatibility warning banner.
   - **Local Classifier Stub:** Intercepts internal classifier review requests (`querySource: auto_mode`, `stop_sequences: ["</block>"]`, `safeguards`) directly in `server.py` and returns an immediate approving response (`<block>no</block>\n<severity>0.0</severity>`) without sending any traffic to the Google Cloud Code backend. This results in instant 0ms tool validation and saves quotas/tokens.

---

6. **Multi-Account Pool & Automatic 429 Failover:**
   - **Pool Management:** Support for multiple stored Google accounts with individual GCP projects, tiers, and token lifecycles in `auth/token_manager.py`.
   - **Smart Cooldowns & Parsing:** Automatic extraction of quota reset cooldown intervals (`parse_retry_delay_from_error`) upon receiving HTTP 429.
   - **Seamless Rotation:** `CloudCodeClient` iterates through available, non-exhausted accounts in the pool before raising an error.
   - **Zero-Downtime Gemini AI Studio Fallback:** When all accounts in the pool hit 429 quota exhaustion, `server.py` seamlessly reroutes requests to the configured standalone Google AI Studio Gemini API key (`GeminiApiClient`).

---

7. **Agent System Directives & Context Preservation:**
   - **CLAUDE.md & Guidelines Injection:** Custom project instructions and Claude Code system prompts are safely preserved and appended as environment directives in `systemInstruction.parts` alongside Google Antigravity identity.
   - **Tool Name Cache Across Turns:** Implemented `ToolNameCache` in `transformer/signatures.py`, ensuring tool results match their correct function declarations even when Claude Code compresses earlier conversation turns.
   - **Calibrated BPE Tokenizer:** Accurate `/v1/messages/count_tokens` calculation in `transformer/tokenizer.py` for precise `/context` display in Claude Code CLI.

---

8. **OpenCode Zen Emulation Fidelity:**
   - **Mid-conversation `system` turns:** Claude Code injects `role: "system"` messages between every model turn (295 of them in a 884-message session, mostly `<total_tokens>` bookkeeping). Chat Completions only allows `system` at index 0, so they are lowered to `<system-update>` user messages with XML escaping, matching opencode's own `lowerMessages`. Buffering keeps them from splitting an assistant `tool_calls` turn from its `tool` replies.
   - **Provider prefix:** the `opencode/` namespace is stripped before the request leaves the proxy; Zen only knows bare model ids.
   - **Error mapping:** upstream HTTP status is mapped to the matching Anthropic error type, so a 400/401/403 from Zen no longer reaches the client disguised as a 500. Streaming errors are detected before the response head is committed, so the HTTP status is real rather than a blanket 200.
   - **Account failover:** OpenCode accounts share the same pool/cooldown machinery as Google accounts and rotate on 429 or a rejected credential. Cooldowns are stored per model, so one throttled model does not mute the rest of the pool, and an entirely cooling pool answers with `rate_limit_error` (429) rather than a misleading auth error.
   - **Block lifecycle:** every Anthropic `content_block_start` is emitted exactly once per index, carrying the block's real type. A tool call used to be preceded by a placeholder `text` start on the same index — malformed, and invisible to the tests because folding overwrites the first start with the second.
   - **Token-bookkeeping shapes:** `<total_tokens>` is recognized with and without the `token(s)` word and `left`/`remaining`, otherwise every bookkeeping turn turns back into a `<system-update>` noise message. A tag quoted inside a real instruction is kept, because the body may not contain sentence punctuation and stripping is line-anchored.
   - **Output cap:** Claude Code always asks for `max_tokens: 32000`, while free Zen models cap far below it and answer 400. A 400 that names the cap is retried once without `max_completion_tokens`; an unrelated 400 (unknown tool, bad schema) is surfaced unchanged and never retried.
   - **Catalogue availability:** `/v1/models` appends the static Zen catalogue on every path, including when Google discovery fails — a Zen-only account has no Google token, and would otherwise be offered no models at all.

---

## 6. Verification Status & Test Suite

All unit & integration tests pass:
```bash
python -m unittest discover -s tests
```
- `tests/test_opencode_client.py`: payload translation, provider-prefix stripping, system-turn lowering and token-bookkeeping filtering, tool adjacency, `is_error`, `strict`, `reasoning_effort`, truncated tool JSON, thinking blocks, one-`content_block_start`-per-index, output-cap retry, error mapping, account failover, and session ownership.
- `tests/test_server.py` (10 tests): verifies health, models resolution, auto-mode classifier stub, accounts REST endpoints, count_tokens, and web dashboard endpoints (/dashboard, /api/stats, /api/events).
- `tests/test_multi_account.py` (6 tests): verifies multi-account pool, active switching, removal, model cooldown tracking, round-robin rotation, and automated 429 failover.

---

## 7. Development Roadmap & Implementation Guides

### Milestone 1: Multi-Account Pool & Automatic 429 Failover (Completed ✓)
- [x] Account cooldown tracking per `(account_email, model_id)` with intelligent parsing of Google reset headers.
- [x] `get_available_accounts`, `mark_account_exhausted`, `remove_account`, `switch_active_account`, `get_next_account_round_robin`.
- [x] Failover loop in `CloudCodeClient.generate_content_stream` to transparently try the next available account on 429.
- [x] CLI pool management (`cli_auth.py pool`, `add`, `switch`, `remove`, `set-key`).
- [x] REST API endpoints (`GET /accounts`, `POST /accounts/switch`).

---

### Milestone 2: Local Web Dashboard (Completed ✓)
- [x] Автономный веб-интерфейс `dashboard.html` доступен на `http://127.0.0.1:<port>/dashboard` или `/`.
- [x] Визуальные шкалы квот моделей (Gemini 3.8 Flash, 3.1 Pro, Opus, Sonnet) с динамическими цветами (зеленый/желтый/красный).
- [x] Карточки пула Google аккаунтов со статусом кулдаунов и кнопкой 1-click активации (`POST /accounts/switch`).
- [x] Статистика сервера: общее число запросов, аптайм, активный GCP проект, статус резервного Gemini Studio ключа.
- [x] Таблица недавних запросов к прокси в реальном времени с таймингами задержки (latency) и HTTP статусами.
- [x] Эндпоинты `GET /api/stats` и `GET /api/events`.

---

### Milestone 3: Automatic Gemini API Studio Fallback (Completed ✓)
- [x] When all accounts in the pool are exhausted, `server.py` automatically detects `RESOURCE_EXHAUSTED` / `429` and dispatches to `GeminiApiClient` (Google AI Studio) if `gemini_api_key` is set.
- [x] Zero downtime for Claude Code during heavy quota exhaustion.

---

### Milestone 4: Prompt Caching & Context Session Resumption (Completed ✓)
- [x] Детерминированный `derive_session_id` в `transformer/content.py` с якорными точками на системный промпт, `cache_control` и нормализованный заголовок диалога.
- [x] Гарантированное совпадение `sessionId` и заголовка `X-Machine-Session-Id` на протяжении всей многошаговой сессии диалога, что максимизирует кэш-хиты в инфраструктуре Google Cloud Code и снижает TTFT.

---

### Milestone 5: Accurate Token Counter Endpoint (Completed ✓)
- [x] Реализован калиброванный BPE-токенизатор `transformer/tokenizer.py` с поддержкой текстовых блоков, framing-оверхеда, схем инструментов и многобайтовых UTF-8 символов (кириллица, CJK).
- [x] Точный отклик `/v1/messages/count_tokens` для корректного отображения команды `/context` в Claude Code CLI.

---

### Milestone 6: OpenCode Zen Backend (Completed ✓)
- [x] Console device-code login (`auth/opencode_oauth.py`) with access-token refresh, stored in the shared pool under its own active pointer.
- [x] `OpenCodeClient` translating Anthropic Messages to Zen's OpenAI-compatible Chat Completions and back, including tool calls, thinking blocks and usage.
- [x] `ProviderRouter` dispatching by model id; Zen models listed by `/v1/models`.
- [x] Per-model account rotation on 429 and on rejected credentials; an all-cooling pool returns 429.
- [x] Upstream-faithful request shape: bare model id, `<system-update>` lowering of mid-conversation system turns, `strict: false` tools, `max_completion_tokens`, `stream_options`, `reasoning_effort`, and the `x-opencode-*` session headers.

