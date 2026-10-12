"""
Google OAuth 2.0 implementation for Antigravity.
Handles authorization flow, callback listener, token exchange, and automatic refresh.
"""

import urllib.parse
import urllib.request
import json
import socket
import webbrowser
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Dict, Any, Optional, Tuple

from config import (
    OAUTH_CLIENT_ID,
    OAUTH_CLIENT_SECRET,
    OAUTH_AUTH_URL,
    OAUTH_TOKEN_URL,
    OAUTH_USERINFO_URL,
    OAUTH_SCOPES,
    OAUTH_CALLBACK_PORTS,
)


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """HTTP request handler for receiving the OAuth redirect."""
    
    server: "OAuthServer"

    def log_message(self, format, *args):
        # Silence standard HTTP access logs
        pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/oauth-callback":
            query = urllib.parse.parse_qs(parsed.query)
            if "code" in query:
                self.server.auth_code = query["code"][0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                html = """
                <html>
                <body style="font-family: system-ui, sans-serif; text-align: center; padding: 50px; background: #0f172a; color: #f8fafc;">
                    <h2 style="color: #38bdf8;">✓ Authorization Successful!</h2>
                    <p>Token acquired. You may now close this tab and return to the terminal.</p>
                </body>
                </html>
                """
                self.wfile.write(html.encode("utf-8"))
            elif "error" in query:
                self.server.error = query.get("error_description", query["error"])[0]
                self.send_response(400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(f"<h2>Authorization Error: {self.server.error}</h2>".encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()


class OAuthServer(HTTPServer):
    def __init__(self, port: int):
        super().__init__(("127.0.0.1", port), OAuthCallbackHandler)
        self.auth_code: Optional[str] = None
        self.error: Optional[str] = None


def find_free_callback_port() -> Tuple[int, str]:
    """Finds an available callback port from the fallback list."""
    for port in OAUTH_CALLBACK_PORTS:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port, f"http://localhost:{port}/oauth-callback"
            except OSError:
                continue
    raise RuntimeError("Failed to bind local port for OAuth callback (all candidate ports busy).")


def build_authorization_url(redirect_uri: str, state: str = "antigravity_auth") -> str:
    """Builds the Google OAuth authorization URL."""
    params = {
        "client_id": OAUTH_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(OAUTH_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{OAUTH_AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_code_for_tokens(code: str, redirect_uri: str) -> Dict[str, Any]:
    """Exchanges an authorization code for access and refresh tokens."""
    data = urllib.parse.urlencode({
        "client_id": OAUTH_CLIENT_ID,
        "client_secret": OAUTH_CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }).encode("utf-8")

    req = urllib.request.Request(OAUTH_TOKEN_URL, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    with urllib.request.urlopen(req) as response:
        payload = json.loads(response.read().decode("utf-8"))
        # Add absolute expiry timestamp
        payload["expires_at"] = time.time() + payload.get("expires_in", 3600)
        return payload


def refresh_access_token(refresh_token: str) -> Dict[str, Any]:
    """Refreshes an expired access token using the refresh token."""
    data = urllib.parse.urlencode({
        "client_id": OAUTH_CLIENT_ID,
        "client_secret": OAUTH_CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode("utf-8")

    req = urllib.request.Request(OAUTH_TOKEN_URL, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    with urllib.request.urlopen(req) as response:
        payload = json.loads(response.read().decode("utf-8"))
        payload["expires_at"] = time.time() + payload.get("expires_in", 3600)
        if "refresh_token" not in payload:
            payload["refresh_token"] = refresh_token
        return payload


def fetch_user_info(access_token: str) -> Dict[str, Any]:
    """Fetches Google profile and email for the authenticated user."""
    req = urllib.request.Request(OAUTH_USERINFO_URL)
    req.add_header("Authorization", f"Bearer {access_token}")

    with urllib.request.urlopen(req) as response:
        return json.loads(response.read().decode("utf-8"))


def login_flow_browser(timeout: int = 120) -> Dict[str, Any]:
    """
    Executes the interactive browser-based OAuth flow.
    Opens the browser, waits for callback, exchanges code, and fetches userinfo.
    """
    port, redirect_uri = find_free_callback_port()
    auth_url = build_authorization_url(redirect_uri)

    server = OAuthServer(port)
    server.timeout = 2.0  # Periodic timeout to allow graceful exit

    print(f"\n[OAuth] Opening browser for Google login...")
    print(f"[OAuth] If page does not open automatically, visit this URL:\n{auth_url}\n")
    webbrowser.open(auth_url)

    start_time = time.time()
    while not server.auth_code and not server.error:
        if time.time() - start_time > timeout:
            server.server_close()
            raise TimeoutError("Timed out waiting for browser authorization.")
        server.handle_request()

    code = server.auth_code
    error = server.error
    server.server_close()

    if error:
        raise RuntimeError(f"Error receiving authorization code: {error}")
    if not code:
        raise RuntimeError("Authorization code was not received.")

    print("[OAuth] Code received! Exchanging for access token...")
    tokens = exchange_code_for_tokens(code, redirect_uri)
    user_info = fetch_user_info(tokens["access_token"])

    return {
        "email": user_info.get("email"),
        "name": user_info.get("name"),
        "picture": user_info.get("picture"),
        "access_token": tokens["access_token"],
        "refresh_token": tokens.get("refresh_token"),
        "expires_at": tokens.get("expires_at"),
        "token_type": tokens.get("token_type", "Bearer"),
    }
