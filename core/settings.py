from __future__ import annotations

import json
from pathlib import Path

from .runtime import USER_CONFIG_DIR, SETTINGS_FILE


class Settings:
    def __init__(self):
        self.data = {
            "bases_dir": "/mnt/Data/bases",
            "updates_dir": str(Path.home() / "1c-updates"),
            "reports_dir": str(Path.home() / "1c-update-reports"),
            "backups_dir": str(Path.home() / "1c-backups"),
            "platform_path": "",
            "oneget_path": "",
            "its_login": "",
        }

    def load(self) -> None:
        try:
            if SETTINGS_FILE.exists():
                loaded = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self.data.update(loaded)
        except Exception:
            pass

    def save(self) -> None:
        USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get(self, key: str, default: str = "") -> str:
        return str(self.data.get(key, default))

    def set(self, key: str, value: str) -> None:
        self.data[key] = value
