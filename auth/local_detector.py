"""
Detects credentials from local Antigravity desktop IDE installation.
Extracts active SQLite session tokens and local HTTP parameters.
"""

import os
import sys
import json
import sqlite3
import urllib.request
from pathlib import Path
from typing import Optional, Dict, Any


def get_antigravity_db_path() -> Path:
    """Returns the default SQLite state.vscdb path based on OS."""
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        return Path(appdata) / "Antigravity" / "User" / "globalStorage" / "state.vscdb"
    elif sys.platform == "darwin":
        home = Path.home()
        return home / "Library" / "Application Support" / "Antigravity" / "User" / "globalStorage" / "state.vscdb"
    else:
        home = Path.home()
        return home / ".config" / "Antigravity" / "User" / "globalStorage" / "state.vscdb"


def extract_from_sqlite() -> Optional[Dict[str, Any]]:
    """
    Reads antigravityAuthStatus from state.vscdb SQLite database if present.
    """
    db_path = get_antigravity_db_path()
    if not db_path.exists():
        return None

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cur = conn.cursor()
        cur.execute("SELECT value FROM ItemTable WHERE key = 'antigravityAuthStatus'")
        row = cur.fetchone()
        conn.close()

        if row and row[0]:
            data = json.loads(row[0])
            api_key = data.get("apiKey")
            if api_key:
                return {
                    "source": "local_sqlite",
                    "email": data.get("email"),
                    "name": data.get("name"),
                    "access_token": api_key,
                    "refresh_token": None,
                    "expires_at": None,
                }
    except Exception:
        pass

    return None


def extract_from_local_port(port: int = 9092) -> Optional[Dict[str, Any]]:
    """
    Fallback: extracts chatParams from Antigravity local webview port.
    """
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/")
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            html = resp.read().decode("utf-8")
            import re
            import base64
            match = re.search(r"window\.chatParams\s*=\s*'([^']+)'", html)
            if match:
                decoded = base64.b64decode(match.group(1)).decode("utf-8")
                params = json.loads(decoded)
                api_key = params.get("apiKey")
                if api_key:
                    return {
                        "source": "local_port",
                        "access_token": api_key,
                        "email": params.get("userEmail"),
                        "refresh_token": None,
                        "expires_at": None,
                    }
    except Exception:
        pass

    return None


def detect_local_credentials() -> Optional[Dict[str, Any]]:
    """Tries SQLite first, then local port."""
    creds = extract_from_sqlite()
    if creds:
        return creds
    return extract_from_local_port()
