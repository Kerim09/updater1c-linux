# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime
import re
import sys
from pathlib import Path


_G = {}
_INSTALLED = False
_CONNECTED_WIDGETS = set()
_TREE_CONNECTED = set()
_LAST_FILL_KEY = ""
_BACKUP_ITEM_PATHS = {}
_COMPACT_TOOLTIP_CONNECTED = set()


def install(globals_dict: dict):
    global _G, _INSTALLED
    _G = globals_dict or {}

    if _INSTALLED:
        return

    _INSTALLED = True

    Gtk, GLib = _gtk()
    if Gtk is not None and GLib is not None:
        GLib.timeout_add(500, _timer)


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


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _safe_name(text: str, default: str = "base") -> str:
    text = str(text or "").strip()
    text = re.sub(r"\s+", "", text)
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text)
    text = re.sub(r"_+", "_", text).strip("._-")
    return text or default


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
    return "treeview" in type(w).__name__.lower()


def _is_combo(w) -> bool:
    t = type(w).__name__.lower()
    return "combo" in t or "dropdown" in t


def _is_button(w) -> bool:
    return "button" in type(w).__name__.lower()


def _is_label(w) -> bool:
    return "label" in type(w).__name__.lower()


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


def _tree_selected_values(tree) -> list[str]:
    try:
        sel = tree.get_selection()
        model, it = sel.get_selected()
        if it is None:
            return []

        values = []
        for i in range(model.get_n_columns()):
            val = model.get_value(it, i)
            if val is None or isinstance(val, bool):
                continue
            txt = str(val).strip()
            if txt:
                values.append(txt)

        return values
    except Exception:
        return []


def _base_from_values(values: list[str]) -> dict:
    info = {
        "name": "",
        "type": "",
        "config": "",
        "version": "",
        "path": "",
        "platform": "",
    }

    if not values:
        return info

    type_idx = None

    for i, txt in enumerate(values):
        low = txt.lower()
        if low in ("file", "server", "web"):
            type_idx = i
            info["type"] = low
            break

    if type_idx is not None and type_idx > 0:
        info["name"] = values[type_idx - 1]
    else:
        # Для группы тип обычно не найден, но это не база.
        info["name"] = values[0]

    for txt in values:
        low = txt.lower()
        if (
            txt.startswith("/")
            or low.startswith("http://")
            or low.startswith("https://")
            or "srvr=" in low
            or "file=" in low
            or "\\" in txt
        ):
            info["path"] = txt

    versions = []
    for txt in values:
        if re.match(r"^\d+(?:\.\d+){1,5}$", txt):
            versions.append(txt)

    cfg_versions = [v for v in versions if len(v.split(".")) >= 3 and not v.startswith(("8.3", "8.5"))]
    if cfg_versions:
        info["version"] = cfg_versions[0]

    platforms = [v for v in versions if v.startswith(("8.3", "8.5"))]
    if platforms:
        info["platform"] = platforms[-1]

    if type_idx is not None and type_idx + 1 < len(values):
        maybe_config = values[type_idx + 1]
        if maybe_config != info["version"] and maybe_config != info["path"]:
            info["config"] = maybe_config

    return info


def _current_base() -> dict:
    win = _main_window()
    if win is None:
        return {}

    # Сначала берем реально выбранную строку из дерева баз.
    for w in _flatten(win):
        if not _is_treeview(w):
            continue

        base = _base_from_values(_tree_selected_values(w))
        if base.get("name") and base.get("type") and base.get("path"):
            return base

    # Запасной вариант: пробуем backup plugin.
    mod = sys.modules.get("updater1c_backup_plugin") or _G.get("_u1c_backup_mod")
    if mod is not None:
        try:
            base = mod._current_base_from_ui()
            if base:
                return base
        except Exception:
            pass

    return {}


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

    # В первую очередь используем настройки реально открытого MainWindow.
    try:
        win = _main_window()
        settings = getattr(win, "settings", None) or {}
        configured = settings.get("backup_dir") or settings.get("backups_dir")
        if configured:
            return Path(str(configured)).expanduser()
    except Exception:
        pass

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


def _find_base_backup_dir(base: dict) -> Path:
    root = _settings_backup_root()
    base_name = _safe_name(base.get("name"), "")

    if not base_name:
        return root

    direct = root / base_name
    if direct.exists():
        return direct

    # Важно для Conversion/conversion и русских имен с разным регистром.
    if root.exists():
        for p in root.iterdir():
            try:
                if p.is_dir() and p.name.lower() == base_name.lower():
                    return p
            except Exception:
                pass

    return direct


def _backup_files(base: dict) -> tuple[Path, list[Path]]:
    folder = _find_base_backup_dir(base)
    root = _settings_backup_root()
    base_name = _safe_name(base.get("name"), "")

    files = []

    if folder.exists():
        for pattern in ("*.dt", "*.DT", "*.zip", "*.ZIP"):
            files.extend(folder.glob(pattern))

    # Если папка базы еще не создана или пуста, смотрим общий корень и фильтруем по имени базы.
    if not files and root.exists() and base_name:
        low_base = base_name.lower()
        for pattern in ("*.dt", "*.DT", "*.zip", "*.ZIP"):
            for p in root.glob(pattern):
                low = p.name.lower()
                if low.startswith(low_base + "_") or low_base in low:
                    files.append(p)

    files = [p for p in files if p.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)

    return folder, files


def _format_backup_item(p: Path) -> str:
    try:
        size = p.stat().st_size
        mtime = datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        size = 0
        mtime = "-"

    if size >= 1024 ** 3:
        size_text = f"{size / 1024 ** 3:.2f} ГБ"
    elif size >= 1024 ** 2:
        size_text = f"{size / 1024 ** 2:.1f} МБ"
    else:
        size_text = f"{size / 1024:.1f} КБ"

    # В строке показываем только имя файла, размер и дату.
    # Полный путь хранится отдельно и показывается в tooltip.
    return f"{p.name} | {size_text} | {mtime}"


def _combo_active_text(combo) -> str:
    try:
        return combo.get_active_text() or ""
    except Exception:
        return ""


def _update_combo_tooltip(combo):
    try:
        active = _combo_active_text(combo)
        full = _BACKUP_ITEM_PATHS.get(id(combo), {}).get(active, active)
        combo.set_tooltip_text(full or "")
    except Exception:
        pass


def _compact_backup_combo(combo):
    # Возвращаем нормальную ширину правого блока и включаем обрезание длинного текста.
    try:
        combo.set_hexpand(False)
    except Exception:
        pass

    try:
        combo.set_size_request(420, -1)
    except Exception:
        pass

    try:
        from gi.repository import Pango
        for cell in combo.get_cells():
            try:
                cell.set_property("ellipsize", Pango.EllipsizeMode.END)
            except Exception:
                pass
            try:
                cell.set_property("width-chars", 48)
            except Exception:
                pass
    except Exception:
        pass

    try:
        if id(combo) not in _COMPACT_TOOLTIP_CONNECTED:
            _COMPACT_TOOLTIP_CONNECTED.add(id(combo))
            combo.connect("changed", lambda c: _update_combo_tooltip(c))
    except Exception:
        pass


def _combo_clear(combo):
    try:
        combo.remove_all()
        return
    except Exception:
        pass

    try:
        model = combo.get_model()
        if model is not None and hasattr(model, "clear"):
            model.clear()
    except Exception:
        pass


def _combo_append(combo, text: str):
    try:
        combo.append_text(text)
        return
    except Exception:
        pass

    try:
        model = combo.get_model()
        if model is not None:
            model.append([text])
    except Exception:
        pass


def _combo_set_active(combo, idx: int):
    try:
        combo.set_active(idx)
        return
    except Exception:
        pass

    try:
        combo.set_selected(idx)
    except Exception:
        pass


def _find_backup_combo(win):
    # Ищем combo в правом блоке "Резервные копии (для отката)".
    header = None

    for w in _flatten(win):
        if not _is_label(w):
            continue

        txt = _norm(_widget_text(w))
        if "резервные копии" in txt and "отката" in txt:
            header = w
            break

    combos = [w for w in _flatten(win) if _is_combo(w)]

    if not combos:
        return None

    if header is None:
        # Запасной вариант: самый правый и нижний combo.
        combos.sort(key=lambda w: (_pos(win, w)[0], _pos(win, w)[1]), reverse=True)
        return combos[0]

    hx, hy, hw, hh = _pos(win, header)

    best = None
    best_score = None

    for combo in combos:
        x, y, ww, wh = _pos(win, combo)

        if y < hy:
            continue

        # Combo списка резервных копий обычно ниже заголовка и справа.
        score = abs(x - hx) + abs(y - (hy + 60)) * 3

        if best is None or score < best_score:
            best = combo
            best_score = score

    return best


def _set_right_backup_base_label(win, base: dict):
    labels = [w for w in _flatten(win) if _is_label(w)]
    headers = []

    for w in labels:
        txt = _norm(_widget_text(w))
        if "резервные копии" in txt and "отката" in txt:
            headers.append(w)

    if not headers:
        return

    headers.sort(key=lambda w: _pos(win, w)[1])
    header = headers[-1]
    hx, hy, hw, hh = _pos(win, header)

    captions = []

    for w in labels:
        txt = _norm(_widget_text(w))
        if txt == "база:":
            x, y, ww, wh = _pos(win, w)
            if y > hy:
                captions.append(w)

    if not captions:
        return

    captions.sort(key=lambda w: _pos(win, w)[1])
    caption = captions[0]
    cx, cy, cw, ch = _pos(win, caption)

    best = None
    best_score = None

    for w in labels:
        if w is caption:
            continue

        x, y, ww, wh = _pos(win, w)

        if x <= cx:
            continue

        dy = abs((y + wh / 2) - (cy + ch / 2))
        if dy > 28:
            continue

        score = dy * 1000 + abs(x - cx)

        if best is None or score < best_score:
            best = w
            best_score = score

    if best is not None:
        try:
            best.set_text(base.get("name") or "-")
        except Exception:
            pass


def refresh_backup_combo(force=True):
    global _LAST_FILL_KEY, _BACKUP_ITEM_PATHS

    win = _main_window()
    if win is None:
        return False

    combo = _find_backup_combo(win)
    if combo is None:
        print("UPDATER1C_BACKUP_COMBO: combo резервных копий не найден")
        return False

    _compact_backup_combo(combo)

    base = _current_base()
    base_key = f"{base.get('name','')}|{base.get('type','')}|{base.get('path','')}"

    if not force and base_key == _LAST_FILL_KEY:
        _update_combo_tooltip(combo)
        return True

    _LAST_FILL_KEY = base_key
    _set_right_backup_base_label(win, base)

    _combo_clear(combo)
    _BACKUP_ITEM_PATHS[id(combo)] = {}

    if not base.get("name") or not base.get("type") or not base.get("path"):
        text = "Выбери файловую или серверную базу в списке слева"
        _combo_append(combo, text)
        _BACKUP_ITEM_PATHS[id(combo)][text] = text
        _combo_set_active(combo, 0)
        _update_combo_tooltip(combo)
        return True

    folder, files = _backup_files(base)

    if not files:
        text = f"Нет резервных копий: {folder}"
        _combo_append(combo, text)
        _BACKUP_ITEM_PATHS[id(combo)][text] = text
        _combo_set_active(combo, 0)
        _update_combo_tooltip(combo)
        print(f"UPDATER1C_BACKUP_COMBO: нет копий для {base.get('name')} в {folder}")
        return True

    for pth in files:
        item_text = _format_backup_item(pth)
        _combo_append(combo, item_text)
        _BACKUP_ITEM_PATHS[id(combo)][item_text] = str(pth)

    _combo_set_active(combo, 0)
    _update_combo_tooltip(combo)

    print(f"UPDATER1C_BACKUP_COMBO: загружено {len(files)} копий для {base.get('name')} из {folder}")
    return True


def _patch_ops_bridge_parser():
    bridge = sys.modules.get("updater1c_ops_ui_bridge")
    if bridge is None:
        return

    if getattr(bridge, "_u1c_backup_combo_refresh_parser_patched", False):
        return

    def selected_backup_from_report_combo():
        win = _main_window()
        if win is None:
            return None

        combo = _find_backup_combo(win)
        if combo is None:
            return None

        try:
            text = combo.get_active_text() or ""
        except Exception:
            text = ""

        if not text:
            return None

        mapped = _BACKUP_ITEM_PATHS.get(id(combo), {}).get(text, "")
        if mapped:
            p = Path(mapped)
            if p.exists() and p.suffix.lower() in (".dt", ".zip"):
                return p

        first = text.split("|", 1)[0].strip()
        low = first.lower()

        if not low.endswith((".dt", ".zip")):
            return None

        p = Path(first)
        if p.exists():
            return p

        base = _current_base()
        folder, _files = _backup_files(base)
        candidate = folder / Path(first).name
        if candidate.exists():
            return candidate

        return None

    try:
        bridge._selected_backup_from_report_combo = selected_backup_from_report_combo
        bridge._u1c_backup_combo_refresh_parser_patched = True
        print("UPDATER1C_BACKUP_COMBO: parser восстановления из списка обновлен")
    except Exception:
        pass


def _connect_refresh_button(win):
    for w in _flatten(win):
        if not _is_button(w):
            continue

        txt = _norm(_widget_text(w))
        if "обновить список резервных копий" not in txt:
            continue

        if id(w) in _CONNECTED_WIDGETS:
            continue

        _CONNECTED_WIDGETS.add(id(w))

        def on_clicked(_btn):
            refresh_backup_combo(force=True)
            return False

        try:
            w.connect("clicked", on_clicked)
        except Exception:
            pass

        try:
            w.connect("button-release-event", lambda *_args: (refresh_backup_combo(force=True), False)[1])
        except Exception:
            pass

        print("UPDATER1C_BACKUP_COMBO: кнопка обновления списка подключена")


def _connect_tree_selection(win):
    for w in _flatten(win):
        if not _is_treeview(w):
            continue

        if id(w) in _TREE_CONNECTED:
            continue

        _TREE_CONNECTED.add(id(w))

        try:
            sel = w.get_selection()
            sel.connect("changed", lambda *_args: refresh_backup_combo(force=True))
            print("UPDATER1C_BACKUP_COMBO: выбор базы подключен к обновлению списка копий")
        except Exception:
            pass


def _timer():
    try:
        win = _main_window()
        if win is None:
            return True

        _patch_ops_bridge_parser()
        _connect_refresh_button(win)
        _connect_tree_selection(win)
        refresh_backup_combo(force=False)

    except Exception as e:
        print("UPDATER1C_BACKUP_COMBO_REFRESH_ERROR:", repr(e))

    return True
