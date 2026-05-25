import re

QT_API = None

try:
    from PyQt6.QtCore import Qt, QTimer, QPoint
    from PyQt6.QtGui import (
        QColor, QTextCharFormat, QFont, QSyntaxHighlighter,
        QAction, QBrush
    )
    from PyQt6.QtWidgets import (
        QApplication, QWidget, QTextEdit, QPlainTextEdit,
        QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
        QCheckBox, QTreeWidget, QTreeWidgetItem
    )
    QT_API = "PyQt6"
except Exception:
    try:
        from PyQt5.QtCore import Qt, QTimer, QPoint
        from PyQt5.QtGui import (
            QColor, QTextCharFormat, QFont, QSyntaxHighlighter,
            QBrush
        )
        from PyQt5.QtWidgets import (
            QApplication, QWidget, QTextEdit, QPlainTextEdit,
            QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
            QCheckBox, QTreeWidget, QTreeWidgetItem, QAction
        )
        QT_API = "PyQt5"
    except Exception:
        try:
            from PySide6.QtCore import Qt, QTimer, QPoint
            from PySide6.QtGui import (
                QColor, QTextCharFormat, QFont, QSyntaxHighlighter,
                QAction, QBrush
            )
            from PySide6.QtWidgets import (
                QApplication, QWidget, QTextEdit, QPlainTextEdit,
                QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
                QCheckBox, QTreeWidget, QTreeWidgetItem
            )
            QT_API = "PySide6"
        except Exception:
            QT_API = None


_ATTEMPTS = 0
_MAX_ATTEMPTS = 160
_INSTALLED = False


class LogKind:
    NORMAL = "normal"
    HEADER = "header"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    COMMAND = "command"
    PATH = "path"
    MUTED = "muted"


def _qt_user_role(offset=0):
    try:
        return int(Qt.ItemDataRole.UserRole) + offset
    except Exception:
        try:
            return int(Qt.UserRole) + offset
        except Exception:
            return 32 + offset


ROLE_KIND = _qt_user_role(1)
ROLE_PROBLEM = _qt_user_role(2)


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

    if re.search(
        r"(Аварийное завершение|Ошибка|ошибка|не удалось|Не удалось|ПРОПУСК|"
        r"Код завершения:\s*[1-9]\d*|завершился с ошибкой|не найден|не найдена|"
        r"не вернула|не определена|не определен|не идентифицирован|не дал результата)",
        s,
    ):
        return LogKind.ERROR

    if re.search(
        r"(Внимание|ТРЕБУЕТ ВНИМАНИЯ|warning|fallback|не заполнена|не заполнен|"
        r"unknown hash format|предупреждение)",
        s,
        re.IGNORECASE,
    ):
        return LogKind.WARNING

    if re.search(
        r"(Готово|завершено|Код завершения:\s*0|Определено:|После проверки определено:|"
        r"Файл скачан:|Распаковано|Папка релиза нормализована|Архив после распаковки удален|"
        r"Скачивание обновлений:.*завершено)",
        s,
    ):
        return LogKind.SUCCESS

    if re.search(r"(/mnt/|/home/|/tmp/|/opt/|\.log|\.json|\.zip|\.cfu|\.htm)", s):
        return LogKind.PATH

    return LogKind.NORMAL


def color_for_kind(kind):
    return {
        LogKind.HEADER: "#0b5394",
        LogKind.SUCCESS: "#1b7f2a",
        LogKind.WARNING: "#b45f06",
        LogKind.ERROR: "#cc0000",
        LogKind.COMMAND: "#555555",
        LogKind.PATH: "#274e13",
        LogKind.MUTED: "#777777",
        LogKind.NORMAL: "#222222",
    }.get(kind, "#222222")


def label_for_kind(kind):
    return {
        LogKind.HEADER: "Раздел",
        LogKind.SUCCESS: "Успех",
        LogKind.WARNING: "Внимание",
        LogKind.ERROR: "Ошибка",
        LogKind.COMMAND: "Команда",
        LogKind.PATH: "Путь",
        LogKind.MUTED: "",
        LogKind.NORMAL: "Инфо",
    }.get(kind, "Инфо")


def icon_for_kind(kind):
    return {
        LogKind.HEADER: "▸",
        LogKind.SUCCESS: "✔",
        LogKind.WARNING: "⚠",
        LogKind.ERROR: "✖",
        LogKind.COMMAND: "⌘",
        LogKind.PATH: "📁",
        LogKind.MUTED: "•",
        LogKind.NORMAL: "•",
    }.get(kind, "•")


def is_important_tree_line(s, kind):
    if kind in (LogKind.ERROR, LogKind.WARNING, LogKind.SUCCESS, LogKind.COMMAND):
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
        r"^Лог проверки:",
        r"^Хвост лога:",
    ]

    return any(re.search(p, s) for p in patterns)


class LogHighlighter(QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)

        self._formats = {}

        for kind in (
            LogKind.NORMAL,
            LogKind.HEADER,
            LogKind.SUCCESS,
            LogKind.WARNING,
            LogKind.ERROR,
            LogKind.COMMAND,
            LogKind.PATH,
            LogKind.MUTED,
        ):
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(color_for_kind(kind)))

            if kind in (LogKind.HEADER, LogKind.SUCCESS, LogKind.WARNING, LogKind.ERROR):
                try:
                    fmt.setFontWeight(QFont.Weight.Bold)
                except Exception:
                    fmt.setFontWeight(QFont.Bold)

            if kind == LogKind.COMMAND:
                fmt.setFontFamily("Monospace")

            self._formats[kind] = fmt

    def highlightBlock(self, text):
        kind = classify_line(text)
        fmt = self._formats.get(kind, self._formats[LogKind.NORMAL])
        self.setFormat(0, len(text), fmt)


class StepsDialog(QDialog):
    def __init__(self, log_widget, parent=None):
        super().__init__(parent)

        self.log_widget = log_widget

        self.setWindowTitle("Структура лога выполнения")
        self.resize(1100, 700)

        root = QVBoxLayout(self)

        toolbar = QHBoxLayout()
        self.btn_refresh = QPushButton("Обновить")
        self.btn_expand = QPushButton("Развернуть все")
        self.btn_collapse = QPushButton("Свернуть все")
        self.chk_only_problems = QCheckBox("Только проблемы")

        toolbar.addWidget(self.btn_refresh)
        toolbar.addWidget(self.btn_expand)
        toolbar.addWidget(self.btn_collapse)
        toolbar.addWidget(self.chk_only_problems)
        toolbar.addStretch(1)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Шаг / событие", "Тип"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setWordWrap(False)
        self.tree.setColumnWidth(0, 850)

        root.addLayout(toolbar)
        root.addWidget(self.tree, 1)

        self.btn_refresh.clicked.connect(self.reload)
        self.btn_expand.clicked.connect(self.tree.expandAll)
        self.btn_collapse.clicked.connect(self.tree.collapseAll)
        self.chk_only_problems.toggled.connect(self.apply_filter)

        self.reload()

    def get_log_text(self):
        try:
            return self.log_widget.toPlainText()
        except Exception:
            return ""

    def reload(self):
        self.tree.clear()

        current_root = None
        current_base = None
        current_step = None
        last_command = None
        inside_command = False

        for line in self.get_log_text().splitlines():
            s = line.strip()
            kind = classify_line(line)

            if not s:
                continue

            root_match = re.match(r"^=+\s+(.*?)\s+=+$", s)
            if root_match:
                current_root = self.add_item(None, root_match.group(1), LogKind.HEADER)
                current_base = None
                current_step = current_root
                inside_command = False
                continue

            index_match = re.match(r"^\[(\d+)/(\d+)\]$", s)
            if index_match:
                current_base = self.add_item(
                    current_root,
                    f"База {index_match.group(1)} из {index_match.group(2)}",
                    LogKind.HEADER,
                )
                current_step = current_base
                inside_command = False
                continue

            section_match = re.match(r"^-{3,}\s+(.*?)\s+-{3,}$", s)
            if section_match:
                title = section_match.group(1)
                parent = current_base or current_root

                if title.startswith("Проверка базы:") or title.startswith("Скачивание обновлений для базы:"):
                    current_base = self.add_item(parent, title, LogKind.HEADER)
                    current_step = current_base
                else:
                    current_step = self.add_item(parent, title, LogKind.HEADER)

                inside_command = False
                continue

            if s == "Команда:":
                parent = current_step or current_base or current_root
                last_command = self.add_item(parent, "Команда запуска", LogKind.COMMAND)
                inside_command = True
                continue

            if inside_command:
                if s.startswith("Окружение запуска:") or s.startswith("Код завершения:"):
                    inside_command = False
                else:
                    if last_command is not None:
                        old = last_command.toolTip(0) or ""
                        new = (old + "\n" + line).strip()
                        last_command.setToolTip(0, new)
                    continue

            if is_important_tree_line(s, kind):
                parent = current_step or current_base or current_root
                self.add_item(parent, s, kind)

        self.apply_filter()

    def add_item(self, parent, text, kind):
        item = QTreeWidgetItem([f"{icon_for_kind(kind)} {text}", label_for_kind(kind)])
        item.setData(0, ROLE_KIND, kind)
        item.setData(0, ROLE_PROBLEM, kind in (LogKind.ERROR, LogKind.WARNING))
        item.setToolTip(0, text)

        brush = QBrush(QColor(color_for_kind(kind)))
        for col in range(2):
            item.setForeground(col, brush)
            font = item.font(col)
            font.setBold(kind in (LogKind.HEADER, LogKind.ERROR, LogKind.WARNING))
            item.setFont(col, font)

        if parent is None:
            self.tree.addTopLevelItem(item)
        else:
            parent.addChild(item)
            parent.setExpanded(True)

        if kind in (LogKind.ERROR, LogKind.WARNING):
            cur = item
            while cur is not None:
                cur.setData(0, ROLE_PROBLEM, True)
                cur = cur.parent()

        return item

    def apply_filter(self):
        only = self.chk_only_problems.isChecked()

        def apply_item(item):
            has_problem = bool(item.data(0, ROLE_PROBLEM))
            item.setHidden(only and not has_problem)

            for i in range(item.childCount()):
                apply_item(item.child(i))

        for i in range(self.tree.topLevelItemCount()):
            apply_item(self.tree.topLevelItem(i))

        if only:
            self.tree.expandAll()


def _set_no_wrap(widget):
    try:
        if isinstance(widget, QTextEdit):
            try:
                widget.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
            except Exception:
                widget.setLineWrapMode(QTextEdit.NoWrap)
        elif isinstance(widget, QPlainTextEdit):
            try:
                widget.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            except Exception:
                widget.setLineWrapMode(QPlainTextEdit.NoWrap)
    except Exception:
        pass


def _widget_text(widget):
    try:
        return widget.toPlainText()
    except Exception:
        return ""


def _score_log_widget(widget):
    if getattr(widget, "_u1c_log_inline_attached", False):
        return -9999

    score = 0

    try:
        if widget.isReadOnly():
            score += 140
    except Exception:
        pass

    try:
        name = (widget.objectName() or "").lower()
        if any(x in name for x in ("log", "journal", "report", "output", "textlog", "txtlog")):
            score += 120
    except Exception:
        pass

    try:
        text = _widget_text(widget)
        if any(marker in text for marker in (
            "Код завершения",
            "--- Проверка базы",
            "=== Скачивание",
            "ПРОПУСК",
            "Готово",
            "Команда:",
            "Окружение запуска:",
        )):
            score += 350
    except Exception:
        pass

    try:
        size = widget.size()
        area = size.width() * size.height()
        score += min(140, area // 5000)
    except Exception:
        pass

    try:
        if widget.isVisible():
            score += 50
    except Exception:
        pass

    return score


def _show_steps_dialog(widget):
    parent = widget.window()
    dlg = StepsDialog(widget, parent)
    dlg.setModal(False)
    dlg.show()

    widget._u1c_steps_dialog = dlg


def _install_context_menu(widget):
    try:
        old_policy = widget.contextMenuPolicy()

        try:
            widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        except Exception:
            widget.setContextMenuPolicy(Qt.CustomContextMenu)

        def open_menu(pos):
            try:
                menu = widget.createStandardContextMenu(pos)
            except Exception:
                menu = widget.createStandardContextMenu()

            menu.addSeparator()
            action_steps = QAction("Показать структуру лога", menu)
            action_steps.triggered.connect(lambda: _show_steps_dialog(widget))
            menu.addAction(action_steps)

            try:
                global_pos = widget.mapToGlobal(pos)
            except Exception:
                global_pos = QPoint(0, 0)

            menu.exec(global_pos)

        widget.customContextMenuRequested.connect(open_menu)
    except Exception:
        pass


def _attach(widget):
    if getattr(widget, "_u1c_log_inline_attached", False):
        return True

    try:
        widget._u1c_log_highlighter = LogHighlighter(widget.document())
        widget._u1c_log_inline_attached = True

        try:
            widget.setFont(QFont("Monospace", 10))
        except Exception:
            pass

        _set_no_wrap(widget)
        _install_context_menu(widget)

        return True
    except Exception as e:
        print("structured_log_qt_inline attach error:", e)
        return False


def _try_install_once():
    app = QApplication.instance()
    if app is None:
        return False

    candidates = []

    for top in app.topLevelWidgets():
        try:
            widgets = top.findChildren(QWidget)
        except Exception:
            widgets = []

        for widget in widgets:
            if isinstance(widget, (QTextEdit, QPlainTextEdit)):
                score = _score_log_widget(widget)
                candidates.append((score, widget))

    candidates.sort(key=lambda x: x[0], reverse=True)

    if not candidates:
        return False

    best_score, best_widget = candidates[0]

    if best_score < 120:
        return False

    return _attach(best_widget)


def _timer_tick():
    global _ATTEMPTS, _INSTALLED

    if _INSTALLED:
        return

    _ATTEMPTS += 1

    try:
        if _try_install_once():
            _INSTALLED = True
            return
    except Exception as e:
        print("structured_log_qt_inline install error:", e)

    if _ATTEMPTS < _MAX_ATTEMPTS:
        QTimer.singleShot(500, _timer_tick)


def install_inline_log_tools():
    if QT_API is None:
        return

    app = QApplication.instance()
    if app is None:
        return

    QTimer.singleShot(300, _timer_tick)
    QTimer.singleShot(1200, _timer_tick)
    QTimer.singleShot(2500, _timer_tick)
