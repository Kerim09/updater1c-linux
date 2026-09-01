import importlib.util
import json
import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).parents[1]


def load_plugin(filename):
    path = ROOT / "gtk_port" / filename
    with patch.object(sys, "path", [str(ROOT / "gtk_port"), *sys.path]):
        spec = importlib.util.spec_from_file_location(filename.removesuffix(".py") + "_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class ReleaseGtkPluginsTests(unittest.TestCase):
    def test_dbms_legacy_profile_is_normalized_without_plaintext_password(self):
        module = load_plugin("updater1c_dbms_profiles_plugin.py")
        legacy = {
            "id": "old-dbms", "name": "Старый PostgreSQL", "type": "PostgreSQL",
            "ops_address": "db.local", "port": 5432, "db_name": "accounting",
            "service_db": "postgres", "admin": "postgres", "password": "plain-secret",
            "linked_bases": "base1", "password_saved": True, "default": True,
        }
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "dbms_profiles.json"
            with patch.object(module, "CONFIG_DIR", Path(directory)), patch.object(module, "PROFILES_FILE", target):
                target.write_text(json.dumps([legacy]), encoding="utf-8")
                profiles = module._load_profiles()
                self.assertEqual(profiles[0]["host"], "db.local")
                self.assertEqual(profiles[0]["database_or_template"], "accounting")
                module._save_profiles(profiles)
                saved_text = target.read_text(encoding="utf-8")
                saved = json.loads(saved_text)
        self.assertNotIn("plain-secret", saved_text)
        self.assertEqual(set(saved[0]), set(module.PROFILE_FIELDS))
        self.assertTrue(saved[0]["is_default"])

    def test_cluster_json_never_contains_plaintext_password(self):
        module = load_plugin("updater1c_clusters_plugin.py")
        profile = {
            "id": "cluster-1", "name": "Основной", "cluster_host": "srv1",
            "agent_port": "1540", "username": "admin", "password": "cluster-secret",
            "password_saved": True, "is_default": True,
        }
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "clusters.json"
            with patch.object(module, "CONFIG_DIR", Path(directory)), patch.object(module, "CLUSTERS_FILE", target):
                module.save_clusters([profile])
                saved_text = target.read_text(encoding="utf-8")
                saved = json.loads(saved_text)
        self.assertNotIn("cluster-secret", saved_text)
        self.assertEqual(set(saved[0]), set(module.CLUSTER_FIELDS))

    def test_legacy_service_settings_are_not_required_or_rendered(self):
        source = (ROOT / "gtk_port" / "updater1c_gtk.py").read_text(encoding="utf-8")
        service_block = source.split("service = [", 1)[1].split("]", 1)[0]
        for label in ('"pg_dump:"', '"rac:"', '"RAS адрес:"', '"RAS порт:"'):
            self.assertNotIn(label, service_block)

        legacy = {"settings": {
            "pg_dump_path": "/old/pg_dump", "rac_path": "/old/rac",
            "ras_address": "old-ras", "ras_port": "1545",
        }}
        # Старые ключи остаются допустимыми данными и не требуют миграции/удаления.
        self.assertEqual(json.loads(json.dumps(legacy)), legacy)
        self.assertNotIn("cluster_profile_id", legacy["settings"])


if __name__ == "__main__":
    unittest.main()
