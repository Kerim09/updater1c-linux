import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parents[1]
MODULE_PATH = ROOT / "gtk_port" / "u1c_credentials_session.py"
GTK_PORT = str(ROOT / "gtk_port")


def load_module():
    gi = types.ModuleType("gi")
    gi.require_version = lambda *_args: None
    repository = types.ModuleType("gi.repository")
    repository.GLib = types.SimpleNamespace()
    gi.repository = repository
    with patch.dict(sys.modules, {"gi": gi, "gi.repository": repository}), patch.object(
        sys, "path", [GTK_PORT, *sys.path]
    ):
        spec = importlib.util.spec_from_file_location("credentials_under_test", MODULE_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


class Collection:
    def __init__(self, label, path, locked=False):
        self.label, self.collection_path, self.locked = label, path, locked

    def get_label(self):
        return self.label

    def is_locked(self):
        return self.locked

    def unlock(self):
        raise RuntimeError("unlock failed")


class CredentialPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def secretstorage(self, collections, default=None):
        service = types.ModuleType("secretstorage")
        service.dbus_init = lambda: object()
        service.get_all_collections = lambda _bus: collections
        service.get_default_collection = lambda _bus: default
        return service

    def test_accepts_nonstandard_persistent_collection(self):
        session = Collection("Session", "/org/freedesktop/secrets/collection/session")
        other = Collection("Other", "/org/freedesktop/secrets/collection/other")
        with patch.dict(sys.modules, {"secretstorage": self.secretstorage([session, other], other)}):
            self.assertIs(self.module._persistent_collection(), other)

    def test_locked_login_collection_is_reported(self):
        login = Collection("Login", "/org/freedesktop/secrets/collection/login", True)
        with patch.dict(sys.modules, {"secretstorage": self.secretstorage([login], login)}):
            with self.assertRaisesRegex(RuntimeError, "заблокированы"):
                self.module._persistent_collection()

    def test_its_id_does_not_depend_on_login(self):
        first = types.SimpleNamespace(settings={"its_login": "first"})
        second = types.SimpleNamespace(settings={"its_login": "second"})
        self.assertEqual(self.module._stable_its_secret_id(first), self.module._stable_its_secret_id(second))

    def test_keyring_store_is_verified(self):
        state = {}
        keyring = types.ModuleType("keyring")
        keyring.set_password = lambda service, account, value: state.update({(service, account): value})
        keyring.get_password = lambda service, account: state.get((service, account))
        with patch.dict(sys.modules, {"keyring": keyring}):
            self.assertTrue(self.module._python_keyring_store("base:test", "secret"))
            keyring.get_password = lambda service, account: ""
            self.assertFalse(self.module._python_keyring_store("base:test", "secret"))

    def test_base_id_is_stable_for_same_path(self):
        with tempfile.TemporaryDirectory() as directory:
            base = {"kind": "file", "connect": str(Path(directory) / "base")}
            renamed = {"name": "другое имя", **base}
            self.assertEqual(self.module._stable_base_secret_id(base), self.module._stable_base_secret_id(renamed))

    def test_desktop_launcher_uses_full_contour(self):
        launcher = (ROOT / "packaging/linux/updater1c-linux").read_text()
        self.assertIn("gtk_port/updater1c_gtk.py", launcher)
        self.assertNotIn('APP_MAIN="$APP_DIR/main_gtk.py"', launcher)
        compatibility_launcher = (ROOT / "packaging/linux/updater1c-gtk-gui").read_text()
        self.assertIn("/usr/local/bin/updater1c-linux", compatibility_launcher)


if __name__ == "__main__":
    unittest.main()
