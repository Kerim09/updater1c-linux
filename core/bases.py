from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class BaseItem:
    name: str
    path: str
    kind: str = "file"
    user: str = ""
    platform_version: str = "8.3"


def scan_file_bases(bases_dir: str) -> list[BaseItem]:
    root = Path(bases_dir).expanduser()
    result: list[BaseItem] = []

    if not root.exists():
        return result

    for item in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not item.is_dir():
            continue

        if (item / "1Cv8.1CD").exists():
            result.append(BaseItem(name=item.name, path=str(item), kind="file"))

    return result
