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
    print("=== Вход в аккаунт Google (Antigravity Cloud Code) ===")
    try:
        account = manager.interactive_login()
        print(f"\n✓ Успешно авторизован: {account.get('email')}")
        print(f"  Проект GCP: {account.get('project_id')}")
        print(f"  Тарифный план: {account.get('tier', 'free').upper()} ({account.get('tier_id')})")
        print(f"  Аккаунт добавлен в пул и назначен активным.")
    except Exception as e:
        print(f"\n✗ Ошибка авторизации: {e}")
        sys.exit(1)


def cmd_status(manager: TokenManager):
    print("=== Статус авторизации ===")
    token, project_id = manager.get_valid_token()
    account = manager.get_active_account()

    gemini_key = manager.get_gemini_api_key()
    if not account and not token:
        print("✗ Нет активных учетных записей Google (Antigravity).")
        if gemini_key:
            print(f"✓ Прямой Gemini API Key: настроен ({gemini_key[:8]}...)")
        print("  Для входа через аккаунт Google выполните: python cli_auth.py login")
        return

    email = account.get("email") if account else "Локальная сессия"
    tier = account.get("tier", "unknown") if account else "unknown"
    expires_at = account.get("expires_at") if account else None

    print(f"Активный аккаунт: {email}")
    print(f"Привязанный проект GCP: {project_id}")
    print(f"Уровень подписки: {tier.upper()}")

    if expires_at:
        exp_dt = datetime.fromtimestamp(expires_at).strftime("%Y-%m-%d %H:%M:%S")
        print(f"Срок действия токена: до {exp_dt}")
    else:
        print("Срок действия токена: управляется внешней сессией")

    total_accs = len(manager.get_accounts())
    print(f"Всего аккаунтов в пуле: {total_accs}")

    gemini_key = manager.get_gemini_api_key()
    if gemini_key:
        print(f"Прямой Gemini API Key: настроен ({gemini_key[:8]}...) [Авто-фоллбек активен]")


def cmd_list(manager: TokenManager):
    accounts = manager.get_accounts()
    active = manager.get_active_account()
    active_email = active.get("email") if active else None

    print(f"=== Сохраненные аккаунты ({len(accounts)}) ===")
    if not accounts:
        print("Нет сохраненных аккаунтов. Запустите 'python cli_auth.py login'")
        return

    exhausted = manager.get_exhausted_status()

    for i, acc in enumerate(accounts, 1):
        email = acc.get("email", "unknown")
        is_active = " [АКТИВЕН]" if email == active_email else ""
        cooldown_info = ""
        if email in exhausted:
            models_cd = ", ".join(f"{m}: {int(sec)}с" for m, sec in exhausted[email].items())
            cooldown_info = f" | ⚠️ Кулдаун ({models_cd})"

        print(f"{i}. {email}{is_active}")
        print(f"   Проект: {acc.get('project_id')} | Тариф: {acc.get('tier', 'free')}{cooldown_info}")


def cmd_pool(manager: TokenManager):
    """Displays comprehensive pool status including quotas and cooldowns."""
    accounts = manager.get_accounts()
    if not accounts:
        print("В пуле нет сохраненных аккаунтов. Выполните 'python cli_auth.py login'")
        return

    active = manager.get_active_account()
    active_email = active.get("email") if active else None
    exhausted = manager.get_exhausted_status()
    gemini_key = manager.get_gemini_api_key()

    print(f"\n=== Пул Google аккаунтов ({len(accounts)} аккаунтов) ===")
    print("-" * 75)
    print(f"{'Email':<30} | {'Статус':<10} | {'Кулдаун':<15} | {'GCP Project'}")
    print("-" * 75)

    for acc in accounts:
        email = acc.get("email", "unknown")
        is_act = "АКТИВЕН" if email == active_email else "Готов"
        cd_str = "-"
        if email in exhausted:
            is_act = "429 Кулдаун"
            max_cd = int(max(exhausted[email].values()))
            cd_str = f"{max_cd} сек"

        short_email = email[:28]
        proj = acc.get("project_id") or "-"
        print(f"{short_email:<30} | {is_act:<10} | {cd_str:<15} | {proj}")

    print("-" * 75)
    if gemini_key:
        print(f"✓ Резервный Gemini API Key: настроен ({gemini_key[:8]}...) - авто-фоллбек включен.")
    else:
        print("ℹ Резервный Gemini API Key не задан. Для настройки: python cli_auth.py set-key <KEY>")
    print()


def cmd_switch(manager: TokenManager, email: str):
    try:
        manager.set_active_account(email)
        print(f"✓ Активным назначен аккаунт: {email}")
    except ValueError as e:
        print(f"✗ Ошибка: {e}")
        sys.exit(1)


def cmd_remove(manager: TokenManager, email: str):
    removed = manager.remove_account(email)
    if removed:
        print(f"✓ Аккаунт '{email}' успешно удален из пула.")
        active = manager.get_active_account()
        if active:
            print(f"  Новый активный аккаунт: {active.get('email')}")
        else:
            print("  В пуле не осталось аккаунтов.")
    else:
        print(f"✗ Аккаунт '{email}' не найден в пуле.")
        sys.exit(1)


def cmd_quotas(manager: TokenManager):
    token, project_id = manager.get_valid_token()
    if not token:
        print("✗ Не удалось получить токен. Сначала выполните вход.")
        return

    account = manager.get_active_account()
    email = account.get("email") if account else "Active"
    print(f"Запрашиваю квоты моделей для [{email}] у Google Cloud Code API...")
    data = fetch_available_models(token, project_id)
    models = data.get("models", {})
    if not models:
        print("Не удалось получить информацию о моделях или ответ пуст.")
        return

    print("\nДоступные модели и квоты:")
    print("-" * 65)
    print(f"{'Модель':<32} | {'Остаток':<10} | {'Сброс квоты'}")
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
    print(f"✓ Прямой ключ Gemini API успешно сохранен ({key[:8]}...)")


def main():
    parser = argparse.ArgumentParser(description="Управление авторизацией и пулом аккаунтов Claude-Gemini Proxy")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("login", help="Интерактивный вход в Google")
    subparsers.add_parser("add", help="Добавить еще один аккаунт Google в пул")
    subparsers.add_parser("status", help="Проверка статуса активной сессии")
    subparsers.add_parser("list", help="Список сохраненных аккаунтов")
    subparsers.add_parser("pool", help="Статус пула аккаунтов, кулдаунов и квот")

    p_switch = subparsers.add_parser("switch", help="Сменить активный аккаунт")
    p_switch.add_argument("email", help="Email аккаунта для активации")

    p_remove = subparsers.add_parser("remove", help="Удалить аккаунт из пула")
    p_remove.add_argument("email", help="Email аккаунта для удаления")

    subparsers.add_parser("quotas", help="Просмотр оставшихся квот")

    p_key = subparsers.add_parser("set-key", help="Установить прямой Gemini API ключ (резервный фоллбек)")
    p_key.add_argument("key", help="API ключ из Google AI Studio")

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
