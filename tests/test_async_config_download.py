from pathlib import Path
import ast
import unittest


ROOT = Path(__file__).resolve().parents[1]

GTK_FILE = (
    ROOT
    / "gtk_port"
    / "updater1c_gtk.py"
)


class AsyncConfigDownloadArchitectureTests(
    unittest.TestCase
):
    @classmethod
    def setUpClass(cls):
        cls.text = GTK_FILE.read_text(
            encoding="utf-8"
        )

        cls.tree = ast.parse(
            cls.text,
            filename=str(GTK_FILE),
        )

        cls.main = next(
            node
            for node in cls.tree.body
            if (
                isinstance(node, ast.ClassDef)
                and node.name == "MainWindow"
            )
        )

        cls.handler = next(
            node
            for node in cls.main.body
            if (
                isinstance(node, ast.FunctionDef)
                and node.name
                == "on_download_updates_real"
            )
        )

        cls.nested = {
            node.name: node
            for node in cls.handler.body
            if isinstance(
                node,
                ast.FunctionDef,
            )
        }

    def test_worker_exists(self):
        self.assertIn(
            "worker",
            self.nested,
        )

    def test_queue_drain_exists(self):
        self.assertIn(
            "drain_events",
            self.nested,
        )

    def test_worker_does_not_reference_gtk_or_self(self):
        worker = self.nested["worker"]

        forbidden = set()

        for node in ast.walk(worker):
            if (
                isinstance(node, ast.Name)
                and node.id in {
                    "self",
                    "Gtk",
                    "Gdk",
                    "GLib",
                }
            ):
                forbidden.add(node.id)

        self.assertEqual(
            forbidden,
            set(),
        )

    def test_handler_has_no_idle_add(self):
        idle_calls = []

        for node in ast.walk(self.handler):
            if not isinstance(node, ast.Call):
                continue

            func = node.func

            if (
                isinstance(func, ast.Attribute)
                and isinstance(
                    func.value,
                    ast.Name,
                )
                and func.value.id == "GLib"
                and func.attr == "idle_add"
            ):
                idle_calls.append(
                    node.lineno
                )

        self.assertEqual(
            idle_calls,
            [],
        )

    def test_single_timeout_drain_is_present(self):
        timeout_calls = []

        for node in ast.walk(self.handler):
            if not isinstance(node, ast.Call):
                continue

            func = node.func

            if (
                isinstance(func, ast.Attribute)
                and isinstance(
                    func.value,
                    ast.Name,
                )
                and func.value.id == "GLib"
                and func.attr == "timeout_add"
            ):
                timeout_calls.append(
                    node.lineno
                )

        self.assertEqual(
            len(timeout_calls),
            1,
        )

    def test_worker_thread_is_present(self):
        found = False

        for node in ast.walk(self.handler):
            if not isinstance(node, ast.Call):
                continue

            func = node.func

            if (
                isinstance(func, ast.Attribute)
                and isinstance(
                    func.value,
                    ast.Name,
                )
                and func.value.id
                == "threading"
                and func.attr == "Thread"
            ):
                found = True

        self.assertTrue(found)

    def test_operation_guard_is_present(self):
        source = (
            ast.get_source_segment(
                self.text,
                self.handler,
            )
            or ""
        )

        self.assertIn(
            "_download_updates_running",
            source,
        )

    def test_old_missing_idle_helper_not_used(self):
        source = (
            ast.get_source_segment(
                self.text,
                self.handler,
            )
            or ""
        )

        self.assertNotIn(
            "idle_set_current_operation",
            source,
        )


if __name__ == "__main__":
    unittest.main()
