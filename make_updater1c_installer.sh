#!/usr/bin/env bash
set -Eeuo pipefail

APP_ID="updater1c-linux"
APP_NAME="Обновлятор 1С Linux"
APP_COMMENT="Linux updater for 1C infobases"
INSTALL_DIR="/opt/${APP_ID}"
BIN_PATH="/usr/bin/${APP_ID}"
DESKTOP_PATH="/usr/share/applications/${APP_ID}.desktop"
ICON_NAME="${APP_ID}"
TAG="STAGE18_POLKIT_INSTALLER"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STAMP="$(date +%Y%m%d_%H%M%S)"

DIST_DIR="${ROOT}/dist"
ASSETS_DIR="${ROOT}/assets"
REPORTS_DIR="${ROOT}/reports"

OUT="${DIST_DIR}/${APP_ID}_${TAG}_${STAMP}.run"

mkdir -p "$DIST_DIR" "$ASSETS_DIR" "$REPORTS_DIR"

echo "== Updater1C Linux installer builder =="
echo "ROOT: $ROOT"
echo "OUT : $OUT"

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

need_file() {
    [ -f "$ROOT/$1" ] || fail "Не найден обязательный файл: $1"
}

need_file "main.py"
need_file "requirements.txt"

if [ ! -f "$ASSETS_DIR/${APP_ID}.svg" ]; then
    cat > "$ASSETS_DIR/${APP_ID}.svg" <<'SVG'
<?xml version="1.0" encoding="UTF-8"?>
<svg width="256" height="256" viewBox="0 0 256 256" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#2e7d32"/>
      <stop offset="100%" stop-color="#0d47a1"/>
    </linearGradient>
  </defs>
  <rect x="20" y="20" width="216" height="216" rx="44" fill="url(#g)"/>
  <rect x="52" y="58" width="152" height="118" rx="18" fill="#ffffff" opacity="0.92"/>
  <path d="M78 92h100M78 122h100M78 152h62" stroke="#1b1b1b" stroke-width="12" stroke-linecap="round"/>
  <circle cx="188" cy="190" r="28" fill="#ffcc00"/>
  <path d="M176 190l9 9 18-22" fill="none" stroke="#1b1b1b" stroke-width="8" stroke-linecap="round" stroke-linejoin="round"/>
</svg>
SVG
fi

echo
echo "== Syntax check =="
if [ -x "$ROOT/.venv/bin/python" ]; then
    "$ROOT/.venv/bin/python" -m py_compile "$ROOT/main.py"
else
    python3 -m py_compile "$ROOT/main.py"
fi
echo "OK: main.py"

WORK="$(mktemp -d)"
cleanup() {
    rm -rf "$WORK"
}
trap cleanup EXIT

PAYLOAD_DIR="$WORK/payload"
APP_SRC_DIR="$PAYLOAD_DIR/app"
mkdir -p "$APP_SRC_DIR"

echo
echo "== Packing app source =="

tar \
  --exclude='./.git' \
  --exclude='./.venv' \
  --exclude='./venv' \
  --exclude='./__pycache__' \
  --exclude='./*.pyc' \
  --exclude='./dist' \
  --exclude='./reports' \
  --exclude='./backups' \
  --exclude='./checkpoints' \
  --exclude='./legacy' \
  --exclude='./make_updater1c_installer.sh' \
  --exclude='./updater1c_preflight_audit.sh' \
  -czf "$WORK/app-src.tar.gz" \
  -C "$ROOT" .

tar -xzf "$WORK/app-src.tar.gz" -C "$APP_SRC_DIR"

cat > "$PAYLOAD_DIR/root-install.sh" <<'ROOT_INSTALL'
#!/usr/bin/env bash
set -Eeuo pipefail

APP_ID="updater1c-linux"
APP_NAME="Обновлятор 1С Linux"
APP_COMMENT="Linux updater for 1C infobases"
INSTALL_DIR="/opt/${APP_ID}"
BIN_PATH="/usr/bin/${APP_ID}"
DESKTOP_PATH="/usr/share/applications/${APP_ID}.desktop"
ICON_NAME="${APP_ID}"

APP_SRC="${1:-}"

LOG="/tmp/${APP_ID}-install-$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$LOG") 2>&1

echo "== ${APP_NAME} root installer =="
echo "Date: $(date)"
echo "User: $(id)"
echo "APP_SRC: $APP_SRC"
echo "INSTALL_DIR: $INSTALL_DIR"
echo "LOG: $LOG"

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

if [ "$(id -u)" -ne 0 ]; then
    fail "root-install.sh должен выполняться от root через pkexec"
fi

[ -d "$APP_SRC" ] || fail "Не найдена папка исходников: $APP_SRC"
[ -f "$APP_SRC/main.py" ] || fail "В payload нет main.py"
[ -f "$APP_SRC/requirements.txt" ] || fail "В payload нет requirements.txt"

export DEBIAN_FRONTEND=noninteractive

install_pkg_if_available() {
    local pkg="$1"

    if dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "install ok installed"; then
        echo "OK already installed: $pkg"
        return 0
    fi

    if apt-cache show "$pkg" >/dev/null 2>&1; then
        echo "Installing package: $pkg"
        apt-get install -y "$pkg"
    else
        echo "WARN: apt package not found, skipped: $pkg"
    fi
}

echo
echo "== apt update =="
apt-get update -y

echo
echo "== installing system dependencies =="

PACKAGES=(
    python3
    python3-venv
    python3-pip
    python3-tk
    zenity
    polkitd
    policykit-1
    desktop-file-utils
    xdg-utils
    tar
    unzip
    curl
    wget
    ca-certificates
    libgl1
    libegl1
    libxkbcommon-x11-0
    libxcb-cursor0
    libxcb-icccm4
    libxcb-image0
    libxcb-keysyms1
    libxcb-render-util0
    libxcb-randr0
    libxcb-shape0
    libxcb-xinerama0
    libxcb-xinput0
    libxcb-xfixes0
)

for pkg in "${PACKAGES[@]}"; do
    install_pkg_if_available "$pkg"
done

echo
echo "== installing application files =="

rm -rf "$INSTALL_DIR"
install -d -m 0755 "$INSTALL_DIR"

cp -a "$APP_SRC/." "$INSTALL_DIR/"

find "$INSTALL_DIR" -type d -exec chmod 0755 {} \;
find "$INSTALL_DIR" -type f -exec chmod 0644 {} \;

if [ -f "$INSTALL_DIR/run.sh" ]; then
    chmod 0755 "$INSTALL_DIR/run.sh"
fi

echo
echo "== creating python venv =="

python3 -m venv "$INSTALL_DIR/.venv"

"$INSTALL_DIR/.venv/bin/python" -m pip install --upgrade pip setuptools wheel
"$INSTALL_DIR/.venv/bin/python" -m pip install -r "$INSTALL_DIR/requirements.txt"

echo
echo "== python syntax check =="

"$INSTALL_DIR/.venv/bin/python" -m py_compile "$INSTALL_DIR/main.py"

echo
echo "== creating launcher =="

cat > "$BIN_PATH" <<'LAUNCHER'
#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="/opt/updater1c-linux"
APP_MAIN="$APP_DIR/main.py"
APP_PY="$APP_DIR/.venv/bin/python"

if [ ! -x "$APP_PY" ]; then
    echo "Не найден Python venv: $APP_PY" >&2
    exit 1
fi

if [ ! -f "$APP_MAIN" ]; then
    echo "Не найден main.py: $APP_MAIN" >&2
    exit 1
fi

# Для Qt/PySide6 на Ubuntu часто стабильнее запуск через XCB/XWayland.
# При необходимости можно переопределить:
# QT_QPA_PLATFORM=wayland updater1c-linux
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"

export UPDATER1C_INSTALL_DIR="$APP_DIR"

USER_DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/updater1c-linux"
mkdir -p "$USER_DATA_DIR"

cd "$USER_DATA_DIR"
exec "$APP_PY" "$APP_MAIN" "$@"
LAUNCHER

chmod 0755 "$BIN_PATH"

echo
echo "== installing icon =="

install -d -m 0755 /usr/share/icons/hicolor/scalable/apps
install -d -m 0755 /usr/share/icons/hicolor/256x256/apps

if [ -f "$INSTALL_DIR/assets/${APP_ID}.svg" ]; then
    install -m 0644 "$INSTALL_DIR/assets/${APP_ID}.svg" "/usr/share/icons/hicolor/scalable/apps/${APP_ID}.svg"
elif [ -f "$INSTALL_DIR/assets/${APP_ID}.png" ]; then
    install -m 0644 "$INSTALL_DIR/assets/${APP_ID}.png" "/usr/share/icons/hicolor/256x256/apps/${APP_ID}.png"
else
    echo "WARN: icon not found in assets"
fi

echo
echo "== creating desktop shortcut =="

cat > "$DESKTOP_PATH" <<DESKTOP
[Desktop Entry]
Type=Application
Name=${APP_NAME}
Name[ru]=${APP_NAME}
Comment=${APP_COMMENT}
Comment[ru]=Обновление и обслуживание информационных баз 1С на Linux
Exec=${BIN_PATH}
Icon=${ICON_NAME}
Terminal=false
StartupNotify=true
Categories=Office;Development;Utility;
Keywords=1C;1С;Updater;Обновлятор;Update;
DESKTOP

chmod 0644 "$DESKTOP_PATH"

if command -v desktop-file-validate >/dev/null 2>&1; then
    desktop-file-validate "$DESKTOP_PATH" || true
fi

echo
echo "== creating uninstaller =="

cat > "$INSTALL_DIR/uninstall-${APP_ID}.sh" <<'UNINSTALLER'
#!/usr/bin/env bash
set -Eeuo pipefail

APP_ID="updater1c-linux"
APP_NAME="Обновлятор 1С Linux"
INSTALL_DIR="/opt/${APP_ID}"
BIN_PATH="/usr/bin/${APP_ID}"
DESKTOP_PATH="/usr/share/applications/${APP_ID}.desktop"
LOG="/tmp/${APP_ID}-uninstall-$(date +%Y%m%d_%H%M%S).log"

ask_confirm_user() {
    if command -v zenity >/dev/null 2>&1 && [ -n "${DISPLAY:-}" ]; then
        zenity --question \
            --title="Удаление ${APP_NAME}" \
            --width=460 \
            --text="Удалить ${APP_NAME}?\n\nБудут удалены:\n- ${INSTALL_DIR}\n- ${BIN_PATH}\n- ярлык приложения\n\nПользовательские данные в ~/.local/share/updater1c-linux не удаляются."
    else
        echo "Удалить ${APP_NAME}? [y/N]"
        read -r ans
        [[ "$ans" == "y" || "$ans" == "Y" ]]
    fi
}

if [ "$(id -u)" -ne 0 ]; then
    ask_confirm_user || exit 0

    if command -v pkexec >/dev/null 2>&1; then
        exec pkexec /bin/bash "$0" --confirmed
    else
        echo "pkexec не найден. Запусти через sudo:" >&2
        echo "sudo bash $0 --confirmed" >&2
        exit 1
    fi
fi

exec > >(tee -a "$LOG") 2>&1

if [ "${1:-}" != "--confirmed" ]; then
    echo "Удаление запущено от root без предварительного подтверждения."
fi

echo "== Uninstall ${APP_NAME} =="
echo "Date: $(date)"
echo "LOG: $LOG"

echo
echo "== stopping running processes =="

pkill -f "/opt/updater1c-linux/main.py" 2>/dev/null || true
pkill -f "updater1c-linux" 2>/dev/null || true

sleep 1

echo
echo "== removing files =="

rm -f "$BIN_PATH"
rm -f "$DESKTOP_PATH"

rm -f "/usr/share/icons/hicolor/scalable/apps/${APP_ID}.svg"
rm -f "/usr/share/icons/hicolor/256x256/apps/${APP_ID}.png"

rm -rf "$INSTALL_DIR"

echo
echo "== updating caches =="

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database /usr/share/applications 2>/dev/null || true
fi

if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor 2>/dev/null || true
fi

echo
echo "Удаление завершено."
echo "Пользовательские данные, если есть, остались тут:"
echo "  ~/.local/share/updater1c-linux"
echo "Лог:"
echo "  $LOG"
UNINSTALLER

chmod 0755 "$INSTALL_DIR/uninstall-${APP_ID}.sh"

echo
echo "== updating desktop/icon caches =="

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database /usr/share/applications 2>/dev/null || true
fi

if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor 2>/dev/null || true
fi

echo
echo "== final check =="

ls -la "$INSTALL_DIR/main.py"
ls -la "$BIN_PATH"
ls -la "$DESKTOP_PATH"
ls -la "$INSTALL_DIR/uninstall-${APP_ID}.sh"

echo
echo "Installation complete."
echo "Run:"
echo "  ${BIN_PATH}"
echo
echo "Uninstall:"
echo "  ${INSTALL_DIR}/uninstall-${APP_ID}.sh"
echo
echo "Log:"
echo "  $LOG"
ROOT_INSTALL

chmod 0755 "$PAYLOAD_DIR/root-install.sh"

echo
echo "== Creating self-extracting .run =="

cat > "$OUT" <<'RUNNER'
#!/usr/bin/env bash
set -Eeuo pipefail

APP_ID="updater1c-linux"
APP_NAME="Обновлятор 1С Linux"

fail_gui() {
    local text="$1"
    if command -v zenity >/dev/null 2>&1 && [ -n "${DISPLAY:-}" ]; then
        zenity --error --title="$APP_NAME" --width=520 --text="$text" || true
    else
        echo "ERROR: $text" >&2
    fi
    exit 1
}

info_gui() {
    local text="$1"
    if command -v zenity >/dev/null 2>&1 && [ -n "${DISPLAY:-}" ]; then
        zenity --info --title="$APP_NAME" --width=520 --text="$text" || true
    else
        echo "$text"
    fi
}

ask_gui() {
    local text="$1"
    if command -v zenity >/dev/null 2>&1 && [ -n "${DISPLAY:-}" ]; then
        zenity --question --title="$APP_NAME" --width=560 --text="$text"
    else
        echo "$text"
        echo
        echo "Продолжить? [y/N]"
        read -r ans
        [[ "$ans" == "y" || "$ans" == "Y" ]]
    fi
}

if [ "$(id -u)" -eq 0 ]; then
    fail_gui "Не запускай установщик через sudo.\n\nЗапусти обычным пользователем:\n./$(basename "$0")\n\nПрава администратора будут запрошены через системное окно Ubuntu / Polkit."
fi

command -v pkexec >/dev/null 2>&1 || fail_gui "Не найден pkexec.\n\nУстанови polkit/pkexec и повтори запуск."
command -v tar >/dev/null 2>&1 || fail_gui "Не найден tar."
command -v awk >/dev/null 2>&1 || fail_gui "Не найден awk."

ask_gui "Установить ${APP_NAME}?\n\nБудет выполнено:\n- установка системных зависимостей через apt;\n- установка приложения в /opt/updater1c-linux;\n- создание команды /usr/bin/updater1c-linux;\n- создание ярлыка в меню Ubuntu;\n- создание uninstaller.\n\nПрава администратора будут запрошены через системное окно Ubuntu." || exit 0

TMP="$(mktemp -d)"
cleanup() {
    rm -rf "$TMP"
}
trap cleanup EXIT

ARCHIVE_LINE="$(awk '/^__UPDATER1C_PAYLOAD_BELOW__$/ {print NR + 1; exit 0}' "$0")"
[ -n "$ARCHIVE_LINE" ] || fail_gui "Не найден payload внутри установщика."

tail -n +"$ARCHIVE_LINE" "$0" | tar -xzf - -C "$TMP"

[ -d "$TMP/app" ] || fail_gui "Payload поврежден: нет папки app."
[ -f "$TMP/root-install.sh" ] || fail_gui "Payload поврежден: нет root-install.sh."

chmod +x "$TMP/root-install.sh"

if ! pkexec /bin/bash "$TMP/root-install.sh" "$TMP/app"; then
    fail_gui "Установка не завершена.\n\nПроверь последний лог:\n/tmp/updater1c-linux-install-*.log"
fi

info_gui "Установка завершена.\n\nЗапуск из меню Ubuntu:\n${APP_NAME}\n\nЗапуск из терминала:\nupdater1c-linux\n\nУдаление:\n/opt/updater1c-linux/uninstall-updater1c-linux.sh"

if ask_gui "Запустить ${APP_NAME} сейчас?"; then
    nohup updater1c-linux >/tmp/updater1c-linux-launch.log 2>&1 &
fi

exit 0

__UPDATER1C_PAYLOAD_BELOW__
RUNNER

tar -czf "$WORK/payload.tar.gz" -C "$PAYLOAD_DIR" .
cat "$WORK/payload.tar.gz" >> "$OUT"

chmod +x "$OUT"

echo
echo "== DONE =="
echo "Installer:"
echo "$OUT"
echo
ls -lh "$OUT"
