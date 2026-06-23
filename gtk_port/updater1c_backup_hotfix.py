# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime
import os
import re
import sys
import threading
import zipfile
from pathlib import Path


_G = {}
_INSTALLED = False
_PATCHED = False


def install(globals_dict: dict):
    global _G, _INSTALLED
    _G = globals_dict or {}

    if _INSTALLED:
        return

    _INSTALLED = True

    Gtk, GLib = _gtk()
    if Gtk is not None and GLib is not None:
        GLib.timeout_add(700, _patch_backup_plugin_timer)


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
    return sys.modules.get("updater1c_backup_plugin")


def _dbms_mod():
    mod = _G.get("_u1c_dbms_mod")
    if mod is not None:
        return mod
    return sys.modules.get("updater1c_dbms_profiles_plugin")


def _patch_backup_plugin_timer():
    global _PATCHED

    try:
        mod = _backup_mod()
        if mod is None:
            return True

        # Повторно патчим, потому что модуль профилей СУБД мог позже перезаписать форму архивации.
        mod._open_backup_dialog = lambda parent, base: _open_backup_dialog_fixed(mod, parent, base)
        mod._output_file = lambda out_dir, base, ext, suffix="": _output_file_fixed(out_dir, base, ext, suffix)

        if hasattr(mod, "_backup_file_zip"):
            mod._backup_file_zip = lambda base, out_dir: _backup_file_zip_fixed(mod, base, out_dir)

        if not _PATCHED:
            print("UPDATER1C: backup hotfix установлен")
            _PATCHED = True

    except Exception as e:
        print("UPDATER1C_BACKUP_HOTFIX_PATCH_ERROR:", repr(e))

    return True


def _safe_name(text: str, default: str = "base") -> str:
    text = str(text or "").strip()
    text = re.sub(r"\s+", "", text)
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text)
    text = re.sub(r"_+", "_", text).strip("._-")
    return text or default


def _safe_release(text: str) -> str:
    text = str(text or "").strip()
    if not text or text in ("-", "auto"):
        return ""
    text = re.sub(r"[^0-9A-Za-zА-Яа-я._-]+", "_", text).strip("._-")
    return text


def _release_from_base(base: dict) -> str:
    for key in (
        "version",
        "config_version",
        "configuration_version",
        "conf_version",
        "release",
        "configRelease",
    ):
        val = _safe_release(base.get(key, ""))
        if val and not val.startswith(("8.3", "8.5")):
            return val

    # Иногда версия попадает текстом в конфигурацию.
    for key in ("config", "configuration", "description"):
        text = str(base.get(key, "") or "")
        m = re.search(r"(\d+\.\d+\.\d+(?:\.\d+)*)", text)
        if m:
            val = _safe_release(m.group(1))
            if val and not val.startswith(("8.3", "8.5")):
                return val

    return ""


def _stamp() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def _output_file_fixed(out_dir, base: dict, ext: str, suffix: str = "") -> Path:
    out_dir = Path(out_dir)
    base_name = _safe_name(base.get("name"), "base")
    release = _release_from_base(base)
    ext = ext.lstrip(".")

    # Для ZIP по требованию: НазваниеБазы_Релиз_Дата.zip, без суффикса 1CD в имени архива.
    if ext.lower() == "zip":
        if release:
            name = f"{base_name}_{release}_{_stamp()}.{ext}"
        else:
            name = f"{base_name}_безРелиза_{_stamp()}.{ext}"
        return out_dir / name

    # Для остальных бэкапов оставляем понятный суффикс, если он нужен.
    suffix_part = f"_{_safe_name(suffix)}" if suffix else ""
    if release:
        name = f"{base_name}_{release}{suffix_part}_{_stamp()}.{ext}"
    else:
        name = f"{base_name}_безРелиза{suffix_part}_{_stamp()}.{ext}"

    return out_dir / name


def _append_log(mod, text: str):
    try:
        mod._append_log(text)
    except Exception:
        print(text)


def _message(mod, parent, title: str, text: str, level: str = "info"):
    try:
        mod._message(parent, title, text, level)
    except Exception:
        print(f"{title}: {text}")


def _backup_file_zip_fixed(mod, base: dict, out_dir) -> Path:
    out_dir = Path(out_dir)
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

    out_file = _output_file_fixed(out_dir, base, "zip")

    _append_log(mod, "")
    _append_log(mod, f"=== Архивирование файловой базы в ZIP: {base.get('name')} ===")
    _append_log(mod, f"Источник: {onecd}")
    _append_log(mod, f"Архив: {out_file}")
    _append_log(mod, "Внутри ZIP имя файла остается: 1Cv8.1CD")

    out_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(out_file, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        zf.write(onecd, arcname=onecd.name)

    _append_log(mod, f"ZIP-архив создан: {out_file}")
    return out_file


def _load_dbms_profiles():
    dbms = _dbms_mod()
    if dbms is None:
        return [], [], None

    try:
        profiles = dbms._load_profiles()
    except Exception:
        profiles = []

    return profiles, [], dbms


def _profile_matches(dbms, profile, base) -> bool:
    if dbms is None:
        return False
    try:
        return bool(dbms._profile_matches_base(profile, base))
    except Exception:
        return False


def _secret_lookup(dbms, profile_id: str) -> str:
    if dbms is None:
        return ""
    try:
        return dbms._secret_lookup(profile_id)
    except Exception:
        return ""


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


def _combo_set_by_contains(combo, text_part: str):
    text_part = text_part.lower()
    try:
        model = combo.get_model()
        for i, row in enumerate(model):
            text = str(row[0])
            if text_part in text.lower():
                combo.set_active(i)
                return True
    except Exception:
        pass
    return False


def _open_backup_dialog_fixed(mod, parent, base: dict):
    Gtk, GLib = _gtk()
    if Gtk is None:
        return

    base_name = _safe_name(base.get("name"), "base")
    base_type = str(base.get("type") or "").lower()
    base_path = str(base.get("path") or "")
    release = _release_from_base(base)

    default_dir = Path("/mnt/DataStore/Updater1C/1c-backups") / base_name
    default_dir.mkdir(parents=True, exist_ok=True)

    profiles, _unused, dbms = _load_dbms_profiles()
    matched = [p for p in profiles if _profile_matches(dbms, p, base)]
    ordered_profiles = matched + [p for p in profiles if p not in matched]

    dlg = Gtk.Dialog(
        title=f"Архивировать базу: {base_name}",
        transient_for=parent,
        flags=0,
    )
    dlg.set_modal(True)
    dlg.set_default_size(840, 560)

    cancel_btn = dlg.add_button("Отмена", Gtk.ResponseType.CANCEL)
    start_btn = dlg.add_button("Запустить архивирование", Gtk.ResponseType.OK)
    start_btn.set_can_default(True)
    start_btn.grab_default()

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
    info = f"{base.get('name')} [{base.get('type')}] {base_path}"
    if release:
        info += f"    релиз: {release}"
    else:
        info += "    релиз: не определен"
    info_lbl = Gtk.Label(label=info)
    info_lbl.set_xalign(0)
    grid.attach(info_lbl, 1, row, 2, 1)
    row += 1

    add_label("Режим:")
    mode_combo = Gtk.ComboBoxText()
    if base_type == "file":
        mode_combo.append_text("Файловая база: выгрузка в .dt через 1С")
        mode_combo.append_text("Файловая база: архивировать 1Cv8.1CD в .zip")
    else:
        mode_combo.append_text("Серверная/веб база: выгрузка в .dt через 1С")
        mode_combo.append_text("PostgreSQL: pg_dump")
        mode_combo.append_text("Microsoft SQL Server: BACKUP DATABASE через sqlcmd")
    mode_combo.set_active(0)
    grid.attach(mode_combo, 1, row, 2, 1)
    row += 1

    add_label("Профиль СУБД:")
    profile_combo = Gtk.ComboBoxText()
    profile_ids = [""]
    profile_combo.append_text("Не использовать профиль СУБД")

    for p in ordered_profiles:
        mark = "★ " if p in matched else ""
        profile_combo.append_text(f"{mark}{p.get('name')} — {p.get('type')} — {p.get('ops_address')}")
        profile_ids.append(p.get("id", ""))

    profile_combo.set_active(1 if matched else 0)
    grid.attach(profile_combo, 1, row, 2, 1)
    row += 1

    add_label("Папка результата:")
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

    sep2 = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
    grid.attach(sep2, 0, row, 3, 1)
    row += 1

    title = Gtk.Label()
    title.set_markup("<b>Параметры СУБД</b>")
    title.set_xalign(0)
    grid.attach(title, 0, row, 3, 1)
    row += 1

    add_label("DB host / server:")
    db_host_entry = add_entry("")
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

    hint = Gtk.Label()
    hint.set_xalign(0)
    hint.set_markup(
        "<small>После нажатия «Запустить архивирование» форма закрывается, "
        "а ход операции пишется во вкладку «Отчет».</small>"
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

        db_host_entry.set_text(p.get("ops_address") or "")
        db_port_entry.set_text(p.get("port") or "")
        db_name_entry.set_text(p.get("db_name") or p.get("service_db") or "")
        db_user_entry.set_text(p.get("admin") or "")

        pwd = _secret_lookup(dbms, p.get("id", ""))
        if pwd:
            db_pwd_entry.set_text(pwd)

        typ = str(p.get("type") or "").lower()
        if "postgres" in typ:
            _combo_set_by_contains(mode_combo, "PostgreSQL")
        elif "sql" in typ:
            _combo_set_by_contains(mode_combo, "Microsoft SQL")

    profile_combo.connect("changed", apply_profile)
    if matched:
        apply_profile()

    launched = {"value": False}

    def launch():
        if launched["value"]:
            return
        launched["value"] = True

        mode = mode_combo.get_active_text() or ""
        out_dir = Path(out_dir_entry.get_text().strip() or str(default_dir))

        args = {
            "parent": parent,
            "base": base,
            "mode": mode,
            "out_dir": out_dir,
            "user": user_entry.get_text().strip(),
            "password": pwd_entry.get_text(),
            "db_host": db_host_entry.get_text().strip(),
            "db_port": db_port_entry.get_text().strip(),
            "db_name": db_name_entry.get_text().strip(),
            "db_user": db_user_entry.get_text().strip(),
            "db_pwd": db_pwd_entry.get_text(),
        }

        expected = _output_file_fixed(out_dir, base, "zip" if ".zip" in mode else "dt")

        _append_log(mod, "")
        _append_log(mod, "=== Запущено архивирование базы ===")
        _append_log(mod, f"База: {base.get('name')}")
        _append_log(mod, f"Режим: {mode}")
        _append_log(mod, f"Папка результата: {out_dir}")
        if ".zip" in mode:
            _append_log(mod, f"Ожидаемое имя ZIP: {expected.name}")
            _append_log(mod, "Файл внутри архива остается без переименования: 1Cv8.1CD")
        if not release:
            _append_log(mod, "ВНИМАНИЕ: релиз выбранной базы не определен, в имени будет использовано 'безРелиза'.")

        try:
            dlg.destroy()
        except Exception:
            pass

        _message(
            mod,
            parent,
            "Архивирование запущено",
            "Операция запущена в фоне. Ход и результат смотри во вкладке «Отчет».",
            "info",
        )

        threading.Thread(
            target=mod._run_backup_thread,
            kwargs=args,
            daemon=True,
        ).start()

    def on_response(_dlg, response_id):
        if response_id == Gtk.ResponseType.OK:
            launch()
        else:
            try:
                dlg.destroy()
            except Exception:
                pass

    dlg.connect("response", on_response)
    start_btn.connect("clicked", lambda _b: launch())
    cancel_btn.connect("clicked", lambda _b: dlg.destroy())

    area.show_all()
    dlg.show_all()

    # Держим ссылку, чтобы окно не собрал GC.
    try:
        parent._u1c_backup_hotfix_dialog = dlg
    except Exception:
        pass
