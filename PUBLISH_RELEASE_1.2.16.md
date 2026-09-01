# Публикация релиза 1.2.16

Репозитории:

- GitHub: `Kerim09/updater1c-linux`
- GitFlic: `kerim0955/updater1c-linux`
- ветка: `release/v1.2.16`
- тег: `v1.2.16`

Артефакты:

- `dist/updater1c-linux-1.2.16.run`
- `dist/updater1c-linux-1.2.16.run.sha256`
- `dist/updater1c-linux-1.2.16-source.tar.gz`
- `dist/updater1c-linux-1.2.16-source.tar.gz.sha256`
- `RELEASE_NOTES_v1.2.16.md`

Перед публикацией должны успешно завершиться модульные тесты, проверка
Bash/Python-синтаксиса, тестовая установка и реальное скачивание обновления с
ИТС-учётной записью пользователя.

В коммит не включаются `dist/`, журналы, Python-кэш, пользовательские
конфигурации, cookies и секреты.

GitFlic API-токен разрешено передавать только через переменную окружения либо
интерактивный скрытый ввод.
