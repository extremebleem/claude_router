"""
Token and Account Manager.
Coordinates accounts persistence, active token resolution, automatic refresh,
and direct Gemini API keys.
"""

import sys
import json
import time
from typing import Dict, Any, List, Optional, Tuple, Union
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from config import ACCOUNTS_FILE
from auth.oauth import refresh_access_token, login_flow_browser
from auth.discovery import discover_project_and_tier
from auth.local_detector import detect_local_credentials
from logger import log


class TokenManager:
    def __init__(self, storage_path: Union[Path, str] = ACCOUNTS_FILE):
        self.storage_path = Path(storage_path) if isinstance(storage_path, str) else storage_path
        self._data = self._load()
        # Cooldown tracking: {(email, model): timestamp_until}
        self._exhausted_accounts: Dict[Tuple[str, str], float] = {}
        self._rotation_index: int = 0

    def _load(self) -> Dict[str, Any]:
        if self.storage_path.exists():
            try:
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"accounts": [], "active_email": None, "gemini_api_key": None}

    def _save(self):
        with open(self.storage_path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)
        try:
            self.storage_path.chmod(0o600)
        except Exception:
            pass

    def set_gemini_api_key(self, api_key: str):
        """Sets a standalone Gemini API key (Google AI Studio)."""
        self._data["gemini_api_key"] = api_key.strip()
        self._save()

    def get_gemini_api_key(self) -> Optional[str]:
        return self._data.get("gemini_api_key")

    def add_or_update_account(self, account: Dict[str, Any]):
        """Adds or updates an account entry in the accounts store."""
        email = account.get("email") or account.get("id")
        if not email:
            email = f"unknown_{int(time.time())}@account"
            account["email"] = email
        if "id" not in account:
            account["id"] = email

        provider = account.get("provider", "google")
        account["provider"] = provider

        # Discover project ID and subscription tier if google and not present
        if provider == "google" and ("project_id" not in account or not account["project_id"]):
            if "access_token" in account and account["access_token"]:
                meta = discover_project_and_tier(account["access_token"])
                account["project_id"] = meta.get("project_id")
                account["tier"] = meta.get("tier")
                account["tier_id"] = meta.get("tier_id")

        existing_index = None
        for i, acc in enumerate(self._data["accounts"]):
            if (acc.get("email") == email) or (acc.get("id") == email):
                existing_index = i
                break

        if existing_index is not None:
            self._data["accounts"][existing_index].update(account)
        else:
            self._data["accounts"].append(account)

        if not self._data.get("active_email") and provider == "google":
            self._data["active_email"] = email

        self._save()

    def add_account(self, account: Dict[str, Any]):
        """Alias for add_or_update_account."""
        return self.add_or_update_account(account)

    def get_accounts(self) -> List[Dict[str, Any]]:
        return self._data.get("accounts", [])

    def remove_account(self, email: str) -> bool:
        """Removes an account from stored accounts and updates active_email if necessary."""
        initial_len = len(self._data.get("accounts", []))
        self._data["accounts"] = [
            acc for acc in self._data.get("accounts", [])
            if acc.get("email") != email
        ]
        removed = len(self._data["accounts"]) < initial_len

        # Clean up any exhausted records for this account
        self._exhausted_accounts = {
            k: v for k, v in self._exhausted_accounts.items()
            if k[0] != email
        }

        if removed:
            if self._data.get("active_email") == email:
                if self._data["accounts"]:
                    self._data["active_email"] = self._data["accounts"][0].get("email")
                else:
                    self._data["active_email"] = None
            self._save()
        return removed

    def mark_account_exhausted(self, email: str, model: str, cooldown_seconds: float = 60.0):
        """Marks an account as temporarily exhausted for a given model."""
        until = time.time() + cooldown_seconds
        self._exhausted_accounts[(email, model)] = until
        log(f"[TokenManager] Аккаунт {email} помечен как exhausted для {model} на {int(cooldown_seconds)}с", level="WARN")

    def is_account_exhausted(self, email: str, model: Optional[str] = None) -> bool:
        """Checks if an account is in cooldown for a model or globally."""
        now = time.time()
        # Check global cooldown
        if self._exhausted_accounts.get((email, "*"), 0) > now:
            return True
        # Check model specific cooldown
        if model and self._exhausted_accounts.get((email, model), 0) > now:
            return True
        return False

    def clear_cooldowns(self):
        """Clears all cooldowns for all accounts."""
        self._exhausted_accounts.clear()

    def get_exhausted_status(self) -> Dict[str, Dict[str, float]]:
        """Returns active cooldowns per account."""
        now = time.time()
        status: Dict[str, Dict[str, float]] = {}
        for (email, model), until in list(self._exhausted_accounts.items()):
            remaining = until - now
            if remaining > 0:
                if email not in status:
                    status[email] = {}
                status[email][model] = remaining
            else:
                self._exhausted_accounts.pop((email, model), None)
        return status

    def get_available_accounts(self, model: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Returns list of stored accounts that are not in cooldown for the requested model.
        The currently active account (if available) is prioritized first.
        """
        all_accounts = self.get_accounts()
        if not all_accounts:
            # Fallback to local Antigravity if nothing is stored
            local_creds = detect_local_credentials()
            if local_creds:
                self.add_or_update_account(local_creds)
                all_accounts = self.get_accounts()

        now = time.time()
        available = []
        active_email = self._data.get("active_email")
        active_acc = None

        for acc in all_accounts:
            email = acc.get("email")
            if not email:
                continue
            # Check cooldown
            if self.is_account_exhausted(email, model):
                continue
            if email == active_email:
                active_acc = acc
            else:
                available.append(acc)

        if active_acc:
            return [active_acc] + available
        if available:
            return available

        # Resilience: if every account is cooling down, prefer the account whose
        # cooldown expires soonest instead of wiping the whole cooldown table.
        # Clearing it entirely caused a retry storm: 429 -> clear -> 429 -> clear,
        # dozens of identical failed requests per second against an exhausted quota.
        if all_accounts:
            known_emails = [a.get("email") for a in all_accounts]
            soonest: Optional[Tuple[str, str, float]] = None
            for (e, m), until in self._exhausted_accounts.items():
                if e not in known_emails:
                    continue
                if soonest is None or until < soonest[2]:
                    soonest = (e, m, until)
            if soonest:
                wait = max(0, int(soonest[2] - now))
                log(
                    f"[TokenManager] All accounts in cooldown. Nearest reset in {wait}s "
                    f"({soonest[0]}/{soonest[1]}). Trying it, keeping other cooldowns.",
                    level="WARN",
                )
                for acc in all_accounts:
                    if acc.get("email") == soonest[0]:
                        return [acc]
            log("[TokenManager] All accounts in cooldown, reset time unknown. Trying all.", level="WARN")
            return all_accounts

        return []

    def get_next_account_round_robin(self, model: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Selects the next available account using round-robin rotation."""
        available = self.get_available_accounts(model)
        if not available:
            return None
        self._rotation_index = (self._rotation_index + 1) % len(available)
        selected = available[self._rotation_index]
        self._data["active_email"] = selected.get("email")
        self._save()
        return selected

    def set_active_account(self, email: str):
        for acc in self._data.get("accounts", []):
            if acc.get("email") == email:
                self._data["active_email"] = email
                self._save()
                return
        raise ValueError(f"Account with email '{email}' not found.")

    def get_accounts(self, provider: Optional[str] = None) -> List[Dict[str, Any]]:
        accounts = self._data.get("accounts", [])
        if provider:
            return [acc for acc in accounts if acc.get("provider", "google") == provider]
        return accounts

    def get_active_account(self, provider: Optional[str] = None) -> Optional[Dict[str, Any]]:
        accounts = self.get_accounts(provider=provider)
        if not accounts:
            return None

        active_email = self._data.get("active_email")
        if active_email:
            for acc in accounts:
                email = acc.get("email") or acc.get("id")
                if email == active_email and not self.is_account_exhausted(email):
                    return acc

        for acc in accounts:
            email = acc.get("email") or acc.get("id")
            if not self.is_account_exhausted(email):
                return acc
        return None

    def mark_account_cooldown(self, email_or_id: str, cooldown_seconds: float = 300.0, model: str = "*", provider: Optional[str] = None):
        """Places account into cooldown."""
        self.mark_account_exhausted(email_or_id, model=model, cooldown_seconds=cooldown_seconds)

    def get_token_for_account(self, account: Dict[str, Any], force_refresh: bool = False) -> Tuple[Optional[str], Optional[str]]:
        """
        Returns (access_token, project_id) for the specified account,
        refreshing if expired or forced.
        """
        access_token = account.get("access_token")
        expires_at = account.get("expires_at")
        refresh_token = account.get("refresh_token")

        if force_refresh or (expires_at and time.time() > (expires_at - 60)):
            if refresh_token:
                try:
                    refreshed = refresh_access_token(refresh_token)
                    account["access_token"] = refreshed["access_token"]
                    account["expires_at"] = refreshed["expires_at"]
                    if "refresh_token" in refreshed:
                        account["refresh_token"] = refreshed["refresh_token"]
                    self._save()
                    return account["access_token"], account.get("project_id")
                except Exception as e:
                    log(f"[TokenManager] Error refreshing token for {account.get('email')}: {e}", level="ERROR")
            else:
                log(f"[TokenManager] Token for {account.get('email')} expired, but refresh_token is missing.", level="WARN")

        return access_token, account.get("project_id")

    def get_valid_token(self, force_refresh: bool = False) -> Tuple[Optional[str], Optional[str]]:
        """
        Returns (access_token, project_id) for the active account.
        Automatically performs token refresh if expired or forced.
        Falls back to local Antigravity session if no account is stored.
        """
        # 1. Check stored accounts
        account = self.get_active_account()
        if account:
            return self.get_token_for_account(account, force_refresh=force_refresh)

        # 2. Try detecting from local Antigravity
        local_creds = detect_local_credentials()
        if local_creds:
            log("[TokenManager] Detected local Antigravity session!")
            self.add_or_update_account(local_creds)
            return local_creds["access_token"], local_creds.get("project_id")

        return None, None

    def interactive_login(self) -> Dict[str, Any]:
        """Runs the interactive OAuth login and registers the new account."""
        account = login_flow_browser()
        self.add_or_update_account(account)
        self.set_active_account(account["email"])
        return account
