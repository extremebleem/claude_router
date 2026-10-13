#!/usr/bin/env python3
"""
CLI utility for managing authentication and multi-account pool in Claude-Gemini Proxy.
Usage:
    python cli_auth.py login           - Interactive Google OAuth login via browser
    python cli_auth.py login --provider opencode - OpenCode Console device login
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
from typing import Optional

# Configure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Add root folder to sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from auth.token_manager import TokenManager
from auth.discovery import fetch_available_models
from backend.router import opencode_model_ids


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


def opencode_account_rows(manager: TokenManager):
    """Yields (email, is_active, kind, expiry text, cooldown text) for each stored OpenCode account."""
    active = manager.get_active_account(provider="opencode")
    active_id = (active.get("email") or active.get("id")) if active else None
    exhausted = manager.get_exhausted_status()
    for acc in manager.get_accounts(provider="opencode"):
        ident = acc.get("email") or acc.get("id")
        if acc.get("refresh_token"):
            kind = "Console login"
            expires_at = acc.get("expires_at")
            if not expires_at:
                expiry = "-"
            elif expires_at < time.time():
                expiry = "access token expired (refreshes on next request)"
            else:
                expiry = "access token until " + datetime.fromtimestamp(expires_at).strftime("%Y-%m-%d %H:%M:%S")
        else:
            kind, expiry = "API key", "no expiry"
        cooldown = ""
        if ident in exhausted:
            cooldown = ", ".join(f"{m}: {int(sec)}s" for m, sec in exhausted[ident].items())
        yield ident, ident == active_id, kind, expiry, cooldown


def print_opencode_section(manager: TokenManager):
    print("\n=== OpenCode Zen ===")
    rows = list(opencode_account_rows(manager))
    if not rows:
        print("✗ No OpenCode accounts. To log in: python cli_auth.py login --provider opencode")
        return
    print(f"Models: {', '.join(opencode_model_ids())}")
    for i, (ident, is_active, kind, expiry, cooldown) in enumerate(rows, 1):
        print(f"{i}. {ident}{' [ACTIVE]' if is_active else ''}")
        print(f"   Auth: {kind} | {expiry}" + (f" | ⚠️ Cooldown ({cooldown})" if cooldown else ""))
    # Worth saying up front: Zen serves its free models to the official client, and a Console
    # session token is not that credential - the 403 otherwise arrives mid-conversation.
    if any(kind == "Console login" for _, _, kind, _, _ in rows):
        print("ℹ Free-tier models need a Console API key: "
              "python cli_auth.py login --provider opencode --api-key <KEY>")
        print("  Verify with: python cli_auth.py check --provider opencode")


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
        print_opencode_section(manager)
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

    total_accs = len(manager.get_accounts(provider="google"))
    print(f"Total Accounts in Pool: {total_accs}")

    gemini_key = manager.get_gemini_api_key()
    if gemini_key:
        print(f"Direct Gemini API Key: configured ({gemini_key[:8]}...) [Auto-fallback active]")

    print_opencode_section(manager)


def cmd_list(manager: TokenManager):
    accounts = manager.get_accounts(provider="google")
    active = manager.get_active_account()
    active_email = active.get("email") if active else None

    print(f"=== Stored Google Accounts ({len(accounts)}) ===")
    if not accounts:
        print("No stored Google accounts found. Run 'python cli_auth.py login'")
        print_opencode_section(manager)
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

    print_opencode_section(manager)


def cmd_pool(manager: TokenManager):
    """Displays comprehensive pool status including quotas and cooldowns."""
    accounts = manager.get_accounts(provider="google")
    if not accounts:
        print("No Google accounts stored in pool. Run 'python cli_auth.py login'")
        print_opencode_section(manager)
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
    print_opencode_section(manager)
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


def cmd_check_opencode(manager: TokenManager, model: Optional[str] = None):
    """Probes Zen with the stored credential and says which of the failure modes it is.

    Worth having as a command: a Console session token, an API key and an account with no funds
    all reach the gateway differently, and only a real request tells them apart.
    """
    from auth.opencode_catalog import get_catalog, output_limit
    from auth.opencode_oauth import probe_access
    from backend.opencode import upstream_error_kind

    accounts = manager.get_accounts(provider="opencode")
    if not accounts:
        print("✗ No OpenCode accounts. Run: python cli_auth.py login --provider opencode")
        return
    active = manager.get_active_account(provider="opencode") or accounts[0]
    ident = active.get("email") or active.get("id")
    token, _ = manager.get_token_for_account(active)
    catalog = get_catalog(active.get("access_token"))
    target = model or next((m for m, i in catalog["models"].items() if i.get("free")), None)
    if not target:
        print("✗ Zen returned no models to probe with.")
        return

    print(f"Account:  {ident}")
    print(f"Endpoint: {catalog['base_url']}  (catalogue: {catalog['source']})")
    print(f"Models:   {len(catalog['models'])} reachable, {sum(1 for i in catalog['models'].values() if i.get('free'))} free")
    print(f"Probing:  {target} (output cap {output_limit(catalog, target) or '?'})")
    result = probe_access(token, target, catalog["base_url"])
    if result["ok"]:
        print("✓ Works.")
        return

    status, body = result["status"], result["message"]
    kind = upstream_error_kind(body) or ""
    print(f"✗ HTTP {status} {kind}".rstrip())
    if kind == "FreeTierError":
        print("  Free-tier models are served only to the official OpenCode client.")
        print("  Create an API key at https://opencode.ai/console and run:")
        print("    python cli_auth.py login --provider opencode --api-key <KEY>")
    elif kind == "RegionError":
        print("  This model is not available in your region.")
    elif kind == "ModelDeprecated":
        print("  This model has been removed. Run 'python cli_auth.py pool' for the current list.")
    elif status == 402 or "Insufficient account funds" in body:
        print("  The account has no credits. Free models do not need any, so try one of those.")
    elif status in (401, 403):
        print("  The credential was rejected. Re-run the login for this provider.")
    else:
        print(f"  {body[:300]}")


def main():
    parser = argparse.ArgumentParser(description="Authentication & Multi-Account Pool Manager for Claude-Gemini Proxy")
    subparsers = parser.add_subparsers(dest="command")

    p_login = subparsers.add_parser("login", help="Interactive OAuth login via browser")
    p_login.add_argument("--provider", choices=["google", "opencode"], default="google", help="Provider to login with (default: google)")
    p_login.add_argument("--api-key", help="OpenCode Console API key (skips the device flow)")

    p_add = subparsers.add_parser("add", help="Add another account to pool")
    p_add.add_argument("--provider", choices=["google", "opencode"], default="google", help="Provider to add account for (default: google)")
    p_add.add_argument("--api-key", help="OpenCode Console API key (skips the device flow)")

    p_check = subparsers.add_parser("check", help="Probe a provider end-to-end and report what is reachable")
    p_check.add_argument("--provider", choices=["google", "opencode"], default="opencode")
    p_check.add_argument("--model", help="Model to probe (default: the first free one Zen advertises)")

    subparsers.add_parser("login-opencode", help="Login to OpenCode via device flow")
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
        provider = getattr(args, "provider", "google")
        if provider == "opencode":
            import asyncio
            from auth.opencode_oauth import login_opencode_flow
            asyncio.run(login_opencode_flow(manager, getattr(args, "api_key", None)))
        else:
            cmd_login(manager)
    elif args.command == "check":
        if args.provider == "opencode":
            cmd_check_opencode(manager, args.model)
        else:
            cmd_status(manager)
    elif args.command == "login-opencode":
        import asyncio
        from auth.opencode_oauth import login_opencode_flow
        asyncio.run(login_opencode_flow(manager))
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
