#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import subprocess
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib


APP_NAME = "Обновлятор 1C Linux"
APP_VERSION = "1.1-gtk-preview"
CONFIG_DIR = Path.home() / ".config" / "updater1c-linux"


def load_json(path: Path, fallback):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return fallback


def find_config_file():
    candidates = [
        CONFIG_DIR / "config.json",
        CONFIG_DIR / "settings.json",
        CONFIG_DIR / "app_config.json",
    ]
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]


class Ui:
    @staticmethod
    def label(text):
        w = Gtk.Label(label=text)
        w.set_xalign(0)
        return w

    @staticmethod
    def entry(text=""):
        w = Gtk.Entry()
        w.set_text(str(text or ""))
        return w

    @staticmethod
    def button(text, icon=None):
        b = Gtk.Button(label=text)
        b.set_size_request(-1, 34)
        return b

    @staticmethod
    def combo(values, active=0):
        c = Gtk.ComboBoxText()
        for v in values:
            c.append_text(v)
        c.set_active(active)
        return c

    @staticmethod
    def check(text, active=False):
        c = Gtk.CheckButton(label=text)
        c.set_active(active)
        return c


class BaseDialog(Gtk.Dialog):
    def __init__(self, parent, title="Добавление базы — Обновлятор 1C Linux", base=None):
        super().__init__(title=title, transient_for=parent, flags=0)
        self.set_default_size(520, 660)
        self.add_button("OK", Gtk.ResponseType.OK)
        self.add_button("Отмена", Gtk.ResponseType.CANCEL)

        box = self.get_content_area()
        grid = Gtk.Grid()
        grid.set_row_spacing(8)
        grid.set_column_spacing(12)
        grid.set_border_width(12)
        box.add(grid)

        base = base or {}

        self.action = Ui.combo([
            "Добавить существующую информационную базу",
            "Создать новую пустую базу без конфигурации",
            "Создать новую базу из шаблона 1С",
            "Создать группу",
        ])
        self.name = Ui.entry(base.get("name", "Новая база"))
        self.group = Ui.combo(["", "Мое", "МФ", "Арктобако", "Перетрубция"])
        self.kind = Ui.combo(["file", "server", "web"])
        self.connect = Ui.entry(base.get("connect", ""))
        self.template = Ui.entry("")
        self.user = Ui.entry(base.get("user", ""))
        self.password = Ui.entry("")
        self.password.set_visibility(False)
        self.platform = Ui.combo(["8.3", "8.*", "auto"])
        self.client_mode = Ui.combo(["Тонкий клиент", "Толстый клиент"])
        self.launch_params = Ui.entry(base.get("launch_parameters", ""))
        self.config_name = Ui.entry(base.get("config_name", ""))
        self.config_synonym = Ui.entry(base.get("config_synonym", ""))
        self.config_version = Ui.entry(base.get("config_version", ""))
        self.update_code = Ui.entry(base.get("update_program_name", ""))
        self.comment = Gtk.TextView()
        self.comment.set_size_request(-1, 90)

        rows = [
            ("Действие:", self.action),
            ("Имя базы:", self.name),
            ("Группа:", self.group),
            ("Тип:", self.kind),
            ("Путь / server\\base / URL:", self._entry_with_button(self.connect)),
            ("Шаблон 1С (.dt/.cf/папка):", self._entry_with_button(self.template)),
            ("Пользователь:", self.user),
            ("Пароль:", self.password),
            ("Версия платформы:", self._combo_with_button(self.platform)),
            ("Режим запуска:", self.client_mode),
            ("Параметры запуска:", self._entry_with_button(self.launch_params)),
            ("Конфигурация:", self.config_name),
            ("Синоним:", self.config_synonym),
            ("Версия конфигурации:", self.config_version),
            ("Код программы обновлений 1С:", self.update_code),
            ("Комментарий:", self.comment),
        ]

        for i, (label, widget) in enumerate(rows):
            grid.attach(Ui.label(label), 0, i, 1, 1)
            grid.attach(widget, 1, i, 1, 1)

        self.show_all()

    def _entry_with_button(self, entry):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.pack_start(entry, True, True, 0)
        box.pack_start(Gtk.Button(label="..."), False, False, 0)
        return box

    def _combo_with_button(self, combo):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.pack_start(combo, True, True, 0)
        box.pack_start(Gtk.Button(label="..."), False, False, 0)
        return box


class AutoUpdateDialog(Gtk.Dialog):
    def __init__(self, parent):
        super().__init__(
            title="Автообновление выбранной базы — Обновлятор 1C Linux",
            transient_for=parent,
            flags=0,
        )
        self.set_default_size(820, 420)
        self.add_button("Запустить автообновление", Gtk.ResponseType.OK)
        self.add_button("Отмена", Gtk.ResponseType.CANCEL)

        box = self.get_content_area()
        grid = Gtk.Grid()
        grid.set_border_width(12)
        grid.set_column_spacing(12)
        grid.set_row_spacing(8)
        box.add(grid)

        info = Ui.label(
            "Автообновление выполняется только пакетными командами 1С без ручного выбора релиза и без нажатия кнопок.\n"
            "Последовательность: /UpdateCfg конкретного 1cv8.cfu → /UpdateDBCfg → ENTERPRISE /C ЗапуститьОбновлениеИнформационнойБазы."
        )
        grid.attach(info, 0, 0, 2, 1)

        rows = [
            ("Резервная копия:", Ui.combo(["Сделать резервную копию перед обновлением", "Не делать резервную копию"])),
            ("Тип резервной копии:", Ui.combo([
                "Авто: файловая=архив 1Cv8.1CD, серверная=.dt",
                "Файловый архив 1Cv8.1CD / PostgreSQL pg_dump / MSSQL .bak",
                "Выгрузка .dt через конфигуратор",
            ])),
            ("", Ui.check("При ошибке попытаться автоматически откатить базу из созданной копии", True)),
            ("", Ui.check("После /UpdateCfg отдельной командой выполнять /UpdateDBCfg", True)),
            ("", Ui.check("Для /UpdateDBCfg пробовать динамическое применение изменений (-Dynamic+)", True)),
            ("", Ui.check("Для серверной базы добавлять к /UpdateDBCfg ключ -Server", False)),
            ("", Ui.check("После каждого релиза запускать обработчики обновления в режиме 1С:Предприятие", True)),
            ("", Ui.check("Остановить цепочку при первой ошибке", True)),
            ("", Ui.check("Скрытый пакетный запуск через xvfb-run, если установлен", True)),
            ("Таймаут одной операции, минут:", Ui.entry("120")),
        ]

        for i, (label, widget) in enumerate(rows, 1):
            grid.attach(Ui.label(label), 0, i, 1, 1)
            grid.attach(widget, 1, i, 1, 1)

        self.show_all()


class PlatformDownloadDialog(Gtk.Dialog):
    def __init__(self, parent):
        super().__init__(
            title="Скачать платформу 1с — Обновлятор 1C Linux",
            transient_for=parent,
            flags=0,
        )
        self.set_default_size(930, 680)
        self.add_button("Отмена", Gtk.ResponseType.CANCEL)
        self.add_button("Скачать", Gtk.ResponseType.OK)

        box = self.get_content_area()
        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        main.set_border_width(12)
        box.add(main)

        grid = Gtk.Grid()
        grid.set_column_spacing(8)
        grid.set_row_spacing(8)
        main.pack_start(grid, False, False, 0)

        self.version_link = Gtk.Button(label="загрузить с releases.1c.ru")
        self.component = Ui.combo(["Все компоненты платформы", "Сервер", "Тонкий клиент", "Клиент"])
        self.os_combo = Ui.combo(["Windows", "Linux", "macOS"])
        self.bit64 = Ui.check("64 бит")
        self.arm = Ui.check("ARM")
        self.elbrus = Ui.check("Эльбрус")
        self.web_clients = Ui.check("Клиенты для веб-сервера")

        grid.attach(Ui.label("Версия 1С:"), 0, 0, 1, 1)
        grid.attach(self.version_link, 1, 0, 4, 1)
        grid.attach(Ui.label("Платформа:"), 0, 1, 1, 1)
        grid.attach(self.component, 1, 1, 1, 1)
        grid.attach(self.bit64, 2, 1, 1, 1)
        grid.attach(self.arm, 3, 1, 1, 1)
        grid.attach(self.elbrus, 4, 1, 1, 1)
        grid.attach(Ui.label("ОС:"), 0, 2, 1, 1)
        grid.attach(self.os_combo, 1, 2, 1, 1)
        grid.attach(self.web_clients, 2, 2, 3, 1)

        main.pack_start(Ui.label("Нажмите ссылку «загрузить с releases.1c.ru», чтобы получить список версий платформы."), False, False, 0)

        main.pack_start(Ui.label("<b>Элементы для выбора (выбор двойным щелчком или нажатием Enter):</b>"), False, False, 0)
        self.available = Gtk.TreeView(model=Gtk.ListStore(str))
        self._add_text_column(self.available, "Элементы для выбора", 0)
        main.pack_start(self._scrolled(self.available), True, True, 0)

        main.pack_start(Ui.label("<b>Элементы для скачивания (используйте Delete для удаления):</b>"), False, False, 0)
        self.queue = Gtk.TreeView(model=Gtk.ListStore(str))
        self._add_text_column(self.queue, "Элементы для скачивания", 0)
        main.pack_start(self._scrolled(self.queue), True, True, 0)

        self.unpack = Ui.check("Распаковать архив после скачивания")
        self.delete_after = Ui.check("Удалить архив после распаковки")
        main.pack_start(self.unpack, False, False, 0)
        main.pack_start(self.delete_after, False, False, 0)

        dir_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        dir_box.pack_start(Ui.label("Скачивать сюда"), False, False, 0)
        dir_box.pack_start(Ui.entry("/mnt/DataStore/Updater1C/1c-platforms"), True, True, 0)
        dir_box.pack_start(Gtk.Button(label="..."), False, False, 0)
        dir_box.pack_start(Gtk.Button(label="Открыть"), False, False, 0)
        main.pack_start(dir_box, False, False, 0)

        self.show_all()

    def _add_text_column(self, tree, title, column):
        renderer = Gtk.CellRendererText()
        col = Gtk.TreeViewColumn(title, renderer, text=column)
        tree.append_column(col)

    def _scrolled(self, child):
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sw.set_size_request(-1, 150)
        sw.add(child)
        return sw


class MainWindow(Gtk.Window):
    def __init__(self):
        super().__init__(title=APP_NAME)
        self.set_default_size(1320, 820)
        self.set_position(Gtk.WindowPosition.CENTER)

        self.config_path = find_config_file()
        self.config = load_json(self.config_path, {})
        self.bases = self.config.get("bases", [])
        self.settings = self.config.get("settings", {})

        self.connect("destroy", Gtk.main_quit)
        self._apply_css()

        self.notebook = Gtk.Notebook()
        self.add(self.notebook)

        self._build_bases_tab()
        self._build_settings_tab()
        self._build_scripts_tab()
        self._build_report_tab()

        self.show_all()

    def _apply_css(self):
        css = b"""
        window { background: #f7f8fa; font-size: 9pt; }
        notebook { background: #f7f8fa; }
        treeview {
            background: #ffffff;
            color: #111827;
            border: 1px solid #dcdfe4;
        }

        treeview.view {
            background: #ffffff;
            color: #111827;
        }

        treeview.view:selected,
        treeview.view:selected:focus,
        treeview.view:selected:hover {
            background-color: #dbeafe;
            color: #111827;
        }

        treeview.view cell:selected,
        treeview.view cell:selected:focus,
        treeview.view cell:selected:hover {
            background-color: #dbeafe;
            color: #111827;
        }

        treeview.view row:selected,
        treeview.view row:selected:focus,
        treeview.view row:selected:hover {
            background-color: #dbeafe;
            color: #111827;
        }

        treeview.view rubberband {
            background-color: rgba(219, 234, 254, 0.45);
            border: 1px solid #93c5fd;
        }

        treeview.view *:selected,
        treeview.view *:selected:focus {
            background-color: #dbeafe;
            color: #111827;
        }

        treeview check,
        treeview checkbutton,
        treeview cell check {
            margin: 0px;
            padding: 0px;
            min-width: 16px;
            min-height: 16px;
        }

        button {
            background: #ffffff;
            border: 1px solid #d9dde3;
            border-radius: 4px;
            padding: 5px 10px;
            min-height: 20px;
        }
        button:hover { background: #f3f7ff; border-color: #a9c7f5; }
        entry, combobox { background: #ffffff; border: 1px solid #d9dde3; border-radius: 4px; }
        .danger { background: #ef4444; color: white; font-weight: bold; }
        .warnbox { background: #fff7ed; border: 1px solid #f59e0b; padding: 8px; }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _build_bases_tab(self):
        tab = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        tab.set_border_width(12)
        self.notebook.append_page(tab, Ui.label("📁  Базы"))

        creds = Gtk.Grid()
        creds.set_column_spacing(14)
        creds.set_row_spacing(8)
        tab.pack_start(creds, False, False, 0)

        self.base_user = Ui.entry("")
        self.base_password = Ui.entry("")
        self.base_password.set_visibility(False)

        creds.attach(Ui.label("Пользователь:"), 0, 0, 1, 1)
        creds.attach(self.base_user, 1, 0, 1, 1)
        creds.attach(Ui.label("Логин и пароль хранятся персонально у выбранной базы.\nЕсли стоят галочки, операции выполняются последовательно по отмеченным базам."), 2, 0, 1, 2)
        creds.attach(Ui.label("Пароль:"), 0, 1, 1, 1)
        creds.attach(self.base_password, 1, 1, 1, 1)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        tab.pack_start(actions, False, False, 0)

        buttons = [
            ("📁  Добавить базу", self.on_add_base),
            ("▤  Свойства", self.on_edit_base),
            ("🔄  Проверить настройки", self.on_stub),
            ("▼  Скачать обновления", self.on_stub),
            ("◻  Скачать платформу", self.on_download_platform),
            ("🔍  Установить обновления", self.on_auto_update),
        ]
        for title, handler in buttons:
            b = Ui.button(title)
            b.connect("clicked", handler)
            actions.pack_start(b, False, False, 0)

        columns = ["", "База", "Тип", "Конфигурация", "Версия", "Путь / сервер", "Платформа", "DB"]
        self.base_store = Gtk.TreeStore(bool, str, str, str, str, str, str, str)
        self.base_tree = Gtk.TreeView(model=self.base_store)
        self.base_tree.get_selection().set_mode(Gtk.SelectionMode.SINGLE)
        self.base_tree.connect("button-press-event", self.on_base_tree_button_press)
        self.base_tree.connect("row-activated", self.on_base_row_activated)
        self._build_base_columns(columns)
        tab.pack_start(self._scrolled(self.base_tree), True, True, 0)

        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        tab.pack_start(bottom, False, False, 0)
        b1 = Ui.button("✔ Отметить все")
        b2 = Ui.button("✖ Снять все")
        b3 = Ui.button("🔄 Синхронизировать со списком баз 1С")

        b1.connect("clicked", self.on_check_all_bases)
        b2.connect("clicked", self.on_uncheck_all_bases)
        b3.connect("clicked", self.on_sync_1c_stub)

        bottom.pack_start(b1, False, False, 0)
        bottom.pack_start(b2, False, False, 0)
        bottom.pack_end(b3, False, False, 0)

        self._load_bases_tree()

    def _build_base_columns(self, titles):
        renderer_toggle = Gtk.CellRendererToggle()
        renderer_toggle.set_property("activatable", True)
        renderer_toggle.set_property("xalign", 0.5)
        renderer_toggle.set_property("yalign", 0.5)
        renderer_toggle.set_property("xpad", 0)
        renderer_toggle.set_property("ypad", 0)
        renderer_toggle.connect("toggled", self.on_base_toggle)

        col_toggle = Gtk.TreeViewColumn("", renderer_toggle, active=0)
        col_toggle.set_resizable(False)
        col_toggle.set_sizing(Gtk.TreeViewColumnSizing.FIXED)
        col_toggle.set_fixed_width(64)
        col_toggle.set_min_width(64)
        col_toggle.set_max_width(64)
        col_toggle.set_alignment(0.5)
        self.base_tree.append_column(col_toggle)

        for i, title in enumerate(titles[1:], 1):
            renderer = Gtk.CellRendererText()
            col = Gtk.TreeViewColumn(title, renderer, text=i)
            col.set_resizable(True)
            if title == "Путь / сервер":
                col.set_expand(True)
            self.base_tree.append_column(col)

    def _load_bases_tree(self):
        self.base_store.clear()

        groups = {}
        for b in self.bases:
            group = b.get("group") or "Без группы"
            groups.setdefault(group, []).append(b)

        if not groups:
            groups = {
                "Мое": [
                    {"name": "AccountingBase", "kind": "file", "connect": "/mnt/Data/bases/AccountingBase", "platform_version": "8.3"},
                    {"name": "Conversion", "kind": "file", "config_synonym": "Конвертация данных, редакция 2.1", "config_version": "2.1.8.2", "connect": "/mnt/Data/bases/Conversion", "platform_version": "8.3"},
                ],
                "МФ": [],
                "Арктобако": [],
                "Перетрубция": [],
            }

        for group, items in groups.items():
            parent = self.base_store.append(None, [False, group, "", "", "", "", "", ""])
            for b in items:
                self.base_store.append(parent, [
                    False,
                    b.get("name", ""),
                    b.get("kind", ""),
                    b.get("config_synonym") or b.get("config_name", ""),
                    b.get("config_version", ""),
                    b.get("connect", ""),
                    b.get("platform_version", "8.3"),
                    b.get("db_type", ""),
                ])
            self.base_tree.expand_row(self.base_store.get_path(parent), False)


    def on_base_toggle(self, renderer, path_string):
        """Переключение галочки в дереве баз.
        Если клик по группе — переключаем всю группу.
        Если клик по базе — переключаем только базу.
        """
        path = Gtk.TreePath.new_from_string(path_string)
        tree_iter = self.base_store.get_iter(path)
        if tree_iter is None:
            return

        current = bool(self.base_store[tree_iter][0])
        new_value = not current
        self.base_store[tree_iter][0] = new_value

        # Если это группа, применяем галочку ко всем дочерним строкам.
        child = self.base_store.iter_children(tree_iter)
        while child is not None:
            self.base_store[child][0] = new_value
            child = self.base_store.iter_next(child)

        self.update_bases_status()

    def on_check_all_bases(self, *_):
        """Отметить все базы и группы."""
        def walk(parent_iter=None):
            child = self.base_store.iter_children(parent_iter)
            while child is not None:
                self.base_store[child][0] = True
                if self.base_store.iter_has_child(child):
                    walk(child)
                child = self.base_store.iter_next(child)

        walk(None)
        self.update_bases_status()

    def on_uncheck_all_bases(self, *_):
        """Снять все отметки."""
        def walk(parent_iter=None):
            child = self.base_store.iter_children(parent_iter)
            while child is not None:
                self.base_store[child][0] = False
                if self.base_store.iter_has_child(child):
                    walk(child)
                child = self.base_store.iter_next(child)

        walk(None)
        self.update_bases_status()

    def update_bases_status(self):
        """Обновление счетчиков. Пока мягко: если статусных label нет, просто ничего не делаем."""
        total = 0
        checked = 0

        def walk(parent_iter=None):
            nonlocal total, checked
            child = self.base_store.iter_children(parent_iter)
            while child is not None:
                is_group = self.base_store.iter_has_child(child)
                if not is_group:
                    total += 1
                    if bool(self.base_store[child][0]):
                        checked += 1
                else:
                    walk(child)
                child = self.base_store.iter_next(child)

        walk(None)

        for attr, value in [
            ("status_bases_count", f"Баз в списке: {total}"),
            ("status_selected_count", f"Выбрано: {checked}"),
        ]:
            w = getattr(self, attr, None)
            if w is not None and hasattr(w, "set_text"):
                w.set_text(value)

    def selected_base_iter(self):
        selection = self.base_tree.get_selection()
        model, tree_iter = selection.get_selected()
        if tree_iter is None:
            return None
        return tree_iter

    def selected_base_values(self):
        tree_iter = self.selected_base_iter()
        if tree_iter is None:
            return None

        return {
            "checked": bool(self.base_store[tree_iter][0]),
            "name": self.base_store[tree_iter][1],
            "kind": self.base_store[tree_iter][2],
            "config": self.base_store[tree_iter][3],
            "version": self.base_store[tree_iter][4],
            "connect": self.base_store[tree_iter][5],
            "platform": self.base_store[tree_iter][6],
            "db": self.base_store[tree_iter][7],
            "is_group": self.base_store.iter_has_child(tree_iter),
        }

    def on_base_row_activated(self, tree, path, column):
        """Двойной клик по базе — запуск.
        Для GTK-preview пока вызываем тот же обработчик, что и кнопка Запустить.
        """
        vals = self.selected_base_values()
        if not vals:
            return
        if vals.get("is_group"):
            if self.base_tree.row_expanded(path):
                self.base_tree.collapse_row(path)
            else:
                self.base_tree.expand_row(path, False)
            return
        self.on_run_base_stub()

    def on_base_tree_button_press(self, tree, event):
        """Правая кнопка мыши: выделить строку под курсором и открыть контекстное меню."""
        if event.button != 3:
            return False

        hit = tree.get_path_at_pos(int(event.x), int(event.y))
        if hit:
            path, column, cell_x, cell_y = hit
            tree.grab_focus()
            tree.get_selection().select_path(path)
            tree.set_cursor(path, column, False)

        self.show_base_context_menu(event)
        return True

    def show_base_context_menu(self, event):
        vals = self.selected_base_values()

        menu = Gtk.Menu()

        if vals:
            title = vals.get("name") or "База"
            item_title = Gtk.MenuItem(label=f"База: {title}")
            item_title.set_sensitive(False)
            menu.append(item_title)

            menu.append(Gtk.SeparatorMenuItem())

        items = [
            ("Запустить", self.on_run_base_stub),
            ("Конфигуратор", self.on_designer_base_stub),
            ("Свойства", self.on_edit_base),
            ("Проверить настройки", self.on_stub),
            ("Скачать обновления", self.on_stub),
            ("Скачать платформу", self.on_download_platform),
            ("Установить обновления", self.on_auto_update),
            ("Очистить кэш", self.on_stub),
        ]

        for label, handler in items:
            item = Gtk.MenuItem(label=label)
            item.connect("activate", lambda _item, h=handler: h())
            menu.append(item)

        menu.append(Gtk.SeparatorMenuItem())

        item_check = Gtk.MenuItem(label="Отметить выбранную строку/группу")
        item_check.connect("activate", lambda *_: self.set_selected_base_checked(True))
        menu.append(item_check)

        item_uncheck = Gtk.MenuItem(label="Снять отметку с выбранной строки/группы")
        item_uncheck.connect("activate", lambda *_: self.set_selected_base_checked(False))
        menu.append(item_uncheck)

        menu.append(Gtk.SeparatorMenuItem())

        item_delete = Gtk.MenuItem(label="Удалить из списка")
        item_delete.connect("activate", lambda *_: self.on_delete_selected_base_stub())
        menu.append(item_delete)

        if not vals:
            for item in menu.get_children():
                item.set_sensitive(False)

        menu.show_all()

        try:
            menu.popup_at_pointer(event)
        except Exception:
            menu.popup(None, None, None, None, event.button, event.time)

    def set_selected_base_checked(self, checked: bool):
        tree_iter = self.selected_base_iter()
        if tree_iter is None:
            return

        self.base_store[tree_iter][0] = checked

        child = self.base_store.iter_children(tree_iter)
        while child is not None:
            self.base_store[child][0] = checked
            child = self.base_store.iter_next(child)

        self.update_bases_status()

    def on_sync_1c_stub(self, *_):
        self._append_log("GTK preview: синхронизация со списком баз 1С будет подключена после переноса логики из Qt.")

    def on_run_base_stub(self, *_):
        vals = self.selected_base_values()
        name = vals.get("name") if vals else "-"
        self._append_log(f"GTK preview: запуск базы будет подключен позже. База: {name}")

    def on_designer_base_stub(self, *_):
        vals = self.selected_base_values()
        name = vals.get("name") if vals else "-"
        self._append_log(f"GTK preview: запуск конфигуратора будет подключен позже. База: {name}")

    def on_delete_selected_base_stub(self, *_):
        tree_iter = self.selected_base_iter()
        if tree_iter is None:
            return

        vals = self.selected_base_values()
        name = vals.get("name") if vals else "-"

        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text="Удалить из списка?",
        )
        dialog.format_secondary_text(f"База/группа: {name}\nФизические файлы базы не удаляются.")
        response = dialog.run()
        dialog.destroy()

        if response == Gtk.ResponseType.OK:
            self.base_store.remove(tree_iter)
            self.update_bases_status()
            self._append_log(f"Удалено из списка GTK-preview: {name}")


    def _build_settings_tab(self):
        tab = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        tab.set_border_width(10)
        self.notebook.append_page(tab, Ui.label("▤  Настройки программы"))

        inner = Gtk.Notebook()
        tab.pack_start(inner, True, True, 0)

        paths = [
            ("1cestart:", self.settings.get("one_c_start", "/opt/1cv8/common/1cestart")),
            ("Папка обновлений конфигураций:", self.settings.get("updates_dir", "/mnt/DataStore/Updater1C/1c-updates")),
            ("Папка новых файловых баз:", self.settings.get("default_new_base_dir", "/mnt/DataStore/bases")),
            ("Папка скачивания платформ:", self.settings.get("platform_download_dir", "/mnt/DataStore/Updater1C/1c-platforms")),
            ("Папка резервных копий:", self.settings.get("backup_dir", "/mnt/DataStore/Updater1C/1c-backups")),
            ("Папка логов:", self.settings.get("reports_dir", "/mnt/DataStore/Updater1C/1c-update-reports")),
        ]
        inner.append_page(self._form_tab(paths), Ui.label("Основные пути"))

        platform = [
            ("Запуск web-баз:", self.settings.get("web_launch_mode", "1cestart")),
            ("Платформа для запуска:", self.settings.get("selected_platform", "/opt/1cv8/x86_64/8.3.27.2130/1cv8")),
        ]
        inner.append_page(self._form_tab(platform, combos=True), Ui.label("Платформа 1С"))

        its = [
            ("Логин ИТС/users.v8.1c.ru:", self.settings.get("its_login", "")),
            ("Пароль ИТС:", "Пароль сохранен в системном хранилище" if self.settings.get("its_password_saved") else ""),
        ]
        inner.append_page(self._form_tab(its), Ui.label("ИТС"))

        service = [
            ("pg_dump:", self.settings.get("pg_dump_path", "")),
            ("rac:", self.settings.get("rac_path", "")),
            ("Внешняя обработка проверки метаданных .epf:", self.settings.get("metadata_probe_path", "/opt/updater1c-linux/tools/metadata_probe/DbInfo.epf")),
            ("Метод определения конфигурации:", self.settings.get("metadata_detection_method", "auto")),
            ("RAS адрес:", self.settings.get("ras_address", "localhost")),
            ("RAS порт:", self.settings.get("ras_port", "1545")),
        ]
        inner.append_page(self._form_tab(service, service=True), Ui.label("Служебное"))

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        tab.pack_start(buttons, False, False, 0)
        buttons.pack_start(Ui.button("сохранить настройки"), False, False, 0)
        buttons.pack_start(Ui.button("Найти платформы 1С"), False, False, 0)

    def _form_tab(self, rows, combos=False, service=False):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_border_width(10)
        grid = Gtk.Grid()
        grid.set_column_spacing(12)
        grid.set_row_spacing(12)
        box.pack_start(grid, False, False, 0)

        for i, (label, value) in enumerate(rows):
            grid.attach(Ui.label(label), 0, i, 1, 1)
            if combos and i < 2:
                w = Ui.combo([str(value)])
            else:
                w = Ui.entry(value)
                if "Пароль" in label:
                    w.set_visibility(False)
            if service and label in ("pg_dump:", "rac:", "Внешняя обработка проверки метаданных .epf:"):
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                row.pack_start(w, True, True, 0)
                row.pack_start(Gtk.Button(label="..."), False, False, 0)
                grid.attach(row, 1, i, 1, 1)
            else:
                grid.attach(w, 1, i, 1, 1)
        return box

    def _build_scripts_tab(self):
        tab = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        tab.set_border_width(10)
        self.notebook.append_page(tab, Ui.label("📄  Скрипты"))

        tab.pack_start(Ui.label("Скрипты для выбранных баз"), False, False, 0)
        self.script_text = Gtk.TextView()
        self.script_text.get_buffer().set_text("# Следующий этап: rac/ras — блокировка пользователей и регламентных заданий.\n")
        tab.pack_start(self._scrolled(self.script_text), True, True, 0)

    def _build_report_tab(self):
        tab = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        tab.set_border_width(14)
        self.notebook.append_page(tab, Ui.label("ℹ  Отчет"))

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        tab.pack_start(top, False, False, 0)

        for text, handler in [
            ("▶ Запустить обновление", self.on_auto_update),
            ("▼ Скачать обновления", self.on_stub),
            ("🔍 Установить обновления", self.on_auto_update),
            ("✖ Отменить", self.on_stub),
        ]:
            b = Ui.button(text)
            b.connect("clicked", handler)
            top.pack_start(b, False, False, 0)

        top.pack_end(Ui.button("💾 Сохранить лог"), False, False, 0)
        top.pack_end(Ui.button("🧹 Очистить лог"), False, False, 0)

        status = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        tab.pack_start(status, False, False, 0)
        status.pack_start(Ui.label("ОЖИДАНИЕ"), False, False, 0)
        status.pack_start(Ui.label("Статус: ожидание"), False, False, 0)

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        tab.pack_start(body, True, True, 0)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        body.pack_start(left, True, True, 0)
        left.pack_start(Ui.label("<b>Лог выполнения</b>"), False, False, 0)
        self.report = Gtk.TextView()
        left.pack_start(self._scrolled(self.report), True, True, 0)

        links = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        left.pack_start(links, False, False, 0)
        links.pack_start(Ui.button("Открыть папку с отчетами"), False, False, 0)
        links.pack_start(Ui.button("Открыть текущий лог"), False, False, 0)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        right.set_size_request(420, -1)
        body.pack_start(right, False, False, 0)

        current = Gtk.Grid()
        current.set_column_spacing(12)
        current.set_row_spacing(6)
        right.pack_start(current, False, False, 0)
        current.attach(Ui.label("<b>Текущая операция</b>"), 0, 0, 2, 1)

        for i, label in enumerate(["База:", "Релиз:", "Шаг:", "Действие:", "Режим:", "Статус:", "PID процесса:", "Время запуска:"], 1):
            current.attach(Ui.label(label), 0, i, 1, 1)
            current.attach(Ui.label("-"), 1, i, 1, 1)

        right.pack_start(Ui.label("<b>Аварийное завершение</b>\nПроцесс 1С может зависнуть и не завершиться автоматически.\nНажмите «Прервать», чтобы завершить его принудительно."), False, False, 0)
        abort = Ui.button("■  Прервать процесс сейчас")
        abort.get_style_context().add_class("danger")
        right.pack_start(abort, False, False, 0)
        right.pack_start(Ui.check("Автоматически предлагать откат после прерывания", True), False, False, 0)

        right.pack_start(Ui.label("<b>Резервные копии (для отката)</b>"), False, False, 0)
        right.pack_start(Ui.label("База: -"), False, False, 0)
        right.pack_start(Ui.combo([""]), False, False, 0)
        right.pack_start(Ui.button("Обновить список резервных копий"), False, False, 0)
        right.pack_start(Ui.button("🔄 Восстановить из резервной копии"), False, False, 0)
        right.pack_start(Ui.check("Закрыть 1С перед восстановлением", True), False, False, 0)
        right.pack_start(Ui.label("<b>Внимание!</b>\nВосстановление заменит текущую базу данными из резервной копии."), False, False, 0)

        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=30)
        tab.pack_start(bottom, False, False, 0)
        bottom.pack_start(Ui.label("Баз в списке: 0"), False, False, 0)
        bottom.pack_start(Ui.label("Выбрано: 0"), False, False, 0)
        bottom.pack_start(Ui.label(f"Версия: {APP_VERSION}"), False, False, 0)

    def _scrolled(self, child):
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sw.add(child)
        return sw

    def _append_log(self, text):
        buf = self.report.get_buffer()
        end = buf.get_end_iter()
        buf.insert(end, text + "\n")

    def on_stub(self, *_):
        self._append_log("GTK preview: обработчик будет подключен на следующем этапе переноса логики.")

    def on_add_base(self, *_):
        dlg = BaseDialog(self, "Добавление базы — Обновлятор 1C Linux")
        dlg.run()
        dlg.destroy()

    def on_edit_base(self, *_):
        dlg = BaseDialog(self, "Свойства базы — Обновлятор 1C Linux", base={"name": "Conversion", "group": "Мое"})
        dlg.run()
        dlg.destroy()

    def on_auto_update(self, *_):
        dlg = AutoUpdateDialog(self)
        dlg.run()
        dlg.destroy()

    def on_download_platform(self, *_):
        dlg = PlatformDownloadDialog(self)
        dlg.run()
        dlg.destroy()


def main():
    MainWindow()
    Gtk.main()


if __name__ == "__main__":
    main()
