#!/usr/bin/env python3
"""
Isolated Sandbox Launcher for Claude Code CLI.
Automatically runs Claude Code in a fully isolated sandbox environment:
- Allocates an ephemeral local port for the proxy
- Generates an isolated session token (protects against unauthorized requests)
- Configures isolated settings in ~/.claude-sandbox with curated Gemini/Claude models
- Leaves any other active Claude sessions on the system completely untouched
"""

import sys
import os
import socket
import secrets
import subprocess
import threading
import time
import json
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

# Fix utf-8 output on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Add root folder to sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from logger import set_quiet_mode, is_quiet_mode, log


def find_free_port() -> int:
    """Finds an available local port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def detect_cli_path() -> Optional[str]:
    """Auto-detects Claude Code CLI binary."""
    user_home = Path(os.environ.get("USERPROFILE", os.path.expanduser("~")))
    candidates = [
        user_home / ".local" / "bin" / "claude.exe",
        user_home / ".local" / "bin" / "claude",
        Path(os.environ.get("APPDATA", "")) / "npm" / "claude.cmd",
        Path(os.environ.get("APPDATA", "")) / "npm" / "claude",
    ]
    for c in candidates:
        if c.exists():
            return str(c)

    # Check PATH
    import shutil
    path_cli = shutil.which("claude")
    if path_cli:
        return path_cli

    return None


def start_proxy_thread(port: int, token: str):
    """Runs the aiohttp proxy server in a background thread."""
    import asyncio
    from server import run_server

    def _target():
        asyncio.run(run_server(host="127.0.0.1", port=port, session_auth_token=token))

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    return t


def wait_for_proxy_health(port: int, timeout: float = 5.0) -> bool:
    """Polls the proxy /health endpoint until it is ready."""
    start = time.time()
    url = f"http://127.0.0.1:{port}/health"
    while time.time() - start < timeout:
        try:
            with urllib.request.urlopen(url, timeout=0.5) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.1)
    return False


def run_session(exe_path: str, model_override: Optional[str], sandbox_dir: Optional[str], extra_args: list, verbose: bool = False, quiet: bool = False, open_dashboard: bool = False):
    set_quiet_mode(not verbose)
    active_model = model_override or "gemini-3.8-flash-tiered"
    user_home = Path(os.environ.get("USERPROFILE", os.path.expanduser("~")))
    sandbox_path = Path(sandbox_dir) if sandbox_dir else user_home / ".claude-sandbox"
    sandbox_path.mkdir(parents=True, exist_ok=True)

    port = find_free_port()
    session_token = secrets.token_hex(16)
    base_url = f"http://127.0.0.1:{port}"

    is_piped = bool(extra_args and any(a in ["-p", "--print"] or a.startswith("-p") for a in extra_args))
    suppress_banner = quiet or is_piped

    if not suppress_banner:
        print("\n" + "=" * 70)
        print(" 🛡️  ИЗОЛИРОВАННАЯ ПЕСОЧНИЦА CLAUDE CODE CLI")
        print("=" * 70)
        print(f" [Бинарник CLI]     : {exe_path}")
        print(f" [Порт прокси]      : {port} (выделен динамически)")
        print(f" [Веб-дашборд]      : http://127.0.0.1:{port}/dashboard")
        print(f" [Токен сессии]    : {session_token[:8]}... (защита от посторонних подключений)")
        print(f" [Папка песочницы]  : {sandbox_path}")
        print(f" [Модель по умолч.] : {active_model}")
        print(f" [Изоляция]         : Вторая сессия в системе НЕ затрагивается")
        print("=" * 70 + "\n")


    # 1. Generate isolated settings for Claude Code with curated modelPicker
    model_picker_options = [
        {
            "model": "gemini-3.8-flash-tiered",
            "label": "Gemini 3.8 Flash (Tiered)",
            "description": "Google Gemini 3.8 Flash Tiered",
            "behavesAs": "sonnet"
        },
        {
            "model": "gemini-3.1-pro-high",
            "label": "Gemini 3.1 Pro (High Budget)",
            "description": "Google Gemini 3.1 Pro High Reasoning",
            "behavesAs": "opus"
        },
        {
            "model": "gemini-3.5-flash-low",
            "label": "Gemini 3.5 Flash",
            "description": "Google Gemini 3.5 Flash Low",
            "behavesAs": "haiku"
        },
        {
            "model": "claude-opus-4-6-thinking",
            "label": "Claude Opus 4.6 (Thinking)",
            "description": "Claude Opus 4.6 Thinking on Google Cloud Code",
            "behavesAs": "opus"
        },
        {
            "model": "claude-sonnet-4-6",
            "label": "Claude Sonnet 4.6",
            "description": "Claude Sonnet 4.6 on Google Cloud Code",
            "behavesAs": "sonnet"
        },
        {
            "model": "gemini-2.5-pro",
            "label": "Gemini 2.5 Pro",
            "description": "Google Gemini 2.5 Pro",
            "behavesAs": "opus"
        },
        {
            "model": "gemini-2.5-flash",
            "label": "Gemini 2.5 Flash",
            "description": "Google Gemini 2.5 Flash",
            "behavesAs": "haiku"
        }
    ]

    settings_file = sandbox_path / "settings.json"
    sandbox_settings = {
        "model": active_model,
        "defaultModel": active_model,
        "modelPicker": {
            "replaceBuiltInOptions": True,
            "options": model_picker_options
        },
        "env": {
            "ANTHROPIC_BASE_URL": base_url,
            "ANTHROPIC_AUTH_TOKEN": session_token,
            "ANTHROPIC_MODEL": active_model,
            "ANTHROPIC_DEFAULT_MODEL": active_model,
            "CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT": "1",
            "CLAUDE_CODE_MAX_CONTEXT_TOKENS": "1000000",
            "CLAUDE_CODE_AUTO_MODE_SERVER": "0"
        }
    }
    with open(settings_file, "w", encoding="utf-8") as f:
        json.dump(sandbox_settings, f, indent=2)

    # Pre-configure .claude.json to skip first-time onboarding and avoid API key prompts
    claude_json_file = sandbox_path / ".claude.json"
    cdata = {}
    if claude_json_file.exists():
        try:
            with open(claude_json_file, "r", encoding="utf-8") as f:
                cdata = json.load(f)
        except Exception:
            cdata = {}
    cdata["hasCompletedOnboarding"] = True
    cdata["customApiKeyResponses"] = {"approved": [], "rejected": []}
    cdata["autoModeClassifierBillingNoticeAcknowledgedAt"] = int(time.time() * 1000)
    try:
        with open(claude_json_file, "w", encoding="utf-8") as f:
            json.dump(cdata, f, indent=2)
    except Exception:
        pass

    # 2. Start local proxy in background
    if not suppress_banner:
        print("[Launcher] Запускаю локальный прокси...")
    start_proxy_thread(port, session_token)

    if not wait_for_proxy_health(port):
        print("✗ Ошибка: Не удалось запустить прокси. Завершение.")
        sys.exit(1)

    if open_dashboard:
        import webbrowser
        dash_url = f"http://127.0.0.1:{port}/dashboard"
        if not suppress_banner:
            print(f"[Launcher] Открываю веб-дашборд в браузере: {dash_url}")
        webbrowser.open(dash_url)

    if not suppress_banner:
        print("[Launcher] Прокси готов! Запускаю Claude Code CLI...\n")

    # 3. Build process-isolated environment variables
    proc_env = os.environ.copy()
    proc_env["ANTHROPIC_BASE_URL"] = base_url
    proc_env["ANTHROPIC_AUTH_TOKEN"] = session_token
    proc_env.pop("ANTHROPIC_API_KEY", None)  # Stripped so Claude uses pure Bearer gateway auth
    proc_env["ANTHROPIC_MODEL"] = active_model
    proc_env["ANTHROPIC_DEFAULT_MODEL"] = active_model
    proc_env["CLAUDE_CODE_AUTO_MODE_SERVER"] = "0"

    proc_env["CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT"] = "1"
    proc_env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = "1000000"
    proc_env["CLAUDE_CONFIG_DIR"] = str(sandbox_path)

    cmd = [exe_path]
    has_model_arg = False
    if extra_args:
        passed = extra_args[1:] if extra_args[0] == "--" else extra_args
        has_model_arg = any(arg in ["--model", "-m"] or arg.startswith("--model=") for arg in passed)
        cmd.extend(passed)

    if model_override and not has_model_arg:
        cmd.extend(["--model", model_override])

    try:
        # Inherit standard terminal IO for interactive Claude Code CLI
        exit_code = subprocess.call(cmd, env=proc_env)
    except KeyboardInterrupt:
        exit_code = 130
    except Exception as e:
        print(f"[Launcher] Ошибка запуска процесса: {e}")
        exit_code = 1

    if not suppress_banner:
        print(f"\n[Launcher] Сеанс CLI завершен (код: {exit_code}). Прокси остановлен.")
    sys.exit(exit_code)


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Изолированный лаунчер песочницы для Claude Code CLI"
    )
    parser.add_argument(
        "mode",
        nargs="?",
        choices=["cli", "desktop", "proxy"],
        default="cli",
        help="Режим запуска: 'cli' (по умолчанию, Claude Code CLI) или 'proxy' (только прокси-сервер)"
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Модель для принудительного переопределения (по умолчанию используется Default: gemini-3.8-flash-tiered)"
    )
    parser.add_argument(
        "--sandbox-dir",
        default=None,
        help="Изолированная директория конфигурации (по умолчанию ~/.claude-sandbox)"
    )
    parser.add_argument(
        "--exe",
        default=None,
        help="Пользовательский путь к исполняемому файлу claude.exe"
    )
    parser.add_argument(
        "--dashboard",
        action="store_true",
        help="Автоматически открыть веб-дашборд в браузере при запуске сессии"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Выводить подробные логи запросов прокси в консоль (по умолчанию выключено для чистоты экрана)"
    )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Подавить вывод информационных сообщений лаунчера"
    )
    parser.add_argument(
        "extra_args",
        nargs=argparse.REMAINDER,
        help="Дополнительные аргументы, передаваемые в Claude Code CLI"
    )

    args = parser.parse_args()

    # 1. Pure Proxy Mode
    if args.mode == "proxy":
        import asyncio
        from server import run_server
        set_quiet_mode(args.quiet)
        if not args.quiet:
            print("[Launcher] Запуск автономного прокси на http://127.0.0.1:8080...")
        try:
            asyncio.run(run_server(host="127.0.0.1", port=8080))
        except KeyboardInterrupt:
            if not args.quiet:
                print("\nПрокси остановлен.")
        return

    # 2. Desktop Mode Notice
    if args.mode == "desktop":
        print("✗ Режим 'desktop' отключен: официальный Claude Desktop не поддерживает кастомный Base URL (захардкожен на claude.ai).")
        print("  Для работы с прокси используйте Claude Code CLI (python launcher.py) или сторонний GUI (Chatbox, LibreChat).")
        sys.exit(1)

    # 3. CLI Mode (Default)
    cli_exe = args.exe or detect_cli_path()
    if not cli_exe:
        print("✗ Claude Code CLI не найден автоматически.")
        print("  Укажите путь вручную через флаг: python launcher.py --exe <путь>")
        sys.exit(1)

    run_session(cli_exe, args.model, args.sandbox_dir, args.extra_args, verbose=args.verbose, quiet=args.quiet, open_dashboard=args.dashboard)


if __name__ == "__main__":
    main()

