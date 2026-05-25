import re
from gi.repository import Gtk, GLib, Pango


class LogKind:
    NORMAL = "normal"
    HEADER = "header"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    COMMAND = "command"
    PATH = "path"
    MUTED = "muted"


class StructuredLogView:
    COL_ICON = 0
    COL_TEXT = 1
    COL_KIND = 2
    COL_DETAILS = 3
    COL_HAS_PROBLEM = 4

    def __init__(self):
        self._plain_lines = []
        self._current_root_iter = None
        self._current_base_iter = None
        self._current_step_iter = None
        self._last_command_iter = None
        self._inside_command = False

        self.widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        self._build_toolbar()
        self._build_notebook()

        self.widget.pack_start(self.toolbar, False, False, 0)
        self.widget.pack_start(self.notebook, True, True, 0)

    # ---------------------------------------------------------------------
    # UI
    # ---------------------------------------------------------------------

    def _build_toolbar(self):
        self.toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        self.btn_expand_all = Gtk.Button(label="Развернуть все")
        self.btn_collapse_all = Gtk.Button(label="Свернуть все")
        self.btn_only_problems = Gtk.ToggleButton(label="Только проблемы")

        self.btn_expand_all.connect("clicked", self._on_expand_all)
        self.btn_collapse_all.connect("clicked", self._on_collapse_all)
        self.btn_only_problems.connect("toggled", self._on_toggle_only_problems)

        self.toolbar.pack_start(self.btn_expand_all, False, False, 0)
        self.toolbar.pack_start(self.btn_collapse_all, False, False, 0)
        self.toolbar.pack_start(self.btn_only_problems, False, False, 0)

    def _build_notebook(self):
        self.notebook = Gtk.Notebook()

        self._build_text_log()
        self._build_steps_tree()

        self.notebook.append_page(self.text_scroller, Gtk.Label(label="Журнал"))
        self.notebook.append_page(self.steps_scroller, Gtk.Label(label="Шаги"))

    def _build_text_log(self):
        self.text_view = Gtk.TextView()
        self.text_view.set_editable(False)
        self.text_view.set_cursor_visible(False)
        self.text_view.set_wrap_mode(Gtk.WrapMode.NONE)
        self.text_view.modify_font(Pango.FontDescription("Monospace 10"))

        self.text_buffer = self.text_view.get_buffer()
        self._create_text_tags()

        self.text_scroller = Gtk.ScrolledWindow()
        self.text_scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.text_scroller.add(self.text_view)

    def _build_steps_tree(self):
        self.tree_store = Gtk.TreeStore(str, str, str, str, bool)

        self.filter_model = self.tree_store.filter_new()
        self.filter_model.set_visible_func(self._steps_visible_func)

        self.steps_tree = Gtk.TreeView(model=self.filter_model)
        self.steps_tree.set_headers_visible(True)
        self.steps_tree.set_enable_search(True)
        self.steps_tree.set_search_column(self.COL_TEXT)

        renderer_icon = Gtk.CellRendererText()
        column_icon = Gtk.TreeViewColumn("", renderer_icon, text=self.COL_ICON)
        column_icon.set_fixed_width(34)
        self.steps_tree.append_column(column_icon)

        renderer_text = Gtk.CellRendererText()
        renderer_text.set_property("ellipsize", Pango.EllipsizeMode.END)

        column_text = Gtk.TreeViewColumn("Шаг / событие", renderer_text)
        column_text.set_cell_data_func(renderer_text, self._render_step_text)
        column_text.set_expand(True)
        self.steps_tree.append_column(column_text)

        renderer_kind = Gtk.CellRendererText()
        column_kind = Gtk.TreeViewColumn("Тип", renderer_kind, text=self.COL_KIND)
        column_kind.set_fixed_width(110)
        self.steps_tree.append_column(column_kind)

        self.steps_scroller = Gtk.ScrolledWindow()
        self.steps_scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.steps_scroller.add(self.steps_tree)

    def _create_text_tags(self):
        self.text_buffer.create_tag(LogKind.NORMAL, foreground="#222222")
        self.text_buffer.create_tag(LogKind.HEADER, foreground="#0b5394", weight=Pango.Weight.BOLD)
        self.text_buffer.create_tag(LogKind.SUCCESS, foreground="#1b7f2a", weight=Pango.Weight.BOLD)
        self.text_buffer.create_tag(LogKind.WARNING, foreground="#b45f06", weight=Pango.Weight.BOLD)
        self.text_buffer.create_tag(LogKind.ERROR, foreground="#cc0000", weight=Pango.Weight.BOLD)
        self.text_buffer.create_tag(LogKind.COMMAND, foreground="#555555", family="monospace")
        self.text_buffer.create_tag(LogKind.PATH, foreground="#274e13")
        self.text_buffer.create_tag(LogKind.MUTED, foreground="#777777")

    # ---------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------

    def append(self, text):
        """
        Основной метод. Его вызываем вместо прямой вставки текста в старый TextView.
        Можно передавать как одну строку, так и многострочный блок.
        """
        if text is None:
            return False

        text = str(text)

        if not text.endswith("\n"):
            text += "\n"

        for raw_line in text.splitlines():
            self._append_line(raw_line)

        self._scroll_text_to_end()
        return False

    def clear(self):
        self._plain_lines.clear()
        self.text_buffer.set_text("")
        self.tree_store.clear()

        self._current_root_iter = None
        self._current_base_iter = None
        self._current_step_iter = None
        self._last_command_iter = None
        self._inside_command = False

    def get_plain_text(self):
        return "\n".join(self._plain_lines)

    # ---------------------------------------------------------------------
    # Text log
    # ---------------------------------------------------------------------

    def _append_line(self, line):
        self._plain_lines.append(line)

        kind = self.classify_line(line)
        self._append_colored_text(line, kind)
        self._append_structured_step(line, kind)

    def _append_colored_text(self, line, kind):
        end_iter = self.text_buffer.get_end_iter()

        if kind == LogKind.PATH:
            self.text_buffer.insert_with_tags_by_name(end_iter, line + "\n", LogKind.PATH)
        else:
            self.text_buffer.insert_with_tags_by_name(end_iter, line + "\n", kind)

    def _scroll_text_to_end(self):
        mark = self.text_buffer.get_insert()
        end_iter = self.text_buffer.get_end_iter()
        self.text_buffer.place_cursor(end_iter)
        self.text_view.scroll_mark_onscreen(mark)

    # ---------------------------------------------------------------------
    # Classification
    # ---------------------------------------------------------------------

    @staticmethod
    def classify_line(line):
        s = line.strip()

        if not s:
            return LogKind.MUTED

        if re.match(r"^=+\s+.*\s+=+$", s):
            return LogKind.HEADER

        if re.match(r"^-{3,}\s+.*\s+-{3,}$", s):
            return LogKind.HEADER

        if re.match(r"^\[\d+/\d+\]$", s):
            return LogKind.HEADER

        if s == "Команда:":
            return LogKind.HEADER

        if s.startswith("/opt/") or s.startswith("flatpak ") or s.startswith("env "):
            return LogKind.COMMAND

        if "Окружение запуска:" in s:
            return LogKind.COMMAND

        if re.search(r"(Аварийное завершение|Ошибка|ошибка|не удалось|Не удалось|ПРОПУСК|Код завершения:\s*[1-9]\d*|завершился с ошибкой|не найден|не найдена|не вернула)", s):
            return LogKind.ERROR

        if re.search(r"(Внимание|ТРЕБУЕТ ВНИМАНИЯ|warning|fallback|не заполнена|не заполнен|unknown hash format|предупреждение)", s, re.IGNORECASE):
            return LogKind.WARNING

        if re.search(r"(Готово|завершено|Код завершения:\s*0|Определено:|После проверки определено:|Файл скачан:|Распаковано|Папка релиза нормализована|Архив после распаковки удален|Скачивание обновлений:.*завершено)", s):
            return LogKind.SUCCESS

        if re.search(r"(/mnt/|/home/|/tmp/|/opt/|\.log|\.json|\.zip|\.cfu|\.htm)", s):
            return LogKind.PATH

        return LogKind.NORMAL

    @staticmethod
    def _kind_to_icon(kind):
        if kind == LogKind.ERROR:
            return "✖"
        if kind == LogKind.WARNING:
            return "⚠"
        if kind == LogKind.SUCCESS:
            return "✔"
        if kind == LogKind.COMMAND:
            return "⌘"
        if kind == LogKind.HEADER:
            return "▸"
        if kind == LogKind.PATH:
            return "📁"
        return "•"

    @staticmethod
    def _kind_to_label(kind):
        if kind == LogKind.ERROR:
            return "Ошибка"
        if kind == LogKind.WARNING:
            return "Внимание"
        if kind == LogKind.SUCCESS:
            return "Успех"
        if kind == LogKind.COMMAND:
            return "Команда"
        if kind == LogKind.HEADER:
            return "Раздел"
        if kind == LogKind.PATH:
            return "Путь"
        if kind == LogKind.MUTED:
            return ""
        return "Инфо"

    # ---------------------------------------------------------------------
    # Structured tree
    # ---------------------------------------------------------------------

    def _append_structured_step(self, line, kind):
        s = line.strip()

        if not s:
            return

        root_match = re.match(r"^=+\s+(.*?)\s+=+$", s)
        if root_match:
            self._current_root_iter = self._add_step(
                None,
                root_match.group(1),
                LogKind.HEADER,
                line,
            )
            self._current_base_iter = None
            self._current_step_iter = self._current_root_iter
            self._inside_command = False
            return

        index_match = re.match(r"^\[(\d+)/(\d+)\]$", s)
        if index_match:
            parent = self._current_root_iter
            self._current_base_iter = self._add_step(
                parent,
                f"База {index_match.group(1)} из {index_match.group(2)}",
                LogKind.HEADER,
                line,
            )
            self._current_step_iter = self._current_base_iter
            self._inside_command = False
            return

        section_match = re.match(r"^-{3,}\s+(.*?)\s+-{3,}$", s)
        if section_match:
            title = section_match.group(1)
            parent = self._current_base_iter or self._current_root_iter

            if title.startswith("Проверка базы:") or title.startswith("Скачивание обновлений для базы:"):
                self._current_base_iter = self._add_step(parent, title, LogKind.HEADER, line)
                self._current_step_iter = self._current_base_iter
            else:
                self._current_step_iter = self._add_step(parent, title, LogKind.HEADER, line)

            self._inside_command = False
            return

        if s == "Команда:":
            parent = self._current_step_iter or self._current_base_iter or self._current_root_iter
            self._last_command_iter = self._add_step(parent, "Команда запуска", LogKind.COMMAND, line)
            self._inside_command = True
            return

        if self._inside_command:
            if s.startswith("Окружение запуска:") or s.startswith("Код завершения:"):
                self._inside_command = False
            else:
                if self._last_command_iter is not None:
                    old_details = self.tree_store[self._last_command_iter][self.COL_DETAILS]
                    new_details = (old_details + "\n" + line).strip()
                    self.tree_store[self._last_command_iter][self.COL_DETAILS] = new_details
                return

        important = self._is_important_tree_line(s, kind)
        if important:
            parent = self._current_step_iter or self._current_base_iter or self._current_root_iter
            self._add_step(parent, s, kind, line)

    def _is_important_tree_line(self, s, kind):
        if kind in (LogKind.ERROR, LogKind.WARNING, LogKind.SUCCESS):
            return True

        patterns = [
            r"^Тип:",
            r"^Подключение:",
            r"^Метод определения конфигурации:",
            r"^Текущая версия конфигурации:",
            r"^Код программы обновлений:",
            r"^Версия платформы для update-api:",
            r"^Целевая версия:",
            r"^Минимальная/требуемая платформа",
            r"^Размер по ответу API:",
            r"^Последовательность обновлений:",
            r"^Информация о релизе:",
            r"^Скачивание \d+/\d+:",
            r"^Имя файла:",
            r"^Папка шаблона из API:",
            r"^Версия релиза:",
            r"^Папка релиза:",
            r"^Скачано ",
            r"^Проверка hashSum:",
        ]

        return any(re.search(p, s) for p in patterns)

    def _add_step(self, parent_iter, text, kind, details=""):
        icon = self._kind_to_icon(kind)
        label = self._kind_to_label(kind)
        has_problem = kind in (LogKind.ERROR, LogKind.WARNING)

        row_iter = self.tree_store.append(
            parent_iter,
            [icon, text, label, details, has_problem],
        )

        if has_problem:
            self._mark_parent_problem(row_iter)

        return row_iter

    def _mark_parent_problem(self, row_iter):
        parent = self.tree_store.iter_parent(row_iter)
        while parent is not None:
            self.tree_store[parent][self.COL_HAS_PROBLEM] = True
            parent = self.tree_store.iter_parent(parent)

    # ---------------------------------------------------------------------
    # Tree rendering / filtering
    # ---------------------------------------------------------------------

    def _steps_visible_func(self, model, tree_iter, data):
        only_problems = self.btn_only_problems.get_active()

        if not only_problems:
            return True

        kind_label = model[tree_iter][self.COL_KIND]
        has_problem = model[tree_iter][self.COL_HAS_PROBLEM]

        return has_problem or kind_label in ("Ошибка", "Внимание")

    def _render_step_text(self, column, cell, model, tree_iter, data):
        text = model[tree_iter][self.COL_TEXT]
        kind_label = model[tree_iter][self.COL_KIND]

        cell.set_property("text", text)
        cell.set_property("weight", Pango.Weight.NORMAL)
        cell.set_property("foreground", None)

        if kind_label == "Ошибка":
            cell.set_property("foreground", "#cc0000")
            cell.set_property("weight", Pango.Weight.BOLD)
        elif kind_label == "Внимание":
            cell.set_property("foreground", "#b45f06")
            cell.set_property("weight", Pango.Weight.BOLD)
        elif kind_label == "Успех":
            cell.set_property("foreground", "#1b7f2a")
        elif kind_label == "Раздел":
            cell.set_property("foreground", "#0b5394")
            cell.set_property("weight", Pango.Weight.BOLD)
        elif kind_label == "Команда":
            cell.set_property("foreground", "#555555")
        elif kind_label == "Путь":
            cell.set_property("foreground", "#274e13")

    # ---------------------------------------------------------------------
    # Buttons
    # ---------------------------------------------------------------------

    def _on_expand_all(self, button):
        self.steps_tree.expand_all()

    def _on_collapse_all(self, button):
        self.steps_tree.collapse_all()

    def _on_toggle_only_problems(self, button):
        self.filter_model.refilter()
        if button.get_active():
            self.steps_tree.expand_all()
