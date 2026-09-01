import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "gtk_port" / "updater1c_gtk.py"


class ReleaseGtkBatchCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source_text = SOURCE.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source_text, filename=str(SOURCE))

    def test_version_is_current_release(self):
        self.assertEqual((ROOT / "VERSION").read_text(encoding="utf-8").strip(), "1.2.17")

    def test_runtime_version_is_not_hardcoded_to_previous_release(self):
        self.assertIn("APP_VERSION = read_application_version()", self.source_text)
        releases_client = (ROOT / "core" / "onec_releases_client.py").read_text(encoding="utf-8")
        self.assertIn('f"updater1c-linux/{_application_version()}"', releases_client)
        self.assertNotIn('updater1c-linux/1.2.12', self.source_text)
        self.assertNotIn('updater1c-linux/1.2.12', releases_client)

    def test_check_settings_uses_checked_leaf_targets(self):
        methods = [
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "on_check_selected_base_real"
        ]
        self.assertEqual(len(methods), 1)

        method_text = ast.get_source_segment(self.source_text, methods[0]) or ""
        self.assertIn("gtk_operation_target_indices()", method_text)
        self.assertIn("for position, (idx, base) in enumerate(targets, 1)", method_text)
        self.assertNotIn("require_current_base_dict()", method_text)
        self.assertIn("Группа сама по себе проверяться не может", method_text)

    def test_batch_handler_does_not_stop_after_one_failed_base(self):
        method = next(
            node for node in ast.walk(self.tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "on_check_selected_base_real"
        )
        has_loop = any(isinstance(node, (ast.For, ast.While)) for node in ast.walk(method))
        has_exception_log = "ОШИБКА проверки базы" in (ast.get_source_segment(self.source_text, method) or "")
        self.assertTrue(has_loop)
        self.assertTrue(has_exception_log)


if __name__ == "__main__":
    unittest.main()
