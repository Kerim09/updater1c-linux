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

exec "$PY" "$APP_DIR/main.py" "$@"
