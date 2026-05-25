from __future__ import annotations

import builtins
import os
import re
import shutil
import uuid
from pathlib import Path
from datetime import datetime

_SECTION_RE = re.compile(r"^\[(.+)\]$")


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode("utf-8", errors="replace")


def _parse(data: bytes | str):
    text = _decode(data) if isinstance(data, bytes) else data
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    sections = []
    current = None
    skip_orphan = False

    for raw in text.split("\n"):
        line = raw.strip()

        if not line:
            continue

        if line == "]":
            skip_orphan = True
            continue

        m = _SECTION_RE.match(line)
        if m:
            skip_orphan = False
            name = m.group(1).strip()
            if not name or name == "]":
                current = None
                continue
            current = {"name": name, "kv": {}, "order": []}
            sections.append(current)
            continue

        if skip_orphan:
            continue

        if current is None or "=" not in raw:
            continue

        key, value = raw.split("=", 1)
        key = key.strip()
        if not key:
            continue

        if key not in current["kv"]:
            current["order"].append(key)
        current["kv"][key] = value.strip()

    return sections


def _max_order(sections) -> int:
    result = 0
    for section in sections:
        for key in ("OrderInList", "OrderInTree"):
            try:
                result = max(result, int(section["kv"].get(key, "0")))
            except Exception:
                pass
    return result


def _repair_bytes(new_bytes: bytes, old_bytes: bytes | None = None) -> bytes:
    old_sections = _parse(old_bytes or new_bytes)
    new_sections = _parse(new_bytes)

    old_by_name = {s["name"].casefold(): s for s in old_sections}
    new_by_name = {s["name"].casefold(): s for s in new_sections}

    result = []

    for old in old_sections:
        new = new_by_name.get(old["name"].casefold())

        merged = {
            "name": old["name"],
            "kv": dict(old["kv"]),
            "order": list(old["order"]),
        }

        if new:
            for key in new["order"]:
                if key in ("ID", "OrderInList", "OrderInTree", "Folder", "External") and key in merged["kv"]:
                    continue
                if key not in merged["order"]:
                    merged["order"].append(key)
                merged["kv"][key] = new["kv"][key]

        result.append(merged)

    next_order = _max_order(result)

    for new in new_sections:
        if new["name"].casefold() in old_by_name:
            continue
        if not new["name"] or new["name"] == "]":
            continue

        if not new["kv"].get("ID"):
            new["kv"]["ID"] = str(uuid.uuid4())
            new["order"].append("ID")

        if not new["kv"].get("Folder"):
            new["kv"]["Folder"] = "/"
            new["order"].append("Folder")

        if not new["kv"].get("External"):
            new["kv"]["External"] = "0"
            new["order"].append("External")

        for key in ("OrderInList", "OrderInTree"):
            try:
                value = int(new["kv"].get(key, "0"))
            except Exception:
                value = 0
            if value <= 0:
                next_order += 16384
                new["kv"][key] = str(next_order)
                new["order"].append(key)

        result.append(new)

    preferred = [
        "Connect", "ID", "OrderInList", "Folder", "OrderInTree",
        "External", "App", "WA", "Version", "DefaultVersion",
        "ClientConnectionSpeed", "AdditionalParameters",
    ]

    lines = []

    for section in result:
        name = section["name"]
        if not name or name == "]":
            continue

        lines.append(f"[{name}]")

        keys = [key for key in preferred if key in section["kv"]]
        keys += [key for key in section["order"] if key not in keys]

        for key in keys:
            lines.append(f'{key}={section["kv"][key]}')

        lines.append("")

    return ("\n".join(lines).rstrip() + "\n").encode("utf-8")


def repair_file(path, old_snapshot: bytes | None = None, make_backup: bool = True) -> bool:
    p = Path(path).expanduser()

    if p.name != "ibases.v8i" or not p.exists():
        return False

    before = p.read_bytes()

    old = old_snapshot
    bak = p.with_suffix(p.suffix + ".bak")

    if old is None and bak.exists():
        old = bak.read_bytes()

    fixed = _repair_bytes(before, old)

    if fixed == before:
        return False

    if make_backup:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy2(p, p.with_name(p.name + f".broken-before-guard-{stamp}"))

    tmp = p.with_name(p.name + ".tmp-guard")
    tmp.write_bytes(fixed)
    os.replace(tmp, p)

    return True


def install_ibases_v8i_guard() -> None:
    if getattr(install_ibases_v8i_guard, "_installed", False):
        return

    install_ibases_v8i_guard._installed = True

    default_path = Path.home() / ".1cv8/1C/1CEStart/ibases.v8i"
    snapshots = {}

    if default_path.exists():
        snapshots[str(default_path)] = default_path.read_bytes()

    original_write_text = Path.write_text
    original_write_bytes = Path.write_bytes
    original_open = builtins.open
    original_replace = os.replace
    original_rename = os.rename
    original_move = shutil.move

    def is_ibases(path) -> bool:
        try:
            return Path(path).name == "ibases.v8i"
        except Exception:
            return False

    def after_write(path) -> None:
        if is_ibases(path):
            repair_file(path, snapshots.get(str(Path(path).expanduser())), make_backup=True)

    def write_text_guard(self, *args, **kwargs):
        result = original_write_text(self, *args, **kwargs)
        after_write(self)
        return result

    def write_bytes_guard(self, *args, **kwargs):
        result = original_write_bytes(self, *args, **kwargs)
        after_write(self)
        return result

    class FileCloseGuard:
        def __init__(self, file_obj, path):
            self._file_obj = file_obj
            self._path = path

        def __getattr__(self, name):
            return getattr(self._file_obj, name)

        def __iter__(self):
            return iter(self._file_obj)

        def __enter__(self):
            self._file_obj.__enter__()
            return self

        def __exit__(self, exc_type, exc, tb):
            result = self._file_obj.__exit__(exc_type, exc, tb)
            after_write(self._path)
            return result

        def close(self):
            result = self._file_obj.close()
            after_write(self._path)
            return result

    def open_guard(file, mode="r", *args, **kwargs):
        file_obj = original_open(file, mode, *args, **kwargs)

        if is_ibases(file) and any(x in mode for x in ("w", "a", "x", "+")):
            return FileCloseGuard(file_obj, file)

        return file_obj

    def replace_guard(src, dst, *args, **kwargs):
        result = original_replace(src, dst, *args, **kwargs)
        after_write(dst)
        return result

    def rename_guard(src, dst, *args, **kwargs):
        result = original_rename(src, dst, *args, **kwargs)
        after_write(dst)
        return result

    def move_guard(src, dst, *args, **kwargs):
        result = original_move(src, dst, *args, **kwargs)
        after_write(dst)
        return result

    Path.write_text = write_text_guard
    Path.write_bytes = write_bytes_guard
    builtins.open = open_guard
    os.replace = replace_guard
    os.rename = rename_guard
    shutil.move = move_guard


if __name__ == "__main__":
    target = Path.home() / ".1cv8/1C/1CEStart/ibases.v8i"
    print("REPAIRED=yes" if repair_file(target, make_backup=True) else "REPAIRED=no")
