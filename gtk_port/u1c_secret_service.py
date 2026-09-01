#!/usr/bin/env python3
"""Desktop-neutral access to the freedesktop.org Secret Service API.

The provider can be GNOME Keyring, KWallet, KeePassXC or another compatible
implementation.  No provider-specific collection name is assumed.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Iterable


SERVICE = "updater1c-linux"
LEGACY_SERVICES = (SERVICE, "Обновлятор 1С", "updater1c")


def _label(collection) -> str:
    try:
        return str(collection.get_label() or "")
    except Exception:
        return ""


def _path(collection) -> str:
    try:
        return str(collection.collection_path or "")
    except Exception:
        return ""


def _is_session(collection) -> bool:
    label = _label(collection).strip().casefold()
    path = _path(collection).strip().casefold()
    return label in {"session", "сеанс"} or path.endswith("/session") or "/session/" in path


def _collections():
    import secretstorage

    bus = secretstorage.dbus_init()
    result = []
    try:
        default = secretstorage.get_default_collection(bus)
        if default is not None:
            result.append(default)
    except Exception:
        pass
    for collection in secretstorage.get_all_collections(bus):
        if all(_path(collection) != _path(existing) for existing in result):
            result.append(collection)
    return result


def _unlock(collection) -> bool:
    try:
        if not collection.is_locked():
            return True
        collection.unlock()
        return not collection.is_locked()
    except Exception:
        return False


def persistent_collection():
    """Return the first writable non-session collection, preferring default."""
    collections = _collections()
    for collection in collections:
        if not _is_session(collection) and _unlock(collection):
            return collection
    if collections and all(_is_session(item) for item in collections):
        raise RuntimeError("Secret Service предоставляет только временную session-коллекцию")
    if collections:
        raise RuntimeError("Все постоянные коллекции Secret Service заблокированы")
    raise RuntimeError("Secret Service не предоставил ни одной коллекции")


def status() -> str:
    try:
        collection = persistent_collection()
        return f"{_label(collection) or '<без имени>'} ({_path(collection) or '<без пути>'})"
    except Exception as exc:
        return f"недоступно: {type(exc).__name__}: {exc}"


def _attribute_sets(account: str, attributes=None, legacy_attributes: Iterable[dict] = ()):
    result = []
    for item in (
        attributes or {"service": SERVICE, "account": account},
        {"service": SERVICE, "account": account},
        *legacy_attributes,
    ):
        normalized = {str(k): str(v) for k, v in dict(item).items()}
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def _secretstorage_lookup(attribute_sets) -> str:
    try:
        collections = _collections()
    except Exception:
        return ""
    # Search every collection: secrets can survive a desktop/provider switch in
    # a non-default collection. A found session item is migrated by lookup().
    for collection in collections:
        if not _unlock(collection):
            continue
        for attrs in attribute_sets:
            try:
                items = collection.search_items(attrs)
            except Exception:
                continue
            for item in items:
                try:
                    if item.is_locked() and not _unlock(item):
                        continue
                    value = item.get_secret()
                    if isinstance(value, bytes):
                        value = value.decode("utf-8", errors="replace")
                    value = str(value or "")
                    if value:
                        return value
                except Exception:
                    continue
    return ""


def _keyring_lookup(account: str) -> str:
    try:
        import keyring
    except Exception:
        return ""
    for service in LEGACY_SERVICES:
        try:
            value = keyring.get_password(service, account) or ""
            if value:
                return value
        except Exception:
            continue
    return ""


def _cli(binary_args, password: str | None = None):
    binary = shutil.which("secret-tool")
    if not binary:
        return None
    try:
        return subprocess.run(
            [binary, *binary_args], input=password, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
        )
    except Exception:
        return None


def _cli_attrs(attributes: dict) -> list[str]:
    result = []
    for key, value in attributes.items():
        result.extend((str(key), str(value)))
    return result


def lookup(account: str, attributes=None, legacy_attributes: Iterable[dict] = ()) -> str:
    if not account:
        return ""
    sets = _attribute_sets(account, attributes, legacy_attributes)
    value = _secretstorage_lookup(sets) or _keyring_lookup(account)
    if not value:
        for attrs in sets:
            result = _cli(["lookup", *_cli_attrs(attrs)])
            if result is not None and result.returncode == 0 and result.stdout.rstrip("\n"):
                value = result.stdout.rstrip("\n")
                break
    if value:
        # Best-effort migration to the current provider/default collection.
        store(account, value, "Обновлятор 1С Linux", attributes=sets[0], verify=False)
    return value


def store(account: str, password: str, label: str, attributes=None, verify: bool = True) -> bool:
    if not account or not password:
        return False
    attrs = _attribute_sets(account, attributes)[0]
    try:
        collection = persistent_collection()
        collection.create_item(str(label or "Обновлятор 1С Linux"), attrs, password, replace=True)
        if not verify or _secretstorage_lookup([attrs]) == password:
            return True
    except Exception:
        pass
    try:
        import keyring
        keyring.set_password(SERVICE, account, password)
        if not verify or (keyring.get_password(SERVICE, account) or "") == password:
            return True
    except Exception:
        pass
    result = _cli(["store", "--label", str(label or "Обновлятор 1С Linux"), *_cli_attrs(attrs)], password)
    if result is None or result.returncode != 0:
        return False
    return not verify or lookup(account, attributes=attrs) == password


def clear(account: str, attributes=None, legacy_attributes: Iterable[dict] = ()) -> bool:
    if not account:
        return False
    changed = False
    sets = _attribute_sets(account, attributes, legacy_attributes)
    try:
        for collection in _collections():
            if not _unlock(collection):
                continue
            for attrs in sets:
                for item in collection.search_items(attrs):
                    try:
                        item.delete()
                        changed = True
                    except Exception:
                        pass
    except Exception:
        pass
    try:
        import keyring
        for service in LEGACY_SERVICES:
            try:
                keyring.delete_password(service, account)
                changed = True
            except Exception:
                pass
    except Exception:
        pass
    for attrs in sets:
        result = _cli(["clear", *_cli_attrs(attrs)])
        changed = changed or bool(result is not None and result.returncode == 0)
    return changed
