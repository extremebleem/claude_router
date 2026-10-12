# Claude-Gemini Proxy & Claude Code Sandbox Launcher

Высокопроизводительный асинхронный прокси-сервер на **Python (`aiohttp`)**, транслирующий запросы из **Claude Code** в облачный бэкенд **Google Antigravity Cloud Code** и **Google AI Studio (Gemini)**.

Обеспечивает запуск **Claude Code CLI** в полностью изолированной песочнице, а также предоставляет API-эндпоинт для сторонних GUI-клиентов (Chatbox, LibreChat, Cline).

---

## 🌟 Ключевые возможности

1. **Изолированный лаунчер для Claude Code CLI**:
   * Автоматически находит исполняемый файл `claude.exe` (в `~/.local/bin` или через `PATH`).
   * Запускает CLI в отдельном окружении без конфликтов с другими сессиями.

2. **Полная изоляция (Sandbox)**:
   * Каждая сессия получает динамический эфемерный порт (`127.0.0.1:<PORT>`).
   * Генерируется уникальный криптографический `session_token` для защиты от посторонних подключений.
   * Изолированная директория конфигурации `~/.claude-sandbox` (`CLAUDE_CONFIG_DIR`).
   * **Вторая сессия** в обычном терминале с официальным аккаунтом Anthropic работает параллельно без каких-либо пересечений.

3. **Совместимость с Anthropic Messages API**:
   * Стриминг Server-Sent Events (SSE) в реальном времени.
   * Поддержка авто-режима (Auto Mode) со встроенным 0ms классификатором (`is_classifier_request`).
   * Конвертация вызова функций (`tools` ➔ Protobuf Schema в верхнем регистре).
   * Поддержка рассуждений (**Thinking**) и кэширование сигнатур `thoughtSignature`.
   * Автоматическая маскировка заголовков и очистка полей `cache_control`.

4. **Гибкая авторизация**:
   * Google OAuth 2.0 (клиент Antigravity) со встроенным callback-сервером.
   * Автоматический рефреш токенов через `refresh_token`.
   * Возможность использования прямого API-ключа Gemini (`AIzaSy...`).

---

## 🚀 Быстрый старт

### 1. Авторизация (выполняется один раз)

```powershell
cd C:\AI\ClaudeGemini

# Войти через Google в браузере
python cli_auth.py login

# Проверить статус и привязанный GCP-проект
python cli_auth.py status

# Посмотреть доступные квоты моделей Cloud Code
python cli_auth.py quotas
```

*(Опционально)* Если хотите использовать прямой ключ Gemini API:
```powershell
python cli_auth.py set-key AIzaSy...
```

---

### 2. Запуск Claude Code CLI в песочнице

```powershell
# Запуск Claude Code CLI (модель по умолчанию: gemini-3.8-flash-tiered):
python launcher.py

# С выбором конкретной модели:
python launcher.py --model gemini-3.1-pro-high

# С открытием веб-дашборда метрик и переключения моделей:
python launcher.py --dashboard
```

---

### 3. Использование с GUI-клиентами (Chatbox, LibreChat, Cline)

Официальный Claude Desktop не поддерживает сторонние эндпоинты (он жестко привязан к `claude.ai`). Но вы можете запустить автономный прокси для любого совместимого GUI:

```powershell
python launcher.py proxy
```
* **Адрес прокси (Base URL)**: `http://127.0.0.1:8080`
* **API Key**: любой произвольный текст (например, `sk-ant-test`)
* В клиенте (например, **Chatbox** или **Cline**) выберите формат **Anthropic** и укажите адрес выше.

---

## ⚙️ Дополнительные параметры

```powershell
# Подробный вывод всех запросов прокси в консоль:
python launcher.py -v

# Указать кастомную папку конфигурации:
python launcher.py --sandbox-dir C:\custom-sandbox
```

---

## 🔍 Поддержка Web Search и Моделей Muse Spark

1. **Клиентский Web Search (Anthropic Tools)**:
   * Прокси полностью поддерживает клиентские инструменты поиска (`WebSearch`, нативные блоки Anthropic `type: "web_search_20250101"`).
   * **Автоматическое обогащение (Fallback Enricher)**: если клиентский инструмент WebSearch возвращает пустой список результатов (из-за недоступности upstream-сервиса поиска), прокси автоматически перехватывает пустой ответ и подставляет реальные сниппеты и ссы��ки через встроенный движок поиска (`transformer/search.py`).

2. **Встроенный Google Search Grounding**:
   * Для использования нативного поиска Google от Gemini можно выбрать модель `gemini-3.8-search` (или добавить флаг `"google_search": true` / `"web_search": true` в тело запроса).
   * Источники поиска автоматически форматируются и добавляются в ответ в секции `**Sources:** [Title](URL)`.

3. **Поддержка Meta Muse Spark (`muse-spark-1.3`, `muse-spark`)**:
   * Включены биндинги и пресеты для расширения **`muse-spark-code`** (VS Code / ACP / Model API).
   * Модели `muse-spark-1.3`, `muse-spark`, `muse-code` доступны в селекторе моделей и автоматически транслируются в производительный бэкенд с поддержкой рассуждений и работы с кодом.

---

## 🧪 Запуск тестов

```powershell
python tests/test_transformer.py
python tests/test_server.py
```
