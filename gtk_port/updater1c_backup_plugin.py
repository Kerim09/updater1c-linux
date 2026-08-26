# -*- coding: utf-8 -*-
"""
Дополнение для Обновлятор 1C Linux:
- имя .cf с учетом версии конфигурации;
- кнопка и контекстное меню "Архивировать базу";
- резервное копирование файловых и серверных баз.
"""

from __future__ import annotations

import datetime
import os
import re
import shlex
import shutil
import subprocess
import threading
import zipfile
from pathlib import Path


_G = {}
_INSTALLED = False
_SUBPROCESS_PATCHED = False
_UI_CONNECTED = set()
_TREE_CONNECTED = set()


def install(globals_dict: dict):
    global _G, _INSTALLED
    _G = globals_dict or {}

    if _INSTALLED:
        return

    _INSTALLED = True
    _patch_dumpcfg_cf_name()
    _install_ui_timer()


def _gtk():
    Gtk = _G.get("Gtk")
    GLib = _G.get("GLib")

    if Gtk is None or GLib is None:
        try:
            from gi.repository import Gtk as _Gtk, GLib as _GLib
            Gtk = Gtk or _Gtk
            GLib = GLib or _GLib
        except Exception:
            pass

    return Gtk, GLib


def _safe_name(text: str, default: str = "base") -> str:
    text = str(text or "").strip()
    text = re.sub(r"\s+", "", text)
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text)
    text = re.sub(r"_+", "_", text).strip("._-")
    return text or default


def _safe_version(text: str) -> str:
    text = str(text or "").strip()
    if not text or text in ("-", "auto"):
        return ""
    text = re.sub(r"[^0-9A-Za-zА-Яа-я._-]+", "_", text).strip("._-")
    return text


def _now_stamp() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def _append_version_before_timestamp(path_text: str, version: str) -> str:
    version = _safe_version(version)
    if not version:
        return path_text

    p = Path(path_text)
    if p.suffix.lower() != ".cf":
        return path_text

    stem = p.stem

    # Уже содержит версию.
    if version in stem:
        return path_text

    m = re.match(r"^(.*?)(_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})$", stem)
    if m:
        new_stem = f"{m.group(1)}_{version}{m.group(2)}"
    else:
        new_stem = f"{stem}_{version}"

    return str(p.with_name(new_stem + p.suffix))


def _cmd_parts(cmd):
    try:
        if isinstance(cmd, (list, tuple)):
            return [str(x) for x in cmd]
        if isinstance(cmd, str):
            return shlex.split(cmd)
    except Exception:
        pass
    return [str(cmd)]


def _cmd_join(parts):
    try:
        return " ".join(shlex.quote(str(x)) for x in parts)
    except Exception:
        return str(parts)


def _is_1c_exe(part: str) -> bool:
    try:
        return Path(str(part)).name.lower() in ("1cv8", "1cv8c", "1cv8s", "1cestart")
    except Exception:
        return False


def _get_arg_after(parts, switch: str) -> str:
    sw = switch.lower()
    for i, part in enumerate(parts):
        low = str(part).lower()
        if low == sw and i + 1 < len(parts):
            return str(parts[i + 1])
        if low.startswith(sw) and len(str(part)) > len(switch):
            return str(part)[len(switch):]
    return ""


def _set_arg_after(parts, switch: str, value: str):
    sw = switch.lower()
    for i, part in enumerate(parts):
        low = str(part).lower()
        if low == sw and i + 1 < len(parts):
            parts[i + 1] = value
            return parts
        if low.startswith(sw) and len(str(part)) > len(switch):
            parts[i] = str(part)[:len(switch)] + value
            return parts
    return parts


def _extract_base_path_from_cmd(parts) -> str:
    for sw in ("/F", "/S", "/IBConnectionString"):
        val = _get_arg_after(parts, sw)
        if val:
            return val.strip('"')
    return ""


def _extract_dumpcfg_path(parts) -> str:
    return _get_arg_after(parts, "/DumpCfg").strip('"')


def _lookup_version_for_base(base_path: str = "") -> str:
    # 1. Берем версию из выбранной строки интерфейса.
    try:
        base = _current_base_from_ui()
        if base and base.get("version"):
            if not base_path or base_path in (base.get("path") or "") or (base.get("path") or "") in base_path:
                return base.get("version") or ""
    except Exception:
        pass

    # 2. Пробуем прочитать AppConfig, если структура доступна.
    try:
        AppConfig = _G.get("AppConfig")
        if AppConfig:
            cfg = AppConfig()
            if hasattr(cfg, "load"):
                cfg.load()

            roots = [cfg, getattr(cfg, "settings", None)]
            for root in roots:
                for attr in ("bases", "ibases", "base_items", "items"):
                    arr = getattr(root, attr, None)
                    if not arr:
                        continue

                    for item in arr:
                        try:
                            d = vars(item)
                        except Exception:
                            d = item if isinstance(item, dict) else {}

                        item_path = str(
                            d.get("path")
                            or d.get("file")
                            or d.get("server")
                            or d.get("connection")
                            or d.get("connect")
                            or ""
                        )

                        if base_path and item_path and base_path not in item_path and item_path not in base_path:
                            continue

                        for k in ("version", "config_version", "conf_version", "configuration_version"):
                            val = d.get(k)
                            if val:
                                return str(val)
        return ""
    except Exception:
        return ""


def _adjust_dumpcfg_command(cmd):
    parts = _cmd_parts(cmd)
    joined = _cmd_join(parts).lower()

    if not any(_is_1c_exe(x) for x in parts):
        return cmd

    if "/dumpcfg" not in joined and " dumpcfg" not in joined:
        return cmd

    out_cf = _extract_dumpcfg_path(parts)
    if not out_cf.lower().endswith(".cf"):
        return cmd

    base_path = _extract_base_path_from_cmd(parts)
    version = _lookup_version_for_base(base_path)

    new_out = _append_version_before_timestamp(out_cf, version)
    if new_out == out_cf:
        return cmd

    parts = _set_arg_after(parts, "/DumpCfg", new_out)

    print(f"UPDATER1C: имя CF с учетом релиза: {new_out}")

    if isinstance(cmd, str):
        return _cmd_join(parts)

    return parts


def _patch_dumpcfg_cf_name():
    global _SUBPROCESS_PATCHED

    if _SUBPROCESS_PATCHED:
        return

    _SUBPROCESS_PATCHED = True

    orig_run = subprocess.run
    orig_popen = subprocess.Popen

    def run_wrapper(cmd, *args, **kwargs):
        return orig_run(_adjust_dumpcfg_command(cmd), *args, **kwargs)

    def popen_wrapper(cmd, *args, **kwargs):
        return orig_popen(_adjust_dumpcfg_command(cmd), *args, **kwargs)

    subprocess.run = run_wrapper
    subprocess.Popen = popen_wrapper


def _children(widget):
    result = []

    try:
        child = widget.get_first_child()
        while child is not None:
            result.append(child)
            child = child.get_next_sibling()
    except Exception:
        pass

    try:
        if hasattr(widget, "get_children"):
            result.extend(widget.get_children())
    except Exception:
        pass

    try:
        if hasattr(widget, "get_child"):
            child = widget.get_child()
            if child is not None:
                result.append(child)
    except Exception:
        pass

    unique = []
    seen = set()

    for item in result:
        if item is not None and id(item) not in seen:
            seen.add(id(item))
            unique.append(item)

    return unique


def _flatten(root):
    result = []
    seen = set()

    def walk(w):
        if w is None or id(w) in seen:
            return
        seen.add(id(w))
        result.append(w)
        for ch in _children(w):
            walk(ch)

    walk(root)
    return result


def _widget_text(w) -> str:
    for meth in ("get_text", "get_label", "get_title", "get_active_text"):
        try:
            if hasattr(w, meth):
                val = getattr(w, meth)()
                if val:
                    return str(val)
        except Exception:
            pass
    return ""


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _toplevels():
    Gtk, _GLib = _gtk()

    if Gtk is None:
        return []

    try:
        return list(Gtk.Window.list_toplevels())
    except Exception:
        pass

    try:
        model = Gtk.Window.get_toplevels()
        return [model.get_item(i) for i in range(model.get_n_items())]
    except Exception:
        return []


def _main_window():
    for win in _toplevels():
        try:
            title = win.get_title() or ""
        except Exception:
            title = ""

        if "Обновлятор" in title or "Updater" in title:
            return win

    return None


def _is_treeview(w) -> bool:
    try:
        return "treeview" in type(w).__name__.lower()
    except Exception:
        return False


def _tree_row_values(tree):
    try:
        sel = tree.get_selection()
        model, it = sel.get_selected()
        if it is None:
            return []

        n = model.get_n_columns()
        return [model.get_value(it, i) for i in range(n)]
    except Exception:
        return []


def _extract_base_from_values(values) -> dict:
    strs = []
    for v in values:
        if isinstance(v, bool):
            continue
        if v is None:
            continue
        text = str(v).strip()
        if text:
            strs.append(text)

    info = {
        "name": "",
        "type": "",
        "config": "",
        "version": "",
        "path": "",
        "platform": "",
    }

    if not strs:
        return info

    type_idx = None
    for i, text in enumerate(strs):
        if text.lower() in ("file", "server", "web"):
            type_idx = i
            info["type"] = text.lower()
            break

    if type_idx is not None and type_idx > 0:
        info["name"] = strs[type_idx - 1]
    else:
        info["name"] = strs[0]

    for text in strs:
        low = text.lower()
        if text.startswith("/") or low.startswith("http://") or low.startswith("https://") or "srvr=" in low or "file=" in low or "\\" in text:
            info["path"] = text

    versions = []
    for text in strs:
        if re.match(r"^\d+(?:\.\d+){1,5}$", text):
            versions.append(text)

    # Последняя короткая версия в строке обычно платформа 8.3/8.5, а версия конфигурации длиннее.
    cfg_versions = [v for v in versions if len(v.split(".")) >= 3]
    if cfg_versions:
        info["version"] = cfg_versions[0]

    platforms = [v for v in versions if v.startswith(("8.3", "8.5"))]
    if platforms:
        info["platform"] = platforms[-1]

    if type_idx is not None and type_idx + 1 < len(strs):
        maybe_config = strs[type_idx + 1]
        if maybe_config != info["version"] and maybe_config != info["path"]:
            info["config"] = maybe_config

    return info


def _current_base_from_tree(tree) -> dict:
    return _extract_base_from_values(_tree_row_values(tree))


def _current_base_from_ui() -> dict:
    win = _main_window()
    if win is None:
        return {}

    for w in _flatten(win):
        if not _is_treeview(w):
            continue

        base = _current_base_from_tree(w)
        if base.get("name") and base.get("type") and base.get("path"):
            return base

    return {}


def _find_base_tree(win):
    candidates = []

    for w in _flatten(win):
        if not _is_treeview(w):
            continue

        base = _current_base_from_tree(w)
        score = 0

        if base.get("name"):
            score += 1
        if base.get("type") in ("file", "web", "server"):
            score += 2
        if base.get("path"):
            score += 2

        candidates.append((score, w))

    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1] if candidates else None


def _find_button_parent(win):
    for w in _flatten(win):
        txt = _norm(_widget_text(w))
        if "добавить базу" in txt:
            try:
                return w.get_parent()
            except Exception:
                pass
    return None


def _install_ui_timer():
    Gtk, GLib = _gtk()
    if Gtk is None or GLib is None:
        return

    try:
        GLib.timeout_add(800, _ui_scan)
    except Exception:
        pass


def _ui_scan():
    Gtk, _GLib = _gtk()

    try:
        win = _main_window()
        if win is None:
            return True

        _add_backup_button(win)
        _connect_tree_context_menu(win)

    except Exception as e:
        try:
            print("UPDATER1C_BACKUP_PLUGIN_UI_ERROR:", repr(e))
        except Exception:
            pass

    return True


def _add_backup_button(win):
    Gtk, _GLib = _gtk()

    if Gtk is None:
        return

    if id(win) in _UI_CONNECTED:
        return

    parent = _find_button_parent(win)
    if parent is None:
        return

    btn = Gtk.Button.new_with_label("📦 Архивировать базу")
    btn.set_tooltip_text("Сделать резервную копию выбранной базы")
    btn.connect("clicked", lambda _b: _open_backup_dialog_for_current_base(win))

    try:
        parent.pack_start(btn, False, False, 6)
    except Exception:
        try:
            parent.append(btn)
        except Exception:
            return

    try:
        parent.show_all()
    except Exception:
        try:
            btn.show()
        except Exception:
            pass

    _UI_CONNECTED.add(id(win))


def _connect_tree_context_menu(win):
    Gtk, _GLib = _gtk()

    if Gtk is None:
        return

    tree = _find_base_tree(win)
    if tree is None:
        return

    if id(tree) in _TREE_CONNECTED:
        return

    _TREE_CONNECTED.add(id(tree))

    def on_button_press(widget, event):
        try:
            if getattr(event, "button", 0) != 3:
                return False

            try:
                path_info = widget.get_path_at_pos(int(event.x), int(event.y))
                if path_info:
                    path = path_info[0]
                    widget.get_selection().select_path(path)
            except Exception:
                pass

            menu = Gtk.Menu()
            item = Gtk.MenuItem(label="📦 Архивировать базу")
            item.connect("activate", lambda _i: _open_backup_dialog_for_current_base(win))
            menu.append(item)
            menu.show_all()

            try:
                menu.popup(None, None, None, None, event.button, event.time)
            except TypeError:
                menu.popup_at_pointer(event)

            return True

        except Exception as e:
            print("UPDATER1C_BACKUP_CONTEXT_MENU_ERROR:", repr(e))
            return False

    try:
        tree.connect("button-press-event", on_button_press)
    except Exception:
        pass


def _message(parent, title: str, text: str, level: str = "info"):
    Gtk, _GLib = _gtk()
    print(f"{title}: {text}")

    if Gtk is None:
        return

    msg_type = Gtk.MessageType.INFO
    if level == "error":
        msg_type = Gtk.MessageType.ERROR
    elif level == "warning":
        msg_type = Gtk.MessageType.WARNING

    try:
        dlg = Gtk.MessageDialog(
            transient_for=parent,
            flags=0,
            message_type=msg_type,
            buttons=Gtk.ButtonsType.OK,
            text=title,
        )
        dlg.format_secondary_text(text)
        dlg.run()
        dlg.destroy()
    except Exception:
        pass


def _append_log(text: str):
    Gtk, GLib = _gtk()

    print(text)

    if Gtk is None or GLib is None:
        return

    def apply():
        try:
            win = _main_window()
            if win is None:
                return False

            for w in _flatten(win):
                if "textview" not in type(w).__name__.lower():
                    continue

                buf = w.get_buffer()
                end = buf.get_end_iter()
                buf.insert(end, text.rstrip() + "\n")
                return False
        except Exception:
            return False

        return False

    try:
        GLib.idle_add(apply)
    except Exception:
        pass


def _open_backup_dialog_for_current_base(parent):
    base = _current_base_from_ui()

    if not base or not base.get("name") or not base.get("path"):
        _message(
            parent,
            "Архивирование базы",
            "Не удалось определить выбранную базу. Выдели строку базы в списке и повтори.",
            "warning",
        )
        return

    _open_backup_dialog(parent, base)


def _choose_dir(entry, parent):
    Gtk, _GLib = _gtk()

    dlg = Gtk.FileChooserDialog(
        title="Выбери папку для резервной копии",
        transient_for=parent,
        action=Gtk.FileChooserAction.SELECT_FOLDER,
    )
    dlg.add_buttons("Отмена", Gtk.ResponseType.CANCEL, "Выбрать", Gtk.ResponseType.OK)

    try:
        current = entry.get_text().strip()
        if current:
            dlg.set_filename(current)
    except Exception:
        pass

    resp = dlg.run()

    if resp == Gtk.ResponseType.OK:
        try:
            entry.set_text(dlg.get_filename())
        except Exception:
            pass

    dlg.destroy()


def _open_backup_dialog(parent, base: dict):
    Gtk, _GLib = _gtk()

    if Gtk is None:
        return

    base_name = _safe_name(base.get("name"), "base")
    base_version = _safe_version(base.get("version"))
    base_type = (base.get("type") or "").lower()
    base_path = base.get("path") or ""

    settings = getattr(parent, "settings", None) or {}
    backup_root = settings.get("backup_dir") or settings.get("backups_dir") or "/mnt/DataStore/Updater1C/1c-backups"
    default_dir = Path(backup_root).expanduser() / base_name
    default_dir.mkdir(parents=True, exist_ok=True)

    dlg = Gtk.Dialog(
        title=f"Архивировать базу: {base_name}",
        transient_for=parent,
        flags=0,
    )
    dlg.add_buttons("Отмена", Gtk.ResponseType.CANCEL, "Запустить архивирование", Gtk.ResponseType.OK)
    dlg.set_default_size(760, 420)

    area = dlg.get_content_area()
    grid = Gtk.Grid()
    grid.set_row_spacing(8)
    grid.set_column_spacing(8)
    grid.set_border_width(12)
    area.add(grid)

    row = 0

    def add_label(text):
        nonlocal row
        lbl = Gtk.Label(label=text)
        lbl.set_xalign(0)
        grid.attach(lbl, 0, row, 1, 1)
        return lbl

    def add_entry(text=""):
        entry = Gtk.Entry()
        entry.set_text(text or "")
        grid.attach(entry, 1, row, 2, 1)
        return entry

    add_label("База:")
    base_lbl = Gtk.Label(label=f"{base_name}   [{base_type or '-'}]   {base_path}")
    base_lbl.set_xalign(0)
    grid.attach(base_lbl, 1, row, 2, 1)
    row += 1

    add_label("Режим:")
    mode_combo = Gtk.ComboBoxText()

    if base_type == "file":
        mode_combo.append_text("Файловая база: выгрузка в .dt через 1С")
        mode_combo.append_text("Файловая база: архивировать 1Cv8.1CD в .zip")
    else:
        mode_combo.append_text("Серверная/веб база: выгрузка в .dt через 1С")
        mode_combo.append_text("PostgreSQL: серверный архив")
        mode_combo.append_text("Microsoft SQL Server: BACKUP DATABASE через sqlcmd")

    mode_combo.set_active(0)
    grid.attach(mode_combo, 1, row, 2, 1)
    row += 1

    add_label("Папка результата:")
    out_dir_entry = Gtk.Entry()
    out_dir_entry.set_text(str(default_dir))
    grid.attach(out_dir_entry, 1, row, 1, 1)

    dir_btn = Gtk.Button(label="...")
    dir_btn.connect("clicked", lambda _b: _choose_dir(out_dir_entry, dlg))
    grid.attach(dir_btn, 2, row, 1, 1)
    row += 1

    add_label("Пользователь 1С:")
    user_entry = add_entry("")
    row += 1

    add_label("Пароль 1С:")
    pwd_entry = add_entry("")
    try:
        pwd_entry.set_visibility(False)
    except Exception:
        pass
    row += 1

    sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
    grid.attach(sep, 0, row, 3, 1)
    row += 1

    add_label("Параметры СУБД")
    hint = Gtk.Label(label="Поля используются выбранным профилем СУБД")
    hint.set_xalign(0)
    grid.attach(hint, 1, row, 2, 1)
    row += 1

    add_label("DB host / server:")
    db_host_entry = add_entry("localhost")
    row += 1

    add_label("DB port:")
    db_port_entry = add_entry("")
    row += 1

    add_label("DB name:")
    db_name_entry = add_entry("")
    row += 1

    add_label("DB user:")
    db_user_entry = add_entry("")
    row += 1

    add_label("DB password:")
    db_pwd_entry = add_entry("")
    try:
        db_pwd_entry.set_visibility(False)
    except Exception:
        pass
    row += 1

    db_widgets = [db_host_entry, db_port_entry, db_name_entry, db_user_entry, db_pwd_entry]

    def update_db_fields(_combo=None):
        mode_text = (mode_combo.get_active_text() or "").casefold()
        enabled = "postgresql" in mode_text or "microsoft sql" in mode_text
        for widget in db_widgets:
            try:
                widget.set_sensitive(enabled)
            except Exception:
                pass

    mode_combo.connect("changed", update_db_fields)
    update_db_fields()

    area.show_all()

    resp = dlg.run()

    if resp != Gtk.ResponseType.OK:
        dlg.destroy()
        return

    mode = mode_combo.get_active_text() or ""
    out_dir = Path(out_dir_entry.get_text().strip() or str(default_dir))
    user = user_entry.get_text().strip()
    password = pwd_entry.get_text()
    db_host = db_host_entry.get_text().strip()
    db_port = db_port_entry.get_text().strip()
    db_name = db_name_entry.get_text().strip()
    db_user = db_user_entry.get_text().strip()
    db_pwd = db_pwd_entry.get_text()

    dlg.destroy()

    args = {
        "parent": parent,
        "base": base,
        "mode": mode,
        "out_dir": out_dir,
        "user": user,
        "password": password,
        "db_host": db_host,
        "db_port": db_port,
        "db_name": db_name,
        "db_user": db_user,
        "db_pwd": db_pwd,
    }

    threading.Thread(target=_run_backup_thread, kwargs=args, daemon=True).start()


def _output_file(out_dir: Path, base: dict, ext: str, suffix: str = "") -> Path:
    base_name = _safe_name(base.get("name"), "base")
    ver = _safe_version(base.get("version"))
    ver_part = f"_{ver}" if ver else ""
    suffix_part = f"_{suffix}" if suffix else ""
    return out_dir / f"{base_name}{ver_part}{suffix_part}_{_now_stamp()}.{ext.lstrip('.')}"


def _find_1c_exe(preferred_platform: str = "") -> str:
    preferred_platform = str(preferred_platform or "").strip()

    if preferred_platform.startswith("/") and Path(preferred_platform).exists():
        return preferred_platform

    candidates = []

    for root in (
        Path("/opt/1cv8/x86_64"),
        Path("/opt/1cv8/i386"),
        Path("/opt/1C/v8.3/x86_64"),
        Path("/opt/1C/v8.3/i386"),
        Path("/opt/1C/v8.5/x86_64"),
        Path("/opt/1C/v8.5/i386"),
    ):
        try:
            if root.exists():
                for exe in root.glob("*/1cv8"):
                    candidates.append(exe)
        except Exception:
            pass

    for name in ("1cv8", "1cv8c"):
        exe = shutil.which(name)
        if exe:
            candidates.append(Path(exe))

    def version_tuple(p: Path):
        m = re.search(r"(\d+\.\d+\.\d+\.\d+)", str(p))
        if not m:
            return (0, 0, 0, 0)
        return tuple(int(x) for x in m.group(1).split("."))

    if preferred_platform:
        matched = [p for p in candidates if preferred_platform in str(p)]
        if matched:
            matched.sort(key=version_tuple, reverse=True)
            return str(matched[0])

    if candidates:
        candidates.sort(key=version_tuple, reverse=True)
        return str(candidates[0])

    return ""


def _with_xvfb(cmd: list[str]) -> list[str]:
    if shutil.which("xvfb-run"):
        return ["xvfb-run", "-a", "-s", "-screen 0 1280x800x24 -nolisten tcp"] + cmd
    return cmd


def _run_backup_thread(**kwargs):
    parent = kwargs["parent"]
    base = kwargs["base"]
    mode = kwargs["mode"]
    out_dir: Path = kwargs["out_dir"]

    try:
        out_dir.mkdir(parents=True, exist_ok=True)

        if "1Cv8.1CD" in mode or ".zip" in mode:
            out_file = _backup_file_zip(base, out_dir)
        elif "pg_dump" in mode or "серверный архив" in mode:
            out_file = _backup_postgresql(
                base,
                out_dir,
                kwargs["db_host"],
                kwargs["db_port"],
                kwargs["db_name"],
                kwargs["db_user"],
                kwargs["db_pwd"],
            )
        elif "sqlcmd" in mode or "Microsoft SQL" in mode:
            out_file = _backup_mssql(
                base,
                out_dir,
                kwargs["db_host"],
                kwargs["db_name"],
                kwargs["db_user"],
                kwargs["db_pwd"],
            )
        else:
            out_file = _backup_dt(
                base,
                out_dir,
                kwargs["user"],
                kwargs["password"],
            )

        _gtk_idle_message(
            parent,
            "Архивирование завершено",
            f"Резервная копия создана:\n{out_file}",
            "info",
        )

    except Exception as e:
        _append_log(f"ОШИБКА архивирования: {type(e).__name__}: {e}")
        _gtk_idle_message(
            parent,
            "Ошибка архивирования",
            f"{type(e).__name__}: {e}",
            "error",
        )


def _gtk_idle_message(parent, title, text, level="info"):
    _Gtk, GLib = _gtk()

    if GLib is None:
        _message(parent, title, text, level)
        return

    try:
        GLib.idle_add(lambda: (_message(parent, title, text, level), False)[1])
    except Exception:
        _message(parent, title, text, level)


def _backup_file_zip(base: dict, out_dir: Path) -> Path:
    base_path = Path(base.get("path") or "")

    if not base_path.exists():
        raise RuntimeError(f"Папка файловой базы не найдена: {base_path}")

    onecd = None
    for p in base_path.iterdir():
        if p.name.lower() == "1cv8.1cd":
            onecd = p
            break

    if onecd is None:
        raise RuntimeError(f"В папке базы не найден файл 1Cv8.1CD: {base_path}")

    out_file = _output_file(out_dir, base, "zip", "1CD")

    _append_log(f"Архивирование файловой базы в ZIP: {base.get('name')}")
    _append_log(f"Источник: {onecd}")
    _append_log(f"Файл: {out_file}")

    with zipfile.ZipFile(out_file, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        zf.write(onecd, arcname=onecd.name)

    _append_log(f"ZIP-архив создан: {out_file}")
    return out_file


def _backup_dt(base: dict, out_dir: Path, user: str, password: str) -> Path:
    onec = _find_1c_exe(base.get("platform") or "")
    if not onec:
        raise RuntimeError("Не найден исполняемый файл 1С 1cv8")

    out_file = _output_file(out_dir, base, "dt")
    log_file = out_file.with_suffix(".log")

    base_type = (base.get("type") or "").lower()
    base_path = base.get("path") or ""

    cmd = [onec, "DESIGNER"]

    if base_type == "file" or base_path.startswith("/"):
        cmd.append("/F" + base_path)
    else:
        low = base_path.lower()

        if low.startswith("http://") or low.startswith("https://"):
            raise RuntimeError(
                "Для веб-ссылки нельзя сделать .dt напрямую. "
                "Укажи серверное подключение 1С или используй дамп СУБД."
            )

        if "srvr=" in low or "file=" in low:
            cmd.extend(["/IBConnectionString", base_path])
        elif "\\" in base_path:
            cmd.append("/S" + base_path)
        else:
            cmd.append("/S" + base_path)

    if user:
        cmd.append("/N" + user)

    if password:
        cmd.append("/P" + password)

    cmd.extend(["/DumpIB", str(out_file), "/Out", str(log_file), "-NoTruncate"])

    real_cmd = _with_xvfb(cmd)

    _append_log(f"Архивирование базы в .dt: {base.get('name')}")
    _append_log("Команда: " + _cmd_join(real_cmd))

    result = subprocess.run(real_cmd)

    if result.returncode != 0:
        raise RuntimeError(f"1С завершилась с кодом {result.returncode}. Лог: {log_file}")

    if not out_file.exists():
        raise RuntimeError(f"Команда завершилась без ошибки, но .dt не найден: {out_file}")

    _append_log(f"DT-архив создан: {out_file}")
    return out_file


def _backup_postgresql(base: dict, out_dir: Path, host: str, port: str, db_name: str, user: str, password: str) -> Path:
    if not shutil.which("pg_dump"):
        raise RuntimeError("Не найден pg_dump. Установи пакет postgresql-client.")

    if not db_name:
        raise RuntimeError("Для pg_dump нужно заполнить DB name.")

    out_file = _output_file(out_dir, base, "dump", "postgresql")

    cmd = ["pg_dump", "-Fc", "-h", host or "localhost", "-f", str(out_file)]

    if port:
        cmd.extend(["-p", port])

    if user:
        cmd.extend(["-U", user])

    cmd.append(db_name)

    env = os.environ.copy()
    if password:
        env["PGPASSWORD"] = password

    _append_log(f"PostgreSQL pg_dump: {db_name}")
    _append_log("Команда: " + _cmd_join(cmd))

    result = subprocess.run(cmd, env=env)

    if result.returncode != 0:
        raise RuntimeError(f"pg_dump завершился с кодом {result.returncode}")

    _append_log(f"PostgreSQL dump создан: {out_file}")
    return out_file


def _backup_mssql(base: dict, out_dir: Path, server: str, db_name: str, user: str, password: str) -> Path:
    if not shutil.which("sqlcmd"):
        raise RuntimeError("Не найден sqlcmd. Установи Microsoft SQL command-line tools.")

    if not db_name:
        raise RuntimeError("Для MSSQL нужно заполнить DB name.")

    out_file = _output_file(out_dir, base, "bak", "mssql")
    server = server or "localhost"

    sql = f"BACKUP DATABASE [{db_name}] TO DISK=N'{out_file}' WITH INIT, COMPRESSION, STATS=10"

    cmd = ["sqlcmd", "-S", server, "-Q", sql]

    if user:
        cmd.extend(["-U", user, "-P", password or ""])
    else:
        cmd.append("-E")

    _append_log(f"Microsoft SQL BACKUP DATABASE: {db_name}")
    _append_log("Команда: " + _cmd_join(cmd))

    result = subprocess.run(cmd)

    if result.returncode != 0:
        raise RuntimeError(f"sqlcmd завершился с кодом {result.returncode}")

    _append_log(f"MSSQL backup создан: {out_file}")
    return out_file
