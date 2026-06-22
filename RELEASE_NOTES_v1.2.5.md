# Обновлятор 1C Linux 1.2.5

## Исправления

- Исправлено падение при открытии списка шаблонов 1С: восстановлен метод `TemplateSelectDialogGtk.load_templates()`.
- Исправлен недоделанный участок группировки шаблонов: `refresh_templates()` теперь безопасно вызывает рабочую загрузку списка.
- Выбор шаблона снова работает через плоскую `Gtk.ListStore`, совместимую с текущим `accept_selected()`.
- Добавлен статический smoke-test `tools/test_template_dialog_static.py`, чтобы такой дефект больше не проходил только через `py_compile`.
- В release-процесс добавлена чистая структура артефактов: `legacy/`, `installer/`, `source/`, `checksums/`, `build-log.txt`, `publish-log.txt`.

## Артефакты

- `updater1c-linux_1.2.5_installer.run`
- `updater1c-linux_1.2.5_source.tar.gz`
- `updater1c-linux_1.2.5_legacy_src_current.tar.gz`
- `SHA256SUMS.txt`

## Установка

```bash
chmod +x updater1c-linux_1.2.5_installer.run
./updater1c-linux_1.2.5_installer.run
```
