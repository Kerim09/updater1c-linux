# -*- coding: utf-8 -*-
"""
Скрытый запуск технических команд 1С через xvfb-run.

Важно:
- обычный двойной клик / ENTERPRISE не прячется;
- обычный ручной запуск DESIGNER не прячется;
- прячутся только пакетные команды DESIGNER: DumpCfg, DumpIB, UpdateDBCfg и т.п.
"""

import os
import shlex
import shutil
import subprocess


_ORIGINAL_RUN = subprocess.run
_ORIGINAL_POPEN = subprocess.Popen

_HEADLESS_FLAGS = (
    "/dumpcfg",
    "/dumpib",
    "/loadcfg",
    "/updatedbcfg",
    "/dumpconfigtofiles",
    "/dumpconfigfromfiles",
    "/checkconfig",
    "/checkmodules",
    "/updatecfg",
    "/loadconfigfromfiles",
)


def _cmd_to_list(args):
    if isinstance(args, (list, tuple)):
        return [str(x) for x in args]
    return []


def _cmd_to_text(args):
    if isinstance(args, (list, tuple)):
        return " ".join(str(x) for x in args)
    return str(args)


def _is_technical_1c_command(args):
    if os.environ.get("UPDATER1C_DISABLE_XVFB", "").strip() == "1":
        return False

    text = _cmd_to_text(args).lower()
    parts = [p.lower() for p in _cmd_to_list(args)]

    if "xvfb-run" in text:
        return False

    has_designer = (
        " designer " in f" {text} "
        or any(p == "designer" for p in parts)
    )
    if not has_designer:
        return False

    has_batch_flag = any(flag in text for flag in _HEADLESS_FLAGS)
    if not has_batch_flag:
        return False

    has_1c_binary = (
        "1cv8" in text
        or "1cv8c" in text
        or "1cestart" in text
    )

    return has_1c_binary


def _wrap_headless(args, shell=False):
    if not _is_technical_1c_command(args):
        return args

    xvfb = shutil.which("xvfb-run")
    if not xvfb:
        return args

    screen_args = "-screen 0 1280x1024x24"

    if shell:
        return (
            shlex.quote(xvfb)
            + " -a -s "
            + shlex.quote(screen_args)
            + " "
            + str(args)
        )

    if isinstance(args, (list, tuple)):
        return [xvfb, "-a", "-s", screen_args] + list(args)

    return [xvfb, "-a", "-s", screen_args] + shlex.split(str(args))


def _patched_run(*popenargs, **kwargs):
    if popenargs:
        args = _wrap_headless(popenargs[0], shell=bool(kwargs.get("shell")))
        popenargs = (args,) + popenargs[1:]
    elif "args" in kwargs:
        kwargs["args"] = _wrap_headless(kwargs["args"], shell=bool(kwargs.get("shell")))

    return _ORIGINAL_RUN(*popenargs, **kwargs)


class _PatchedPopen(_ORIGINAL_POPEN):
    def __init__(self, args, *popenargs, **kwargs):
        args = _wrap_headless(args, shell=bool(kwargs.get("shell")))
        super().__init__(args, *popenargs, **kwargs)


subprocess.run = _patched_run
subprocess.Popen = _PatchedPopen
