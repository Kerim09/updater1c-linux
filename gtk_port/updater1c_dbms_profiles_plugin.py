# -*- coding: utf-8 -*-
"""
Профили СУБД для Обновлятор 1C Linux.

Что дает:
- отдельный список серверов СУБД;
- форма добавления/редактирования;
- хранение паролей через secret-tool;
- тест подключения;
- интеграция с формой "Архивировать базу", если установлен updater1c_backup_plugin.py.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from u1c_secret_service import clear as secret_clear
from u1c_secret_service import lookup as secret_lookup
from u1c_secret_service import store as secret_store
import sys
import threading
import uuid
from pathlib import Path


_G = {}
_INSTALLED = False
_UI_CONNECTED = set()
_BACKUP_PATCHED = False

CONFIG_DIR = Path.home() / ".config" / "updater1c-linux"
PROFILES_FILE = CONFIG_DIR / "dbms_profiles.json"
PROFILE_FIELDS = (
    "id", "name", "dbms_type", "host", "port", "database_or_template",
    "username", "password_saved", "comment", "is_default",
)


def install(globals_dict: dict):
    global _G, _INSTALLED

    _G = globals_dict or {}

    if _INSTALLED:
        return

    _INSTALLED = True

    Gtk, GLib = _gtk()

    if Gtk is not None and GLib is not None:
        try:
            GLib.timeout_add(800, _ui_scan)
            GLib.timeout_add(1200, _patch_backup_plugin_timer)
        except Exception as e:
            print("UPDATER1C_DBMS_PROFILES_TIMER_ERROR:", repr(e))


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


def _ensure_config_dir():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def _normalize_profile(profile: dict) -> dict:
    """Convert both legacy and current profile records to the 1.2.12 schema."""
    source = profile if isinstance(profile, dict) else {}
    dbms_type = source.get("dbms_type") or source.get("type") or "PostgreSQL"
    host = (
        source.get("host") or source.get("ops_address")
        or source.get("new_base_address") or source.get("server") or ""
    )
    database = (
        source.get("database_or_template") or source.get("db_name")
        or source.get("service_db") or source.get("template") or ""
    )
    username = source.get("username") or source.get("admin") or source.get("user") or ""
    return {
        "id": str(source.get("id") or uuid.uuid4()),
        "name": _safe_text(source.get("name") or host or dbms_type),
        "dbms_type": _safe_text(dbms_type),
        "host": _safe_text(host),
        "port": _safe_text(source.get("port")),
        "database_or_template": _safe_text(database),
        "username": _safe_text(username),
        "password_saved": bool(source.get("password_saved")),
        "comment": str(source.get("comment") or ""),
        "is_default": bool(source.get("is_default") or source.get("default")),
    }


def _load_profiles() -> list[dict]:
    _ensure_config_dir()

    if not PROFILES_FILE.exists():
        return []

    try:
        data = json.loads(PROFILES_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [_normalize_profile(item) for item in data if isinstance(item, dict)]
        if isinstance(data, dict) and isinstance(data.get("profiles"), list):
            return [_normalize_profile(item) for item in data["profiles"] if isinstance(item, dict)]
    except Exception as e:
        print("UPDATER1C_DBMS_LOAD_ERROR:", repr(e))

    return []


def _save_profiles(profiles: list[dict]):
    _ensure_config_dir()

    clean = [_normalize_profile(p) for p in profiles]
    default_seen = False
    for item in clean:
        if item["is_default"] and not default_seen:
            default_seen = True
        elif item["is_default"]:
            item["is_default"] = False

    PROFILES_FILE.write_text(
        json.dumps(clean, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _secret_attrs(profile_id: str) -> list[str]:
    return [
        "application", "updater1c-linux",
        "kind", "dbms-profile",
        "id", str(profile_id),
    ]


def _secret_store(profile_id: str, password: str) -> bool:
    if not password:
        return _secret_clear(profile_id)

    label = f"Обновлятор 1C Linux — пароль СУБД {profile_id}"
    attrs = dict(zip(_secret_attrs(profile_id)[::2], _secret_attrs(profile_id)[1::2]))
    return secret_store(f"dbms:{profile_id}", password, label, attributes=attrs)


def _secret_lookup(profile_id: str) -> str:
    attrs = dict(zip(_secret_attrs(profile_id)[::2], _secret_attrs(profile_id)[1::2]))
    return secret_lookup(f"dbms:{profile_id}", attributes=attrs)


def _secret_clear(profile_id: str) -> bool:
    attrs = dict(zip(_secret_attrs(profile_id)[::2], _secret_attrs(profile_id)[1::2]))
    return secret_clear(f"dbms:{profile_id}", attributes=attrs)


def _safe_text(text: str) -> str:
    return str(text or "").strip()


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


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


def _find_settings_button_parent(win):
    candidates = []

    for w in _flatten(win):
        txt = _norm(_widget_text(w))
        if txt in ("сохранить настройки", "сохранить"):
            try:
                parent = w.get_parent()
                if parent is not None:
                    candidates.append(parent)
            except Exception:
                pass

    if candidates:
        return candidates[0]

    for w in _flatten(win):
        txt = _norm(_widget_text(w))
        if "найти платформы" in txt or "платформы 1с" in txt:
            try:
                parent = w.get_parent()
                if parent is not None:
                    return parent
            except Exception:
                pass

    return None


def _ui_scan():
    try:
        Gtk, _GLib = _gtk()

        if Gtk is None:
            return True

        win = _main_window()

        if win is None:
            return True

        if id(win) in _UI_CONNECTED:
            return True

        parent = _find_settings_button_parent(win)

        if parent is None:
            return True

        btn = Gtk.Button.new_with_label("🗄 Серверы СУБД")
        btn.set_tooltip_text("Настроить список серверов СУБД для архивирования баз")
        btn.connect("clicked", lambda _b: _open_profiles_dialog(win))

        try:
            parent.pack_start(btn, False, False, 6)
        except Exception:
            try:
                parent.append(btn)
            except Exception:
                return True

        try:
            parent.show_all()
        except Exception:
            try:
                btn.show()
            except Exception:
                pass

        _UI_CONNECTED.add(id(win))

    except Exception as e:
        print("UPDATER1C_DBMS_UI_SCAN_ERROR:", repr(e))

    return True


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


def _new_profile() -> dict:
    return _normalize_profile({"id": str(uuid.uuid4())})


def _profile_display_db(p: dict) -> str:
    return p.get("database_or_template") or ""


def _fill_store(store, profiles):
    store.clear()

    for p in profiles:
        store.append([
            p.get("id", ""),
            p.get("name", ""),
            p.get("dbms_type", ""),
            p.get("host", ""),
            _profile_display_db(p),
            p.get("username", ""),
            "★" if p.get("is_default") else "",
        ])


def _selected_profile_id(tree) -> str:
    try:
        sel = tree.get_selection()
        model, it = sel.get_selected()
        if it is None:
            return ""
        return model.get_value(it, 0)
    except Exception:
        return ""


def _open_profiles_dialog(parent):
    Gtk, _GLib = _gtk()

    if Gtk is None:
        return

    profiles = _load_profiles()

    dlg = Gtk.Dialog(
        title="Серверы СУБД — Обновлятор 1C Linux",
        transient_for=parent,
        flags=0,
    )
    dlg.add_buttons("Закрыть", Gtk.ResponseType.CLOSE)
    dlg.set_default_size(920, 520)

    area = dlg.get_content_area()
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.set_border_width(10)
    area.add(box)

    toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    box.pack_start(toolbar, False, False, 0)

    add_btn = Gtk.Button.new_with_label("➕ Добавить")
    edit_btn = Gtk.Button.new_with_label("✏️ Изменить")
    del_btn = Gtk.Button.new_with_label("❌ Удалить")
    test_btn = Gtk.Button.new_with_label("🔌 Тест подключения")
    reload_btn = Gtk.Button.new_with_label("↻ Обновить")

    toolbar.pack_start(add_btn, False, False, 0)
    toolbar.pack_start(edit_btn, False, False, 0)
    toolbar.pack_start(del_btn, False, False, 0)
    toolbar.pack_start(test_btn, False, False, 0)
    toolbar.pack_end(reload_btn, False, False, 0)

    store = Gtk.ListStore(str, str, str, str, str, str, str)
    _fill_store(store, profiles)

    tree = Gtk.TreeView(model=store)
    tree.set_headers_visible(True)

    columns = [
        ("ID", 0, False),
        ("Название", 1, True),
        ("Тип", 2, True),
        ("Хост", 3, True),
        ("База / шаблон", 4, True),
        ("Пользователь", 5, True),
        ("По умолчанию", 6, True),
    ]

    for title, idx, visible in columns:
        renderer = Gtk.CellRendererText()
        col = Gtk.TreeViewColumn(title, renderer, text=idx)
        col.set_resizable(True)
        col.set_visible(visible)
        tree.append_column(col)

    scrolled = Gtk.ScrolledWindow()
    scrolled.set_hexpand(True)
    scrolled.set_vexpand(True)
    scrolled.add(tree)
    box.pack_start(scrolled, True, True, 0)

    hint = Gtk.Label()
    hint.set_xalign(0)
    hint.set_markup("<b>Подсказка:</b> пароль хранится только в системном Secret Service.")
    box.pack_start(hint, False, False, 0)

    def reload_store():
        nonlocal profiles
        profiles = _load_profiles()
        _fill_store(store, profiles)

    def save_and_reload():
        _save_profiles(profiles)
        reload_store()

    def find_selected():
        pid = _selected_profile_id(tree)
        if not pid:
            return None
        for p in profiles:
            if p.get("id") == pid:
                return p
        return None

    def on_add(_btn):
        p = _new_profile()
        edited = _edit_profile_dialog(dlg, p, is_new=True)
        if edited:
            if edited.get("is_default"):
                for item in profiles:
                    item["is_default"] = False
            profiles.append(edited)
            save_and_reload()

    def on_edit(_btn):
        p = find_selected()
        if not p:
            _message(dlg, "Серверы СУБД", "Выдели профиль СУБД для редактирования.", "warning")
            return

        edited = _edit_profile_dialog(dlg, dict(p), is_new=False)
        if edited:
            if edited.get("is_default"):
                for item in profiles:
                    item["is_default"] = False
            for i, item in enumerate(profiles):
                if item.get("id") == edited.get("id"):
                    profiles[i] = edited
                    break
            save_and_reload()

    def on_delete(_btn):
        p = find_selected()
        if not p:
            _message(dlg, "Серверы СУБД", "Выдели профиль СУБД для удаления.", "warning")
            return

        confirm = Gtk.MessageDialog(
            transient_for=dlg,
            flags=0,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text="Удалить профиль СУБД?",
        )
        confirm.format_secondary_text(p.get("name") or p.get("id"))
        resp = confirm.run()
        confirm.destroy()

        if resp != Gtk.ResponseType.OK:
            return

        profiles[:] = [x for x in profiles if x.get("id") != p.get("id")]
        _secret_clear(p.get("id", ""))
        save_and_reload()

    def on_test(_btn):
        p = find_selected()
        if not p:
            _message(dlg, "Серверы СУБД", "Выдели профиль СУБД для теста.", "warning")
            return

        threading.Thread(target=_test_profile_thread, args=(dlg, p), daemon=True).start()

    add_btn.connect("clicked", on_add)
    edit_btn.connect("clicked", on_edit)
    del_btn.connect("clicked", on_delete)
    test_btn.connect("clicked", on_test)
    reload_btn.connect("clicked", lambda _b: reload_store())
    tree.connect("row-activated", lambda *_args: on_edit(None))

    area.show_all()
    dlg.run()
    dlg.destroy()


def _edit_profile_dialog(parent, profile: dict, is_new: bool) -> dict | None:
    Gtk, _GLib = _gtk()

    if Gtk is None:
        return None

    dlg = Gtk.Dialog(
        title="Новый сервер СУБД" if is_new else f"СУБД {profile.get('name') or profile.get('id')}",
        transient_for=parent,
        flags=0,
    )
    dlg.add_buttons("Отмена", Gtk.ResponseType.CANCEL, "Сохранить", Gtk.ResponseType.OK)
    dlg.set_default_size(760, 620)

    area = dlg.get_content_area()
    grid = Gtk.Grid()
    grid.set_row_spacing(8)
    grid.set_column_spacing(8)
    grid.set_border_width(12)
    area.add(grid)

    row = 0

    def label(text):
        nonlocal row
        lbl = Gtk.Label(label=text)
        lbl.set_xalign(0)
        grid.attach(lbl, 0, row, 1, 1)
        return lbl

    def entry(value="", width=2):
        ent = Gtk.Entry()
        ent.set_text(str(value or ""))
        grid.attach(ent, 1, row, width, 1)
        return ent

    label("Id:")
    id_entry = entry(profile.get("id") or str(uuid.uuid4()))
    id_entry.set_sensitive(False)
    row += 1

    label("Название:")
    name_entry = entry(profile.get("name", ""))
    row += 1

    label("Тип СУБД:")
    type_combo = Gtk.ComboBoxText()
    for t in ("PostgreSQL", "MS SQL Server", "Oracle Database", "IBM DB2"):
        type_combo.append_text(t)

    current_type = profile.get("dbms_type") or "PostgreSQL"
    idx = {"PostgreSQL": 0, "MS SQL Server": 1, "Oracle Database": 2, "IBM DB2": 3}.get(current_type, 0)
    type_combo.set_active(idx)
    grid.attach(type_combo, 1, row, 2, 1)
    row += 1

    label("Хост:")
    ops_entry = entry(profile.get("host", ""))
    row += 1

    label("Порт:")
    port_entry = entry(profile.get("port", ""))
    row += 1

    label("База / шаблон:")
    db_name_entry = entry(profile.get("database_or_template", ""))
    row += 1

    label("Пользователь:")
    admin_entry = entry(profile.get("username", ""))
    row += 1

    label("Пароль:")
    pwd_entry = entry("")
    try:
        pwd_entry.set_visibility(False)
    except Exception:
        pass

    saved = bool(profile.get("password_saved"))
    if saved:
        pwd_entry.set_placeholder_text("Пароль сохранен в хранилище. Оставь пустым, чтобы не менять.")
    else:
        pwd_entry.set_placeholder_text("Пароль будет сохранен в системном хранилище")
    row += 1

    label("Использовать по умолчанию:")
    default_check = Gtk.CheckButton()
    default_check.set_active(bool(profile.get("is_default")))
    grid.attach(default_check, 1, row, 2, 1)
    row += 1

    label("Комментарий:")
    comment_view = Gtk.TextView()
    comment_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
    comment_buf = comment_view.get_buffer()
    comment_buf.set_text(profile.get("comment", "") or "")

    comment_scroll = Gtk.ScrolledWindow()
    comment_scroll.set_min_content_height(90)
    comment_scroll.add(comment_view)
    grid.attach(comment_scroll, 1, row, 2, 1)
    row += 1

    hint = Gtk.Label()
    hint.set_xalign(0)
    hint.set_markup(
        "<small>Для PostgreSQL обычно: адрес операций = host, порт = 5432, service DB = postgres, "
        "DB name = имя базы PostgreSQL. Для MS SQL Server: адрес операций = server[,port], DB name = база SQL.</small>"
    )
    grid.attach(hint, 0, row, 3, 1)

    area.show_all()

    resp = dlg.run()

    if resp != Gtk.ResponseType.OK:
        dlg.destroy()
        return None

    start, end = comment_buf.get_bounds()
    comment = comment_buf.get_text(start, end, True)

    pid = profile.get("id") or str(uuid.uuid4())

    result = {
        "id": pid,
        "name": _safe_text(name_entry.get_text()) or _safe_text(ops_entry.get_text()) or pid,
        "dbms_type": type_combo.get_active_text() or "PostgreSQL",
        "host": _safe_text(ops_entry.get_text()),
        "port": _safe_text(port_entry.get_text()),
        "username": _safe_text(admin_entry.get_text()),
        "password_saved": bool(profile.get("password_saved")),
        "database_or_template": _safe_text(db_name_entry.get_text()),
        "comment": comment,
        "is_default": bool(default_check.get_active()),
    }

    new_password = pwd_entry.get_text()

    if new_password:
        result["password_saved"] = _secret_store(pid, new_password)
    elif not result["password_saved"]:
        result["password_saved"] = False

    dlg.destroy()
    return result


def _cmd_from_bin(profile: dict, command: str) -> str:
    return shutil.which(command) or ""


def _test_profile_thread(parent, profile: dict):
    Gtk, GLib = _gtk()

    def done(title, text, level="info"):
        if GLib is not None:
            GLib.idle_add(lambda: (_message(parent, title, text, level), False)[1])
        else:
            _message(parent, title, text, level)

    try:
        typ = (profile.get("dbms_type") or "").lower()
        password = _secret_lookup(profile.get("id", ""))
        env = os.environ.copy()

        if "postgres" in typ:
            cmd_bin = _cmd_from_bin(profile, "pg_isready")
            if not cmd_bin:
                raise FileNotFoundError("pg_isready")
            host = profile.get("host") or "localhost"
            port = profile.get("port") or "5432"
            user = profile.get("username") or ""
            db = profile.get("database_or_template") or "postgres"

            cmd = [cmd_bin, "-h", host, "-p", str(port), "-d", db]
            if user:
                cmd += ["-U", user]
            if password:
                env["PGPASSWORD"] = password

            result = subprocess.run(cmd, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)

        elif "sql" in typ:
            cmd_bin = _cmd_from_bin(profile, "sqlcmd")
            if not cmd_bin:
                raise FileNotFoundError("sqlcmd")
            server = profile.get("host") or "localhost"
            port = profile.get("port") or ""

            if port and "," not in server:
                server = f"{server},{port}"

            cmd = [cmd_bin, "-S", server, "-Q", "SELECT 1"]

            user = profile.get("username") or ""
            if user:
                cmd += ["-U", user, "-P", password or ""]
            else:
                cmd += ["-E"]

            result = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)

        elif "oracle" in typ:
            cmd_bin = _cmd_from_bin(profile, "sqlplus")
            if not cmd_bin:
                raise FileNotFoundError("sqlplus")
            user = profile.get("username") or ""
            db = profile.get("database_or_template") or profile.get("host") or ""
            conn = f"{user}/{password}@{db}" if user else db

            result = subprocess.run(
                [cmd_bin, "-L", "-S", conn],
                input="select 1 from dual;\nexit;\n",
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
            )

        elif "db2" in typ:
            cmd_bin = _cmd_from_bin(profile, "db2cli")
            if not cmd_bin:
                raise FileNotFoundError("db2cli")
            db = profile.get("database_or_template") or profile.get("host") or ""

            result = subprocess.run(
                [cmd_bin, "validate", "-dsn", db],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
            )

        else:
            done("Тест СУБД", f"Неизвестный тип СУБД: {profile.get('dbms_type')}", "warning")
            return

        out = (result.stdout or "").strip()
        err = (result.stderr or "").strip()
        text = (out + "\n" + err).strip()

        if result.returncode == 0:
            done("Тест подключения успешен", text or "Команда завершилась успешно.", "info")
        else:
            done("Тест подключения не прошел", text or f"Код возврата: {result.returncode}", "error")

    except FileNotFoundError as e:
        done("Тест подключения не прошел", f"Не найдена утилита: {e}", "error")
    except subprocess.TimeoutExpired:
        done("Тест подключения не прошел", "Истек таймаут проверки подключения.", "error")
    except Exception as e:
        done("Тест подключения не прошел", f"{type(e).__name__}: {e}", "error")


def _profile_matches_base(profile: dict, base: dict) -> bool:
    return bool(profile.get("is_default"))


def _patch_backup_plugin_timer():
    try:
        _patch_backup_plugin()
    except Exception as e:
        print("UPDATER1C_DBMS_BACKUP_PATCH_TIMER_ERROR:", repr(e))

    return True


def _patch_backup_plugin():
    global _BACKUP_PATCHED

    if _BACKUP_PATCHED:
        return

    backup_mod = _G.get("_u1c_backup_mod")

    if backup_mod is None:
        backup_mod = sys.modules.get("updater1c_backup_plugin")

    if backup_mod is None:
        return

    required = ("_run_backup_thread", "_message")

    for name in required:
        if not hasattr(backup_mod, name):
            return

    backup_mod._open_backup_dialog = lambda parent, base: _enhanced_backup_dialog(backup_mod, parent, base)

    _BACKUP_PATCHED = True
    print("UPDATER1C: форма архивирования подключена к профилям СУБД")


def _choose_dir(entry, parent):
    Gtk, _GLib = _gtk()

    if Gtk is None:
        return

    dlg = Gtk.FileChooserDialog(
        title="Выбери папку для резервной копии",
        transient_for=parent,
        action=Gtk.FileChooserAction.SELECT_FOLDER,
    )
    dlg.add_buttons("Отмена", Gtk.ResponseType.CANCEL, "Выбрать", Gtk.ResponseType.OK)

    current = entry.get_text().strip()

    if current:
        try:
            dlg.set_filename(current)
        except Exception:
            pass

    resp = dlg.run()

    if resp == Gtk.ResponseType.OK:
        entry.set_text(dlg.get_filename())

    dlg.destroy()


def _enhanced_backup_dialog(backup_mod, parent, base: dict):
    Gtk, _GLib = _gtk()

    if Gtk is None:
        return

    profiles = _load_profiles()
    matched = [p for p in profiles if _profile_matches_base(p, base)]
    ordered_profiles = matched + [p for p in profiles if p not in matched]

    base_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(base.get("name") or "base")).strip("._-")
    base_type = (base.get("type") or "").lower()

    default_dir = Path("/mnt/DataStore/Updater1C/1c-backups") / base_name
    default_dir.mkdir(parents=True, exist_ok=True)

    dlg = Gtk.Dialog(
        title=f"Архивировать базу: {base_name}",
        transient_for=parent,
        flags=0,
    )
    dlg.add_buttons("Отмена", Gtk.ResponseType.CANCEL, "Запустить архивирование", Gtk.ResponseType.OK)
    dlg.set_default_size(820, 560)

    area = dlg.get_content_area()
    grid = Gtk.Grid()
    grid.set_row_spacing(8)
    grid.set_column_spacing(8)
    grid.set_border_width(12)
    area.add(grid)

    row = 0

    def label(text):
        nonlocal row
        lbl = Gtk.Label(label=text)
        lbl.set_xalign(0)
        grid.attach(lbl, 0, row, 1, 1)
        return lbl

    def entry(text=""):
        ent = Gtk.Entry()
        ent.set_text(str(text or ""))
        grid.attach(ent, 1, row, 2, 1)
        return ent

    label("База:")
    base_lbl = Gtk.Label(label=f"{base.get('name')}   [{base.get('type')}]   {base.get('path')}")
    base_lbl.set_xalign(0)
    grid.attach(base_lbl, 1, row, 2, 1)
    row += 1

    label("Режим:")
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

    label("Профиль СУБД:")
    profile_combo = Gtk.ComboBoxText()
    profile_combo.append_text("Не использовать профиль СУБД")

    profile_ids = [""]

    for p in ordered_profiles:
        mark = "★ " if p in matched else ""
        profile_combo.append_text(f"{mark}{p.get('name')} — {p.get('dbms_type')} — {p.get('host')}")
        profile_ids.append(p.get("id", ""))

    profile_combo.set_active(1 if matched else 0)
    grid.attach(profile_combo, 1, row, 2, 1)
    row += 1

    label("Папка результата:")
    out_dir_entry = Gtk.Entry()
    out_dir_entry.set_text(str(default_dir))
    grid.attach(out_dir_entry, 1, row, 1, 1)

    dir_btn = Gtk.Button(label="...")
    dir_btn.connect("clicked", lambda _b: _choose_dir(out_dir_entry, dlg))
    grid.attach(dir_btn, 2, row, 1, 1)
    row += 1

    sep1 = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
    grid.attach(sep1, 0, row, 3, 1)
    row += 1

    label("Пользователь 1С:")
    user_entry = entry("")
    row += 1

    label("Пароль 1С:")
    pwd_entry = entry("")
    try:
        pwd_entry.set_visibility(False)
    except Exception:
        pass
    row += 1

    sep2 = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
    grid.attach(sep2, 0, row, 3, 1)
    row += 1

    title = Gtk.Label()
    title.set_markup("<b>Параметры СУБД</b>")
    title.set_xalign(0)
    grid.attach(title, 0, row, 3, 1)
    row += 1

    label("DB host / server:")
    db_host_entry = entry("")
    row += 1

    label("DB port:")
    db_port_entry = entry("")
    row += 1

    label("DB name:")
    db_name_entry = entry("")
    row += 1

    label("DB user:")
    db_user_entry = entry("")
    row += 1

    label("DB password:")
    db_pwd_entry = entry("")
    try:
        db_pwd_entry.set_visibility(False)
    except Exception:
        pass
    row += 1

    hint = Gtk.Label()
    hint.set_xalign(0)
    hint.set_markup(
        "<small>Если профиль СУБД связан с выбранной базой, он отмечен звездочкой и подставляется первым.</small>"
    )
    grid.attach(hint, 0, row, 3, 1)

    def selected_profile():
        idx = profile_combo.get_active()
        if idx is None or idx < 0 or idx >= len(profile_ids):
            return None

        pid = profile_ids[idx]

        if not pid:
            return None

        for p in ordered_profiles:
            if p.get("id") == pid:
                return p

        return None

    def apply_profile(_combo=None):
        p = selected_profile()

        if not p:
            return

        db_host_entry.set_text(p.get("host") or "")
        db_port_entry.set_text(p.get("port") or "")
        db_name_entry.set_text(p.get("database_or_template") or "")
        db_user_entry.set_text(p.get("username") or "")

        pwd = _secret_lookup(p.get("id", ""))

        if pwd:
            db_pwd_entry.set_text(pwd)

        typ = (p.get("dbms_type") or "").lower()

        if "postgres" in typ:
            for i in range(3):
                try:
                    if "PostgreSQL" in (mode_combo.get_model()[i][0]):
                        mode_combo.set_active(i)
                        break
                except Exception:
                    pass

        if "sql" in typ:
            for i in range(3):
                try:
                    if "Microsoft SQL" in (mode_combo.get_model()[i][0]):
                        mode_combo.set_active(i)
                        break
                except Exception:
                    pass

    profile_combo.connect("changed", apply_profile)

    if matched:
        apply_profile()

    area.show_all()

    resp = dlg.run()

    if resp != Gtk.ResponseType.OK:
        dlg.destroy()
        return

    args = {
        "parent": parent,
        "base": base,
        "mode": mode_combo.get_active_text() or "",
        "out_dir": Path(out_dir_entry.get_text().strip() or str(default_dir)),
        "user": user_entry.get_text().strip(),
        "password": pwd_entry.get_text(),
        "db_host": db_host_entry.get_text().strip(),
        "db_port": db_port_entry.get_text().strip(),
        "db_name": db_name_entry.get_text().strip(),
        "db_user": db_user_entry.get_text().strip(),
        "db_pwd": db_pwd_entry.get_text(),
    }

    dlg.destroy()

    threading.Thread(
        target=backup_mod._run_backup_thread,
        kwargs=args,
        daemon=True,
    ).start()
