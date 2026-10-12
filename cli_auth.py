#!/usr/bin/env python3
"""
CLI utility for managing authentication and multi-account pool in Claude-Gemini Proxy.
Usage:
    python cli_auth.py login           - Interactive Google OAuth login via browser
    python cli_auth.py add             - Alias for login (add another Google account)
    python cli_auth.py status          - Show active account status and token validity
    python cli_auth.py list            - List all stored accounts
    python cli_auth.py pool            - Detailed view of all accounts, quotas and cooldowns
    python cli_auth.py switch <email>  - Switch active account
    python cli_auth.py remove <email>  - Remove account from pool
    python cli_auth.py quotas          - Fetch available models and remaining quotas for active account
    python cli_auth.py set-key <key>   - Set a standalone Google AI Studio Gemini API key
"""

import sys
import os
import argparse
import time
from datetime import datetime

# Configure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Add root folder to sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from auth.token_manager import TokenManager
from auth.discovery import fetch_available_models


def cmd_login(manager: TokenManager):
    print("=== Google Account Login (Antigravity Cloud Code) ===")
    try:
        account = manager.interactive_login()
        print(f"\n✓ Successfully authorized: {account.get('email')}")
        print(f"  GCP Project: {account.get('project_id')}")
        print(f"  Subscription Tier: {account.get('tier', 'free').upper()} ({account.get('tier_id')})")
        print(f"  Account added to pool and set as active.")
    except Exception as e:
        print(f"\n✗ Authorization error: {e}")
        sys.exit(1)


def cmd_status(manager: TokenManager):
    print("=== Authorization Status ===")
    token, project_id = manager.get_valid_token()
    account = manager.get_active_account()

    gemini_key = manager.get_gemini_api_key()
    if not account and not token:
        print("✗ No active Google (Antigravity) accounts found.")
        if gemini_key:
            print(f"✓ Direct Gemini API Key configured ({gemini_key[:8]}...)")
        print("  To log in via Google account, run: python cli_auth.py login")
        return

    email = account.get("email") if account else "Local session"
    tier = account.get("tier", "unknown") if account else "unknown"
    expires_at = account.get("expires_at") if account else None

    print(f"Active Account: {email}")
    print(f"Linked GCP Project: {project_id}")
    print(f"Subscription Tier: {tier.upper()}")

    if expires_at:
        exp_dt = datetime.fromtimestamp(expires_at).strftime("%Y-%m-%d %H:%M:%S")
        print(f"Token Expiry: until {exp_dt}")
    else:
        print("Token Expiry: managed by external session")

    total_accs = len(manager.get_accounts())
    print(f"Total Accounts in Pool: {total_accs}")

    gemini_key = manager.get_gemini_api_key()
    if gemini_key:
        print(f"Direct Gemini API Key: configured ({gemini_key[:8]}...) [Auto-fallback active]")


def cmd_list(manager: TokenManager):
    accounts = manager.get_accounts()
    active = manager.get_active_account()
    active_email = active.get("email") if active else None

    print(f"=== Stored Accounts ({len(accounts)}) ===")
    if not accounts:
        print("No stored accounts found. Run 'python cli_auth.py login'")
        return

    exhausted = manager.get_exhausted_status()

    for i, acc in enumerate(accounts, 1):
        email = acc.get("email", "unknown")
        is_active = " [ACTIVE]" if email == active_email else ""
        cooldown_info = ""
        if email in exhausted:
            models_cd = ", ".join(f"{m}: {int(sec)}s" for m, sec in exhausted[email].items())
            cooldown_info = f" | ⚠️ Cooldown ({models_cd})"

        print(f"{i}. {email}{is_active}")
        print(f"   Project: {acc.get('project_id')} | Tier: {acc.get('tier', 'free')}{cooldown_info}")


def cmd_pool(manager: TokenManager):
    """Displays comprehensive pool status including quotas and cooldowns."""
    accounts = manager.get_accounts()
    if not accounts:
        print("No accounts stored in pool. Run 'python cli_auth.py login'")
        return

    active = manager.get_active_account()
    active_email = active.get("email") if active else None
    exhausted = manager.get_exhausted_status()
    gemini_key = manager.get_gemini_api_key()

    print(f"\n=== Google Accounts Pool ({len(accounts)} accounts) ===")
    print("-" * 75)
    print(f"{'Email':<30} | {'Status':<10} | {'Cooldown':<15} | {'GCP Project'}")
    print("-" * 75)

    for acc in accounts:
        email = acc.get("email", "unknown")
        is_act = "ACTIVE" if email == active_email else "Ready"
        cd_str = "-"
        if email in exhausted:
            is_act = "429 Cooldown"
            max_cd = int(max(exhausted[email].values()))
            cd_str = f"{max_cd} sec"

        short_email = email[:28]
        proj = acc.get("project_id") or "-"
        print(f"{short_email:<30} | {is_act:<10} | {cd_str:<15} | {proj}")

    print("-" * 75)
    if gemini_key:
        print(f"✓ Backup Gemini API Key: configured ({gemini_key[:8]}...) - auto-fallback active.")
    else:
        print("ℹ Backup Gemini API Key is not set. To configure: python cli_auth.py set-key <KEY>")
    print()


def cmd_switch(manager: TokenManager, email: str):
    try:
        manager.set_active_account(email)
        print(f"✓ Set active account: {email}")
    except ValueError as e:
        print(f"✗ Error: {e}")
        sys.exit(1)


def cmd_remove(manager: TokenManager, email: str):
    removed = manager.remove_account(email)
    if removed:
        print(f"✓ Account '{email}' removed from pool.")
        active = manager.get_active_account()
        if active:
            print(f"  New active account: {active.get('email')}")
        else:
            print("  No accounts remaining in pool.")
    else:
        print(f"✗ Account '{email}' not found in pool.")
        sys.exit(1)


def cmd_quotas(manager: TokenManager):
    token, project_id = manager.get_valid_token()
    if not token:
        print("✗ Failed to acquire token. Please log in first.")
        return

    account = manager.get_active_account()
    email = account.get("email") if account else "Active"
    print(f"Fetching model quotas for [{email}] from Google Cloud Code API...")
    data = fetch_available_models(token, project_id)
    models = data.get("models", {})
    if not models:
        print("Failed to retrieve model info or response was empty.")
        return

    print("\nAvailable Models and Quotas:")
    print("-" * 65)
    print(f"{'Model':<32} | {'Remaining':<10} | {'Quota Reset'}")
    print("-" * 65)
    for model_id, info in sorted(models.items()):
        q_info = info.get("quotaInfo", {})
        rem = q_info.get("remainingFraction")
        rem_str = f"{int(rem * 100)}%" if rem is not None else "N/A"
        reset = q_info.get("resetTime", "-")
        print(f"{model_id:<32} | {rem_str:<10} | {reset}")
    print("-" * 65)


def cmd_set_key(manager: TokenManager, key: str):
    manager.set_gemini_api_key(key)
    print(f"✓ Direct Gemini API key successfully saved ({key[:8]}...)")


def main():
    parser = argparse.ArgumentParser(description="Authentication & Multi-Account Pool Manager for Claude-Gemini Proxy")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("login", help="Interactive Google OAuth login via browser")
    subparsers.add_parser("add", help="Add another Google account to pool")
    subparsers.add_parser("status", help="Check active session status")
    subparsers.add_parser("list", help="List stored accounts")
    subparsers.add_parser("pool", help="Show accounts pool status, cooldowns, and quotas")

    p_switch = subparsers.add_parser("switch", help="Switch active account")
    p_switch.add_argument("email", help="Account email to activate")

    p_remove = subparsers.add_parser("remove", help="Remove account from pool")
    p_remove.add_argument("email", help="Account email to remove")

    subparsers.add_parser("quotas", help="View remaining model quotas")

    p_key = subparsers.add_parser("set-key", help="Set direct Gemini API key (fallback)")
    p_key.add_argument("key", help="API key from Google AI Studio")

    args = parser.parse_args()
    manager = TokenManager()

    if args.command in ("login", "add"):
        cmd_login(manager)
    elif args.command == "status":
        cmd_status(manager)
    elif args.command == "list":
        cmd_list(manager)
    elif args.command == "pool":
        cmd_pool(manager)
    elif args.command == "switch":
        cmd_switch(manager, args.email)
    elif args.command == "remove":
        cmd_remove(manager, args.email)
    elif args.command == "quotas":
        cmd_quotas(manager)
    elif args.command == "set-key":
        cmd_set_key(manager, args.key)
    else:
        cmd_status(manager)


if __name__ == "__main__":
    main()
