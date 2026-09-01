import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).parents[1]
MODULE_PATH = ROOT / "gtk_port" / "u1c_secret_service.py"


def load_module():
    spec = importlib.util.spec_from_file_location("secret_service_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Collection:
    def __init__(self, label, path, locked=False):
        self.label = label
        self.collection_path = path
        self.locked = locked
        self.items = []

    def get_label(self):
        return self.label

    def is_locked(self):
        return self.locked

    def unlock(self):
        self.locked = False

    def search_items(self, attrs):
        return [item for item in self.items if item.attrs == attrs]

    def create_item(self, label, attrs, password, replace=False):
        if replace:
            self.items = [item for item in self.items if item.attrs != attrs]
        self.items.append(Item(attrs, password))


class Item:
    def __init__(self, attrs, password):
        self.attrs, self.password = attrs, password

    def is_locked(self):
        return False

    def get_secret(self):
        return self.password.encode()


def secretstorage(collections, default=None):
    module = types.ModuleType("secretstorage")
    module.dbus_init = lambda: object()
    module.get_default_collection = lambda _bus: default
    module.get_all_collections = lambda _bus: collections
    return module


class SecretServiceBackendTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_kwallet_named_collection_is_accepted(self):
        wallet = Collection("kdewallet", "/org/freedesktop/secrets/collection/kdewallet")
        with patch.dict(sys.modules, {"secretstorage": secretstorage([wallet], wallet)}):
            self.assertIs(self.module.persistent_collection(), wallet)

    def test_store_and_lookup_without_cli_or_keyring(self):
        wallet = Collection("KeePassXC", "/org/freedesktop/secrets/collection/keepassxc")
        with patch.dict(sys.modules, {"secretstorage": secretstorage([wallet], wallet)}), patch.object(
            self.module.shutil, "which", return_value=None
        ):
            self.assertTrue(self.module.store("base:1", "secret", "Test"))
            self.assertEqual(self.module.lookup("base:1"), "secret")

    def test_keyring_fallback_when_secretstorage_is_unavailable(self):
        state = {}
        keyring = types.ModuleType("keyring")
        keyring.set_password = lambda service, account, value: state.update({(service, account): value})
        keyring.get_password = lambda service, account: state.get((service, account))
        with patch.dict(sys.modules, {"secretstorage": None, "keyring": keyring}), patch.object(
            self.module.shutil, "which", return_value=None
        ):
            self.assertTrue(self.module.store("its", "secret", "Test"))
            self.assertEqual(self.module.lookup("its"), "secret")


if __name__ == "__main__":
    unittest.main()
