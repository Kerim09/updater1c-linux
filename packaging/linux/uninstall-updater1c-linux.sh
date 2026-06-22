#!/usr/bin/env bash
set -Eeuo pipefail

APP_ID="updater1c-linux"
APP_NAME="Обновлятор 1C Linux"
INSTALL_DIR="/opt/${APP_ID}"
LOG="/tmp/${APP_ID}-uninstall-$(date +%Y%m%d_%H%M%S).log"
DELETE_SETTINGS=1
YES=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --yes|-y|--confirmed) YES=1 ;;
    --keep-settings|--keep-user-data) DELETE_SETTINGS=0 ;;
    --delete-settings|--delete-user-data) DELETE_SETTINGS=1 ;;
    *) ;;
  esac
  shift || true
done

ask_confirm() {
  local text="Удалить ${APP_NAME}?\n\nБудут удалены:\n- ${INSTALL_DIR}\n- команды запуска /usr/local/bin/${APP_ID} и /usr/bin/${APP_ID}\n- системные и пользовательские ярлыки приложения\n- иконки приложения"
  if [ "$DELETE_SETTINGS" = "1" ]; then
    text="${text}\n- настройки приложения в ~/.config, ~/.local/share и ~/.cache для пользователей"
  else
    text="${text}\n\nНастройки пользователей будут сохранены."
  fi
  text="${text}\n\nБазы 1С, резервные копии и отчеты в /mnt/DataStore не удаляются."

  if command -v zenity >/dev/null 2>&1 && [ -n "${DISPLAY:-}" ]; then
    zenity --question --title="Удаление ${APP_NAME}" --width=560 --text="$text"
  else
    printf '%b\n' "$text"
    echo
    echo "Продолжить? [y/N]"
    read -r ans
    [[ "$ans" == "y" || "$ans" == "Y" ]]
  fi
}

cleanup_current_user_keyring() {
  [ "$DELETE_SETTINGS" = "1" ] || return 0
  command -v python3 >/dev/null 2>&1 || return 0
  python3 - <<'PYKEYRING' || true
import json
import os
from pathlib import Path

try:
    import keyring
except Exception:
    raise SystemExit

home = Path.home()
accounts = {"its_password"}
services = {"updater1c-linux", "Обновлятор 1С", "updater1c"}

cfg_dir = home / ".config" / "updater1c-linux"
for name in ("config.json", "settings.json", "app_config.json"):
    p = cfg_dir / name
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        continue

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if str(k) in ("password_secret_id", "secret_id") and isinstance(v, str) and v.strip():
                    accounts.add(v.strip())
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(data)

for service in sorted(services):
    for account in sorted(accounts):
        try:
            keyring.delete_password(service, account)
        except Exception:
            pass
PYKEYRING
}

if [ "$(id -u)" -ne 0 ]; then
  [ "$YES" = "1" ] || ask_confirm || exit 0
  cleanup_current_user_keyring
  if command -v pkexec >/dev/null 2>&1; then
    exec pkexec env DISPLAY="${DISPLAY:-}" XAUTHORITY="${XAUTHORITY:-}" \
      /bin/bash "$0" --yes "$([ "$DELETE_SETTINGS" = "1" ] && echo --delete-settings || echo --keep-settings)"
  fi
  exec sudo /bin/bash "$0" --yes "$([ "$DELETE_SETTINGS" = "1" ] && echo --delete-settings || echo --keep-settings)"
fi

exec > >(tee -a "$LOG") 2>&1

echo "=== Удаление ${APP_NAME} ==="
echo "Дата: $(date)"
echo "Лог: $LOG"
echo "DELETE_SETTINGS=$DELETE_SETTINGS"

echo
echo "=== Остановка запущенного приложения ==="
pkill -f "/opt/updater1c-linux/gtk_port/updater1c_gtk.py" 2>/dev/null || true
pkill -f "/opt/updater1c-linux/main.py" 2>/dev/null || true
sleep 1

echo
echo "=== Удаление системных файлов ==="
rm -f \
  /usr/local/bin/updater1c-linux \
  /usr/bin/updater1c-linux \
  /usr/local/bin/updater1c-gtk-gui \
  /usr/bin/updater1c-gtk-gui

rm -f \
  /usr/share/applications/updater1c-linux.desktop \
  /usr/share/applications/io.github.kerim1c.updater1clinux.desktop \
  /usr/share/applications/io.github.kerim1c.updater1clinux.gtk.desktop

rm -f \
  /usr/share/pixmaps/updater1c-linux.png \
  /usr/share/pixmaps/updater1c.png \
  /usr/share/icons/hicolor/scalable/apps/updater1c-linux.svg \
  /usr/share/icons/hicolor/scalable/apps/io.github.kerim1c.updater1clinux.svg \
  /usr/share/icons/hicolor/scalable/apps/io.github.kerim1c.updater1clinux.gtk.svg \
  /usr/share/icons/hicolor/256x256/apps/updater1c-linux.png \
  /usr/share/icons/hicolor/512x512/apps/updater1c-linux.png \
  /usr/share/icons/hicolor/256x256/apps/io.github.kerim1c.updater1clinux.png \
  /usr/share/icons/hicolor/512x512/apps/io.github.kerim1c.updater1clinux.png \
  /usr/share/icons/hicolor/256x256/apps/io.github.kerim1c.updater1clinux.gtk.png \
  /usr/share/icons/hicolor/512x512/apps/io.github.kerim1c.updater1clinux.gtk.png

rm -rf "$INSTALL_DIR"

echo
echo "=== Удаление пользовательских ярлыков и настроек ==="
for home in /home/* /root; do
  [ -d "$home" ] || continue
  rm -f \
    "$home/.local/share/applications/updater1c-linux.desktop" \
    "$home/.local/share/applications/io.github.kerim1c.updater1clinux.desktop" \
    "$home/.local/share/applications/io.github.kerim1c.updater1clinux.gtk.desktop"

  rm -f \
    "$home/.local/share/icons/hicolor/256x256/apps/updater1c-linux.png" \
    "$home/.local/share/icons/hicolor/512x512/apps/updater1c-linux.png" \
    "$home/.local/share/icons/hicolor/scalable/apps/updater1c-linux.svg" 2>/dev/null || true

  if [ "$DELETE_SETTINGS" = "1" ]; then
    rm -rf \
      "$home/.config/updater1c-linux" \
      "$home/.local/share/updater1c-linux" \
      "$home/.cache/updater1c-linux"
  fi
done

echo
echo "=== Обновление кэшей ярлыков/иконок ==="
update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor >/dev/null 2>&1 || true

echo
echo "Удаление завершено."
echo "Базы 1С, резервные копии и отчеты не удалялись."
echo "Лог: $LOG"
