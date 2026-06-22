#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import ast
from pathlib import Path

src = Path("gtk_port/updater1c_gtk.py")
tree = ast.parse(src.read_text(encoding="utf-8"), filename=str(src))
classes = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
cls = classes.get("TemplateSelectDialogGtk")
if cls is None:
    raise SystemExit("TemplateSelectDialogGtk not found")
methods = {n.name for n in cls.body if isinstance(n, ast.FunctionDef)}
required = {
    "load_templates",
    "refresh_templates",
    "template_roots",
    "template_variants",
    "accept_selected",
    "choose_root",
    "choose_file_or_folder",
}
missing = sorted(required - methods)
if missing:
    raise SystemExit("Missing methods: " + ", ".join(missing))
text = src.read_text(encoding="utf-8")
if "self.load_templates()" not in text:
    raise SystemExit("No self.load_templates() calls found")
print("OK: TemplateSelectDialogGtk static smoke-test passed")
