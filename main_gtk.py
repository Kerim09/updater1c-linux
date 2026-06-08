#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

try:
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
except Exception as e:
    print("ОШИБКА: не удалось загрузить GTK/PyGObject.")
    print("Установите зависимости:")
    print("  sudo apt install python3-gi gir1.2-gtk-3.0")
    print(f"Детали: {type(e).__name__}: {e}")
    raise SystemExit(1)

from core.runtime import APP_NAME, ICON_PATH, ensure_dir
from core.settings import Settings
from core.diagnostics import collect_diagnostics, find_1c_platforms
from core.bases import scan_file_bases


class UpdaterGtkApp(Gtk.Window):
    def __init__(self):
        super().__init__(title=APP_NAME)

        self.settings = Settings()
        self.settings.load()

        self.set_default_size(1180, 760)
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

        main = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        root.pack_start(main, True, True, 0)

        main.pack1(self.build_left_panel(), resize=False, shrink=False)
        main.pack2(self.build_right_panel(), resize=True, shrink=False)

        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bottom.set_border_width(8)
        self.status_label = Gtk.Label(label="Готово")
        self.status_label.set_xalign(0)
        bottom.pack_start(self.status_label, True, True, 0)
        root.pack_end(bottom, False, False, 0)

        self.log("GTK-версия Обновлятора 1С запущена.")
        self.log("PySide6 не используется.")
        self.log("")
        self.run_diagnostics()

    def build_left_panel(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_border_width(10)
        box.set_size_request(350, -1)

        title = Gtk.Label()
        title.set_markup("<b>Действия</b>")
        title.set_xalign(0)
        box.pack_start(title, False, False, 0)

        for text, handler in [
            ("Диагностика", self.on_diagnostics),
            ("Найти платформы 1С", self.on_find_platforms),
            ("Сканировать файловые базы", self.on_scan_bases),
            ("Открыть папку обновлений", self.on_open_updates_dir),
            ("Открыть папку отчетов", self.on_open_reports_dir),
            ("Сохранить настройки", self.on_save_settings),
            ("Очистить лог", self.on_clear_log),
        ]:
            b = Gtk.Button(label=text)
            b.connect("clicked", handler)
            box.pack_start(b, False, False, 0)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        box.pack_start(sep, False, False, 8)

        form_title = Gtk.Label()
        form_title.set_markup("<b>Пути</b>")
        form_title.set_xalign(0)
        box.pack_start(form_title, False, False, 0)

        self.entries = {}
        self.add_path_row(box, "Базы:", "bases_dir")
        self.add_path_row(box, "Обновления:", "updates_dir")
        self.add_path_row(box, "Отчеты:", "reports_dir")
        self.add_path_row(box, "Бэкапы:", "backups_dir")
        self.add_path_row(box, "Платформа:", "platform_path")
        self.add_path_row(box, "oneget:", "oneget_path")
        self.add_path_row(box, "Логин ИТС:", "its_login")

        return box

    def add_path_row(self, parent, label_text, key):
        label = Gtk.Label(label=label_text)
        label.set_xalign(0)
        parent.pack_start(label, False, False, 0)

        entry = Gtk.Entry()
        entry.set_text(self.settings.get(key))
        parent.pack_start(entry, False, False, 0)
        self.entries[key] = entry

    def build_right_panel(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_border_width(10)

        notebook = Gtk.Notebook()
        box.pack_start(notebook, True, True, 0)

        self.log_view = Gtk.TextView()
        self.log_view.set_editable(False)
        self.log_view.set_monospace(True)
        self.log_buffer = self.log_view.get_buffer()

        log_scroll = Gtk.ScrolledWindow()
        log_scroll.add(self.log_view)
        notebook.append_page(log_scroll, Gtk.Label(label="Журнал"))

        self.bases_store = Gtk.ListStore(str, str, str)
        self.bases_tree = Gtk.TreeView(model=self.bases_store)

        for idx, title in enumerate(["Имя", "Тип", "Путь"]):
            renderer = Gtk.CellRendererText()
            col = Gtk.TreeViewColumn(title, renderer, text=idx)
            col.set_resizable(True)
            self.bases_tree.append_column(col)

        bases_scroll = Gtk.ScrolledWindow()
        bases_scroll.add(self.bases_tree)
        notebook.append_page(bases_scroll, Gtk.Label(label="Базы"))

        return box

    def log(self, text=""):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {text}\n" if text else "\n"

        end = self.log_buffer.get_end_iter()
        self.log_buffer.insert(end, line)
        mark = self.log_buffer.create_mark(None, self.log_buffer.get_end_iter(), False)
        self.log_view.scroll_to_mark(mark, 0.0, True, 0.0, 1.0)

        self.status_label.set_text(text[:180] if text else "Готово")
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)

    def on_clear_log(self, _button):
        self.log_buffer.set_text("")
        self.log("Лог очищен.")

    def on_save_settings(self, _button):
        for key, entry in self.entries.items():
            self.settings.set(key, entry.get_text().strip())
        self.settings.save()
        self.log("Настройки сохранены.")

    def on_diagnostics(self, _button):
        self.run_diagnostics()

    def run_diagnostics(self):
        for line in collect_diagnostics():
            self.log(line)

    def on_find_platforms(self, _button):
        self.log("=== Поиск платформ 1С ===")
        platforms = find_1c_platforms()
        if not platforms:
            self.log("Платформы 1С не найдены.")
            return

        for p in platforms:
            self.log(p)

        current = self.entries["platform_path"].get_text().strip()
        if not current:
            preferred = next((p for p in platforms if p.endswith("/1cv8")), platforms[0])
            self.entries["platform_path"].set_text(preferred)
            self.log(f"Платформа выбрана автоматически: {preferred}")

    def on_scan_bases(self, _button):
        bases_dir = self.entries["bases_dir"].get_text().strip()
        self.log(f"=== Сканирование баз: {bases_dir} ===")

        bases = scan_file_bases(bases_dir)
        self.bases_store.clear()

        for base in bases:
            self.bases_store.append([base.name, base.kind, base.path])

        self.log(f"Найдено файловых баз: {len(bases)}")

    def on_open_updates_dir(self, _button):
        self.open_dir(self.entries["updates_dir"].get_text().strip())

    def on_open_reports_dir(self, _button):
        self.open_dir(self.entries["reports_dir"].get_text().strip())

    def open_dir(self, path):
        p = ensure_dir(path)
        self.log(f"Открываю папку: {p}")
        subprocess.Popen(["xdg-open", str(p)])


def main():
    win = UpdaterGtkApp()
    win.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
