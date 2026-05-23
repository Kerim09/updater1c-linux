#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

VENV="$APP_DIR/.venv"
PY="$VENV/bin/python"

if [ ! -x "$PY" ]; then
  echo "Создаю виртуальное окружение: $VENV"
  python3 -m venv "$VENV"
fi

echo "Использую Python: $PY"

"$PY" -m pip install --upgrade pip setuptools wheel

if [ -f requirements.txt ]; then
  "$PY" -m pip install -r requirements.txt
else
  "$PY" -m pip install PySide6 requests
fi

# На Ubuntu/GNOME/Wayland PySide6 иногда падает на xdg-desktop-portal:
# qt.qpa.services: Failed to register with host portal
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"
export QT_NO_USE_PORTAL="${QT_NO_USE_PORTAL:-1}"

exec -a io.github.kerim1c.updater1clinux "$PY" "$APP_DIR/main.py" "$@"
