#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
from pathlib import Path

src = Path("main.py")
text = src.read_text(encoding="utf-8", errors="replace")
lines = text.splitlines()

out = []

out.append("=== QT UI INVENTORY: main.py ===")
out.append(f"Lines: {len(lines)}")
out.append("")

patterns = {
    "classes": r"^\s*class\s+([A-Za-z_А-Яа-я0-9]+)\s*\(",
    "functions": r"^\s*def\s+([A-Za-z_А-Яа-я0-9]+)\s*\(",
    "qt_widgets": r"(QMainWindow|QWidget|QDialog|QTabWidget|QSplitter|QGroupBox|QFrame|QScrollArea|QTableWidget|QTreeWidget|QListWidget|QTextEdit|QPlainTextEdit|QLineEdit|QComboBox|QCheckBox|QRadioButton|QPushButton|QToolButton|QLabel|QProgressBar|QSpinBox|QDateEdit|QFileDialog)",
    "layouts": r"(QVBoxLayout|QHBoxLayout|QGridLayout|QFormLayout|addWidget|addLayout|addTab|setLayout|setCentralWidget)",
    "buttons": r"(QPushButton|QToolButton|addAction|QAction|setText|clicked\.connect)",
    "signals": r"\.connect\(",
    "object_names": r"setObjectName\(",
    "styles": r"(setStyleSheet|QPalette|QColor|background|color:|border|font)",
    "texts_ru": r"['\"]([^'\"]*[А-Яа-яЁё][^'\"]*)['\"]",
}

def add_section(title, matches):
    out.append("")
    out.append(f"=== {title} ===")
    if not matches:
        out.append("not found")
        return
    for n, line in matches:
        out.append(f"{n:05d}: {line}")

for title, pat in patterns.items():
    rx = re.compile(pat)
    matches = []
    for i, line in enumerate(lines, 1):
        if rx.search(line):
            matches.append((i, line.rstrip()))
    add_section(title, matches[:500])
    if len(matches) > 500:
        out.append(f"... truncated, total: {len(matches)}")

out.append("")
out.append("=== BUTTON/TEXT CANDIDATES GROUPED ===")
for i, line in enumerate(lines, 1):
    if any(x in line for x in [
        "QPushButton", "QToolButton", "QAction", "clicked.connect",
        "setText(", "addTab(", "QGroupBox", "QTabWidget"
    ]):
        chunk = "\n".join(f"{j:05d}: {lines[j-1]}" for j in range(max(1, i-3), min(len(lines), i+4)+1))
        out.append("")
        out.append(chunk)

Path("reports/qt_ui_inventory.txt").write_text("\n".join(out), encoding="utf-8")
print("Готово: reports/qt_ui_inventory.txt")
