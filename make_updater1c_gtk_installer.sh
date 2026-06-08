#!/usr/bin/env bash
set -euo pipefail

APP_ID="io.github.kerim1c.updater1clinux.gtk"
APP_NAME="Обновлятор 1С Linux GTK"
APP_DIR="/opt/updater1c-linux"
VERSION="${VERSION:-1.1.1-gtk}"
TS="$(date +%Y%m%d_%H%M%S)"

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
DIST_DIR="$PROJECT_DIR/dist"
BUILD_DIR="/tmp/updater1c-gtk-build-$TS"
STAGE_DIR="$BUILD_DIR/stage"
PAYLOAD="$BUILD_DIR/payload.tar.gz"
OUT="$DIST_DIR/updater1c-linux-${VERSION}.run"

mkdir -p "$DIST_DIR"
rm -rf "$BUILD_DIR"

mkdir -p "$STAGE_DIR$APP_DIR/app/assets"
mkdir -p "$STAGE_DIR$APP_DIR/bin"
mkdir -p "$STAGE_DIR$APP_DIR/icons"
mkdir -p "$STAGE_DIR/usr/share/applications"

echo "== Copy GTK runtime files =="

cp -f "$PROJECT_DIR/main_gtk.py" "$STAGE_DIR$APP_DIR/app/"

# Кладем рядом текущие runtime-модули, которые могут понадобиться при переносе функций.
for f in secret_store.py ibases_v8i_guard.py structured_log.py structured_log_qt_inline.py; do
  if [ -f "$PROJECT_DIR/$f" ]; then
    cp -f "$PROJECT_DIR/$f" "$STAGE_DIR$APP_DIR/app/"
  fi
done

if [ -f "$PROJECT_DIR/assets/updater1c.png" ]; then
  cp -f "$PROJECT_DIR/assets/updater1c.png" "$STAGE_DIR$APP_DIR/icons/updater1c.png"
  cp -f "$PROJECT_DIR/assets/updater1c.png" "$STAGE_DIR$APP_DIR/app/assets/updater1c.png"
elif [ -f "$PROJECT_DIR/icons/updater1c.png" ]; then
  cp -f "$PROJECT_DIR/icons/updater1c.png" "$STAGE_DIR$APP_DIR/icons/updater1c.png"
  cp -f "$PROJECT_DIR/icons/updater1c.png" "$STAGE_DIR$APP_DIR/app/assets/updater1c.png"
else
  echo "ОШИБКА: не найдена иконка assets/updater1c.png или icons/updater1c.png"
  exit 1
fi

cp -f "$PROJECT_DIR/packaging/linux/updater1c-gtk-gui" "$STAGE_DIR$APP_DIR/bin/updater1c-gtk-gui"
chmod 755 "$STAGE_DIR$APP_DIR/bin/updater1c-gtk-gui"
chmod 644 "$STAGE_DIR$APP_DIR/icons/updater1c.png"

cp -f "$PROJECT_DIR/packaging/linux/io.github.kerim1c.updater1clinux.gtk.desktop" \
  "$STAGE_DIR/usr/share/applications/${APP_ID}.desktop"

chmod 644 "$STAGE_DIR/usr/share/applications/${APP_ID}.desktop"

echo "== Create payload =="

tar -C "$STAGE_DIR" -czf "$PAYLOAD" .

echo "== Create self-extracting GTK installer =="

cat > "$OUT" <<'HEADER'
#!/usr/bin/env bash
set -euo pipefail

APP_ID="io.github.kerim1c.updater1clinux.gtk"
APP_DIR="/opt/updater1c-linux"

need_root() {
  if [ "$(id -u)" -ne 0 ]; then
    if command -v pkexec >/dev/null 2>&1; then
      exec pkexec env DISPLAY="${DISPLAY:-}" XAUTHORITY="${XAUTHORITY:-}" bash "$0" "$@"
    elif command -v sudo >/dev/null 2>&1; then
      exec sudo bash "$0" "$@"
    else
      echo "ОШИБКА: нужны права root. Запустите через sudo."
      exit 1
    fi
  fi
}

install_deps() {
  echo
  echo "== Installing GTK dependencies from system repositories =="

  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update || true

    apt-get install -y \
      python3 \
      python3-gi \
      gir1.2-gtk-3.0 \
      python3-keyring \
      python3-secretstorage \
      libsecret-1-0 \
      x11-utils \
      xauth \
      xvfb \
      curl \
      wget \
      ca-certificates \
      tar \
      gzip \
      desktop-file-utils \
      xdg-utils \
      policykit-1 \
      || true
  fi

  echo
  echo "== Checking GTK =="
  if python3 - <<'PY'
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
print("GTK OK")
PY
  then
    echo "GTK dependencies OK"
  else
    echo
    echo "ОШИБКА: GTK/PyGObject не установлен."
    echo "Попробуйте вручную:"
    echo "  sudo apt install python3-gi gir1.2-gtk-3.0"
    exit 1
  fi
}

install_app() {
  echo
  echo "== Installing GTK application files =="

  TMP_DIR="$(mktemp -d)"
  trap 'rm -rf "$TMP_DIR"' EXIT

  ARCHIVE_LINE="$(awk '/^__PAYLOAD_BELOW__/ {print NR + 1; exit 0; }' "$0")"
  tail -n +"$ARCHIVE_LINE" "$0" | tar -xz -C "$TMP_DIR"

  mkdir -p "$APP_DIR"

  # Не трогаем возможную Qt-ветку целиком. Меняем только GTK-файлы и общие иконки.
  mkdir -p "$APP_DIR/app" "$APP_DIR/bin" "$APP_DIR/icons"

  cp -a "$TMP_DIR/opt/updater1c-linux/app/." "$APP_DIR/app/"
  cp -a "$TMP_DIR/opt/updater1c-linux/bin/." "$APP_DIR/bin/"
  cp -a "$TMP_DIR/opt/updater1c-linux/icons/." "$APP_DIR/icons/"

  chmod 755 "$APP_DIR/bin/updater1c-gtk-gui"
  chmod 644 "$APP_DIR/icons/updater1c.png"

  install -Dm644 \
    "$TMP_DIR/usr/share/applications/${APP_ID}.desktop" \
    "/usr/share/applications/${APP_ID}.desktop"

  update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
  gtk-update-icon-cache -f /usr/share/icons/hicolor >/dev/null 2>&1 || true

  echo
  echo "== Installed =="
  echo "Application: $APP_DIR"
  echo "Launcher:    $APP_DIR/bin/updater1c-gtk-gui"
  echo "Desktop:     /usr/share/applications/${APP_ID}.desktop"
}

need_root "$@"
install_app
install_deps

echo
echo "Готово."
echo "Запуск:"
echo "  /opt/updater1c-linux/bin/updater1c-gtk-gui"
echo "  gtk-launch io.github.kerim1c.updater1clinux.gtk"

exit 0

__PAYLOAD_BELOW__
HEADER

cat "$PAYLOAD" >> "$OUT"
chmod +x "$OUT"

echo
echo "== DONE =="
echo "Installer:"
echo "$OUT"
ls -lh "$OUT"

echo
echo "== Payload size =="
du -h "$PAYLOAD"

rm -rf "$BUILD_DIR"
