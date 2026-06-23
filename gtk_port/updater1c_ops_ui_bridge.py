# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path


_G = {}
_INSTALLED = False
_PATCHED_LOG = False
_PATCHED_BACKUP_THREAD = False
_PATCHED_RESTORE_THREAD = False
_PATCHED_ZIP_BACKUP = False
_PATCHED_ZIP_RESTORE = False
_CONNECTED_RESTORE_BUTTONS = set()
_PROGRESS_ACTIVE = False
_PROGRESS_TEXT = ""


def install(globals_dict: dict):
    global _G, _INSTALLED
    _G = globals_dict or {}

    if _INSTALLED:
        return

    _INSTALLED = True

    Gtk, GLib = _gtk()
    if Gtk is not None and GLib is not None:
        try:
            GLib.timeout_add(400, _patch_timer)
            GLib.timeout_add(250, _pulse_timer)
        except Exception as e:
            print("UPDATER1C_OPS_UI_BRIDGE_TIMER_ERROR:", repr(e))


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


def _restore_mod():
    mod = _G.get("_u1c_restore_mod")
    if mod is not None:
        return mod
    return sys.modules.get("updater1c_restore_plugin")


def _patch_timer():
    try:
        _patch_backup_log()
        _patch_backup_thread()
        _patch_backup_zip()
        _patch_restore_thread()
        _patch_restore_zip()
        _bind_report_restore_button()
    except Exception as e:
        print("UPDATER1C_OPS_UI_BRIDGE_PATCH_ERROR:", repr(e))

    return True


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
                if val is not None:
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


def _output_zip_path(out_dir, base: dict) -> Path:
    out_dir = Path(out_dir)
    base_name = _safe_name(base.get("name"), "base")
    release = _release_from_base(base)

    if release:
        return out_dir / f"{base_name}_{release}_{_stamp()}.zip"

    return out_dir / f"{base_name}_безРелиза_{_stamp()}.zip"


def _idle(fn):
    _Gtk, GLib = _gtk()

    if GLib is None:
        try:
            fn()
        except Exception:
            pass
        return

    try:
        GLib.idle_add(lambda: (fn(), False)[1])
    except Exception:
        try:
            fn()
        except Exception:
            pass


def _append_to_report(text: str):
    text = str(text).rstrip("\n")

    def apply():
        win = _main_window()
        if win is None:
            return

        for w in _flatten(win):
            if "textview" not in type(w).__name__.lower():
                continue

            try:
                buf = w.get_buffer()
                end = buf.get_end_iter()
                buf.insert(end, text + "\n")
            except Exception:
                pass

    _idle(apply)


def _patch_backup_log():
    global _PATCHED_LOG

    mod = _backup_mod()
    if mod is None or not hasattr(mod, "_append_log"):
        return

    if getattr(mod._append_log, "_u1c_ops_ui_bridge_log", False):
        _PATCHED_LOG = True
        return

    original = mod._append_log

    def append_log_wrapper(text: str):
        try:
            original(text)
        except Exception:
            try:
                print(text)
            except Exception:
                pass

        # Принудительно дублируем в отчет, потому что внешние операции ZIP/restore
        # раньше могли не попадать в TextView отчета.
        try:
            _append_to_report(str(text))
        except Exception:
            pass

    append_log_wrapper._u1c_ops_ui_bridge_log = True
    append_log_wrapper._u1c_ops_ui_bridge_original = original
    mod._append_log = append_log_wrapper
    _PATCHED_LOG = True


def _append_log(text: str):
    mod = _backup_mod()
    if mod is not None and hasattr(mod, "_append_log"):
        try:
            mod._append_log(text)
            return
        except Exception:
            pass

    print(text)
    _append_to_report(text)


def _message(parent, title: str, text: str, level: str = "info"):
    mod = _backup_mod()

    if mod is not None and hasattr(mod, "_message"):
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


def _is_label(w):
    try:
        return "label" in type(w).__name__.lower() and hasattr(w, "set_text")
    except Exception:
        return False


def _pos(root, w):
    try:
        ok, x, y = w.translate_coordinates(root, 0, 0)
        if ok:
            a = w.get_allocation()
            return int(x), int(y), int(a.width), int(a.height)
    except Exception:
        pass

    try:
        a = w.get_allocation()
        return int(a.x), int(a.y), int(a.width), int(a.height)
    except Exception:
        return 0, 0, 0, 0


def _find_value_label_on_row(root, caption):
    flat = _flatten(root)
    cap_norm = _norm(caption)

    caption_widget = None

    for w in flat:
        if not _is_label(w):
            continue

        txt = _norm(_widget_text(w))
        if txt == cap_norm:
            caption_widget = w
            break

    if caption_widget is None:
        return None

    lx, ly, lw, lh = _pos(root, caption_widget)
    lcy = ly + lh / 2

    best = None
    best_score = None

    for w in flat:
        if w is caption_widget or not _is_label(w):
            continue

        txt = _widget_text(w)
        if _norm(txt).endswith(":"):
            continue

        x, y, ww, wh = _pos(root, w)
        cy = y + wh / 2

        if x <= lx:
            continue

        dy = abs(cy - lcy)
        if dy > max(18, lh, wh):
            continue

        score = dy * 1000 + abs(x - lx)
        if best is None or score < best_score:
            best = w
            best_score = score

    return best


def _set_operation(base="-", release="-", step="-", action="-", mode="-", status="-", pid="-", started="-"):
    def apply():
        win = _main_window()
        if win is None:
            return

        mapping = {
            "База:": base,
            "Релиз:": release,
            "Шаг:": step,
            "Действие:": action,
            "Режим:": mode,
            "Статус:": status,
            "PID процесса:": pid,
            "Время запуска:": started,
        }

        for caption, value in mapping.items():
            lbl = _find_value_label_on_row(win, caption)
            if lbl is not None:
                try:
                    lbl.set_text(str(value))
                except Exception:
                    pass

    _idle(apply)


def _set_progress(fraction=None, text=None, active=None):
    global _PROGRESS_ACTIVE, _PROGRESS_TEXT

    if active is not None:
        _PROGRESS_ACTIVE = bool(active)

    if text is not None:
        _PROGRESS_TEXT = str(text)

    def apply():
        win = _main_window()
        if win is None:
            return

        for w in _flatten(win):
            if "progressbar" not in type(w).__name__.lower():
                continue

            try:
                w.set_show_text(True)
            except Exception:
                pass

            if text is not None:
                try:
                    w.set_text(str(text))
                except Exception:
                    pass

            if fraction is not None:
                try:
                    w.set_fraction(max(0.0, min(1.0, float(fraction))))
                except Exception:
                    pass

    _idle(apply)


def _pulse_timer():
    if _PROGRESS_ACTIVE:
        def apply():
            win = _main_window()
            if win is None:
                return

            for w in _flatten(win):
                if "progressbar" not in type(w).__name__.lower():
                    continue

                try:
                    w.set_show_text(True)
                    if _PROGRESS_TEXT:
                        w.set_text(_PROGRESS_TEXT)
                    w.pulse()
                except Exception:
                    pass

        _idle(apply)

    return True


def _current_base():
    mod = _backup_mod()
    if mod is not None:
        try:
            base = mod._current_base_from_ui()
            if base:
                return base
        except Exception:
            pass

    rmod = _restore_mod()
    if rmod is not None:
        try:
            base = rmod._current_base_from_ui()
            if base:
                return base
        except Exception:
            pass

    return {}


def _patch_backup_thread():
    global _PATCHED_BACKUP_THREAD

    mod = _backup_mod()
    if mod is None or not hasattr(mod, "_run_backup_thread"):
        return

    if getattr(mod._run_backup_thread, "_u1c_ops_ui_bridge_backup_thread", False):
        _PATCHED_BACKUP_THREAD = True
        return

    original = mod._run_backup_thread

    def run_backup_thread_wrapper(**kwargs):
        base = kwargs.get("base") or {}
        mode = kwargs.get("mode") or "-"
        out_dir = kwargs.get("out_dir") or "-"
        base_name = base.get("name") or "-"
        release = _release_from_base(base) or "-"

        _set_operation(
            base=base_name,
            release=release,
            step="1/1",
            action="Архивирование базы",
            mode=mode,
            status="выполняется",
            pid="-",
            started=datetime.datetime.now().strftime("%H:%M:%S"),
        )
        _set_progress(0.02, "архивирование...", True)

        _append_log("")
        _append_log("=== Запущено архивирование базы ===")
        _append_log(f"База: {base_name}")
        _append_log(f"Релиз: {release}")
        _append_log(f"Режим: {mode}")
        _append_log(f"Папка результата: {out_dir}")

        try:
            result = original(**kwargs)
            _set_operation(
                base=base_name,
                release=release,
                step="1/1",
                action="Архивирование базы",
                mode=mode,
                status="завершено",
                pid="-",
                started="-",
            )
            _set_progress(1.0, "завершено", False)
            _append_log("=== Архивирование завершено ===")
            return result

        except Exception as e:
            _set_operation(
                base=base_name,
                release=release,
                step="1/1",
                action="Архивирование базы",
                mode=mode,
                status="ошибка",
                pid="-",
                started="-",
            )
            _set_progress(1.0, "ошибка", False)
            _append_log(f"ОШИБКА архивирования: {type(e).__name__}: {e}")
            raise

    run_backup_thread_wrapper._u1c_ops_ui_bridge_backup_thread = True
    run_backup_thread_wrapper._u1c_ops_ui_bridge_original = original
    mod._run_backup_thread = run_backup_thread_wrapper
    _PATCHED_BACKUP_THREAD = True


def _patch_backup_zip():
    global _PATCHED_ZIP_BACKUP

    mod = _backup_mod()
    if mod is None:
        return

    if hasattr(mod, "_backup_file_zip") and getattr(mod._backup_file_zip, "_u1c_ops_ui_bridge_zip_backup", False):
        _PATCHED_ZIP_BACKUP = True
        return

    def backup_file_zip_progress(base: dict, out_dir) -> Path:
        out_dir = Path(out_dir)
        base_path = Path(str(base.get("path") or ""))

        if not base_path.exists():
            raise RuntimeError(f"Папка файловой базы не найдена: {base_path}")

        onecd = None
        for p in base_path.iterdir():
            if p.name.lower() == "1cv8.1cd":
                onecd = p
                break

        if onecd is None:
            raise RuntimeError(f"В папке базы не найден файл 1Cv8.1CD: {base_path}")

        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = _output_zip_path(out_dir, base)

        total = max(onecd.stat().st_size, 1)
        copied = 0
        last_log = 0

        _append_log("")
        _append_log(f"=== Архивирование файловой базы в ZIP: {base.get('name')} ===")
        _append_log(f"Источник: {onecd}")
        _append_log(f"Размер 1Cv8.1CD: {total / 1024 / 1024:.1f} МБ")
        _append_log(f"Архив: {out_file}")
        _append_log("Внутри ZIP имя файла остается: 1Cv8.1CD")

        _set_operation(
            base=base.get("name") or "-",
            release=_release_from_base(base) or "-",
            step="1/1",
            action="Архивирование 1Cv8.1CD в ZIP",
            mode="ZIP",
            status="выполняется",
            pid="-",
            started=datetime.datetime.now().strftime("%H:%M:%S"),
        )
        _set_progress(0.01, "архивирование ZIP...", True)

        info = zipfile.ZipInfo(onecd.name)
        try:
            mtime = datetime.datetime.fromtimestamp(onecd.stat().st_mtime)
            info.date_time = (mtime.year, mtime.month, mtime.day, mtime.hour, mtime.minute, mtime.second)
        except Exception:
            pass
        info.compress_type = zipfile.ZIP_DEFLATED

        with zipfile.ZipFile(out_file, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            with onecd.open("rb") as src, zf.open(info, "w") as dst:
                while True:
                    chunk = src.read(8 * 1024 * 1024)
                    if not chunk:
                        break

                    dst.write(chunk)
                    copied += len(chunk)

                    fraction = min(0.98, copied / total)
                    _set_progress(fraction, f"ZIP {fraction * 100:.0f}%", True)

                    if copied - last_log >= 512 * 1024 * 1024 or copied == total:
                        last_log = copied
                        _append_log(f"Сжато: {copied / 1024 / 1024:.1f} из {total / 1024 / 1024:.1f} МБ")

        _set_progress(1.0, "ZIP готов", False)
        _append_log(f"ZIP-архив создан: {out_file}")
        return out_file

    backup_file_zip_progress._u1c_ops_ui_bridge_zip_backup = True
    mod._backup_file_zip = backup_file_zip_progress
    _PATCHED_ZIP_BACKUP = True


def _patch_restore_thread():
    global _PATCHED_RESTORE_THREAD

    rmod = _restore_mod()
    if rmod is None or not hasattr(rmod, "_restore_thread"):
        return

    if getattr(rmod._restore_thread, "_u1c_ops_ui_bridge_restore_thread", False):
        _PATCHED_RESTORE_THREAD = True
        return

    original = rmod._restore_thread

    def restore_thread_wrapper(mod, parent, base: dict, archive_path: Path, user: str, password: str):
        archive_path = Path(archive_path)
        base_name = base.get("name") or "-"
        release = _release_from_base(base) or "-"

        mode = "RestoreIB .dt" if archive_path.suffix.lower() == ".dt" else "ZIP 1Cv8.1CD"

        _set_operation(
            base=base_name,
            release=release,
            step="1/1",
            action="Восстановление из архива",
            mode=mode,
            status="выполняется",
            pid="-",
            started=datetime.datetime.now().strftime("%H:%M:%S"),
        )
        _set_progress(0.02, "восстановление...", True)

        _append_log("")
        _append_log("=== Запущено восстановление из архива ===")
        _append_log(f"База: {base_name}")
        _append_log(f"Релиз: {release}")
        _append_log(f"Архив: {archive_path}")
        _append_log(f"Режим: {mode}")

        try:
            result = original(mod, parent, base, archive_path, user, password)
            _set_operation(
                base=base_name,
                release=release,
                step="1/1",
                action="Восстановление из архива",
                mode=mode,
                status="завершено",
                pid="-",
                started="-",
            )
            _set_progress(1.0, "восстановление завершено", False)
            _append_log("=== Восстановление завершено ===")
            return result

        except Exception as e:
            _set_operation(
                base=base_name,
                release=release,
                step="1/1",
                action="Восстановление из архива",
                mode=mode,
                status="ошибка",
                pid="-",
                started="-",
            )
            _set_progress(1.0, "ошибка восстановления", False)
            _append_log(f"ОШИБКА восстановления: {type(e).__name__}: {e}")
            raise

    restore_thread_wrapper._u1c_ops_ui_bridge_restore_thread = True
    restore_thread_wrapper._u1c_ops_ui_bridge_original = original
    rmod._restore_thread = restore_thread_wrapper
    _PATCHED_RESTORE_THREAD = True


def _patch_restore_zip():
    global _PATCHED_ZIP_RESTORE

    rmod = _restore_mod()
    if rmod is None:
        return

    if hasattr(rmod, "_restore_zip_file_base") and getattr(rmod._restore_zip_file_base, "_u1c_ops_ui_bridge_zip_restore", False):
        _PATCHED_ZIP_RESTORE = True
        return

    def zip_find_1cd_member(zf: zipfile.ZipFile) -> str:
        for name in zf.namelist():
            if Path(name).name.lower() == "1cv8.1cd":
                return name

        for name in zf.namelist():
            if Path(name).suffix.lower() == ".1cd":
                return name

        raise RuntimeError("В ZIP не найден файл 1Cv8.1CD")

    def restore_zip_file_base_fixed(mod, base: dict, archive_path: Path):
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

        archive_path = Path(archive_path)
        tmp_dir = None
        same_fs_tmp = None
        success = False

        try:
            tmp_dir = Path(tempfile.mkdtemp(prefix="updater1c_restore_"))
            tmp_1cd = tmp_dir / "1Cv8.1CD"

            _append_log("Режим: восстановление файловой базы из ZIP")
            _append_log(f"ZIP архив: {archive_path}")
            _append_log(f"Временная папка распаковки: {tmp_dir}")
            _append_log("ZIP не копируется во временную папку; файл 1Cv8.1CD извлекается напрямую из архива.")

            with zipfile.ZipFile(archive_path, "r") as zf:
                member = zip_find_1cd_member(zf)
                info = zf.getinfo(member)
                total = max(info.file_size, 1)
                copied = 0
                last_log = 0

                _append_log(f"Файл в архиве: {member}")
                _append_log(f"Размер в архиве: {total / 1024 / 1024:.1f} МБ")

                with zf.open(member, "r") as src, tmp_1cd.open("wb") as dst:
                    while True:
                        chunk = src.read(8 * 1024 * 1024)
                        if not chunk:
                            break

                        dst.write(chunk)
                        copied += len(chunk)

                        fraction = min(0.80, copied / total * 0.80)
                        _set_progress(fraction, f"распаковка {copied / total * 100:.0f}%", True)

                        if copied - last_log >= 512 * 1024 * 1024 or copied == total:
                            last_log = copied
                            _append_log(f"Распаковано: {copied / 1024 / 1024:.1f} из {total / 1024 / 1024:.1f} МБ")

            if not tmp_1cd.exists() or tmp_1cd.stat().st_size <= 0:
                raise RuntimeError("После распаковки во временную папку файл 1Cv8.1CD не создан или пустой")

            same_fs_tmp = base_dir / f".1Cv8.1CD.restore.tmp.{os.getpid()}.{_stamp()}"
            _append_log(f"Копирование во временный файл рядом с базой: {same_fs_tmp}")

            shutil.copy2(tmp_1cd, same_fs_tmp)
            _set_progress(0.90, "подготовка замены...", True)

            if not same_fs_tmp.exists() or same_fs_tmp.stat().st_size <= 0:
                raise RuntimeError("Временный файл рядом с базой не создан или пустой")

            _append_log(f"Замена файла базы: {dst_1cd}")
            os.replace(str(same_fs_tmp), str(dst_1cd))

            success = True
            _set_progress(1.0, "файл базы заменен", False)
            _append_log("Файл базы 1Cv8.1CD успешно заменен")

        finally:
            if success and tmp_dir and tmp_dir.exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)
                _append_log(f"Временная папка удалена: {tmp_dir}")

            if success and same_fs_tmp and same_fs_tmp.exists():
                try:
                    same_fs_tmp.unlink()
                except Exception:
                    pass

            if not success:
                if tmp_dir and tmp_dir.exists():
                    _append_log(f"Временная папка сохранена для диагностики ошибки: {tmp_dir}")
                if same_fs_tmp and same_fs_tmp.exists():
                    _append_log(f"Временный файл рядом с базой сохранен для диагностики ошибки: {same_fs_tmp}")

    restore_zip_file_base_fixed._u1c_ops_ui_bridge_zip_restore = True
    rmod._restore_zip_file_base = restore_zip_file_base_fixed
    _PATCHED_ZIP_RESTORE = True


def _selected_backup_from_report_combo() -> Path | None:
    win = _main_window()
    if win is None:
        return None

    candidates = []

    for w in _flatten(win):
        text = ""
        try:
            if hasattr(w, "get_active_text"):
                text = w.get_active_text() or ""
        except Exception:
            pass

        if not text:
            continue

        low = text.lower()
        if ".dt" not in low and ".zip" not in low:
            continue

        # Иногда combo показывает полный путь, иногда только имя файла.
        raw = text.strip()
        if raw.startswith("/"):
            p = Path(raw)
            if p.exists():
                return p

        candidates.append(raw)

    if not candidates:
        return None

    base = _current_base()
    root = Path("/mnt/DataStore/Updater1C/1c-backups")
    base_name = _safe_name(base.get("name"), "")

    search_roots = []
    if base_name:
        search_roots.append(root / base_name)
    search_roots.append(root)

    for raw in candidates:
        name = Path(raw).name
        for sr in search_roots:
            p = sr / name
            if p.exists():
                return p

    return None


def _bind_report_restore_button():
    Gtk, _GLib = _gtk()
    if Gtk is None:
        return

    win = _main_window()
    if win is None:
        return

    for w in _flatten(win):
        txt = _norm(_widget_text(w))
        if "восстановить из резервной копии" not in txt:
            continue

        if id(w) in _CONNECTED_RESTORE_BUTTONS:
            continue

        _CONNECTED_RESTORE_BUTTONS.add(id(w))

        try:
            w.set_tooltip_text("Восстановить выбранную file/server базу из .dt или .zip архива")
        except Exception:
            pass

        def run_restore(_widget=None):
            base = _current_base()
            rmod = _restore_mod()
            mod = _backup_mod()

            if not base or not base.get("name"):
                _message(
                    win,
                    "Восстановление из архива",
                    "Выдели базу в списке слева и повтори восстановление.",
                    "warning",
                )
                return True

            selected_archive = _selected_backup_from_report_combo()

            try:
                if selected_archive is not None and selected_archive.exists():
                    if hasattr(rmod, "_confirm_restore"):
                        rmod._confirm_restore(mod, win, base, selected_archive)
                    else:
                        _message(
                            win,
                            "Восстановление из архива",
                            f"Выбран архив: {selected_archive}\nНо модуль восстановления еще не готов.",
                            "warning",
                        )
                else:
                    if rmod is not None and hasattr(rmod, "_open_restore_file_dialog"):
                        rmod._open_restore_file_dialog(mod, win, base)
                    else:
                        _message(
                            win,
                            "Восстановление из архива",
                            "Модуль восстановления еще не загружен. Перезапусти приложение.",
                            "warning",
                        )
            except Exception as e:
                _message(win, "Ошибка восстановления", f"{type(e).__name__}: {e}", "error")

            return True

        # Перехватываем нажатие мышью, чтобы старая кнопка не запускала старую логику.
        try:
            w.connect("button-press-event", lambda _w, event: run_restore(_w) if getattr(event, "button", 0) == 1 else False)
        except Exception:
            pass

        # Запасной вариант для клавиатуры/Enter.
        try:
            w.connect("clicked", lambda _w: run_restore(_w))
        except Exception:
            pass

        print("UPDATER1C: кнопка 'Восстановить из резервной копии' привязана к восстановлению из архива")
