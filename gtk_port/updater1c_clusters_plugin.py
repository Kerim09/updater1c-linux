# -*- coding: utf-8 -*-
"""Профили кластеров 1С для GTK-контура Updater1C Linux."""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import threading
import uuid
from pathlib import Path

_G = {}
_INSTALLED = False
_UI_CONNECTED = set()

CONFIG_DIR = Path.home() / ".config" / "updater1c-linux"
CLUSTERS_FILE = CONFIG_DIR / "clusters.json"
CLUSTER_FIELDS = (
    "id", "name", "cluster_host", "agent_port", "server_name", "username",
    "password_saved", "connection_type", "comment", "is_default",
)


def install(globals_dict: dict):
    global _G, _INSTALLED
    _G = globals_dict or {}
    if _INSTALLED:
        return
    _INSTALLED = True
    Gtk, GLib = _gtk()
    if Gtk is not None and GLib is not None:
        GLib.timeout_add(900, _ui_scan)


def _gtk():
    Gtk, GLib = _G.get("Gtk"), _G.get("GLib")
    if Gtk is None or GLib is None:
        try:
            from gi.repository import Gtk as real_gtk, GLib as real_glib
            Gtk, GLib = Gtk or real_gtk, GLib or real_glib
        except Exception:
            pass
    return Gtk, GLib


def _text(value) -> str:
    return str(value or "").strip()


def _normalize_cluster(item: dict) -> dict:
    source = item if isinstance(item, dict) else {}
    return {
        "id": str(source.get("id") or uuid.uuid4()),
        "name": _text(source.get("name") or source.get("cluster_host") or "Кластер 1С"),
        "cluster_host": _text(source.get("cluster_host") or source.get("host") or source.get("address")),
        "agent_port": _text(source.get("agent_port") or source.get("port") or "1540"),
        "server_name": _text(source.get("server_name") or source.get("server")),
        "username": _text(source.get("username") or source.get("user") or source.get("admin")),
        "password_saved": bool(source.get("password_saved")),
        "connection_type": _text(source.get("connection_type") or "tcp"),
        "comment": str(source.get("comment") or ""),
        "is_default": bool(source.get("is_default") or source.get("default")),
    }


def load_clusters() -> list[dict]:
    if not CLUSTERS_FILE.exists():
        return []
    try:
        data = json.loads(CLUSTERS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("clusters", [])
        if isinstance(data, list):
            return [_normalize_cluster(x) for x in data if isinstance(x, dict)]
    except Exception as exc:
        print("UPDATER1C_CLUSTERS_LOAD_ERROR:", repr(exc))
    return []


def save_clusters(clusters: list[dict]):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    clean = [_normalize_cluster(x) for x in clusters]
    default_seen = False
    for item in clean:
        if item["is_default"] and not default_seen:
            default_seen = True
        elif item["is_default"]:
            item["is_default"] = False
    CLUSTERS_FILE.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")


def _secret_attrs(profile_id: str):
    return ["application", "updater1c-linux", "kind", "cluster-profile", "id", str(profile_id)]


def _secret_store(profile_id: str, password: str) -> bool:
    binary = shutil.which("secret-tool")
    if not binary:
        return False
    if not password:
        return _secret_clear(profile_id)
    result = subprocess.run(
        [binary, "store", "--label", f"Обновлятор 1С Linux — кластер {profile_id}"] + _secret_attrs(profile_id),
        input=password, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return result.returncode == 0


def _secret_clear(profile_id: str) -> bool:
    binary = shutil.which("secret-tool")
    if not binary:
        return True
    result = subprocess.run(
        [binary, "clear"] + _secret_attrs(profile_id),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return result.returncode == 0


def _children(widget):
    try:
        return list(widget.get_children())
    except Exception:
        return []


def _flatten(root):
    result = []
    def walk(widget):
        result.append(widget)
        for child in _children(widget):
            walk(child)
    walk(root)
    return result


def _ui_scan():
    try:
        Gtk, _ = _gtk()
        if Gtk is None:
            return True
        for win in Gtk.Window.list_toplevels():
            if id(win) in _UI_CONNECTED:
                continue
            for widget in _flatten(win):
                try:
                    if (widget.get_label() or "").strip().lower() == "сохранить настройки":
                        parent = widget.get_parent()
                        button = Gtk.Button.new_with_label("🖧 Кластеры 1С")
                        button.set_tooltip_text("Профили подключений к агентам серверов 1С")
                        button.connect("clicked", lambda _b, w=win: _open_dialog(w))
                        parent.pack_start(button, False, False, 6)
                        parent.show_all()
                        _UI_CONNECTED.add(id(win))
                        break
                except Exception:
                    continue
    except Exception as exc:
        print("UPDATER1C_CLUSTERS_UI_ERROR:", repr(exc))
    return True


def _message(parent, title, text, error=False):
    Gtk, _ = _gtk()
    dialog = Gtk.MessageDialog(
        transient_for=parent, flags=0,
        message_type=Gtk.MessageType.ERROR if error else Gtk.MessageType.INFO,
        buttons=Gtk.ButtonsType.OK, text=title,
    )
    dialog.format_secondary_text(text)
    dialog.run()
    dialog.destroy()


def _edit_dialog(parent, cluster):
    Gtk, _ = _gtk()
    cluster = _normalize_cluster(cluster)
    dialog = Gtk.Dialog(title="Кластер 1С", transient_for=parent, flags=0)
    dialog.add_buttons("Отмена", Gtk.ResponseType.CANCEL, "Сохранить", Gtk.ResponseType.OK)
    grid = Gtk.Grid(row_spacing=8, column_spacing=10)
    grid.set_border_width(12)
    dialog.get_content_area().add(grid)
    entries = {}
    rows = (
        ("name", "Название"), ("cluster_host", "Хост кластера"),
        ("agent_port", "Порт агента"), ("server_name", "Имя сервера"),
        ("username", "Пользователь"), ("password", "Пароль"),
        ("connection_type", "Тип подключения"), ("comment", "Комментарий"),
    )
    for row, (key, caption) in enumerate(rows):
        label = Gtk.Label(label=caption + ":")
        label.set_xalign(0)
        entry = Gtk.Entry()
        entry.set_text("" if key == "password" else cluster.get(key, ""))
        if key == "password":
            entry.set_visibility(False)
            if cluster["password_saved"]:
                entry.set_placeholder_text("Сохранён в Secret Service; пусто — не менять")
        grid.attach(label, 0, row, 1, 1)
        grid.attach(entry, 1, row, 1, 1)
        entries[key] = entry
    default_check = Gtk.CheckButton(label="Использовать по умолчанию")
    default_check.set_active(cluster["is_default"])
    grid.attach(default_check, 1, len(rows), 1, 1)
    dialog.show_all()
    if dialog.run() != Gtk.ResponseType.OK:
        dialog.destroy()
        return None
    result = {key: _text(entries[key].get_text()) for key, _caption in rows if key != "password"}
    result.update(id=cluster["id"], password_saved=cluster["password_saved"],
                  is_default=default_check.get_active())
    password = entries["password"].get_text()
    if password:
        result["password_saved"] = _secret_store(cluster["id"], password)
        if not result["password_saved"]:
            _message(dialog, "Пароль не сохранён",
                     "Secret Service/secret-tool недоступен. Пароль не записан в JSON.", True)
    dialog.destroy()
    return _normalize_cluster(result)


def _open_dialog(parent):
    Gtk, _ = _gtk()
    profiles = load_clusters()
    dialog = Gtk.Dialog(title="Кластеры 1С — Обновлятор 1C Linux", transient_for=parent, flags=0)
    dialog.add_buttons("Закрыть", Gtk.ResponseType.CLOSE)
    dialog.set_default_size(820, 480)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.set_border_width(10)
    dialog.get_content_area().add(box)
    toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    box.pack_start(toolbar, False, False, 0)
    buttons = [Gtk.Button.new_with_label(x) for x in ("Добавить", "Изменить", "Удалить", "Проверить доступность")]
    for button in buttons:
        toolbar.pack_start(button, False, False, 0)
    store = Gtk.ListStore(str, str, str, str, str)
    tree = Gtk.TreeView(model=store)
    for title, index in (("Название", 1), ("Хост", 2), ("Порт", 3), ("По умолчанию", 4)):
        tree.append_column(Gtk.TreeViewColumn(title, Gtk.CellRendererText(), text=index))
    scroll = Gtk.ScrolledWindow()
    scroll.add(tree)
    box.pack_start(scroll, True, True, 0)
    hint = Gtk.Label(label="Проверка выполняет TCP-подключение к host:port; действия запускаются из профиля кластера.")
    hint.set_xalign(0)
    box.pack_start(hint, False, False, 0)

    def refresh():
        store.clear()
        for item in profiles:
            store.append([item["id"], item["name"], item["cluster_host"], item["agent_port"],
                          "★" if item["is_default"] else ""])

    def selected():
        model, iterator = tree.get_selection().get_selected()
        pid = model.get_value(iterator, 0) if iterator is not None else ""
        return next((x for x in profiles if x["id"] == pid), None)

    def add(_button):
        item = _edit_dialog(dialog, {})
        if item:
            if item["is_default"]:
                for profile in profiles:
                    profile["is_default"] = False
            profiles.append(item)
            save_clusters(profiles)
            profiles[:] = load_clusters()
            refresh()

    def edit(_button):
        current = selected()
        if not current:
            return _message(dialog, "Кластеры 1С", "Выберите профиль.", True)
        item = _edit_dialog(dialog, current)
        if item:
            if item["is_default"]:
                for profile in profiles:
                    profile["is_default"] = False
            profiles[profiles.index(current)] = item
            save_clusters(profiles)
            profiles[:] = load_clusters()
            refresh()

    def delete(_button):
        current = selected()
        if not current:
            return
        confirm = Gtk.MessageDialog(
            transient_for=dialog, flags=0, message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.OK_CANCEL, text="Удалить профиль кластера 1С?",
        )
        confirm.format_secondary_text(current["name"])
        response = confirm.run()
        confirm.destroy()
        if response != Gtk.ResponseType.OK:
            return
        profiles.remove(current)
        _secret_clear(current["id"])
        save_clusters(profiles)
        refresh()

    def check(_button):
        current = selected()
        if not current:
            return _message(dialog, "Кластеры 1С", "Выберите профиль.", True)
        def worker():
            try:
                with socket.create_connection(
                    (current["cluster_host"], int(current["agent_port"] or "1540")), timeout=5
                ):
                    text, error = "TCP-порт агента доступен. Регистрация и управление кластером не проверялись.", False
            except Exception as exc:
                text, error = f"TCP-подключение не установлено: {type(exc).__name__}: {exc}", True
            _G["GLib"].idle_add(lambda: (_message(dialog, "Доступность кластера", text, error), False)[1])
        threading.Thread(target=worker, daemon=True).start()

    for button, callback in zip(buttons, (add, edit, delete, check)):
        button.connect("clicked", callback)
    tree.connect("row-activated", lambda *_args: edit(None))
    refresh()
    dialog.show_all()
    dialog.run()
    dialog.destroy()
