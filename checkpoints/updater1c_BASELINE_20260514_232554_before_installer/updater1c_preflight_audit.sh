#!/usr/bin/env bash
set -u

ROOT="${1:-$PWD}"
REPORT_DIR="$ROOT/reports"
mkdir -p "$REPORT_DIR"

REPORT="$REPORT_DIR/updater1c_preflight_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee "$REPORT") 2>&1

section() {
    echo
    echo "============================================================"
    echo "$1"
    echo "============================================================"
}

section "SYSTEM"
date
echo "USER=$USER"
echo "HOME=$HOME"
echo "PWD=$PWD"
echo "ROOT=$ROOT"
echo "SESSION=${XDG_SESSION_TYPE:-}"
echo "DESKTOP=${XDG_CURRENT_DESKTOP:-}"
echo "SHELL=$SHELL"
lsb_release -a 2>/dev/null || cat /etc/os-release

section "PROJECT TOP LEVEL"
ls -la "$ROOT"

section "PROJECT TREE"
find "$ROOT" -maxdepth 3 \
    \( -path "$ROOT/.git" -o -path "$ROOT/__pycache__" -o -path "$ROOT/.venv" -o -path "$ROOT/venv" \) -prune -o \
    -print | sort | sed "s|$ROOT|.|" | head -500

section "IMPORTANT FILES CHECK"

for p in \
    "main.py" \
    "app.py" \
    "requirements.txt" \
    "pyproject.toml" \
    "setup.py" \
    "README.md" \
    "install.sh" \
    "uninstall.sh" \
    "make_installer.sh" \
    "dist" \
    "legacy" \
    "backups" \
    "checkpoints" \
    "reports"
do
    if [ -e "$ROOT/$p" ]; then
        echo "OK   $p"
    else
        echo "MISS $p"
    fi
done

section "PYTHON FILES"
find "$ROOT" -type f -name "*.py" \
    -not -path "*/.venv/*" \
    -not -path "*/venv/*" \
    -not -path "*/__pycache__/*" \
    -printf "%p\n" | sort

section "SHELL FILES"
find "$ROOT" -type f \( -name "*.sh" -o -name "*.run" \) \
    -not -path "*/.venv/*" \
    -not -path "*/venv/*" \
    -printf "%p\n" | sort

section "DESKTOP FILES"
find "$ROOT" -type f -name "*.desktop" -printf "%p\n" -exec sed -n '1,120p' {} \;

section "PYTHON VERSION AND IMPORTS"
python3 --version

python3 - <<'PY'
mods = [
    "tkinter",
    "sqlite3",
    "json",
    "subprocess",
    "pathlib",
    "venv",
]

for mod in mods:
    try:
        __import__(mod)
        print(f"OK   import {mod}")
    except Exception as e:
        print(f"FAIL import {mod}: {type(e).__name__}: {e}")

optional = [
    "gi",
    "requests",
    "cryptography",
    "bs4",
    "lxml",
    "yaml",
    "PyQt5",
    "PySide6",
]

for mod in optional:
    try:
        __import__(mod)
        print(f"OK   optional import {mod}")
    except Exception as e:
        print(f"MISS optional import {mod}: {type(e).__name__}: {e}")

try:
    import gi
    for lib, ver in [
        ("Gtk", "3.0"),
        ("Gtk", "4.0"),
        ("Adw", "1"),
    ]:
        try:
            gi.require_version(lib, ver)
            print(f"OK   gi.require_version {lib} {ver}")
        except Exception as e:
            print(f"MISS gi.require_version {lib} {ver}: {e}")
except Exception:
    pass
PY

section "PYTHON SYNTAX CHECK"
python3 -m compileall -q "$ROOT" || echo "PYTHON COMPILE ERRORS FOUND"

section "REQUIREMENTS"
if [ -f "$ROOT/requirements.txt" ]; then
    cat "$ROOT/requirements.txt"
else
    echo "No requirements.txt"
fi

section "APT / SYSTEM COMMANDS CHECK"

for c in \
    python3 \
    pip3 \
    bash \
    pkexec \
    zenity \
    yad \
    desktop-file-validate \
    update-desktop-database \
    gtk-update-icon-cache \
    tar \
    unzip \
    curl \
    wget \
    xdg-open \
    xdg-desktop-menu \
    xdg-desktop-icon
do
    if command -v "$c" >/dev/null 2>&1; then
        echo "OK   $c -> $(command -v "$c")"
    else
        echo "MISS $c"
    fi
done

section "1C PLATFORM CHECK"

echo "Common launcher:"
ls -la /opt/1cv8/common/1cestart 2>/dev/null || echo "MISS /opt/1cv8/common/1cestart"

echo
echo "Installed 1C platform binaries:"
find /opt/1cv8 -maxdepth 4 -type f \( -name "1cv8" -o -name "1cv8c" -o -name "1cestart" \) 2>/dev/null | sort

echo
echo "Platform dirs:"
find /opt/1cv8/x86_64 -maxdepth 1 -mindepth 1 -type d 2>/dev/null | sort || true

section "PROJECT REFERENCES TO 1C"
grep -RIn \
    --exclude-dir=.git \
    --exclude-dir=.venv \
    --exclude-dir=venv \
    --exclude-dir=__pycache__ \
    -E "1cv8|1cestart|DESIGNER|ENTERPRISE|DumpConfigToFiles|/Execute|/Out|/F|/S|/WS|CU1C|Метаданные|Infobase|ibases|base" \
    "$ROOT" 2>/dev/null | head -300 || true

section "GROUPS / IMPORT REFERENCES"
grep -RIn \
    --exclude-dir=.git \
    --exclude-dir=.venv \
    --exclude-dir=venv \
    --exclude-dir=__pycache__ \
    -E "group|groups|Группа|Группы|Без группы|import|Импорт|ibases|bases|Базы|Список" \
    "$ROOT" 2>/dev/null | head -300 || true

section "CONFIG / DATA FILES"
find "$ROOT" -type f \( \
    -name "*.json" -o \
    -name "*.yaml" -o \
    -name "*.yml" -o \
    -name "*.ini" -o \
    -name "*.conf" -o \
    -name "*.db" -o \
    -name "*.sqlite" \
\) -printf "%p\n" | sort

section "POSSIBLE ENTRYPOINTS"
grep -RIn \
    --exclude-dir=.git \
    --exclude-dir=.venv \
    --exclude-dir=venv \
    --exclude-dir=__pycache__ \
    -E "if __name__ == .__main__.|Tk\(|Application\(|Gtk.Application|QApplication|argparse|click.command|def main" \
    "$ROOT" 2>/dev/null | head -300 || true

section "INSTALLED APP CHECK"

for p in \
    "/opt/updater1c-linux" \
    "/opt/updater1c" \
    "/usr/bin/updater1c-linux" \
    "/usr/bin/updater1c" \
    "/usr/share/applications/updater1c-linux.desktop" \
    "/usr/share/applications/updater1c.desktop"
do
    if [ -e "$p" ]; then
        echo "OK   $p"
        ls -la "$p"
    else
        echo "MISS $p"
    fi
done

section "AUTOSTART CHECK"
find "$HOME/.config/autostart" /etc/xdg/autostart \
    -maxdepth 1 \
    \( -iname "*updater*1c*.desktop" -o -iname "*1c*update*.desktop" \) \
    -print \
    -exec sed -n '1,160p' {} \; 2>/dev/null || true

section "RUNNING PROCESSES"
pgrep -af "updater1c|1c-update|update.*1c|python.*1c|python.*updater" || true

section "REPORT SAVED"
echo "$REPORT"
