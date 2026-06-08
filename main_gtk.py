#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
GTK-версия Обновлятора 1С для Astra Linux.

Цель ветки:
- не зависеть от PySide6;
- запускаться через python3-gi / GTK3;
- постепенно перенести сюда функции из Qt-версии main.py.
"""

import os
import sys
import json
import shutil
import subprocess
from pathlib import Path
from datetime import datetime


APP_NAME = "Обновлятор 1С Linux GTK"
APP_DIR = Path("/opt/updater1c-linux")
APP_WORKDIR = APP_DIR / "app"
ICON_PATH = APP_DIR / "icons" / "updater1c.png"

USER_CONFIG_DIR = Path.home() / ".config" / "updater1c-linux"
SETTINGS_FILE = USER_CONFIG_DIR / "settings-gtk.json"


try:
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk, GdkPixbuf, GLib
except Exception as e:
    print("ОШИБКА: не удалось загрузить GTK/PyGObject.")
    print("Проверьте установку пакетов:")
    print("  sudo apt install python3-gi gir1.2-gtk-3.0")
    print()
    print(f"Детали: {type(e).__name__}: {e}")
    sys.exit(1)


def run_command(command, cwd=None):
    try:
        p = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        return p.returncode, p.stdout or ""
    except Exception as e:
        return 999, f"{type(e).__name__}: {e}"


def which(cmd):
    return shutil.which(cmd) or ""


class Settings:
    def __init__(self):
        self.data = {
            "bases_dir": "/mnt/Data/bases",
            "updates_dir": str(Path.home() / "1c-updates"),
            "reports_dir": str(Path.home() / "1c-update-reports"),
            "platform_path": "",
        }

    def load(self):
        try:
            if SETTINGS_FILE.exists():
                loaded = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self.data.update(loaded)
        except Exception:
            pass

    def save(self):
        USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


class UpdaterGtkApp(Gtk.Window):
    def __init__(self):
        super().__init__(title=APP_NAME)

        self.settings = Settings()
        self.settings.load()

        self.set_default_size(1100, 720)
        self.set_position(Gtk.WindowPosition.CENTER)

        if ICON_PATH.exists():
            try:
                self.set_icon_from_file(str(ICON_PATH))
            except Exception:
                pass

        self.connect("destroy", Gtk.main_quit)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(root)

        header = Gtk.HeaderBar(title=APP_NAME)
        header.set_subtitle("GTK-ветка для Astra Linux")
        header.set_show_close_button(True)
        self.set_titlebar(header)

        self.status_label = Gtk.Label(label="Готово")
        self.status_label.set_xalign(0)

        main_paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        root.pack_start(main_paned, True, True, 0)

        left = self.build_left_panel()
        right = self.build_right_panel()

        main_paned.pack1(left, resize=False, shrink=False)
        main_paned.pack2(right, resize=True, shrink=False)

        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bottom.set_border_width(8)
        bottom.pack_start(self.status_label, True, True, 0)
        root.pack_end(bottom, False, False, 0)

        self.log("GTK-версия Обновлятора 1С запущена.")
        self.log("Эта ветка не использует PySide6.")
        self.log("Следующий этап — перенос функций из Qt-версии main.py в GTK-интерфейс.")
        self.log("")
        self.run_diagnostics()

    def build_left_panel(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_border_width(10)
        box.set_size_request(310, -1)

        title = Gtk.Label()
        title.set_markup("<b>Действия</b>")
        title.set_xalign(0)
        box.pack_start(title, False, False, 0)

        buttons = [
            ("Диагностика Astra/GTK", self.on_diagnostics),
            ("Найти платформы 1С", self.on_find_platforms),
            ("Открыть папку обновлений", self.on_open_updates_dir),
            ("Открыть папку отчетов", self.on_open_reports_dir),
            ("Проверить зависимости", self.on_check_deps),
            ("Запустить Qt-версию, если доступна", self.on_try_qt_version),
            ("Очистить лог", self.on_clear_log),
        ]

        for text, handler in buttons:
            b = Gtk.Button(label=text)
            b.set_hexpand(True)
            b.connect("clicked", handler)
            box.pack_start(b, False, False, 0)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        box.pack_start(sep, False, False, 8)

        form_title = Gtk.Label()
        form_title.set_markup("<b>Основные пути</b>")
        form_title.set_xalign(0)
        box.pack_start(form_title, False, False, 0)

        self.bases_entry = self.add_path_row(box, "Базы:", "bases_dir")
        self.updates_entry = self.add_path_row(box, "Обновления:", "updates_dir")
        self.reports_entry = self.add_path_row(box, "Отчеты:", "reports_dir")
        self.platform_entry = self.add_path_row(box, "Платформа:", "platform_path")

        save_btn = Gtk.Button(label="Сохранить настройки GTK")
        save_btn.connect("clicked", self.on_save_settings)
        box.pack_start(save_btn, False, False, 8)

        info = Gtk.Label()
        info.set_xalign(0)
        info.set_line_wrap(True)
        info.set_markup(
            "<small>Примечание: это первая GTK-ветка. "
            "Пока здесь каркас и диагностика. Основные функции будем переносить поэтапно.</small>"
        )
        box.pack_end(info, False, False, 0)

        return box

    def add_path_row(self, parent, label_text, key):
        label = Gtk.Label(label=label_text)
        label.set_xalign(0)
        parent.pack_start(label, False, False, 0)

        entry = Gtk.Entry()
        entry.set_text(str(self.settings.data.get(key, "")))
        entry.set_hexpand(True)
        parent.pack_start(entry, False, False, 0)
        return entry

    def build_right_panel(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_border_width(10)

        title = Gtk.Label()
        title.set_markup("<b>Журнал</b>")
        title.set_xalign(0)
        box.pack_start(title, False, False, 0)

        self.log_view = Gtk.TextView()
        self.log_view.set_editable(False)
        self.log_view.set_monospace(True)
        self.log_buffer = self.log_view.get_buffer()

        scroll = Gtk.ScrolledWindow()
        scroll.set_hexpand(True)
        scroll.set_vexpand(True)
        scroll.add(self.log_view)

        box.pack_start(scroll, True, True, 0)

        return box

    def log(self, text=""):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {text}\n" if text else "\n"
        end = self.log_buffer.get_end_iter()
        self.log_buffer.insert(end, line)
        mark = self.log_buffer.create_mark(None, self.log_buffer.get_end_iter(), False)
        self.log_view.scroll_to_mark(mark, 0.0, True, 0.0, 1.0)
        self.status_label.set_text(text[:160] if text else "Готово")
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)

    def on_clear_log(self, _button):
        self.log_buffer.set_text("")
        self.log("Лог очищен.")

    def on_save_settings(self, _button):
        self.settings.data["bases_dir"] = self.bases_entry.get_text().strip()
        self.settings.data["updates_dir"] = self.updates_entry.get_text().strip()
        self.settings.data["reports_dir"] = self.reports_entry.get_text().strip()
        self.settings.data["platform_path"] = self.platform_entry.get_text().strip()
        self.settings.save()
        self.log(f"Настройки сохранены: {SETTINGS_FILE}")

    def on_diagnostics(self, _button):
        self.run_diagnostics()

    def run_diagnostics(self):
        self.log("=== Диагностика окружения ===")
        self.log(f"Python: {sys.version.split()[0]}")
        self.log(f"Исполняемый Python: {sys.executable}")
        self.log(f"Рабочая папка: {Path.cwd()}")
        self.log(f"APP_WORKDIR: {APP_WORKDIR}")
        self.log(f"GTK доступен: да")
        self.log(f"DISPLAY={os.environ.get('DISPLAY', '')}")
        self.log(f"XDG_SESSION_TYPE={os.environ.get('XDG_SESSION_TYPE', '')}")
        self.log(f"QT_QPA_PLATFORM={os.environ.get('QT_QPA_PLATFORM', '')}")
        self.log("")

        for cmd in ["1cestart", "python3", "apt", "xdg-open", "xvfb-run"]:
            self.log(f"{cmd}: {which(cmd) or 'не найден'}")

        self.log("")
        code, out = run_command(["bash", "-lc", "lsb_release -a 2>/dev/null || cat /etc/os-release"])
        self.log(out.strip())

    def on_check_deps(self, _button):
        self.log("=== Проверка зависимостей GTK/Astra ===")

        checks = [
            ("gi", "import gi"),
            ("Gtk 3.0", "import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk"),
            ("keyring", "import keyring"),
            ("secretstorage", "import secretstorage"),
        ]

        for name, code in checks:
            rc, out = run_command(["python3", "-c", code])
            if rc == 0:
                self.log(f"{name}: OK")
            else:
                self.log(f"{name}: ERROR")
                if out.strip():
                    self.log(out.strip())

        self.log("")
        self.log("Команды установки GTK-зависимостей:")
        self.log("sudo apt update")
        self.log("sudo apt install -y python3-gi gir1.2-gtk-3.0 python3-keyring python3-secretstorage")

    def on_find_platforms(self, _button):
        self.log("=== Поиск платформ 1С ===")

        cmd = r"""
find /opt/1cv8 /opt/1C /usr/bin /usr/local/bin \
  -maxdepth 5 \
  \( -type f -o -type l \) \
  \( -name '1cv8' -o -name '1cestart' -o -name 'ibcmd' \) \
  -printf '%p\n' 2>/dev/null | sort
"""
        rc, out = run_command(["bash", "-lc", cmd])
        if out.strip():
            self.log(out.strip())
        else:
            self.log("Платформы 1С не найдены в стандартных путях.")

    def on_open_updates_dir(self, _button):
        self.open_dir(self.updates_entry.get_text().strip())

    def on_open_reports_dir(self, _button):
        self.open_dir(self.reports_entry.get_text().strip())

    def open_dir(self, path):
        p = Path(path).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        self.log(f"Открываю папку: {p}")
        run_command(["xdg-open", str(p)])

    def on_try_qt_version(self, _button):
        qt_main = APP_WORKDIR / "main.py"
        self.log("=== Пробую запустить Qt-версию ===")
        if not qt_main.exists():
            self.log(f"Не найден файл: {qt_main}")
            return

        rc, out = run_command(["python3", "-c", "import PySide6; print('PySide6 OK')"])
        if rc != 0:
            self.log("PySide6 недоступен. Qt-версия не будет запущена.")
            self.log(out.strip())
            return

        self.log("PySide6 найден. Запускаю main.py отдельным процессом.")
        try:
            subprocess.Popen(["python3", str(qt_main)], cwd=str(APP_WORKDIR))
            self.log("Qt-версия запущена.")
        except Exception as e:
            self.log(f"Ошибка запуска Qt-версии: {type(e).__name__}: {e}")


def main():
    app = UpdaterGtkApp()
    app.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
