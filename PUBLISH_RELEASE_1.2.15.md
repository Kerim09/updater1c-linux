# Публикация релиза 1.2.15

Репозитории:

- GitHub: `Kerim09/updater1c-linux`
- GitFlic: `kerim0955/updater1c-linux`
- ветка: `release/v1.2.15`
- тег: `v1.2.15`

Артефакты:

- `dist/updater1c-linux-1.2.15.run`
- `dist/updater1c-linux-1.2.15.run.sha256`
- `dist/updater1c-linux-1.2.15-source.tar.gz`
- `dist/updater1c-linux-1.2.15-source.tar.gz.sha256`
- `RELEASE_NOTES_v1.2.15.md`

Перед публикацией должны успешно завершиться модульные тесты, проверка
Bash/Python-синтаксиса и проверка обеих контрольных сумм.

В коммит не включаются `dist/`, журналы `mcp-server.log*`, `__pycache__`,
`.pyc`, пользовательские конфигурации и секреты.

GitFlic API-токен разрешено передавать только через переменную окружения либо
интерактивный скрытый ввод.
