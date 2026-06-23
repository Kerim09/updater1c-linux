# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
import zipfile
from pathlib import Path


_G = {}
_INSTALLED = False
_MENU_PATCHED = False


def install(globals_dict: dict):
    global _G, _INSTALLED

    _G = globals_dict or {}

    if _INSTALLED:
        return

    _INSTALLED = True

    Gtk, GLib = _gtk()
    if Gtk is not None and GLib is not None:
        _patch_gtk_menu_popup()
        try:
            GLib.timeout_add(1000, _ensure_menu_patch_timer)
        except Exception:
            pass


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


def _backup_mod():
    mod = _G.get("_u1c_backup_mod")
    if mod is not None:
        return mod

    import sys
    return sys.modules.get("updater1c_backup_plugin")


def _ensure_menu_patch_timer():
    _patch_gtk_menu_popup()
    return True


def _patch_gtk_menu_popup():
    global _MENU_PATCHED

    Gtk, _GLib = _gtk()
    if Gtk is None:
        return

    if _MENU_PATCHED:
        return

    try:
        if getattr(Gtk.Menu, "_updater1c_restore_popup_patched", False):
            _MENU_PATCHED = True
            return

        orig_popup = Gtk.Menu.popup
        orig_popup_at_pointer = getattr(Gtk.Menu, "popup_at_pointer", None)

        def popup_wrapper(menu, *args, **kwargs):
            try:
                _prepare_context_menu(menu)
            except Exception as e:
                print("UPDATER1C_RESTORE_PREPARE_MENU_ERROR:", repr(e))
            return orig_popup(menu, *args, **kwargs)

        Gtk.Menu.popup = popup_wrapper

        if orig_popup_at_pointer is not None:
            def popup_at_pointer_wrapper(menu, *args, **kwargs):
                try:
                    _prepare_context_menu(menu)
                except Exception as e:
                    print("UPDATER1C_RESTORE_PREPARE_MENU_AT_POINTER_ERROR:", repr(e))
                return orig_popup_at_pointer(menu, *args, **kwargs)

            Gtk.Menu.popup_at_pointer = popup_at_pointer_wrapper

        Gtk.Menu._updater1c_restore_popup_patched = True
        _MENU_PATCHED = True
        print("UPDATER1C: контекстное меню баз пропатчено: восстановление добавлено, скачать платформу убрано")

    except Exception as e:
        print("UPDATER1C_RESTORE_MENU_PATCH_ERROR:", repr(e))


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


def _menu_item_text(item) -> str:
    txt = _widget_text(item)
    if txt:
        return txt

    try:
        child = item.get_child()
        txt = _widget_text(child)
        if txt:
            return txt
    except Exception:
        pass

    return ""


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
        low = text.lower()
        if low in ("file", "server", "web"):
            type_idx = i
            info["type"] = low
            break

    if type_idx is not None and type_idx > 0:
        info["name"] = strs[type_idx - 1]
    else:
        info["name"] = strs[0]

    for text in strs:
        low = text.lower()
        if (
            text.startswith("/")
            or low.startswith("http://")
            or low.startswith("https://")
            or "srvr=" in low
            or "file=" in low
            or "\\" in text
        ):
            info["path"] = text

    versions = []
    for text in strs:
        if re.match(r"^\d+(?:\.\d+){1,5}$", text):
            versions.append(text)

    cfg_versions = [v for v in versions if len(v.split(".")) >= 3 and not v.startswith(("8.3", "8.5"))]
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
    mod = _backup_mod()
    if mod is not None:
        try:
            base = mod._current_base_from_tree(tree)
            if base:
                return base
        except Exception:
            pass

    return _extract_base_from_values(_tree_row_values(tree))


def _current_base_from_ui() -> dict:
    win = _main_window()
    if win is None:
        return {}

    mod = _backup_mod()
    if mod is not None:
        try:
            base = mod._current_base_from_ui()
            if base:
                return base
        except Exception:
            pass

    best = {}

    for w in _flatten(win):
        if not _is_treeview(w):
            continue

        base = _current_base_from_tree(w)
        if base.get("name"):
            best = base
            if base.get("type") and base.get("path"):
                return base

    return best


def _is_group_or_web(base: dict) -> bool:
    typ = str(base.get("type") or "").strip().lower()
    path = str(base.get("path") or "").strip()

    if typ in ("web", "http", "https"):
        return True

    if typ not in ("file", "server"):
        return True

    if not path:
        return True

    if path.lower().startswith(("http://", "https://")):
        return True

    return False


def _menu_looks_like_base_context(labels: list[str], base: dict) -> bool:
    if not base.get("name"):
        return False

    joined = " | ".join(_norm(x) for x in labels)

    markers = (
        "архивировать",
        "свойства",
        "проверить настройки",
        "скачать обновления",
        "скачать платформу",
        "установить обновления",
        "запустить",
        "открыть",
    )

    return any(m in joined for m in markers)


def _prepare_context_menu(menu):
    Gtk, _GLib = _gtk()
    if Gtk is None:
        return

    try:
        children = list(menu.get_children())
    except Exception:
        return

    labels = [_menu_item_text(ch) for ch in children]
    norm_labels = [_norm(x) for x in labels]

    # Убираем "Скачать платформу" из любых контекстных меню.
    for child, label in list(zip(children, norm_labels)):
        if "скачать платформу" in label:
            try:
                menu.remove(child)
                child.destroy()
            except Exception:
                try:
                    child.set_visible(False)
                    child.set_sensitive(False)
                except Exception:
                    pass

    # Не добавляем дубликат.
    children = list(menu.get_children())
    labels = [_menu_item_text(ch) for ch in children]
    norm_labels = [_norm(x) for x in labels]

    if any("восстановить из архива" in x for x in norm_labels):
        return

    base = _current_base_from_ui()
    if not _menu_looks_like_base_context(labels, base):
        return

    try:
        menu.append(Gtk.SeparatorMenuItem())
    except Exception:
        pass

    item = Gtk.MenuItem(label="♻️ Восстановить из архива")
    item.set_sensitive(not _is_group_or_web(base))
    item.connect("activate", lambda _i, _base=dict(base): _open_restore_file_dialog(_backup_mod(), _main_window(), _base))
    menu.append(item)

    if _is_group_or_web(base):
        hint = Gtk.MenuItem(label="Доступно только для file/server баз")
        hint.set_sensitive(False)
        menu.append(hint)

    try:
        menu.show_all()
    except Exception:
        pass


def _safe_name(text: str, default: str = "base") -> str:
    text = str(text or "").strip()
    text = re.sub(r"\s+", "", text)
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text)
    text = re.sub(r"_+", "_", text).strip("._-")
    return text or default


def _stamp() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def _append_log(mod, text: str):
    try:
        mod._append_log(text)
    except Exception:
        print(text)


def _message(mod, parent, title: str, text: str, level: str = "info"):
    try:
        mod._message(parent, title, text, level)
        return
    except Exception:
        pass

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


def _cmd_join(cmd) -> str:
    if isinstance(cmd, str):
        return cmd
    return " ".join(shlex.quote(str(x)) for x in cmd)


def _with_xvfb(cmd: list[str]) -> list[str]:
    if shutil.which("xvfb-run"):
        return ["xvfb-run", "-a", "-s", "-screen 0 1280x800x24 -nolisten tcp"] + cmd
    return cmd


def _find_1c_exe(mod, base: dict) -> str:
    try:
        exe = mod._find_1c_exe(base.get("platform") or "")
        if exe:
            return exe
    except Exception:
        pass

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
                candidates.extend(root.glob("*/1cv8"))
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

    candidates = [p for p in candidates if Path(p).exists()]
    candidates.sort(key=version_tuple, reverse=True)

    return str(candidates[0]) if candidates else ""


def _settings_backup_root() -> Path:
    names = (
        "backups_dir",
        "backup_dir",
        "backups_path",
        "backup_path",
        "archive_dir",
        "archives_dir",
        "backup_root",
        "backups_root",
    )

    try:
        AppConfig = _G.get("AppConfig")
        if AppConfig:
            cfg = AppConfig()
            if hasattr(cfg, "load"):
                cfg.load()

            for obj in (cfg, getattr(cfg, "settings", None)):
                try:
                    d = vars(obj)
                except Exception:
                    d = {}

                for name in names:
                    val = d.get(name)
                    if isinstance(val, str) and val.startswith("/"):
                        return Path(val)
    except Exception:
        pass

    return Path("/mnt/DataStore/Updater1C/1c-backups")


def _base_backup_dir(base: dict) -> Path:
    root = _settings_backup_root()
    base_name = _safe_name(base.get("name"), "base")

    direct = root / base_name
    if direct.exists():
        return direct

    return root


def _open_restore_file_dialog(mod, parent, base: dict):
    Gtk, _GLib = _gtk()
    if Gtk is None:
        return

    if mod is None:
        mod = _backup_mod()

    if _is_group_or_web(base):
        _message(
            mod,
            parent,
            "Восстановление из архива",
            "Восстановление доступно только для файловых и серверных баз. Для групп и web-баз пункт неактивен.",
            "warning",
        )
        return

    start_dir = _base_backup_dir(base)
    start_dir.mkdir(parents=True, exist_ok=True)

    dlg = Gtk.FileChooserDialog(
        title=f"Выбери архив для восстановления: {base.get('name')}",
        transient_for=parent,
        action=Gtk.FileChooserAction.OPEN,
    )
    dlg.add_buttons("Отмена", Gtk.ResponseType.CANCEL, "Восстановить", Gtk.ResponseType.OK)

    try:
        dlg.set_current_folder(str(start_dir))
    except Exception:
        pass

    filter_archives = Gtk.FileFilter()
    filter_archives.set_name("Архивы 1С (*.dt, *.zip)")
    filter_archives.add_pattern("*.dt")
    filter_archives.add_pattern("*.DT")
    filter_archives.add_pattern("*.zip")
    filter_archives.add_pattern("*.ZIP")
    dlg.add_filter(filter_archives)

    resp = dlg.run()
    archive = dlg.get_filename() if resp == Gtk.ResponseType.OK else ""
    dlg.destroy()

    if not archive:
        return

    archive_path = Path(archive)
    suffix = archive_path.suffix.lower()

    if suffix not in (".dt", ".zip"):
        _message(mod, parent, "Неверный тип архива", "Выбери файл .dt или .zip.", "warning")
        return

    if suffix == ".zip" and str(base.get("type") or "").lower() != "file":
        _message(
            mod,
            parent,
            "ZIP восстановление недоступно",
            "ZIP-восстановление заменяет файл 1Cv8.1CD и доступно только для файловых баз. Для серверной базы выбери .dt.",
            "warning",
        )
        return

    _confirm_restore(mod, parent, base, archive_path)


def _confirm_restore(mod, parent, base: dict, archive_path: Path):
    Gtk, _GLib = _gtk()
    if Gtk is None:
        return

    is_dt = archive_path.suffix.lower() == ".dt"
    base_name = _safe_name(base.get("name"), "base")

    dlg = Gtk.Dialog(
        title=f"Восстановить базу: {base_name}",
        transient_for=parent,
        flags=0,
    )
    dlg.set_modal(True)
    dlg.set_default_size(720, 360)
    dlg.add_buttons("Отмена", Gtk.ResponseType.CANCEL, "Запустить восстановление", Gtk.ResponseType.OK)

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
        ent = Gtk.Entry()
        ent.set_text(str(text or ""))
        grid.attach(ent, 1, row, 2, 1)
        return ent

    add_label("База:")
    base_lbl = Gtk.Label(label=f"{base.get('name')} [{base.get('type')}] {base.get('path')}")
    base_lbl.set_xalign(0)
    grid.attach(base_lbl, 1, row, 2, 1)
    row += 1

    add_label("Архив:")
    archive_lbl = Gtk.Label(label=str(archive_path))
    archive_lbl.set_xalign(0)
    archive_lbl.set_selectable(True)
    grid.attach(archive_lbl, 1, row, 2, 1)
    row += 1

    add_label("Режим:")
    mode_text = "Загрузка .dt через 1С /RestoreIB" if is_dt else "Распаковка 1Cv8.1CD из ZIP во временную папку и замена файла базы"
    mode_lbl = Gtk.Label(label=mode_text)
    mode_lbl.set_xalign(0)
    grid.attach(mode_lbl, 1, row, 2, 1)
    row += 1

    user_entry = None
    pwd_entry = None

    if is_dt:
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

    warn = Gtk.Label()
    warn.set_xalign(0)
    warn.set_markup(
        "<b>Внимание:</b> восстановление заменит текущие данные выбранной базы. "
        "Перед восстановлением закрой пользователей и фоновые процессы 1С."
    )
    grid.attach(warn, 0, row, 3, 1)
    row += 1

    if not is_dt:
        note = Gtk.Label()
        note.set_xalign(0)
        note.set_markup(
            "<small>ZIP не копируется во временную папку. Из архива сразу извлекается файл 1Cv8.1CD во временный каталог, "
            "после успешной замены временный файл удаляется.</small>"
        )
        grid.attach(note, 0, row, 3, 1)

    area.show_all()

    resp = dlg.run()

    if resp != Gtk.ResponseType.OK:
        dlg.destroy()
        return

    user = user_entry.get_text().strip() if user_entry is not None else ""
    password = pwd_entry.get_text() if pwd_entry is not None else ""

    dlg.destroy()

    _append_log(mod, "")
    _append_log(mod, "=== Запущено восстановление из архива ===")
    _append_log(mod, f"База: {base.get('name')}")
    _append_log(mod, f"Тип базы: {base.get('type')}")
    _append_log(mod, f"Путь / сервер: {base.get('path')}")
    _append_log(mod, f"Архив: {archive_path}")

    _message(
        mod,
        parent,
        "Восстановление запущено",
        "Операция запущена в фоне. Ход и результат смотри во вкладке «Отчет».",
        "info",
    )

    threading.Thread(
        target=_restore_thread,
        args=(mod, parent, base, archive_path, user, password),
        daemon=True,
    ).start()


def _restore_thread(mod, parent, base: dict, archive_path: Path, user: str, password: str):
    try:
        if archive_path.suffix.lower() == ".dt":
            _restore_dt(mod, base, archive_path, user, password)
        elif archive_path.suffix.lower() == ".zip":
            _restore_zip_file_base(mod, base, archive_path)
        else:
            raise RuntimeError("Поддерживаются только .dt и .zip")

        _gtk_idle_message(
            mod,
            parent,
            "Восстановление завершено",
            f"База восстановлена из архива:\n{archive_path}",
            "info",
        )

    except Exception as e:
        _append_log(mod, f"ОШИБКА восстановления: {type(e).__name__}: {e}")
        _gtk_idle_message(
            mod,
            parent,
            "Ошибка восстановления",
            f"{type(e).__name__}: {e}",
            "error",
        )


def _gtk_idle_message(mod, parent, title: str, text: str, level: str = "info"):
    _Gtk, GLib = _gtk()

    if GLib is None:
        _message(mod, parent, title, text, level)
        return

    try:
        GLib.idle_add(lambda: (_message(mod, parent, title, text, level), False)[1])
    except Exception:
        _message(mod, parent, title, text, level)


def _reports_dir() -> Path:
    names = ("reports_dir", "report_dir", "logs_dir", "log_dir")

    try:
        AppConfig = _G.get("AppConfig")
        if AppConfig:
            cfg = AppConfig()
            if hasattr(cfg, "load"):
                cfg.load()

            for obj in (cfg, getattr(cfg, "settings", None)):
                try:
                    d = vars(obj)
                except Exception:
                    d = {}

                for name in names:
                    val = d.get(name)
                    if isinstance(val, str) and val.startswith("/"):
                        return Path(val)
    except Exception:
        pass

    return Path("/mnt/DataStore/Updater1C/1c-update-reports")


def _restore_dt(mod, base: dict, archive_path: Path, user: str, password: str):
    onec = _find_1c_exe(mod, base)
    if not onec:
        raise RuntimeError("Не найден исполняемый файл 1С 1cv8")

    base_type = str(base.get("type") or "").lower()
    base_path = str(base.get("path") or "").strip()

    if base_type not in ("file", "server"):
        raise RuntimeError("Восстановление .dt доступно только для файловых и серверных баз")

    if not base_path:
        raise RuntimeError("У выбранной базы не заполнен путь/сервер")

    reports_dir = _reports_dir()
    reports_dir.mkdir(parents=True, exist_ok=True)

    log_file = reports_dir / f"{_safe_name(base.get('name'), 'base')}_RestoreIB_{_stamp()}.log"

    cmd = [onec, "DESIGNER"]

    low = base_path.lower()

    if base_type == "file" or base_path.startswith("/"):
        cmd.append("/F" + base_path)
    else:
        if low.startswith(("http://", "https://")):
            raise RuntimeError("Для web-базы восстановление отключено. Выбери файловую или серверную базу.")
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

    cmd.extend(["/RestoreIB", str(archive_path), "/Out", str(log_file), "-NoTruncate"])

    real_cmd = _with_xvfb(cmd)

    _append_log(mod, "Режим: восстановление .dt через 1С /RestoreIB")
    _append_log(mod, "Команда: " + _cmd_join(real_cmd))
    _append_log(mod, f"Лог 1С: {log_file}")

    result = subprocess.run(real_cmd)

    if result.returncode != 0:
        raise RuntimeError(f"1С /RestoreIB завершилась с кодом {result.returncode}. Лог: {log_file}")

    _append_log(mod, "Восстановление .dt успешно завершено")


def _zip_find_1cd_member(zf: zipfile.ZipFile) -> str:
    names = zf.namelist()

    for name in names:
        if Path(name).name.lower() == "1cv8.1cd":
            return name

    for name in names:
        if Path(name).suffix.lower() == ".1cd":
            return name

    raise RuntimeError("В ZIP не найден файл 1Cv8.1CD")


def _restore_zip_file_base(mod, base: dict, archive_path: Path):
    base_type = str(base.get("type") or "").lower()
    if base_type != "file":
        raise RuntimeError("ZIP-восстановление доступно только для файловой базы")

    base_dir = Path(str(base.get("path") or "").strip())

    if not base_dir.exists():
        raise RuntimeError(f"Папка файловой базы не найдена: {base_dir}")

    dst_1cd = base_dir / "1Cv8.1CD"

    if not dst_1cd.exists():
        found = None
        for p in base_dir.iterdir():
            if p.name.lower() == "1cv8.1cd":
                found = p
                break
        if found is not None:
            dst_1cd = found

    tmp_dir = None
    success = False

    try:
        tmp_dir = Path(tempfile.mkdtemp(prefix="updater1c_restore_"))
        tmp_1cd = tmp_dir / "1Cv8.1CD"

        _append_log(mod, "Режим: восстановление файловой базы из ZIP")
        _append_log(mod, f"ZIP архив: {archive_path}")
        _append_log(mod, f"Временная папка: {tmp_dir}")
        _append_log(mod, "ZIP не копируется во временную папку; файл 1Cv8.1CD извлекается напрямую из архива.")

        with zipfile.ZipFile(archive_path, "r") as zf:
            member = _zip_find_1cd_member(zf)
            _append_log(mod, f"Файл в архиве: {member}")

            with zf.open(member, "r") as src, tmp_1cd.open("wb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)

        if not tmp_1cd.exists() or tmp_1cd.stat().st_size <= 0:
            raise RuntimeError("После распаковки во временную папку файл 1Cv8.1CD не создан или пустой")

        _append_log(mod, f"Извлечено во временный файл: {tmp_1cd}")
        _append_log(mod, f"Замена файла базы: {dst_1cd}")

        os.replace(str(tmp_1cd), str(dst_1cd))

        success = True
        _append_log(mod, "Файл базы 1Cv8.1CD успешно заменен")

    finally:
        if success and tmp_dir and tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)
            _append_log(mod, f"Временная папка удалена: {tmp_dir}")
        elif tmp_dir and tmp_dir.exists():
            _append_log(mod, f"Временная папка сохранена для диагностики ошибки: {tmp_dir}")
