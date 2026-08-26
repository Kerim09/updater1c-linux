#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
APP_ID="updater1c-linux"
INSTALL_DIR="/opt/${APP_ID}"
DESKTOP_ID="io.github.kerim.updater1clinux.desktop"
OUT="$ROOT/dist/${APP_ID}-${VERSION}.run"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

fail() { echo "ОШИБКА: $*" >&2; exit 1; }
need_file() { [[ -f "$ROOT/$1" ]] || fail "Не найден обязательный файл: $1"; }

need_file VERSION
need_file gtk_port/updater1c_gtk.py
need_file core/onec_releases_client.py
need_file assets/updater1c.png
need_file packaging/linux/updater1c-linux
need_file packaging/linux/io.github.kerim.updater1clinux.desktop

mkdir -p "$ROOT/dist" "$WORK/stage/opt/$APP_ID" \
  "$WORK/stage/usr/local/bin" \
  "$WORK/stage/usr/share/applications" \
  "$WORK/stage/usr/share/icons/hicolor/256x256/apps"

echo "== Сборка GTK-установщика $VERSION =="
echo "Исходники: $ROOT"
echo "Результат: $OUT"

echo "[1/6] Копирование GTK runtime"
cp -a "$ROOT/core" "$WORK/stage/opt/$APP_ID/"
cp -a "$ROOT/gtk_port" "$WORK/stage/opt/$APP_ID/"
cp "$ROOT/VERSION" "$WORK/stage/opt/$APP_ID/"
cp "$ROOT/assets/updater1c.png" "$WORK/stage/opt/$APP_ID/"
cp "$ROOT/assets/updater1c-linux.svg" "$WORK/stage/opt/$APP_ID/" 2>/dev/null || true
cp "$ROOT/README.md" "$WORK/stage/opt/$APP_ID/" 2>/dev/null || true
cp "$ROOT/RELEASE_NOTES_v${VERSION}.md" "$WORK/stage/opt/$APP_ID/" 2>/dev/null || true

echo "[2/6] Очистка payload от старых веток и кэшей"
find "$WORK/stage/opt/$APP_ID" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$WORK/stage/opt/$APP_ID/gtk_port" -type f \
  \( -name '*.pyc' -o -name '*.bak' -o -name '*.backup*' -o -name '*.before_*' \) -delete
rm -f "$WORK/stage/opt/$APP_ID/gtk_port/run-gtk.sh"

echo "[3/6] Установка launcher, desktop-файла и иконки"
install -m 0755 "$ROOT/packaging/linux/updater1c-linux" \
  "$WORK/stage/usr/local/bin/updater1c-linux"
install -m 0644 "$ROOT/packaging/linux/io.github.kerim.updater1clinux.desktop" \
  "$WORK/stage/usr/share/applications/$DESKTOP_ID"
install -m 0644 "$ROOT/assets/updater1c.png" \
  "$WORK/stage/usr/share/icons/hicolor/256x256/apps/updater1c-linux.png"

[[ "$(find "$WORK/stage/usr/share/applications" -type f -name '*.desktop' | wc -l)" -eq 1 ]] \
  || fail "В payload должен быть ровно один desktop-файл"

echo "[4/6] Проверка GTK и Python"
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile \
  "$ROOT/gtk_port/updater1c_gtk.py" \
  "$ROOT/core/onec_releases_client.py" \
  "$ROOT/gtk_port/u1c_credentials_session.py"
bash -n "$ROOT/packaging/linux/updater1c-linux"

echo "[5/6] Формирование self-extracting installer"
tar -C "$WORK/stage" -czf "$WORK/payload.tar.gz" .

cat > "$OUT" <<'INSTALLER'
#!/usr/bin/env bash
set -Eeuo pipefail

APP_ID="updater1c-linux"
APP_NAME="Обновлятор 1С Linux"
INSTALL_DIR="/opt/${APP_ID}"
BIN_PATH="/usr/local/bin/${APP_ID}"
DESKTOP_ID="io.github.kerim.updater1clinux.desktop"
DESKTOP_PATH="/usr/share/applications/${DESKTOP_ID}"
STAMP="$(date +%Y%m%d_%H%M%S)"
LOG="/tmp/${APP_ID}-install-${STAMP}.log"

if [[ "$(id -u)" -ne 0 ]]; then
    if command -v pkexec >/dev/null 2>&1; then
        exec pkexec /bin/bash "$0" "$@"
    fi
    exec sudo -H /bin/bash "$0" "$@"
fi

exec > >(tee -a "$LOG") 2>&1

fail() { echo "ОШИБКА: $*" >&2; exit 1; }
step() { echo; echo "== $* =="; }

TARGET_UID="${SUDO_UID:-${PKEXEC_UID:-}}"
if [[ -z "$TARGET_UID" || "$TARGET_UID" == "0" ]]; then
    TARGET_UID="$(id -u 2>/dev/null || echo 0)"
fi
TARGET_USER="$(getent passwd "$TARGET_UID" | cut -d: -f1 || true)"
TARGET_HOME="$(getent passwd "$TARGET_UID" | cut -d: -f6 || true)"
TARGET_HOME="${TARGET_HOME:-/root}"
TARGET_GID="$(id -g "$TARGET_USER" 2>/dev/null || echo "$TARGET_UID")"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

step "Проверка системных зависимостей"
command -v python3 >/dev/null 2>&1 || fail "Не найден python3"
if ! python3 -c 'import gi; gi.require_version("Gtk", "3.0"); from gi.repository import Gtk' >/dev/null 2>&1 \
   || ! python3 -c 'import requests' >/dev/null 2>&1 \
   || ! python3 -c 'import secretstorage' >/dev/null 2>&1 \
   || ! command -v secret-tool >/dev/null 2>&1; then
    command -v apt-get >/dev/null 2>&1 || fail "Не хватает GTK/requests/Secret Service, а apt-get недоступен"
    echo "Устанавливаю зависимости: python3-gi gir1.2-gtk-3.0 python3-requests python3-secretstorage python3-keyring libsecret-tools"
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
        python3-gi gir1.2-gtk-3.0 python3-requests python3-secretstorage python3-keyring libsecret-tools
fi
python3 - <<'PY' || fail "Не установлены Python GI/GTK 3"
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
PY
python3 - <<'PY' || fail "Не установлен python3-requests"
import requests
PY

step "Распаковка payload"
LINE="$(awk '/^__PAYLOAD_BELOW__$/ {print NR+1; exit}' "$0")"
[[ -n "$LINE" ]] || fail "Не найден payload"
tail -n +"$LINE" "$0" | tar -xz -C "$TMP"
APP_SRC="$TMP/opt/$APP_ID"
[[ -f "$APP_SRC/gtk_port/updater1c_gtk.py" ]] || fail "Payload повреждён: нет GTK runtime"

step "Резервирование предыдущего runtime"
OLD_BACKUP=""
if [[ -d "$INSTALL_DIR" ]]; then
    OLD_BACKUP="${INSTALL_DIR}.backup-${STAMP}"
    mv "$INSTALL_DIR" "$OLD_BACKUP"
    echo "Старая программная копия сохранена: $OLD_BACKUP"
fi

step "Установка GTK runtime"
install -d -m 0755 "$INSTALL_DIR"
cp -a "$APP_SRC/." "$INSTALL_DIR/"
find "$INSTALL_DIR" -type d -exec chmod 0755 {} +
find "$INSTALL_DIR" -type f -exec chmod 0644 {} +
chmod 0755 "$BIN_PATH" 2>/dev/null || true

step "Миграция пользовательских данных"
USER_CONFIG="$TARGET_HOME/.config/updater1c-linux"
install -d -m 0700 -o "$TARGET_UID" -g "$TARGET_GID" "$USER_CONFIG"

# Пользовательские настройки не удаляются. Старые копии из /opt переносятся
# только при отсутствии файла у пользователя; существующие Secret Service
# записи и keyring не трогаются.
if [[ -n "$OLD_BACKUP" ]]; then
    for name in config.json settings.json settings-gtk.json clusters.json dbms_profiles.json; do
        if [[ -f "$OLD_BACKUP/$name" && ! -e "$USER_CONFIG/$name" ]]; then
            install -o "$TARGET_UID" -g "$TARGET_GID" -m 0600 "$OLD_BACKUP/$name" "$USER_CONFIG/$name"
            echo "Мигрирован файл настроек: $name"
        fi
    done
    for name in backups releases downloads data; do
        if [[ -e "$OLD_BACKUP/$name" && ! -e "$INSTALL_DIR/$name" ]]; then
            cp -a "$OLD_BACKUP/$name" "$INSTALL_DIR/$name"
            echo "Сохранён каталог runtime-данных: $name"
        fi
    done
fi

chown -R "$TARGET_UID:$TARGET_GID" "$USER_CONFIG"

step "Установка launcher и канонического ярлыка"
install -Dm755 "$TMP/usr/local/bin/updater1c-linux" "$BIN_PATH"
install -Dm644 "$TMP/usr/share/applications/$DESKTOP_ID" "$DESKTOP_PATH"
install -Dm644 "$TMP/usr/share/icons/hicolor/256x256/apps/updater1c-linux.png" \
    /usr/share/icons/hicolor/256x256/apps/updater1c-linux.png

for old in \
    updater1c-linux.desktop \
    updater1c-linux-gtk.desktop \
    io.github.kerim1c.updater1clinux.desktop \
    io.github.kerim1c.updater1clinux.gtk.desktop \
    updater1c-gtk.desktop; do
    [[ "$old" == "$DESKTOP_ID" ]] || rm -f "/usr/share/applications/$old"
done
rm -f /usr/local/bin/updater1c-gtk /usr/local/bin/updater1c-linux-gtk

update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor >/dev/null 2>&1 || true
if command -v kbuildsycoca6 >/dev/null 2>&1; then
    kbuildsycoca6 >/dev/null 2>&1 || true
elif command -v kbuildsycoca5 >/dev/null 2>&1; then
    kbuildsycoca5 >/dev/null 2>&1 || true
fi

step "Финальная проверка"
[[ -x "$BIN_PATH" ]] || fail "Не установлен launcher: $BIN_PATH"
[[ -f "$DESKTOP_PATH" ]] || fail "Не установлен desktop-файл: $DESKTOP_PATH"
[[ "$(find /usr/share/applications -maxdepth 1 -type f -name '*.desktop' -printf '%f\n' | grep -E '^(updater1c|io\.github\.kerim)' | sort | wc -l)" -eq 1 ]] \
  || fail "В системном каталоге остались дублирующие ярлыки Updater1C"
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile "$INSTALL_DIR/gtk_port/updater1c_gtk.py"

echo
echo "Установка завершена: $APP_NAME $(<"$INSTALL_DIR/VERSION")"
echo "Канонический ярлык: $DESKTOP_PATH"
echo "Лог установки: $LOG"
[[ -n "$OLD_BACKUP" ]] && echo "Резервная копия runtime: $OLD_BACKUP"
exit 0
__PAYLOAD_BELOW__
INSTALLER

cat "$WORK/payload.tar.gz" >> "$OUT"
chmod 0755 "$OUT"
sha256sum "$OUT" > "$OUT.sha256"

echo "[6/6] Готово: $OUT"
echo "SHA256: $OUT.sha256"
