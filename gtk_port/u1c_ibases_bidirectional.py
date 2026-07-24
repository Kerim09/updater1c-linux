#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import copy
import os
import re
import shutil
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk


PATCH_ID = "UPDATER1C_IBASES_BIDIRECTIONAL_20260710_V2"


def _log(window, message: str) -> None:
    try:
        window._append_log(str(message))
    except Exception:
        print(str(message))


def _show_error(window, title: str, message: str) -> None:
    try:
        dialog = Gtk.MessageDialog(
            transient_for=window,
            flags=0,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text=title,
        )
        dialog.format_secondary_text(str(message))
        dialog.run()
        dialog.destroy()
    except Exception:
        _log(window, f"{title}: {message}")


def _show_info(window, title: str, message: str) -> None:
    try:
        dialog = Gtk.MessageDialog(
            transient_for=window,
            flags=0,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text=title,
        )
        dialog.format_secondary_text(str(message))
        dialog.run()
        dialog.destroy()
    except Exception:
        _log(window, f"{title}: {message}")


def _confirm_delete(window, name: str, connect: str) -> bool:
    dialog = Gtk.MessageDialog(
        transient_for=window,
        flags=0,
        message_type=Gtk.MessageType.QUESTION,
        buttons=Gtk.ButtonsType.YES_NO,
        text="Удалить базу из списков?",
    )
    dialog.format_secondary_text(
        f"База: {name or '-'}\n"
        f"Подключение: {connect or '-'}\n\n"
        "Запись будет удалена из Обновлятора и из списка запуска 1С.\n"
        "Файлы базы и сама база данных удаляться не будут."
    )
    try:
        dialog.set_default_response(Gtk.ResponseType.NO)
    except Exception:
        pass
    response = dialog.run()
    dialog.destroy()
    return response == Gtk.ResponseType.YES


def _is_group(base) -> bool:
    if not isinstance(base, dict):
        return True
    kind = str(base.get("kind") or base.get("type") or "").strip().lower()
    return bool(base.get("is_group")) or kind == "group"


def _v8i_quoted_value(value: str) -> str:
    value = str(value or "").strip()
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        value = value[1:-1]
    return value.replace('""', '"')


def _extract_connect_value(connect: str, key: str) -> str:
    connect = str(connect or "")
    match = re.search(
        rf'(?i)(?:^|;)\s*{re.escape(key)}\s*=\s*"((?:[^"]|"")*)"',
        connect,
    )
    if match:
        return match.group(1).replace('""', '"').strip()

    match = re.search(
        rf"(?i)(?:^|;)\s*{re.escape(key)}\s*=\s*([^;]*)",
        connect,
    )
    if match:
        return _v8i_quoted_value(match.group(1))
    return ""


def _kind_connect(base: dict) -> tuple[str, str]:
    kind = str(base.get("kind") or base.get("type") or "file").strip().lower()
    connect = str(base.get("connect") or "").strip()
    low = connect.lower()

    if low.startswith("file="):
        return "file", _extract_connect_value(connect, "File")

    if "srvr=" in low and "ref=" in low:
        server = _extract_connect_value(connect, "Srvr")
        database = _extract_connect_value(connect, "Ref")
        return "server", f"{server}\\{database}" if server and database else connect

    if low.startswith(("ws=", "wsurl=", "web=")):
        for key in ("WS", "WsUrl", "Web"):
            value = _extract_connect_value(connect, key)
            if value:
                return "web", value

    if low.startswith(("http://", "https://")):
        return "web", connect

    if kind in ("filesystem", "файловая"):
        kind = "file"

    return kind or "file", connect


def _sync_key(base: dict):
    if not isinstance(base, dict) or _is_group(base):
        return None

    kind, connect = _kind_connect(base)
    kind = str(kind or "file").strip().lower()
    connect = str(connect or "").strip()

    if kind == "file":
        try:
            connect = os.path.normpath(str(Path(connect).expanduser()))
        except Exception:
            connect = os.path.normpath(connect)
    elif kind == "server":
        connect = connect.replace("/", "\\").strip("\\")
    elif kind == "web":
        connect = connect.rstrip("/")

    return kind, connect.casefold()


def _detect_encoding(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"

    for encoding in ("utf-8", "cp1251"):
        try:
            data.decode(encoding)
            return encoding
        except Exception:
            pass
    return "utf-8"


def _read_document(path: Path) -> dict:
    path = Path(path)
    if not path.exists():
        return {
            "encoding": "utf-8",
            "newline": "\n",
            "preamble": [],
            "sections": [],
        }

    raw = path.read_bytes()
    encoding = _detect_encoding(raw)
    text = raw.decode(encoding, errors="replace")
    newline = "\r\n" if "\r\n" in text else "\n"

    preamble: list[str] = []
    sections: list[dict] = []
    current = None

    for line in text.splitlines():
        header = re.match(r"^\s*\[(.*)]\s*$", line)
        if header:
            current = {
                "name": header.group(1).strip(),
                "lines": [line],
            }
            sections.append(current)
            continue

        if current is None:
            preamble.append(line)
        else:
            current["lines"].append(line)

    return {
        "encoding": encoding,
        "newline": newline,
        "preamble": preamble,
        "sections": sections,
    }


def _section_values(section: dict) -> dict:
    result = {}
    for line in section.get("lines") or []:
        text = str(line).strip()
        if not text or text.startswith(("[", ";", "#")) or "=" not in text:
            continue
        key, value = text.split("=", 1)
        result[key.strip().casefold()] = _v8i_quoted_value(value.strip())
    return result


def _section_key(section: dict):
    values = _section_values(section)
    connect_raw = str(values.get("connect") or "").strip()
    if not connect_raw:
        return None

    low = connect_raw.lower()
    if low.startswith("file="):
        base = {
            "kind": "file",
            "connect": _extract_connect_value(connect_raw, "File"),
        }
    elif "srvr=" in low and "ref=" in low:
        server = _extract_connect_value(connect_raw, "Srvr")
        database = _extract_connect_value(connect_raw, "Ref")
        base = {
            "kind": "server",
            "connect": f"{server}\\{database}",
        }
    elif low.startswith(("ws=", "wsurl=", "web=")):
        url = (
            _extract_connect_value(connect_raw, "WS")
            or _extract_connect_value(connect_raw, "WsUrl")
            or _extract_connect_value(connect_raw, "Web")
        )
        base = {"kind": "web", "connect": url}
    else:
        base = {"kind": "file", "connect": connect_raw}

    return _sync_key(base)


def _escape(value) -> str:
    return (
        str(value or "")
        .replace("\r", " ")
        .replace("\n", " ")
        .replace('"', '""')
        .strip()
    )


def _connect_string(base: dict) -> str:
    kind, connect = _kind_connect(base)
    kind = str(kind or "file").strip().lower()
    connect = str(connect or "").strip()
    user = str(
        base.get("user")
        or base.get("login")
        or base.get("username")
        or ""
    ).strip()

    if kind == "server":
        if "\\" in connect:
            server, database = connect.split("\\", 1)
        elif "/" in connect and not connect.lower().startswith(("http://", "https://")):
            server, database = connect.split("/", 1)
        else:
            server = str(base.get("server_name") or connect).strip()
            database = str(base.get("db_name") or "").strip()

        if not server or not database:
            raise RuntimeError(
                "Для серверной базы должны быть заполнены сервер и имя информационной базы."
            )
        result = f'Srvr="{_escape(server)}";Ref="{_escape(database)}";'

    elif kind == "web":
        if not connect:
            raise RuntimeError("Для web-базы не заполнен URL.")
        result = f'ws="{_escape(connect)}";'

    else:
        if not connect:
            raise RuntimeError("Для файловой базы не заполнен путь.")
        result = f'File="{_escape(str(Path(connect).expanduser()))}";'

    if user:
        result += f'Usr="{_escape(user)}";'

    return result


def _replace_value(lines: list[str], key: str, value: str | None) -> list[str]:
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=", re.I)
    result = []
    replaced = False

    for line in lines:
        if pattern.match(str(line)):
            if value is not None and not replaced:
                result.append(f"{key}={value}")
                replaced = True
            continue
        result.append(line)

    if value is not None and not replaced:
        insert_at = 1 if result and str(result[0]).lstrip().startswith("[") else 0
        result.insert(insert_at, f"{key}={value}")

    return result


def _section_for_base(base: dict, old_section: dict | None = None) -> dict:
    name = str(base.get("name") or "").strip()
    if not name:
        raise RuntimeError("Не заполнено имя базы.")

    name = name.replace("\r", " ").replace("\n", " ").replace("]", ")")
    group = str(base.get("group") or "").strip().strip("/\\")
    if group.casefold() == "без группы":
        group = ""

    connect_value = _connect_string(base)

    if old_section is not None:
        lines = list(old_section.get("lines") or [])
        if lines and str(lines[0]).lstrip().startswith("["):
            lines[0] = f"[{name}]"
        else:
            lines.insert(0, f"[{name}]")
    else:
        lines = [f"[{name}]"]

    lines = _replace_value(lines, "Connect", connect_value)
    lines = _replace_value(lines, "Folder", f'"{_escape(group)}"' if group else None)

    values = _section_values({"lines": lines})
    if "app" not in values:
        lines.append("App=Auto")
    if "version" not in values:
        lines.append("Version=8.3")

    return {"name": name, "lines": lines}


def _render_document(document: dict) -> str:
    newline = document.get("newline") or "\n"
    lines = list(document.get("preamble") or [])

    while lines and lines[-1] == "":
        lines.pop()

    for section in document.get("sections") or []:
        section_lines = list(section.get("lines") or [])
        while section_lines and section_lines[-1] == "":
            section_lines.pop()
        if not section_lines:
            continue
        if lines:
            lines.append("")
        lines.extend(section_lines)

    text = newline.join(lines)
    if text and not text.endswith(newline):
        text += newline
    return text


def _write_document(path: Path, document: dict) -> bool:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    encoding = document.get("encoding") or "utf-8"
    new_bytes = _render_document(document).encode(encoding)
    old_bytes = path.read_bytes() if path.exists() else None

    if old_bytes == new_bytes:
        return False

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    if path.exists():
        backup = path.with_name(path.name + f".updater1c_backup_{timestamp}")
        shutil.copy2(path, backup)

    temporary = path.with_name(path.name + f".tmp.{os.getpid()}")
    try:
        temporary.write_bytes(new_bytes)
        if path.exists():
            try:
                os.chmod(temporary, path.stat().st_mode)
            except Exception:
                pass
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except Exception:
            pass

    try:
        backups = sorted(
            path.parent.glob(path.name + ".updater1c_backup_*"),
            key=lambda item: item.stat().st_mtime_ns,
            reverse=True,
        )
        for old_backup in backups[10:]:
            old_backup.unlink()
    except Exception:
        pass

    return True


def _candidate_paths(window) -> list[Path]:
    result = []
    seen = set()

    try:
        for path in window.ibases_v8i_candidate_paths() or []:
            path = Path(path).expanduser()
            key = str(path)
            if key not in seen:
                seen.add(key)
                result.append(path)
    except Exception:
        pass

    home = Path.home()
    xdg = Path(os.environ.get("XDG_CONFIG_HOME") or (home / ".config"))

    for path in (
        home / ".1C" / "1cestart" / "ibases.v8i",
        home / ".1C" / "1CEStart" / "ibases.v8i",
        home / ".1cv8" / "1C" / "1CEStart" / "ibases.v8i",
        xdg / "1C" / "1CEStart" / "ibases.v8i",
        xdg / "1C" / "1cestart" / "ibases.v8i",
    ):
        key = str(path)
        if key not in seen:
            seen.add(key)
            result.append(path)

    return result


def _primary_path(window, base: dict | None = None, create: bool = False) -> Path:
    if isinstance(base, dict):
        source = str(base.get("_source_v8i") or "").strip()
        if source:
            source_path = Path(source).expanduser()
            if source_path.exists() or create:
                return source_path

    settings = getattr(window, "settings", None)
    if not isinstance(settings, dict):
        settings = {}
        window.settings = settings

    configured = str(settings.get("one_c_ibases_path") or "").strip()
    if configured:
        configured_path = Path(configured).expanduser()
        if configured_path.exists() or create:
            return configured_path

    existing = []
    for path in _candidate_paths(window):
        try:
            if path.is_file():
                existing.append((path.stat().st_mtime_ns, path))
        except Exception:
            pass

    if existing:
        existing.sort(key=lambda item: item[0], reverse=True)
        selected = existing[0][1]
    else:
        selected = Path.home() / ".1C" / "1cestart" / "ibases.v8i"

    settings["one_c_ibases_path"] = str(selected)
    try:
        window.config["settings"] = settings
        window.save_config_safe()
    except Exception:
        pass

    return selected


def _upsert(window, base: dict, old_base: dict | None = None) -> bool:
    path = _primary_path(window, base=old_base or base, create=True)
    document = _read_document(path)
    new_key = _sync_key(base)
    old_key = _sync_key(old_base) if isinstance(old_base, dict) else new_key

    kept = []
    target = None

    for section in document["sections"]:
        section_key = _section_key(section)
        if section_key in (new_key, old_key):
            if target is None:
                target = section
            continue
        kept.append(section)

    kept.append(_section_for_base(base, target))
    document["sections"] = kept
    changed = _write_document(path, document)

    base["_source_v8i"] = str(path)
    base["source"] = "ibases.v8i"
    return changed


def _remove(window, base: dict) -> bool:
    path = _primary_path(window, base=base, create=False)
    if not path.exists():
        return False

    target_key = _sync_key(base)
    document = _read_document(path)
    kept = []
    removed = 0

    for section in document["sections"]:
        if _section_key(section) == target_key:
            removed += 1
        else:
            kept.append(section)

    if not removed:
        return False

    document["sections"] = kept
    _write_document(path, document)
    return True


def _save_and_reload(window) -> None:
    window.config["bases"] = window.bases
    try:
        window.save_config_safe()
    except Exception:
        from pathlib import Path as _Path
        _Path(window.config_path).write_text(
            __import__("json").dumps(window.config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    window._load_bases_tree()
    try:
        window.base_tree.expand_all()
    except Exception:
        pass
    try:
        window.update_bases_status()
    except Exception:
        pass


def _restore(window, bases_snapshot) -> None:
    window.bases = copy.deepcopy(bases_snapshot)
    _save_and_reload(window)


def _sync_delta(window, before: list, after: list) -> tuple[int, int, int]:
    before_real = [item for item in before if isinstance(item, dict) and not _is_group(item)]
    after_real = [item for item in after if isinstance(item, dict) and not _is_group(item)]

    changed = 0
    added = 0
    removed = 0
    count = max(len(before_real), len(after_real))

    for index in range(count):
        old = before_real[index] if index < len(before_real) else None
        new = after_real[index] if index < len(after_real) else None

        if old is None and new is not None:
            _upsert(window, new)
            added += 1
            continue

        if new is None and old is not None:
            if _remove(window, old):
                removed += 1
            continue

        if old == new:
            continue

        if _sync_key(old) == _sync_key(new):
            _upsert(window, new, old_base=old)
            changed += 1
        else:
            _upsert(window, new, old_base=old)
            changed += 1

    return added, changed, removed


def _reconcile_from_v8i(window, automatic: bool) -> tuple[int, int, int]:
    path = _primary_path(window, create=False)
    if not path.exists():
        real_bases = [
            item
            for item in (window.bases or [])
            if isinstance(item, dict) and not _is_group(item)
        ]
        if not real_bases:
            _log(window, f"Файл списка баз 1С пока не создан: {path}")
            return 0, 0, 0

        for item in real_bases:
            _upsert(window, item)

        window.config["bases"] = window.bases
        window.save_config_safe()
        _log(
            window,
            f"Создан список баз 1С: {path}; записано баз: {len(real_bases)}.",
        )
        return 0, len(real_bases), 0

    imported = window.parse_ibases_v8i_file(path)
    unique = {}
    ordered_keys = []

    for item in imported:
        key = _sync_key(item)
        if key is None:
            continue

        kind, connect = _kind_connect(item)
        if kind == "file" and connect and not Path(connect).expanduser().is_dir():
            continue

        if key not in unique:
            ordered_keys.append(key)
        unique[key] = item

    existing_by_key = {}
    groups = []

    for item in window.bases or []:
        if not isinstance(item, dict):
            continue
        if _is_group(item):
            groups.append(item)
            continue
        existing_by_key[_sync_key(item)] = item

    merged = list(groups)
    added = 0
    updated = 0

    controlled_fields = (
        "name",
        "group",
        "kind",
        "type",
        "connect",
        "server_name",
        "db_name",
        "source",
        "_source_v8i",
    )

    for key in ordered_keys:
        imported_item = unique[key]
        old = existing_by_key.get(key)

        if old is None:
            merged.append(imported_item)
            added += 1
            continue

        result = dict(old)
        for field in controlled_fields:
            if field in imported_item:
                result[field] = imported_item.get(field)

        imported_user = str(
            imported_item.get("user")
            or imported_item.get("login")
            or imported_item.get("username")
            or ""
        ).strip()
        if imported_user:
            result["user"] = imported_user
            result["login"] = imported_user
            result["username"] = imported_user

        merged.append(result)
        updated += 1

    removed = sum(1 for key in existing_by_key if key not in unique)

    window.bases = merged
    _save_and_reload(window)

    mode = "Автосинхронизация" if automatic else "Синхронизация"
    _log(
        window,
        f"{mode} со списком баз 1С завершена: "
        f"добавлено {added}, обновлено {updated}, удалено {removed}. "
        f"Файл: {path}",
    )
    return added, updated, removed


def _on_sync_with_1c_list(self, *_):
    _log(self, "=== Синхронизация со списком баз 1С ===")
    try:
        return _reconcile_from_v8i(self, automatic=False)
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        _log(self, f"ОШИБКА синхронизации: {message}")
        _show_error(self, "Ошибка синхронизации", message)
        return None


def _on_delete_selected_base(self, *_):
    try:
        tree_iter = self.selected_base_iter()
        if tree_iter is None:
            return

        if self.is_group_iter(tree_iter):
            _show_info(
                self,
                "Выбрана группа",
                "Клавишей Delete удаляется конкретная база. "
                "Сначала выберите строку базы внутри группы.",
            )
            return

        index = self.selected_base_index()
        if index < 0 or index >= len(self.bases):
            _log(self, "Удаление невозможно: выбранная база не найдена.")
            return

        base = self.bases[index]
        if not isinstance(base, dict) or _is_group(base):
            _log(self, "Удаление невозможно: выбрана не база.")
            return

        name = str(base.get("name") or "")
        _kind, connect = _kind_connect(base)

        if not _confirm_delete(self, name, connect):
            _log(self, f"Удаление отменено: {name}")
            return

        removed_from_v8i = _remove(self, base)

        del self.bases[index]
        _save_and_reload(self)

        try:
            self._loading_base_credentials = True
            self.base_user.set_text("")
            self.base_password.set_text("")
        finally:
            self._loading_base_credentials = False

        if removed_from_v8i:
            _log(self, f"Удалено из Обновлятора и списка баз 1С: {name}")
        else:
            _log(
                self,
                f"Удалено из Обновлятора: {name}. "
                "В основном ibases.v8i соответствующая запись отсутствовала.",
            )

    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        _log(self, f"ОШИБКА удаления: {message}")
        _show_error(self, "Не удалось удалить базу", message)


def _on_key_press(self, _tree, event):
    # UPDATER1C_SHIFT_DELETE_KEY_HANDLER_20260711
    try:
        from gi.repository import Gdk as _u1c_Gdk
        _u1c_key_name = (_u1c_Gdk.keyval_name(event.keyval) or '').lower()
        _u1c_shift_pressed = bool(
            int(event.state)
            & int(_u1c_Gdk.ModifierType.SHIFT_MASK)
        )
        if (
            _u1c_shift_pressed
            and _u1c_key_name in ('delete', 'kp_delete')
        ):
            return _u1c_shift_delete_file_base(self)
    except Exception as _u1c_shift_delete_error:
        try:
            self._append_log(
                'Ошибка Shift+Delete: '
                f'{type(_u1c_shift_delete_error).__name__}: '
                f'{_u1c_shift_delete_error}'
            )
        except Exception:
            pass

    try:
        key_name = (Gdk.keyval_name(event.keyval) or "").lower()
        if key_name in ("delete", "kp_delete"):
            self.on_delete_selected_base_stub()
            return True
    except Exception as exc:
        _log(self, f"Ошибка обработки Delete: {type(exc).__name__}: {exc}")
    return False


def install(main_window_class) -> None:
    if getattr(main_window_class, "_u1c_ibases_patch_id", "") == PATCH_ID:
        return

    original_init = main_window_class.__init__
    original_add = main_window_class.on_add_base
    original_edit = main_window_class.on_edit_base

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)

        try:
            if not getattr(self.base_tree, "_u1c_delete_key_connected", False):
                self.base_tree.connect("key-press-event", self.on_base_tree_key_press)
                self.base_tree._u1c_delete_key_connected = True
        except Exception as exc:
            _log(self, f"Не удалось подключить Delete: {type(exc).__name__}: {exc}")

        def startup_sync():
            try:
                _reconcile_from_v8i(self, automatic=True)
            except Exception as exc:
                _log(
                    self,
                    "Автосинхронизация списка баз 1С не выполнена: "
                    f"{type(exc).__name__}: {exc}",
                )
            return False

        GLib.idle_add(startup_sync)

    def patched_add(self, *args, **kwargs):
        before = copy.deepcopy(list(self.bases or []))
        result = original_add(self, *args, **kwargs)
        after = copy.deepcopy(list(self.bases or []))

        if before == after:
            return result

        try:
            added, changed, removed = _sync_delta(self, before, self.bases)
            self.config["bases"] = self.bases
            self.save_config_safe()
            if added or changed or removed:
                _log(
                    self,
                    "Список баз 1С обновлён после добавления: "
                    f"добавлено {added}, изменено {changed}, удалено {removed}.",
                )
        except Exception as exc:
            _restore(self, before)
            message = f"{type(exc).__name__}: {exc}"
            _log(self, f"Добавление отменено из-за ошибки записи списка 1С: {message}")
            _show_error(
                self,
                "Не удалось зарегистрировать базу в списке 1С",
                message,
            )

        return result

    def patched_edit(self, *args, **kwargs):
        before = copy.deepcopy(list(self.bases or []))
        result = original_edit(self, *args, **kwargs)
        after = copy.deepcopy(list(self.bases or []))

        if before == after:
            return result

        try:
            added, changed, removed = _sync_delta(self, before, self.bases)
            self.config["bases"] = self.bases
            self.save_config_safe()
            if added or changed or removed:
                _log(
                    self,
                    "Список баз 1С обновлён после изменения свойств: "
                    f"добавлено {added}, изменено {changed}, удалено {removed}.",
                )
        except Exception as exc:
            _restore(self, before)
            message = f"{type(exc).__name__}: {exc}"
            _log(self, f"Изменение отменено из-за ошибки записи списка 1С: {message}")
            _show_error(
                self,
                "Не удалось обновить базу в списке 1С",
                message,
            )

        return result

    main_window_class.__init__ = patched_init
    main_window_class.on_add_base = patched_add
    main_window_class.on_edit_base = patched_edit
    main_window_class.on_sync_with_1c_list = _on_sync_with_1c_list
    main_window_class.on_delete_selected_base_stub = _on_delete_selected_base
    main_window_class.on_base_tree_key_press = _on_key_press
    main_window_class._u1c_ibases_patch_id = PATCH_ID

    print(f"{PATCH_ID}: установлен")
# UPDATER1C_SHIFT_DELETE_FILE_BASE_HELPER_20260711
def _u1c_shift_delete_file_base(window):
    """
    Безвозвратно удаляет каталог выбранной файловой базы,
    затем удаляет её запись из Обновлятора и ibases.v8i.
    """
    import os
    import re
    import shutil
    import subprocess
    from pathlib import Path

    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk
    except Exception as exc:
        try:
            window._append_log(
                "Shift+Delete: не удалось загрузить GTK: "
                f"{type(exc).__name__}: {exc}"
            )
        except Exception:
            pass
        return True

    def show_message(message_type, title, details):
        dialog = Gtk.MessageDialog(
            transient_for=window,
            flags=0,
            message_type=message_type,
            buttons=Gtk.ButtonsType.OK,
            text=title,
        )
        dialog.format_secondary_text(details)
        dialog.run()
        dialog.destroy()

    try:
        tree_iter = window.selected_base_iter()
    except Exception:
        tree_iter = None

    if tree_iter is None:
        show_message(
            Gtk.MessageType.INFO,
            "База не выбрана",
            "Выделите файловую базу, которую требуется удалить.",
        )
        return True

    try:
        values = window.selected_base_values() or {}
    except Exception:
        values = {}

    try:
        if values.get("is_group") or window.is_group_iter(tree_iter):
            show_message(
                Gtk.MessageType.INFO,
                "Выбрана группа",
                "Shift+Delete применяется только к конкретной файловой базе.",
            )
            return True
    except Exception:
        pass

    try:
        index = window.selected_base_index()
    except Exception:
        index = -1

    bases = getattr(window, "bases", None)

    if (
        not isinstance(bases, list)
        or index < 0
        or index >= len(bases)
        or not isinstance(bases[index], dict)
    ):
        show_message(
            Gtk.MessageType.ERROR,
            "Не удалось определить базу",
            "Выбранная запись не найдена во внутреннем списке приложения.",
        )
        return True

    base = bases[index]
    name = str(
        base.get("name")
        or values.get("name")
        or "Без имени"
    ).strip()

    kind = str(
        base.get("kind")
        or base.get("type")
        or values.get("kind")
        or values.get("type")
        or ""
    ).strip().lower()

    connect = str(
        base.get("connect")
        or base.get("path")
        or base.get("file")
        or values.get("connect")
        or values.get("path")
        or ""
    ).strip()

    file_match = re.search(
        r'(?i)(?:^|;)\s*File\s*=\s*"((?:[^"]|"")*)"',
        connect,
    )

    if file_match:
        connect = file_match.group(1).replace('""', '"').strip()
        kind = "file"

    if not kind and connect.startswith("/"):
        kind = "file"

    if kind not in ("file", "файловая", "filesystem"):
        show_message(
            Gtk.MessageType.WARNING,
            "Удаление каталога недоступно",
            "Shift+Delete разрешён только для файловых баз.\n\n"
            f"База: {name}\n"
            f"Тип: {kind or 'не определён'}",
        )
        return True

    raw_path = Path(connect).expanduser()

    if not raw_path.is_absolute():
        show_message(
            Gtk.MessageType.ERROR,
            "Некорректный путь",
            "Путь файловой базы должен быть абсолютным.\n\n"
            f"Получено: {raw_path}",
        )
        return True

    if raw_path.is_symlink():
        show_message(
            Gtk.MessageType.ERROR,
            "Удаление заблокировано",
            "Каталог базы является символической ссылкой.\n"
            "Для безопасности удаление через Shift+Delete запрещено.\n\n"
            f"Путь: {raw_path}",
        )
        return True

    try:
        resolved = raw_path.resolve(strict=True)
    except FileNotFoundError:
        show_message(
            Gtk.MessageType.ERROR,
            "Каталог не найден",
            "Каталог выбранной файловой базы уже отсутствует.\n\n"
            f"Путь: {raw_path}",
        )
        return True
    except Exception as exc:
        show_message(
            Gtk.MessageType.ERROR,
            "Не удалось проверить путь",
            f"{type(exc).__name__}: {exc}",
        )
        return True

    if not resolved.is_dir():
        show_message(
            Gtk.MessageType.ERROR,
            "Удаление заблокировано",
            "Путь файловой базы не является каталогом.\n\n"
            f"Путь: {resolved}",
        )
        return True

    home = Path.home().resolve()

    forbidden = {
        Path("/"),
        Path("/opt"),
        Path("/usr"),
        Path("/var"),
        Path("/etc"),
        Path("/home"),
        Path("/mnt"),
        Path("/media"),
        Path("/run"),
        home,
        Path("/mnt/DataStore"),
        Path("/mnt/DataStore/bases"),
    }

    forbidden_resolved = set()

    for item in forbidden:
        try:
            forbidden_resolved.add(item.resolve())
        except Exception:
            forbidden_resolved.add(item)

    if resolved in forbidden_resolved or len(resolved.parts) < 4:
        show_message(
            Gtk.MessageType.ERROR,
            "Опасный путь",
            "Удаление этого каталога заблокировано защитой приложения.\n\n"
            f"Путь: {resolved}",
        )
        return True

    try:
        names = {
            item.name.casefold()
            for item in resolved.iterdir()
        }
    except Exception as exc:
        show_message(
            Gtk.MessageType.ERROR,
            "Не удалось прочитать каталог",
            f"{type(exc).__name__}: {exc}",
        )
        return True

    if "1cv8.1cd".casefold() not in names:
        show_message(
            Gtk.MessageType.ERROR,
            "Каталог не похож на файловую базу 1С",
            "В каталоге отсутствует файл 1Cv8.1CD.\n"
            "Для защиты от удаления произвольных данных операция отменена.\n\n"
            f"Путь: {resolved}",
        )
        return True

    try:
        process_result = subprocess.run(
            [
                "pgrep",
                "-af",
                r"/opt/1cv8/.*(1cv8|1cv8c|1cv8s|1cestart)",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        matching_processes = [
            line
            for line in process_result.stdout.splitlines()
            if str(resolved) in line
        ]
    except Exception:
        matching_processes = []

    if matching_processes:
        show_message(
            Gtk.MessageType.ERROR,
            "База используется",
            "Обнаружен запущенный процесс 1С с этой базой.\n"
            "Закройте базу и повторите удаление.\n\n"
            + "\n".join(matching_processes[:5]),
        )
        return True

    dialog = Gtk.MessageDialog(
        transient_for=window,
        flags=0,
        message_type=Gtk.MessageType.WARNING,
        buttons=Gtk.ButtonsType.YES_NO,
        text="Безвозвратно удалить файловую базу?",
    )

    dialog.format_secondary_text(
        "Будут безвозвратно удалены:\n"
        "• каталог базы и всё его содержимое;\n"
        "• запись в Обновляторе;\n"
        "• запись в списке запуска 1С.\n\n"
        f"База: {name}\n"
        f"Каталог: {resolved}\n\n"
        "Резервная копия автоматически не создаётся."
    )

    try:
        dialog.set_default_response(Gtk.ResponseType.NO)
    except Exception:
        pass

    response = dialog.run()
    dialog.destroy()

    if response != Gtk.ResponseType.YES:
        try:
            window._append_log(
                f"Shift+Delete отменён пользователем: {name}"
            )
        except Exception:
            pass
        return True

    try:
        window._append_log(
            "=== Shift+Delete: безвозвратное удаление файловой базы ==="
        )
        window._append_log(f"База: {name}")
        window._append_log(f"Каталог: {resolved}")
    except Exception:
        pass

    try:
        shutil.rmtree(resolved)
    except Exception as exc:
        message = (
            "Не удалось удалить каталог файловой базы.\n\n"
            f"{type(exc).__name__}: {exc}\n\n"
            f"Путь: {resolved}"
        )

        try:
            window._append_log(
                "ОШИБКА Shift+Delete: "
                f"{type(exc).__name__}: {exc}"
            )
        except Exception:
            pass

        show_message(
            Gtk.MessageType.ERROR,
            "Ошибка удаления каталога",
            message,
        )
        return True

    try:
        window._append_log(
            f"Каталог файловой базы удалён: {resolved}"
        )
    except Exception:
        pass

    logical_handler = globals().get("_on_delete_selected_base")

    if not callable(logical_handler):
        logical_handler = getattr(
            window,
            "on_delete_selected_base_stub",
            None,
        )

    if not callable(logical_handler):
        show_message(
            Gtk.MessageType.WARNING,
            "Каталог удалён, но запись осталась",
            "Каталог базы удалён, однако обработчик удаления записи "
            "из приложения не найден.\n\n"
            "Нажмите обычный Delete для удаления оставшейся записи.",
        )
        return True

    # Обычный обработчик уже умеет корректно удалять запись
    # из приложения и ibases.v8i. Его подтверждение автоматически
    # принимается, поскольку пользователь уже подтвердил Shift+Delete.
    auto_confirm_installed = False
    real_message_dialog = Gtk.MessageDialog

    class _AutoYesMessageDialog:
        def __init__(self, *args, **kwargs):
            pass

        def format_secondary_text(self, *args, **kwargs):
            return None

        def set_default_response(self, *args, **kwargs):
            return None

        def set_title(self, *args, **kwargs):
            return None

        def run(self):
            return Gtk.ResponseType.YES

        def destroy(self):
            return None

        def __getattr__(self, _name):
            return lambda *args, **kwargs: None

    try:
        Gtk.MessageDialog = _AutoYesMessageDialog
        auto_confirm_installed = True
    except Exception:
        auto_confirm_installed = False

    try:
        logical_handler(window)
    except TypeError:
        # Для уже привязанного метода self передавать не нужно.
        logical_handler()
    except Exception as exc:
        try:
            window._append_log(
                "Каталог удалён, но возникла ошибка удаления записи: "
                f"{type(exc).__name__}: {exc}"
            )
        except Exception:
            pass

        show_message(
            Gtk.MessageType.WARNING,
            "Каталог удалён, но запись могла остаться",
            "Физический каталог базы уже удалён.\n"
            "При удалении записи из списков произошла ошибка:\n\n"
            f"{type(exc).__name__}: {exc}\n\n"
            "При следующем запуске отсутствующая файловая база "
            "должна быть исключена автоматически.",
        )
    finally:
        if auto_confirm_installed:
            try:
                Gtk.MessageDialog = real_message_dialog
            except Exception:
                pass

    try:
        window._append_log(
            f"Shift+Delete завершён: {name}"
        )
    except Exception:
        pass

    return True
