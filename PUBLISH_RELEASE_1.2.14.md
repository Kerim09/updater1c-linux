# Публикация релиза 1.2.14

Артефакты релиза:

- `dist/updater1c-linux-1.2.14.run`
- `dist/updater1c-linux-1.2.14.run.sha256`
- `dist/updater1c-linux-1.2.14-source.tar.gz`
- `dist/updater1c-linux-1.2.14-source.tar.gz.sha256`
- `RELEASE_NOTES_v1.2.14.md`

## GitHub

В рабочей копии должен быть настроен remote GitHub и создан тег `v1.2.14`:

```bash
git add .
git commit -m "Release 1.2.14: P0-P1 interface stabilization"
git push origin main
git tag -a v1.2.14 -m "Обновлятор 1С Linux 1.2.14"
git push origin v1.2.14
```

Затем создать GitHub Release для тега `v1.2.14` и прикрепить четыре файла из
`dist/`, а также `RELEASE_NOTES_v1.2.14.md`.

## GitFlic

Для GitFlic используется тот же коммит и тег:

```bash
git push gitflic main
git push gitflic v1.2.14
```

В релиз GitFlic прикрепить те же пять файлов. API-токен передавать только через
переменную окружения или интерактивный ввод; не записывать его в скрипты,
репозиторий и описание релиза.

## Перед публикацией

```bash
sha256sum -c dist/updater1c-linux-1.2.14.run.sha256
sha256sum -c dist/updater1c-linux-1.2.14-source.tar.gz.sha256
```
