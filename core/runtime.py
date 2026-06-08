from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


APP_ID = "io.github.kerim1c.updater1clinux.gtk"
APP_NAME = "Обновлятор 1С Linux GTK"

OPT_DIR = Path("/opt/updater1c-linux")
APP_DIR = OPT_DIR / "app"
ICON_PATH = OPT_DIR / "icons" / "updater1c.png"

USER_CONFIG_DIR = Path.home() / ".config" / "updater1c-linux"
SETTINGS_FILE = USER_CONFIG_DIR / "settings-gtk.json"


def which(command: str) -> str:
    return shutil.which(command) or ""


def run_command(command: list[str], cwd: str | Path | None = None) -> tuple[int, str]:
    try:
        p = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        return p.returncode, p.stdout or ""
    except Exception as e:
        return 999, f"{type(e).__name__}: {e}"


def is_wayland() -> bool:
    return os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"


def ensure_dir(path: str | Path) -> Path:
    p = Path(path).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p
