# Claude-Gemini Proxy & Claude Code Sandbox Launcher

A high-performance asynchronous proxy server built with **Python (`aiohttp`)** that bridges requests from **Claude Code** to the **Google Antigravity Cloud Code** and **Google AI Studio (Gemini)** backends.

Enables running the **Claude Code CLI** in a fully isolated sandbox, while also providing an Anthropic-compatible API endpoint for third-party GUI clients (Chatbox, LibreChat, Cline).

---

## 🌟 Key Features

1. **Isolated Launcher for Claude Code CLI**:
   * Automatically discovers the `claude.exe` executable (in `~/.local/bin` or via `PATH`).
   * Launches the CLI in an isolated environment without interfering with other sessions.

2. **Full Sandbox Isolation**:
   * Every session gets a dynamic ephemeral port (`127.0.0.1:<PORT>`).
   * Generates a unique cryptographic `session_token` to prevent unauthorized local connections.
   * Isolated configuration directory at `~/.claude-sandbox` (`CLAUDE_CONFIG_DIR`).
   * Run a **second session** simultaneously in a regular terminal with your official Anthropic account without any conflicts or state leaks.

3. **Anthropic Messages API Compatibility**:
   * Real-time Server-Sent Events (SSE) streaming.
   * Auto Mode support with a built-in instant local classifier stub (`is_classifier_request`).
   * Function calling conversion (`tools` ➔ Protobuf Schema in UPPERCASE).
   * Extended thinking support with `thoughtSignature` caching.
   * Automatic header masking and `cache_control` cleanup.

4. **Flexible Authentication**:
   * Google OAuth 2.0 (Antigravity client) with a built-in local callback server.
   * Automatic token refreshing via `refresh_token`.
   * Support for direct Gemini API keys (`AIzaSy...`).

---

## 🚀 Quick Start

### 1. Authentication (one-time setup)

```powershell
cd C:\AI\ClaudeGemini

# Sign in via Google in your browser
python cli_auth.py login

# Check status and linked GCP project
python cli_auth.py status

# View available Cloud Code model quotas
python cli_auth.py quotas
```

*(Optional)* If you prefer using a direct Gemini API key:
```powershell
python cli_auth.py set-key AIzaSy...
```

---

### 2. Launching Claude Code CLI in Sandbox

```powershell
# Launch Claude Code CLI (default model: gemini-3.8-flash-tiered):
python launcher.py

# Launch with a specific model:
python launcher.py --model gemini-3.1-pro-high

# Launch with the web dashboard for metrics and model switching:
python launcher.py --dashboard
```

---

### 3. Using with GUI Clients (Chatbox, LibreChat, Cline)

Official Claude Desktop does not support third-party endpoints (it is hard-coded to `claude.ai`). However, you can run a standalone proxy for any compatible GUI client:

```powershell
python launcher.py proxy
```
* **Base URL**: `http://127.0.0.1:8080`
* **API Key**: any non-empty string (e.g. `sk-ant-test`)
* In your client (e.g., **Chatbox** or **Cline**), select the **Anthropic** provider and specify the Base URL above.

---

## ⚙️ Additional Options

```powershell
# Verbose logging of all proxy requests to console:
python launcher.py -v

# Custom sandbox configuration directory:
python launcher.py --sandbox-dir C:\custom-sandbox
```

---

## 🔍 Web Search & Muse Spark Model Support

1. **Client-Side Web Search (Anthropic Tools)**:
   * Full support for client search tools (`WebSearch`, native Anthropic `type: "web_search_20250101"` blocks).
   * **Automatic Fallback Enricher**: If the client's WebSearch tool returns an empty result set (due to upstream search provider downtime), the proxy automatically intercepts the empty response and enriches it with real search snippets and URLs via the built-in search engine (`transformer/search.py`).

2. **Built-in Google Search Grounding**:
   * For native Gemini Google Search, select `gemini-3.8-search` (or pass `"google_search": true` / `"web_search": true` in the request body).
   * Search sources are automatically formatted and appended to responses under `**Sources:** [Title](URL)`.

3. **Meta Muse Spark Support (`muse-spark-1.3`, `muse-spark`)**:
   * Built-in bindings and presets for the **`muse-spark-code`** extension (VS Code / ACP / Model API).
   * Models `muse-spark-1.3`, `muse-spark`, and `muse-code` are available in the model selector and mapped to a high-performance backend with coding and reasoning capabilities.

---

## 📝 Custom System Prompts (`prompts/`)

The proxy automatically sanitizes and transforms Claude Code system prompts, replacing upstream default instructions and stripping technical billing headers. Custom prompt templates are stored as Markdown files in the `prompts/` directory:

* **`prompts/antigravity_identity.md`**: Sets the Antigravity identity (Google DeepMind) in place of Claude Code mentions.
* **`prompts/auto_mode.md`**: Autonomous mode rules (enforcing native `Read`, `Edit`, `Write` tools over ad-hoc Python file manipulation scripts).
* **`prompts/environment.md`**: Actual environment description (PowerShell/bash integration, Gemini models).

These files can be freely customized to fit your workflow — changes are reloaded on the fly.

---

## 🧪 Running Tests

```powershell
python -m unittest discover -s tests
```
