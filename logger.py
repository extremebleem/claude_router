"""
Centralized logging for Claude-Gemini Proxy.
Writes detailed logs to logs/proxy.log without polluting stdout/stderr
when running in CLI mode (so the Claude terminal interface remains clean).
"""

import os
import sys
import time
from pathlib import Path

LOGS_DIR = Path(__file__).parent / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOGS_DIR / "proxy.log"

_quiet_mode = os.environ.get("PROXY_QUIET", "0") == "1"


def set_quiet_mode(quiet: bool):
    """Enables or disables quiet mode (suppressing console output)."""
    global _quiet_mode
    _quiet_mode = quiet
    if quiet:
        os.environ["PROXY_QUIET"] = "1"
    else:
        os.environ.pop("PROXY_QUIET", None)


def is_quiet_mode() -> bool:
    """Returns True if quiet mode is enabled."""
    return _quiet_mode or os.environ.get("PROXY_QUIET", "0") == "1"


def log(msg: str, level: str = "INFO"):
    """
    Logs message to logs/proxy.log and optionally to stdout if not in quiet mode.
    """
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] [{level}] {msg}\n"

    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception:
        pass

    if not is_quiet_mode():
        sys.stdout.write(f"{msg}\n")
        sys.stdout.flush()
