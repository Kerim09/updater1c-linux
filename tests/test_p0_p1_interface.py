import ast
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GTK_SOURCE = ROOT / "gtk_port" / "updater1c_gtk.py"


def _function_source(name):
    tree = ast.parse(GTK_SOURCE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(GTK_SOURCE.read_text(encoding="utf-8"), node) or ""
    raise AssertionError(f"function not found: {name}")


class P0P1InterfaceStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = GTK_SOURCE.read_text(encoding="utf-8")

    def test_sensitive_arguments_are_masked(self):
        tree = ast.parse(self.source)
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "mask_sensitive_command_args")
        namespace = {}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(GTK_SOURCE), "exec"), {"re": re}, namespace)
        mask = namespace["mask_sensitive_command_args"]
        result = mask(["1cv8", "/Nuser", "/P", "secret", "password=another-secret", "--safe"])
        text = " ".join(result)
        self.assertNotIn("secret", text)
        self.assertNotIn("another-secret", text)
        self.assertIn("***", text)

    def test_launch_diagnostic_does_not_use_raw_args_repr(self):
        launch = _function_source("launch_selected_base")
        self.assertNotIn("repr(args)", launch)
        self.assertIn("ARGS_REPR=<redacted>", launch)
        self.assertIn("start_new_session=True", launch)

    def test_settings_are_collected_before_save(self):
        save = _function_source("on_save_settings_real")
        form = _function_source("_form_tab")
        self.assertIn("collect_settings_from_widgets", save)
        self.assertIn("self._settings_widgets[key]", form)

    def test_group_toggle_is_recursive(self):
        toggle = _function_source("on_base_toggle")
        selected = _function_source("set_selected_base_checked")
        self.assertIn("set_tree_iter_checked_recursive", toggle)
        self.assertIn("set_tree_iter_checked_recursive", selected)

    def test_update_process_is_tracked_and_grouped(self):
        update = _function_source("run_1c_update_batch")
        cancel = _function_source("on_cancel_operation_real")
        self.assertIn("self._active_process", update)
        self.assertIn("start_new_session=True", update)
        self.assertIn("os.killpg", cancel)

    def test_report_has_no_visible_stub_bindings(self):
        report = _function_source("_build_report_tab")
        self.assertNotIn("self.on_stub", report)
        self.assertIn("self.on_cancel_operation_real", report)
        self.assertIn("self.on_save_current_log", report)

    def test_backup_modes_have_changed_handlers(self):
        for name in ("updater1c_backup_plugin.py", "updater1c_backup_hotfix.py"):
            source = (ROOT / "gtk_port" / name).read_text(encoding="utf-8")
            self.assertIn("mode_combo.connect(\"changed\", update_db_fields)", source)
            self.assertIn("settings.get(\"backup_dir\")", source)


if __name__ == "__main__":
    unittest.main()
