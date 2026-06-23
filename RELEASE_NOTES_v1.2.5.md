# Обновлятор 1C Linux 1.2.6

## Перезаливка релиза 1.2.6

- Установщик пересобран как slim/self-extracting `.run`: внутри только файлы приложения, без `dist/`, `build/`, `releases/`, `.git`, старых backup-файлов и release-артефактов.
- Добавлен штатный uninstaller: `/opt/updater1c-linux/uninstall-updater1c-linux.sh`.
- Uninstaller удаляет приложение, системные и пользовательские ярлыки, иконки, настройки `~/.config/updater1c-linux`, `~/.local/share/updater1c-linux`, `~/.cache/updater1c-linux`.
- Uninstaller не удаляет базы 1С, резервные копии и отчеты в `/mnt/DataStore`.
- Исправлено падение при открытии списка шаблонов 1С: восстановлен `TemplateSelectDialogGtk.load_templates()`.
- Добавлен статический smoke-test диалога шаблонов.

## Установка

```bash
chmod +x updater1c-linux_1.2.6_installer.run
./updater1c-linux_1.2.6_installer.run
```

## Удаление

```bash
/opt/updater1c-linux/uninstall-updater1c-linux.sh
```

Сохранить настройки при удалении:

```bash
/opt/updater1c-linux/uninstall-updater1c-linux.sh --keep-settings
```
