"""
Configuration and constants for Claude-Gemini Proxy.
Contains OAuth client credentials, Antigravity Cloud Code endpoints,
and request simulation headers.
"""

import os
from pathlib import Path

# Paths
APP_DIR = Path(os.environ.get("USERPROFILE", os.path.expanduser("~"))) / ".claude-gemini-proxy"
APP_DIR.mkdir(parents=True, exist_ok=True)
ACCOUNTS_FILE = APP_DIR / "accounts.json"

# Antigravity Google OAuth Client Configuration
OAUTH_CLIENT_ID = "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
OAUTH_CLIENT_SECRET = "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"

OAUTH_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
OAUTH_USERINFO_URL = "https://www.googleapis.com/oauth2/v1/userinfo"

OAUTH_SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/cclog",
    "https://www.googleapis.com/auth/experimentsandconfigs"
]

OAUTH_CALLBACK_PORTS = [51121, 51122, 51123, 51124, 51125]

# Official Antigravity Cloud Code Endpoints (from language_server.exe)
CLOUDCODE_DAILY_ENDPOINT = "https://daily-cloudcode-pa.googleapis.com"
CLOUDCODE_PROD_ENDPOINT = "https://cloudcode-pa.googleapis.com"

CLOUDCODE_ENDPOINTS = [
    CLOUDCODE_DAILY_ENDPOINT,
    CLOUDCODE_PROD_ENDPOINT,
]

LOAD_CODE_ASSIST_ENDPOINTS = [
    CLOUDCODE_DAILY_ENDPOINT,
    CLOUDCODE_PROD_ENDPOINT,
]

# Client Metadata for Antigravity Identity Simulation
# Corresponds to Protobuf: IdeType.ANTIGRAVITY (9), Platform.WINDOWS_AMD64 (5), PluginType.GEMINI (2)
CLIENT_METADATA = {
    "ideType": 9,
    "platform": 5 if os.name == "nt" else (1 if os.uname().sysname == "Darwin" else 3),
    "pluginType": 2
}

ANTIGRAVITY_VERSION = "2.22.0"

ANTIGRAVITY_HEADERS = {
    "User-Agent": f"antigravity/{ANTIGRAVITY_VERSION} windows/amd64",
    "Content-Type": "application/json",
    "X-Client-Name": "antigravity",
    "X-Client-Version": ANTIGRAVITY_VERSION,
    "x-goog-api-client": "google-cloud-sdk vscode/1.96.0 gl-node/18.18.2"
}

# OpenCode Zen gateway.
# Two endpoints exist and both accept the same credentials:
#   * https://opencode.ai/inference/v1        - the documented public API (docs.opencode.ai/zen),
#                                              also serves /models; this is what an API key is issued for.
#   * https://opencode.ai/inference/openai/v1 - the endpoint the opencode app itself gets from
#                                              GET /console/api/v2/config.
# The public one is the supported programmatic surface, so it is the default. The Console config
# still overrides it per account (see auth/opencode_catalog.py), and OPENCODE_ZEN_BASE_URL wins over both.
OPENCODE_ZEN_BASE_URL = os.environ.get("OPENCODE_ZEN_BASE_URL", "https://opencode.ai/inference/v1")
OPENCODE_CONSOLE_URL = os.environ.get("OPENCODE_CONSOLE_URL", "https://opencode.ai/console")
# Sent as x-opencode-project. opencode sends the real session.projectID; there is no equivalent
# in the Anthropic request, so this is the closest stable stand-in.
OPENCODE_PROJECT_ID = os.environ.get("OPENCODE_PROJECT_ID", "global")
# Sent as x-opencode-client (App.name in packages/core/src/app.ts).
OPENCODE_CLIENT_NAME = os.environ.get("OPENCODE_CLIENT_NAME", "opencode")
# Free-tier models are the point of the Zen account, so they are what the access check probes.
OPENCODE_PROBE_MODEL = os.environ.get("OPENCODE_PROBE_MODEL", "space-bunny-free")
# Zen normally receives three system messages (opencode's own prompt, the CLI agent prompt, the
# client's instructions). Chat Completions only guarantees one leading system turn, so a gateway that
# refuses the others can be served by folding all three into a single system message instead.
OPENCODE_MERGE_SYSTEM_BLOCKS = os.environ.get("OPENCODE_MERGE_SYSTEM_BLOCKS", "0").lower() in ("1", "true")


DEFAULT_FALLBACK_PROJECT_ID = os.environ.get("PROXY_FALLBACK_PROJECT_ID", "rising-fact-p41fc")

# Debug and Logging
SAVE_DETAILED_REQUEST_LOGS = os.environ.get("PROXY_DEBUG_LOGS", "0").lower() in ("1", "true")

# Tool output sent to a model. Claude Code re-sends every result on every later turn, so an
# unbounded result is paid for the rest of the session: one 10 KB read cost 2.4 MB of upstream
# traffic across 255 turns. The head carries the answer; the tail marker says it was cut.
TOOL_RESULT_MAX_CHARS = int(os.environ.get("PROXY_TOOL_RESULT_MAX_CHARS", "8000"))
TOOL_RESULT_TAIL_CHARS = 600

# Tool results replayed from history. Claude Code re-sends every past tool message on every turn,
# and Zen does not cache, so an old 9 KB read cost 2.4 MB of upstream traffic across 255 turns.
# Only the most recent TOOL_HISTORY_KEEP tool results survive; the rest become one marker message.
TOOL_HISTORY_KEEP = int(os.environ.get("PROXY_TOOL_HISTORY_KEEP", "8"))

# Whole-conversation budget for a Zen turn. Zen does not cache, so the input grows with every
# message for the rest of the session; this is where the oldest non-system turns start going.
CONTEXT_MAX_CHARS = int(os.environ.get("PROXY_CONTEXT_MAX_CHARS", "120000"))

