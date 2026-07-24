#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
import subprocess
from typing import Any

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib

PATCH_ID = "UPDATER1C_CREDENTIAL_PERSISTENT_20260724_V4"
SERVICE = "updater1c-linux"
ITS_ACCOUNT = "its_password"


def _log(window: Any, message: str) -> None:
    try:
        window._append_log(str(message))
    except Exception:
        print(str(message))


def _base_cache(window: Any) -> dict[str, str]:
    cache = getattr(window, "_base_password_runtime_cache", None)
    if not isinstance(cache, dict):
        cache = {}
        window._base_password_runtime_cache = cache
    return cache


# UPDATER1C_PERSISTENT_SECRET_HELPERS_20260724_V4
def _collection_label(collection):
    try:
        return str(collection.get_label() or "")
    except Exception:
        return ""


def _collection_path(collection):
    try:
        return str(collection.collection_path or "")
    except Exception:
        return ""


def _is_session_collection(collection):
    label = _collection_label(collection).strip().casefold()
    path_value = _collection_path(collection).strip().casefold()

    return (
        label in ("session", "сеанс")
        or path_value.endswith("/session")
        or "/session/" in path_value
    )


def _persistent_collection():
    import secretstorage

    bus = secretstorage.dbus_init()
    collections = list(secretstorage.get_all_collections(bus))

    candidates = []

    try:
        default = secretstorage.get_default_collection(bus)
        if default is not None:
            candidates.append(default)
    except Exception:
        pass

    for collection in collections:
        if collection not in candidates:
            candidates.append(collection)

    chosen = None

    for collection in candidates:
        if _is_session_collection(collection):
            continue

        label = _collection_label(collection).strip().casefold()
        path_value = _collection_path(collection).strip().casefold()

        if (
            label in ("login", "вход", "default", "по умолчанию")
            or path_value.endswith("/login")
            or "/login/" in path_value
        ):
            chosen = collection
            break

    if chosen is None:
        for collection in candidates:
            if not _is_session_collection(collection):
                chosen = collection
                break

    if chosen is None:
        raise RuntimeError(
            "Постоянная коллекция Secret Service не найдена."
        )

    try:
        if chosen.is_locked():
            chosen.unlock()
    except Exception:
        pass

    try:
        if chosen.is_locked():
            raise RuntimeError(
                "Постоянная коллекция защищенного хранилища заблокирована."
            )
    except AttributeError:
        pass

    if _is_session_collection(chosen):
        raise RuntimeError(
            "Выбрана временная сессионная коллекция."
        )

    return chosen


def _persistent_collection_status():
    try:
        collection = _persistent_collection()
        return (
            f"{_collection_label(collection) or '<без имени>'} "
            f"({_collection_path(collection) or '<без пути>'})"
        )
    except Exception as exc:
        return f"недоступно: {type(exc).__name__}: {exc}"


def _normalize_base_for_secret(base):
    kind = str(
        base.get("kind")
        or base.get("type")
        or "file"
    ).strip().casefold()

    connect = str(
        base.get("connect")
        or base.get("path")
        or base.get("server_path")
        or ""
    ).strip()

    match = re.search(
        r'(?i)(?:^|;)\s*File\s*=\s*"((?:[^"]|"")*)"',
        connect,
    )

    if match:
        kind = "file"
        connect = match.group(1).replace('""', '"').strip()

    if kind in ("file", "файловая", "filesystem"):
        kind = "file"
        try:
            connect = str(
                Path(connect).expanduser().resolve(strict=False)
            )
        except Exception:
            connect = os.path.normpath(connect)
    elif kind in ("server", "серверная"):
        kind = "server"
        connect = connect.replace("/", "\\").strip("\\").casefold()
    elif kind in ("web", "http", "https"):
        kind = "web"
        connect = connect.rstrip("/").casefold()

    return kind, connect


def _stable_base_secret_id(base):
    kind, connect = _normalize_base_for_secret(base)
    digest = hashlib.sha256(
        f"{kind}\0{connect}".encode("utf-8", errors="replace")
    ).hexdigest()
    return f"base:{digest}"


def _stable_its_secret_id(window):
    login = str(
        getattr(window, "settings", {}).get("its_login")
        or ""
    ).strip().casefold()

    try:
        if hasattr(window, "its_login_entry"):
            current = str(
                window.its_login_entry.get_text()
                or ""
            ).strip().casefold()
            if current:
                login = current
    except Exception:
        pass

    digest = hashlib.sha256(
        login.encode("utf-8", errors="replace")
    ).hexdigest()

    return f"its:{digest}"



def _secret_tool_lookup(account: str) -> str:
    if not account:
        return ""

    try:
        collection = _persistent_collection()
        items = collection.search_items(
            {
                "service": SERVICE,
                "account": account,
            }
        )

        for item in items:
            try:
                if item.is_locked():
                    item.unlock()
            except Exception:
                pass

            try:
                value = item.get_secret()
            except Exception:
                continue

            if isinstance(value, bytes):
                value = value.decode(
                    "utf-8",
                    errors="replace",
                )
            else:
                value = str(value or "")

            if value:
                return value
    except Exception:
        pass

    return ""




def _secret_tool_store(
    account: str,
    password: str,
    label: str,
) -> bool:
    if not account or not password:
        return False

    try:
        collection = _persistent_collection()
        collection.create_item(
            str(label or "Обновлятор 1С Linux"),
            {
                "service": SERVICE,
                "account": account,
            },
            password,
            replace=True,
        )
    except Exception:
        return False

    return _secret_tool_lookup(account) == password




def _secret_tool_clear(account: str) -> bool:
    if not account:
        return False

    changed = False

    try:
        collection = _persistent_collection()
        items = collection.search_items(
            {
                "service": SERVICE,
                "account": account,
            }
        )

        for item in items:
            try:
                item.delete()
                changed = True
            except Exception:
                pass
    except Exception:
        pass

    return changed



def _python_keyring_lookup(account: str) -> str:
    for service in (SERVICE, "Обновлятор 1С", "updater1c"):
        try:
            import keyring
            value = keyring.get_password(service, account) or ""
            if value:
                return value
        except Exception:
            pass
    return ""


def _python_keyring_store(account: str, password: str) -> bool:
    try:
        import keyring
        keyring.set_password(SERVICE, account, password)
        return True
    except Exception:
        return False


def _python_keyring_clear(account: str) -> bool:
    changed = False
    try:
        import keyring
    except Exception:
        return False
    for service in (SERVICE, "Обновлятор 1С", "updater1c"):
        try:
            keyring.delete_password(service, account)
            changed = True
        except Exception:
            pass
    return changed


def _legacy_its_lookup() -> str:
    try:
        from secret_store import get_secret, its_password_account
        return get_secret(its_password_account()) or ""
    except Exception:
        return ""


def _legacy_its_store(password: str) -> bool:
    try:
        from secret_store import set_secret, its_password_account
        set_secret(its_password_account(), password)
        return True
    except Exception:
        return False



def _candidate_ids(window: Any, base: dict) -> list[str]:
    result = []

    stable = _stable_base_secret_id(base)
    if stable:
        result.append(stable)

    configured = str(
        base.get("password_secret_id")
        or ""
    ).strip()

    if configured and configured not in result:
        result.append(configured)

    try:
        generated = str(
            window.base_secret_id(base)
            or ""
        ).strip()

        if generated and generated not in result:
            result.append(generated)
    except Exception:
        pass

    return result




def _lookup_base(
    window: Any,
    base: dict,
) -> tuple[str, str]:
    cache = _base_cache(window)
    stable = _stable_base_secret_id(base)

    for secret_id in _candidate_ids(window, base):
        if cache.get(secret_id):
            value = str(cache[secret_id])
        else:
            value = (
                _secret_tool_lookup(secret_id)
                or _python_keyring_lookup(secret_id)
            )

        if not value:
            continue

        if secret_id != stable:
            _secret_tool_store(
                stable,
                value,
                f"Обновлятор 1С — {base.get('name') or 'база 1С'}",
            )

        cache[stable] = value
        base["password_secret_id"] = stable
        base["password_saved"] = True
        return stable, value

    base["password_secret_id"] = stable
    base["password_saved"] = False
    return stable, ""




def _store_base(
    window: Any,
    base: dict,
    password: str,
) -> tuple[str, bool]:
    secret_id = _stable_base_secret_id(base)

    if not secret_id:
        return "", False

    _base_cache(window)[secret_id] = password

    stored = _secret_tool_store(
        secret_id,
        password,
        f"Обновлятор 1С — {base.get('name') or 'база 1С'}",
    )

    if not stored:
        stored = _python_keyring_store(
            secret_id,
            password,
        )

    base["password_secret_id"] = secret_id
    base["password_saved"] = bool(stored)

    return secret_id, stored




def _lookup_its(window: Any) -> str:
    stable = _stable_its_secret_id(window)

    cached = str(
        getattr(
            window,
            "_its_password_runtime_cache",
            "",
        )
        or ""
    )

    if cached:
        return cached

    candidates = [
        stable,
        ITS_ACCOUNT,
        "updater1c-linux:its-password",
    ]

    value = ""

    for account in candidates:
        value = (
            _secret_tool_lookup(account)
            or _python_keyring_lookup(account)
        )

        if value:
            if account != stable:
                _secret_tool_store(
                    stable,
                    value,
                    "Обновлятор 1С — пароль ИТС",
                )
            break

    if not value:
        value = _legacy_its_lookup()

        if value:
            _secret_tool_store(
                stable,
                value,
                "Обновлятор 1С — пароль ИТС",
            )

    if value:
        window._its_password_runtime_cache = value
        window.settings["its_password_saved"] = True
    else:
        window.settings["its_password_saved"] = False

    return value




def _store_its(
    window: Any,
    password: str,
) -> bool:
    stable = _stable_its_secret_id(window)

    window._its_password_runtime_cache = password

    stored = _secret_tool_store(
        stable,
        password,
        "Обновлятор 1С — пароль ИТС",
    )

    if stored:
        _secret_tool_store(
            ITS_ACCOUNT,
            password,
            "Обновлятор 1С — пароль ИТС",
        )

    if not stored:
        stored = _python_keyring_store(
            stable,
            password,
        )

    window.settings["its_password_saved"] = bool(stored)

    return stored



def install(main_window_class: Any) -> None:
    if getattr(main_window_class, "_u1c_credentials_patch_id", "") == PATCH_ID:
        return

    original_init = main_window_class.__init__

    def keyring_get_password(self, secret_id):
        secret_id = str(secret_id or "").strip()
        if not secret_id:
            return ""
        cache = _base_cache(self)
        if cache.get(secret_id):
            return str(cache[secret_id])
        value = _secret_tool_lookup(secret_id) or _python_keyring_lookup(secret_id)
        if value:
            cache[secret_id] = value
        return value

    def keyring_set_password(self, secret_id, password):
        secret_id = str(secret_id or "").strip()
        password = str(password or "")
        if not secret_id:
            return False
        _base_cache(self)[secret_id] = password
        if password:
            return (
                _secret_tool_store(
                    secret_id,
                    password,
                    "Обновлятор 1С — пароль базы",
                )
                or _python_keyring_store(secret_id, password)
            )
        return _secret_tool_clear(secret_id) or _python_keyring_clear(secret_id)

    def get_base_password(self, base):
        base = base if isinstance(base, dict) else {}
        secret_id, value = _lookup_base(self, base)
        if value:
            base["password_secret_id"] = secret_id
            base["password_saved"] = True
            return value

        old_plain = str(
            base.get("password")
            or base.get("pwd")
            or base.get("pass")
            or ""
        )
        if old_plain:
            secret_id, stored = _store_base(self, base, old_plain)
            if secret_id:
                base["password_secret_id"] = secret_id
                base["password_saved"] = bool(stored)
            for key in ("password", "pwd", "pass"):
                base.pop(key, None)
            try:
                self.save_config_safe()
            except Exception:
                pass
            return old_plain
        return ""

    def set_base_password(self, base, password):
        base = base if isinstance(base, dict) else {}
        password = str(password or "")

        if not password:
            _secret_id, existing = _lookup_base(self, base)
            base["password_saved"] = bool(existing)
            return True

        secret_id, stored = _store_base(self, base, password)
        if secret_id:
            base["password_secret_id"] = secret_id
        base["password_saved"] = bool(stored)

        for key in ("password", "pwd", "pass"):
            base.pop(key, None)

        if not stored:
            _log(
                self,
                "ВНИМАНИЕ: пароль базы сохранён только в памяти "
                "текущей сессии приложения.",
            )
        return stored

    def save_current_credentials_to_selected_base(self, silent=False):
        vals = self.selected_base_values()
        if not vals or vals.get("is_group"):
            return False

        idx = self.selected_base_index()
        if idx < 0 or idx >= len(self.bases):
            return False

        base = self.bases[idx]
        if not isinstance(base, dict):
            return False

        user = self.get_base_user_text()
        typed_password = self.get_base_password_text()

        base["user"] = user
        base["login"] = user
        base["username"] = user

        if typed_password:
            self.set_base_password(base, typed_password)
        else:
            self.get_base_password(base)

        self.save_config_safe()

        if not silent:
            _log(
                self,
                f"Учётные данные сохранены: {base.get('name') or '-'}; "
                f"пароль в хранилище: "
                f"{'да' if base.get('password_saved') else 'нет'}.",
            )
        return True

    def get_launch_credentials_for_selected_base(self, base):
        base = base if isinstance(base, dict) else {}

        form_user = self.get_base_user_text()
        form_password = self.get_base_password_text()

        user = form_user or str(
            base.get("user")
            or base.get("username")
            or base.get("login")
            or ""
        ).strip()

        password = form_password or self.get_base_password(base)

        if form_password:
            ids = _candidate_ids(self, base)
            if ids:
                _base_cache(self)[ids[0]] = form_password

        return user, password

    def on_base_selection_changed(self, *_):
        if getattr(self, "_loading_base_credentials", False):
            return

        vals = self.selected_base_values()
        self._loading_base_credentials = True
        try:
            if not vals or vals.get("is_group"):
                self.base_user.set_text("")
                self.base_password.set_text("")
                return

            base = self.selected_base_config(vals)
            user = str(
                base.get("user")
                or base.get("username")
                or base.get("login")
                or ""
            )
            password = self.get_base_password(base)

            self.base_user.set_text(user)
            self.base_password.set_text(password)
        finally:
            self._loading_base_credentials = False

    def current_base_dict(self):
        vals = self.selected_base_values()
        if not vals or vals.get("is_group"):
            return None

        base = self.selected_base_config(vals)
        result = dict(base if isinstance(base, dict) else {})
        result.setdefault("name", vals.get("name") or "")
        result.setdefault("kind", vals.get("kind") or "file")
        result.setdefault("connect", vals.get("connect") or "")
        result.setdefault("platform_version", vals.get("platform") or "8.*")
        result.setdefault("db_type", vals.get("db") or "")

        user, password = self.get_launch_credentials_for_selected_base(base)
        result["user"] = user
        result["login"] = user
        result["username"] = user
        result["password"] = password
        return result

    def get_its_password_for_update(self):
        try:
            typed = str(self.its_password_entry.get_text() or "")
            if typed:
                self._its_password_runtime_cache = typed
                return typed
        except Exception:
            pass

        direct = str(self.settings.get("its_password") or "")
        if direct:
            self._its_password_runtime_cache = direct
            return direct

        return _lookup_its(self)

    def save_its_settings_to_store(self):
        login = (
            self.its_login_entry.get_text().strip()
            if hasattr(self, "its_login_entry")
            else str(self.settings.get("its_login") or "")
        )
        typed_password = (
            str(self.its_password_entry.get_text() or "")
            if hasattr(self, "its_password_entry")
            else ""
        )

        self.settings["its_login"] = login

        if typed_password:
            saved = _store_its(self, typed_password)
            self.settings["its_password_saved"] = bool(saved)
            self.settings["its_password"] = ""
            try:
                self.its_password_entry.set_text("")
                if saved:
                    self.its_password_entry.set_placeholder_text(
                        "Пароль сохранён в системном хранилище"
                    )
            except Exception:
                pass
        elif self.settings.get("its_password_saved"):
            self.settings["its_password_saved"] = bool(_lookup_its(self))

        self.config["settings"] = self.settings
        self.save_config_safe()

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        _base_cache(self)
        self._its_password_runtime_cache = ""

        def preload():
            loaded = 0
            missing = 0
            for base in list(getattr(self, "bases", []) or []):
                if not isinstance(base, dict):
                    continue
                referenced = bool(
                    base.get("password_secret_id")
                    or base.get("password_saved")
                )
                if self.get_base_password(base):
                    loaded += 1
                elif referenced:
                    missing += 1

            its_loaded = bool(self.get_its_password_for_update())

            try:
                self.config["bases"] = self.bases
                self.config["settings"] = self.settings
                self.save_config_safe()
            except Exception:
                pass

            try:
                self.on_base_selection_changed()
            except Exception:
                pass

            _log(
                self,
                "Защищённое хранилище при запуске: "
                f"коллекция {_persistent_collection_status()}; "
                f"паролей баз загружено {loaded}; "
                f"не прочитано {missing}; "
                f"ИТС-пароль: {'загружен' if its_loaded else 'не задан'}.",
            )
            return False

        GLib.idle_add(preload)

    main_window_class.__init__ = patched_init
    main_window_class.keyring_get_password = keyring_get_password
    main_window_class.keyring_set_password = keyring_set_password
    main_window_class.get_base_password = get_base_password
    main_window_class.set_base_password = set_base_password
    main_window_class.save_current_credentials_to_selected_base = (
        save_current_credentials_to_selected_base
    )
    main_window_class.get_launch_credentials_for_selected_base = (
        get_launch_credentials_for_selected_base
    )
    main_window_class.on_base_selection_changed = on_base_selection_changed
    main_window_class.current_base_dict = current_base_dict
    main_window_class.get_its_password_for_update = get_its_password_for_update
    main_window_class.save_its_settings_to_store = save_its_settings_to_store
    main_window_class._u1c_credentials_patch_id = PATCH_ID

    print(f"{PATCH_ID}: установлен")
