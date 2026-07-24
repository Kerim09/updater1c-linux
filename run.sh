#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
APP_DIR="$(dirname "$SCRIPT_PATH")"

PYTHON="/usr/bin/python3"
MAIN="$APP_DIR/gtk_port/updater1c_gtk.py"

STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/updater1c-linux"
LOG_FILE="$STATE_DIR/launch.log"

mkdir -p "$STATE_DIR"

fail() {
    echo "ОШИБКА: $*" >&2
    echo "Журнал запуска: $LOG_FILE" >&2
    exit 1
}

[ -x "$PYTHON" ] \
    || fail "не найден системный Python: $PYTHON"

[ -f "$MAIN" ] \
    || fail "не найден GTK-файл приложения: $MAIN"

if ! "$PYTHON" - <<'PY' >/dev/null 2>&1
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
PY
then
    fail "не установлены Python GI / GTK 3"
fi

export PYTHONUNBUFFERED=1
export PYTHONPATH="$APP_DIR/gtk_port${PYTHONPATH:+:$PYTHONPATH}"

cd "$APP_DIR"

exec "$PYTHON" "$MAIN" "$@" >>"$LOG_FILE" 2>&1
