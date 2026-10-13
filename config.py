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
# The public catalog (models.dev) lists api = https://opencode.ai/inference/v1, but a signed-in
# Console account is served its own endpoint by GET /console/api/config ("Personal / OpenCode"
# provider), and that is the one the real CLI uses. Default to the Console form; override with
# OPENCODE_ZEN_BASE_URL if the catalog endpoint is what your plan expects.
OPENCODE_ZEN_BASE_URL = os.environ.get("OPENCODE_ZEN_BASE_URL", "https://opencode.ai/inference/openai/v1")
# Sent as x-opencode-project. opencode sends the real session.projectID; there is no equivalent
# in the Anthropic request, so this is the closest stable stand-in.
OPENCODE_PROJECT_ID = os.environ.get("OPENCODE_PROJECT_ID", "global")
# Sent as x-opencode-client (App.name in packages/core/src/app.ts).
OPENCODE_CLIENT_NAME = os.environ.get("OPENCODE_CLIENT_NAME", "opencode")


DEFAULT_FALLBACK_PROJECT_ID = os.environ.get("PROXY_FALLBACK_PROJECT_ID", "rising-fact-p41fc")

# Debug and Logging
SAVE_DETAILED_REQUEST_LOGS = os.environ.get("PROXY_DEBUG_LOGS", "0").lower() in ("1", "true")

