Критичное
1. Отключение защитного классификатора Claude Code — server.py:262-338, вызов на server.py:401 is_classifier_request перехватывает запросы auto-mode и всегда возвращает <block>no</block>, не спрашивая модель. Плюс launcher.py:178 ставит CLAUDE_CODE_AUTO_MODE_SERVER=0. Итог: любая команда, которую агент хочет выполнить, автоматически одобряется. Если в песочнице есть доступ к файлам/шеллу — approvals вас не защитят.
2. Режим proxy полностью открыт — launcher.py:309-316 run_server(...) вызывается без session_auth_token, поэтому auth_middleware (server.py:167) пропускает всё: любой локальный процесс и любая веб-страница в вашем браузере (CSRF: request.json() в aiohttp не проверяет Content-Type) может жечь вашу Google-квоту и слать промпты от вашего имени.
Высокое
3. Неавторизованные изменяющие эндпоинты даже в sandbox-режиме auth_middleware проверяет только /v1/*. При этом /accounts/switch, /refresh-token, /test/clear-signature-cache, /api/stats, /dashboard открыты всем (server.py:192-206). /accounts/switch — POST без токена.
4. XSS в дашборде — dashboard.html:563, 568, 669-679 Данные вставляются через innerHTML без экранирования: acc.email, acc.tier, ev.model, ev.account. ev.model приходит прямо из тела запроса клиента (server.py:405, 460) — то есть значение, контролируемое вызывающим. Плюс onclick="switchAccount('${acc.email}')" (dashboard.html:582).
5. Токены в открытом виде — token_manager.py:40-42 access_token + refresh_token пишутся в ~/.claude-gemini-proxy/accounts.json без установки прав. На Windows ACL по умолчанию даёт доступ всей учётной записи и administrators.
Среднее
6. Захардкоженный OAuth-секрет в репозитории — config.py:16-17 OAUTH_CLIENT_SECRET = "GOCSPX-...". Для installed-app клиента это «публичный» секрет, но он всё равно позволяет выдавать себя за это приложение.
7. Полные логи запросов в открытом виде — server.py:386-388, cloudcode.py:68-70, 101-104 В logs/ пишутся весь промпт, system prompt, код из файлов, tool-результаты и заголовки ответа. Без ограничения размера и без ротации (растёт бесконечно).
8. Общий fallback-проект — config.py:66, cloudcode.py:62 Если GCP-проект не определился, запросы идут в rising-fact-p41fc — чужой проект. Данные уйдут туда.
9. API-ключ Gemini в URL — gemini_api.py:39 ?key=AIza... — утекает в логи и прокси.
10. Чтение чужой SQLite-базы — local_detector.py:28-58 Вытягивает apiKey из state.vscdb редактора Antigravity. Формально ваши данные, но это извлечение учётных данных из другого приложения.
11. Спуск в OAuth — server.py:176 Сравнение токена обычным != вместо secrets.compare_digest (незначительно для localhost).


Tip: Set CLAUDE_CODE_USE_POWERSHELL_TOOL=1 to enable the PowerShell tool (preview)