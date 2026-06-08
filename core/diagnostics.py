from __future__ import annotations

import os
import sys

from .runtime import run_command, which


def collect_diagnostics() -> list[str]:
    lines: list[str] = []

    lines.append("=== Диагностика окружения ===")
    lines.append(f"Python: {sys.version.split()[0]}")
    lines.append(f"Python executable: {sys.executable}")
    lines.append(f"DISPLAY={os.environ.get('DISPLAY', '')}")
    lines.append(f"XDG_SESSION_TYPE={os.environ.get('XDG_SESSION_TYPE', '')}")
    lines.append(f"GDK_BACKEND={os.environ.get('GDK_BACKEND', '')}")
    lines.append("")

    for cmd in ["1cestart", "1cv8", "ibcmd", "python3", "apt", "xdg-open", "xvfb-run"]:
        lines.append(f"{cmd}: {which(cmd) or 'не найден'}")

    lines.append("")
    rc, out = run_command(["bash", "-lc", "lsb_release -a 2>/dev/null || cat /etc/os-release"])
    lines.append(out.strip())

    lines.append("")
    lines.append("=== Проверка Python-модулей ===")
    for module in ["gi", "keyring", "secretstorage"]:
        rc, out = run_command(["python3", "-c", f"import {module}; print('OK')"])
        if rc == 0:
            lines.append(f"{module}: OK")
        else:
            lines.append(f"{module}: ERROR")
            if out.strip():
                lines.append(out.strip())

    return lines


def find_1c_platforms() -> list[str]:
    cmd = r"""
find /opt/1cv8 /opt/1C /usr/bin /usr/local/bin \
  -maxdepth 5 \
  \( -type f -o -type l \) \
  \( -name '1cv8' -o -name '1cestart' -o -name 'ibcmd' \) \
  -printf '%p\n' 2>/dev/null | sort
"""
    rc, out = run_command(["bash", "-lc", cmd])
    return [x.strip() for x in out.splitlines() if x.strip()]
