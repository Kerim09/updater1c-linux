#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import html
import base64
import re
import shlex
import shutil
import subprocess
import sys
import time
import threading
import tempfile
import xml.etree.ElementTree as ET
import urllib.request
import urllib.error
from urllib.parse import urljoin
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib


APP_NAME = "Обновлятор 1C Linux"
APP_VERSION = "1.2.2"
CONFIG_DIR = Path.home() / ".config" / "updater1c-linux"

DEFAULT_1CESTART = "/opt/1cv8/common/1cestart"


def save_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def safe_name(value: str) -> str:
    import re
    return re.sub(r"[^0-9A-Za-zА-Яа-яЁё_.-]+", "_", (value or "base").strip())


def now_stamp() -> str:
    return time.strftime("%Y-%m-%d_%H-%M-%S")


def normalize_base_kind_and_connect(base: dict) -> tuple[str, str]:
    kind = (base.get("kind") or "file").strip()
    connect = (base.get("connect") or "").strip()

    low = connect.lower()

    def extract_quoted(key):
        import re
        m = re.search(rf'{key}\s*=\s*"([^"]*)"', connect, flags=re.I)
        if m:
            return m.group(1)
        m = re.search(rf"{key}\s*=\s*([^;]+)", connect, flags=re.I)
        return m.group(1).strip() if m else ""

    if low.startswith("file="):
        return "file", extract_quoted("File") or connect

    if "srvr=" in low and "ref=" in low:
        srv = extract_quoted("Srvr")
        ref = extract_quoted("Ref")
        return "server", f"{srv}\\{ref}" if srv and ref else connect

    if low.startswith(("ws=", "wsurl=", "web=")):
        url = extract_quoted("WS") or extract_quoted("Ws") or extract_quoted("WsUrl") or extract_quoted("Web")
        return "web", url or connect.split("=", 1)[-1].strip('"; ')

    if low.startswith(("http://", "https://")):
        return "web", connect

    return kind, connect





def platform_search_roots():
    return [
        Path("/opt/1cv8/x86_64"),
        Path("/opt/1cv8/i386"),
        Path("/opt/1cv8"),
        Path("/opt/1C/v8.3/x86_64"),
        Path("/opt/1C/v8.3/i386"),
        Path("/opt/1C/v8.5/x86_64"),
        Path("/opt/1C/v8.5/i386"),
        Path("/opt/1C"),
    ]

def version_key(version: str):
    import re
    nums = [int(x) for x in re.findall(r"\d+", version or "")]
    return tuple(nums or [0])





def find_platforms():
    found = {}
    exe_names = ("1cv8", "1cv8c", "1cv8s", "1cestart")

    def is_version_name(name: str) -> bool:
        return bool(re.match(r"^\d+\.\d+(?:\.\d+){1,3}$", str(name or "")))

    def add_version(version_dir: Path):
        if not version_dir.is_dir():
            return

        version = version_dir.name
        if not is_version_name(version):
            return

        # Для DESIGNER и универсального запуска предпочтительнее 1cv8.
        for exe_name in exe_names:
            exe = version_dir / exe_name
            if exe.is_file():
                found[version] = str(exe)
                return

    for root in platform_search_roots():
        if not root.exists():
            continue

        add_version(root)

        try:
            for child in root.iterdir():
                add_version(child)
        except Exception:
            pass

    for root in (Path("/opt/1cv8"), Path("/opt/1C")):
        if not root.exists():
            continue

        for exe_name in ("1cv8", "1cv8c"):
            try:
                for exe in root.rglob(exe_name):
                    if exe.is_file():
                        add_version(exe.parent)
            except Exception:
                pass

    return dict(sorted(found.items(), key=lambda x: version_key(x[0]), reverse=True))

def find_1cestart(configured=""):
    candidates = [
        configured,
        DEFAULT_1CESTART,
        "/opt/1C/v8.3/common/1cestart",
        "/opt/1C/v8.5/common/1cestart",
        shutil.which("1cestart") or "",
    ]

    for c in candidates:
        if c and Path(c).exists() and os.access(c, os.X_OK):
            return c

    return ""


def select_platform_exe_for_base(base: dict, settings: dict, mode: str):
    version = (base.get("platform_version") or "8.*").strip()
    client_mode = (base.get("client_mode") or "thin").strip().lower()

    if version == "8.*":
        launcher = find_1cestart(settings.get("one_c_common_path") or settings.get("one_c_start") or "")
        if launcher:
            return launcher

    platforms = find_platforms()

    selected = ""
    if version in platforms:
        selected = platforms[version]
    else:
        prefix = ""
        if version in ("8.3", "8.5"):
            prefix = version + "."
        elif version.endswith(".*"):
            prefix = version[:-2] + "."

        if prefix:
            matches = [(v, e) for v, e in platforms.items() if v.startswith(prefix)]
            if matches:
                matches.sort(key=lambda x: version_key(x[0]), reverse=True)
                selected = matches[0][1]

    if not selected:
        configured = settings.get("selected_platform") or ""
        if configured and Path(configured).exists():
            selected = configured

    if not selected and platforms:
        selected = next(iter(platforms.values()))

    if not selected:
        return ""

    version_dir = Path(selected).parent

    if mode == "ENTERPRISE":
        if client_mode in ("thick", "толстый"):
            order = ["1cv8s", "1cv8", "1cv8c", "1cestart"]
        else:
            order = ["1cv8c", "1cv8", "1cv8s", "1cestart"]
    else:
        order = ["1cv8", "1cv8c", "1cv8s", "1cestart"]

    for name in order:
        exe = version_dir / name
        if exe.exists() and os.access(exe, os.X_OK):
            return str(exe)

    return selected


def build_1c_args(base: dict, settings: dict, mode: str):
    exe = select_platform_exe_for_base(base, settings, mode)
    if not exe:
        raise RuntimeError("Платформа 1С не найдена. Проверьте настройки платформы.")

    kind, connect = normalize_base_kind_and_connect(base)

    args = [exe, mode]

    if kind == "server":
        args.append("/S" + connect)
    elif kind == "web":
        args.append("/WS" + connect)
    else:
        args.append("/F" + connect)

    user = base.get("user") or ""
    password = base.get("password") or ""
    launch_parameters = base.get("launch_parameters") or ""

    if user:
        args.append("/N" + user)
    if password:
        args.append("/P" + password)
    if launch_parameters:
        args.extend(shlex.split(launch_parameters))

    return args


def command_to_text(args):
    return " ".join(shlex.quote(str(x)) for x in args)


def start_process_and_log(args, reports_dir: str, base_name: str, suffix: str = "launch"):
    Path(reports_dir or str(Path.home() / "1c-update-reports")).mkdir(parents=True, exist_ok=True)
    log_file = Path(reports_dir or str(Path.home() / "1c-update-reports")) / f"{safe_name(base_name)}_{suffix}_{now_stamp()}.log"

    with log_file.open("w", encoding="utf-8", errors="replace") as f:
        p = subprocess.Popen(args, stdout=f, stderr=subprocess.STDOUT)

    return p.pid, log_file





def run_command_capture(args, timeout=7200, env=None):
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)

    p = subprocess.run(
        args,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        env=merged_env,
        check=False,
    )
    return p.returncode, p.stdout or ""


def local_xml_name(tag: str) -> str:
    tag = str(tag or "")
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def good_synonym_text(value: str) -> bool:
    v = (value or "").strip()
    if not v:
        return False
    return v.lower() not in {"ru", "en", "de", "fr", "language", "content"}


def synonym_from_xml(elem) -> str:
    for x in elem.iter():
        if local_xml_name(x.tag).lower() == "content" and good_synonym_text(x.text or ""):
            return (x.text or "").strip()

    for x in elem.iter():
        if good_synonym_text(x.text or ""):
            return (x.text or "").strip()

    return ""


def parse_configuration_xml_file(xml_path: Path):
    tree = ET.parse(str(xml_path))
    root = tree.getroot()

    best = None
    for elem in root.iter():
        if local_xml_name(elem.tag).lower() == "configuration":
            best = elem
            break

    if best is None:
        best = root

    config_name = ""
    config_version = ""
    config_synonym = ""

    for elem in best.iter():
        lname = local_xml_name(elem.tag).lower()
        value = (elem.text or "").strip()

        if lname == "name" and not config_name and value:
            config_name = value

        if lname == "version" and not config_version and value:
            config_version = value

        if lname == "synonym" and not config_synonym:
            config_synonym = synonym_from_xml(elem)

    return config_name, config_synonym, config_version


def find_configuration_xml(dump_dir: Path):
    for name in ("Configuration.xml", "configuration.xml"):
        found = list(dump_dir.rglob(name))
        if found:
            return found[0]

    for f in dump_dir.rglob("*.xml"):
        try:
            head = f.read_text(encoding="utf-8", errors="ignore")[:10000].lower()
            if "configuration" in head and "version" in head:
                return f
        except Exception:
            pass

    return None


def detect_metadata_by_dump(base: dict, settings: dict, log_func):
    kind, connect = normalize_base_kind_and_connect(base)

    if kind == "web":
        raise RuntimeError("Для web-базы DumpConfigToFiles напрямую не выполняется.")

    platform = select_platform_exe_for_base(base, settings, "DESIGNER")
    if not platform:
        raise RuntimeError("Платформа 1С для DESIGNER не найдена.")

    reports_dir = Path(settings.get("reports_dir") or "/mnt/DataStore/Updater1C/1c-update-reports")
    reports_dir.mkdir(parents=True, exist_ok=True)

    tmp_root = Path(tempfile.mkdtemp(prefix="u1c_gtk_metadata_dump_"))
    dump_dir = tmp_root / "dump"
    dump_dir.mkdir(parents=True, exist_ok=True)

    list_file = tmp_root / "list.txt"
    list_file.write_text("Configuration\n", encoding="utf-8")

    out_file = reports_dir / f"{safe_name(base.get('name') or 'base')}_DumpConfigToFiles_{now_stamp()}.out"

    args = build_1c_args(base, settings, "DESIGNER")
    args.extend([
        "/DisableStartupDialogs",
        "/DisableStartupMessages",
        "/DumpConfigToFiles",
        str(dump_dir),
        "-Format",
        "Plain",
        "-listFile",
        str(list_file),
        "/Out",
        str(out_file),
        "-NoTruncate",
    ])

    log_func("Пробую определить конфигурацию и версию через мини-дамп Configuration.xml...")
    log_func("Выгружается только объект Configuration через -listFile, без полного дампа конфигурации.")
    log_func("Команда:")
    log_func(command_to_text(args))

    rc, out = run_command_capture(args, timeout=7200)

    if out.strip():
        log_func(out.strip())

    log_func(f"Код завершения: {rc}")
    log_func(f"Лог проверки: {out_file}")
    log_func(f"Каталог дампа: {dump_dir}")

    if out_file.exists():
        tail = out_file.read_text(encoding="utf-8", errors="replace")[-4000:]
        if tail.strip():
            log_func("Хвост лога:")
            log_func(tail.strip())

    xml_path = find_configuration_xml(dump_dir)
    if not xml_path:
        raise RuntimeError("Configuration.xml не найден после DumpConfigToFiles.")

    name, synonym, version = parse_configuration_xml_file(xml_path)

    if not name and not synonym and not version:
        raise RuntimeError(f"Не удалось прочитать имя/синоним/версию из {xml_path}")

    return {
        "config_name": name,
        "config_synonym": synonym,
        "config_version": version,
        "xml_path": str(xml_path),
        "dump_dir": str(dump_dir),
        "out_file": str(out_file),
    }


def combo_set_values(combo, values, current=""):
    try:
        combo.remove_all()
    except Exception:
        pass

    values = [str(x) for x in values if str(x) != ""]
    current = str(current or "")

    if current and current not in values:
        values.insert(0, current)

    if not values:
        values = [""]

    for v in values:
        combo.append_text(v)

    try:
        idx = values.index(current) if current in values else 0
        combo.set_active(idx)
    except Exception:
        combo.set_active(0)


def combo_get_text(combo):
    try:
        return combo.get_active_text() or ""
    except Exception:
        return ""


def widget_text_value(widget):
    try:
        if isinstance(widget, Gtk.Entry):
            return widget.get_text() or ""
    except Exception:
        pass

    try:
        return widget.get_active_text() or ""
    except Exception:
        return ""



def known_groups_from_bases(bases):
    result = []
    for b in bases or []:
        g = (b.get("group") or "").strip()
        if g and g not in result:
            result.append(g)
    for g in ["Мое", "МФ", "Арктобако", "Перетрубция"]:
        if g not in result:
            result.append(g)
    return result





def known_platform_versions(settings=None, current=""):
    result = ["8.*", "8.3", "8.5"]

    try:
        for v in find_platforms().keys():
            if v not in result:
                result.append(v)
    except Exception:
        pass

    current = str(current or "").strip()
    if current and current not in result:
        result.insert(0, current)

    return result

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



def u1c_fix_1c_public_download_url(url):
    """Нормализует endpoint скачивания файлов 1С."""
    url = str(url or "").strip()
    url = url.replace("/public/file/get/", "/public/file/get/")
    url = url.replace("/public/file/get", "/public/file/get")
    return url



def u1c_download_url_variants(url):
    """Возвращает варианты URL скачивания файла 1С.

    update-api для обновлений конфигураций иногда отдает:
      /public/file/tmplts/get/<uuid>

    Но рабочий endpoint у dl*.1c.ru часто:
      /public/file/get/<uuid>

    Поэтому пробуем оба варианта.
    """
    url = str(url or "").strip()
    if not url:
        return []

    variants = []

    def add(u):
        if u and u not in variants:
            variants.append(u)

    add(url)

    add(url.replace("/public/file/tmplts/get/", "/public/file/get/"))
    add(url.replace("/public/file/tmplts/get", "/public/file/get"))

    add(url.replace("/public/file/get/", "/public/file/tmplts/get/"))
    add(url.replace("/public/file/get", "/public/file/tmplts/get"))

    return variants


def u1c_normalize_first_download_url(url):
    """Для логирования сразу показывает предпочтительный URL."""
    variants = u1c_download_url_variants(url)

    for u in variants:
        if "/public/file/get/" in u:
            return u

    return variants[0] if variants else str(url or "")


class Ui:
    @staticmethod
    def label(text):
        w = Gtk.Label()
        text = str(text or "")
        if "<b>" in text or "</b>" in text or "<small>" in text or "</small>" in text:
            try:
                w.set_markup(text)
            except Exception:
                w.set_text(text)
        else:
            w.set_text(text)
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





def http_json_post(url: str, body: dict, timeout: int = 120) -> dict:
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "1C+Enterprise/8.3",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            text = raw.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {err[:1200]}")
    except Exception as e:
        raise RuntimeError(f"{type(e).__name__}: {e}")

    try:
        data = json.loads(text)
    except Exception:
        raise RuntimeError(f"Ответ update-api не JSON: {text[:1200]}")

    if isinstance(data, dict) and data.get("errorName"):
        raise RuntimeError(f"{data.get('errorName')}: {data.get('errorMessage')}")

    return data


class GtkOneCUpdateApi:
    BASE_URL = "https://update-api.1c.ru"
    UPDATE_INFO_PATH = "/update-platform/programs/update/info"
    UPDATE_PATH = "/update-platform/programs/update/"

    UPDATE_TYPE_WORKING = "NewConfigurationAndOrPlatform"
    UPDATE_TYPE_PROGRAM_OR_REDACTION = "NewProgramOrRedaction"

    def __init__(self, login: str, password: str):
        self.login = login or ""
        self.password = password or ""

    def get_update_info(self, program: str, version: str, update_type: str, platform_version: str) -> dict:
        body = {
            "programName": (program or "").strip(),
            "versionNumber": (version or "").strip(),
            "updateType": update_type,
            "platformVersion": (platform_version or "").strip() or "8.3",
        }
        return http_json_post(self.BASE_URL + self.UPDATE_INFO_PATH, body)

    def check_conf_update(self, program: str, version: str, platform_version: str, allow_next_redaction: bool = False) -> dict:
        update_type = self.UPDATE_TYPE_PROGRAM_OR_REDACTION if allow_next_redaction else self.UPDATE_TYPE_WORKING
        full = self.get_update_info(program, version, update_type, platform_version)
        return full.get("configurationUpdateResponse") or {}

    def get_conf_download_data(self, upgrade_sequence, program_uin: str) -> list:
        body = {
            "programVersionUin": program_uin or "",
            "upgradeSequence": upgrade_sequence or [],
            "platformDistributionUin": "",
            "login": self.login,
            "password": self.password,
        }
        data = http_json_post(self.BASE_URL + self.UPDATE_PATH, body)
        return data.get("configurationUpdateDataList") or []


def gtk_get_its_password(settings: dict) -> str:
    # 1. Старый/plaintext вариант, если он есть.
    value = settings.get("its_password") or ""
    if value:
        return value

    # 2. Новый вариант через secret_store, если модуль рядом и пароль сохранён.
    try:
        from secret_store import get_secret, its_password_account
        return get_secret(its_password_account()) or ""
    except Exception:
        return ""


def extract_release_from_any(value) -> str:
    m = re.search(r"\b\d+(?:\.\d+){2,4}\b", str(value or ""))
    return m.group(0) if m else ""


def update_item_release_version(item: dict, fallback: str = "") -> str:
    candidates = [
        item.get("version"),
        item.get("versionNumber"),
        item.get("release"),
        item.get("releaseVersion"),
        item.get("templatePath"),
        item.get("fileName"),
        item.get("updateFileName"),
        item.get("name"),
        fallback,
    ]
    for c in candidates:
        v = extract_release_from_any(c)
        if v:
            return v
    return ""


def update_item_url(item: dict) -> str:
    for key in (
        "downloadUrl",
        "url",
        "configurationUpdateFileUrl",
        "updateFileUrl",
        "templateUrl",
        "fileUrl",
    ):
        value = item.get(key)
        if value:
            return str(value)
    return ""


def update_item_filename(item: dict, url: str, fallback: str = "update.zip") -> str:
    for key in ("fileName", "updateFileName", "name"):
        value = item.get(key)
        if value:
            return safe_name(Path(str(value)).name)

    if url:
        name = Path(url.split("?", 1)[0]).name
        if name:
            return safe_name(name)

    return fallback


def update_program_dir(settings: dict, program: str) -> Path:
    root = Path(settings.get("updates_dir") or "/mnt/DataStore/Updater1C/1c-updates")
    return root / safe_name(program or "UnknownProgram")



def download_url_to_file(url: str, dest: Path, log_func, login: str = "", password: str = ""):
    dest.parent.mkdir(parents=True, exist_ok=True)

    headers = {
        "User-Agent": "Mozilla/5.0",
    }
    headers.update(auth_header_basic(login, password))

    req = urllib.request.Request(url, headers=headers)

    with urllib.request.urlopen(req, timeout=120) as r:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        last_percent = -1

        with dest.open("wb") as f:
            while True:
                chunk = r.read(1024 * 1024)
                if not chunk:
                    break

                f.write(chunk)
                done += len(chunk)

                if total:
                    percent = int(done * 100 / total)
                    if percent != last_percent and (percent % 5 == 0 or percent == 100):
                        last_percent = percent
                        log_func(f"Скачано {percent}% ({done // 1024 // 1024} / {total // 1024 // 1024} МБ)")
                else:
                    mb = done // 1024 // 1024
                    log_func(f"Скачано {mb} МБ")

    log_func(f"Файл скачан: {dest}")
    return dest

def find_download_url_recursive(obj):
    if isinstance(obj, dict):
        direct = update_item_url(obj)
        if direct:
            return direct

        for v in obj.values():
            found = find_download_url_recursive(v)
            if found:
                return found

    elif isinstance(obj, list):
        for v in obj:
            found = find_download_url_recursive(v)
            if found:
                return found

    return ""





class PlatformVersionSelectDialog(Gtk.Dialog):
    def __init__(self, parent, current=""):
        super().__init__(
            title="Выбор версии платформы 1С — Обновлятор 1C Linux",
            transient_for=parent,
            flags=0,
        )
        self.set_default_size(680, 470)
        self.add_button("OK", Gtk.ResponseType.OK)
        self.add_button("Отмена", Gtk.ResponseType.CANCEL)

        box = self.get_content_area()
        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        main.set_border_width(12)
        box.add(main)

        main.pack_start(Ui.label("Выберите версию платформы для строки базы:"), False, False, 0)

        self.store = Gtk.ListStore(str, str)
        self.tree = Gtk.TreeView(model=self.store)
        self.tree.set_headers_visible(True)
        self.tree.set_enable_tree_lines(True)

        r1 = Gtk.CellRendererText()
        c1 = Gtk.TreeViewColumn("Версия", r1, text=0)
        c1.set_fixed_width(160)
        c1.set_resizable(True)
        self.tree.append_column(c1)

        r2 = Gtk.CellRendererText()
        c2 = Gtk.TreeViewColumn("Исполняемый файл / режим выбора", r2, text=1)
        c2.set_expand(True)
        c2.set_resizable(True)
        self.tree.append_column(c2)

        rows = [
            ("8.*", "любая самая свежая установленная версия"),
            ("8.3", "самая свежая установленная версия 8.3"),
            ("8.5", "самая свежая установленная версия 8.5"),
        ]

        try:
            for version, exe in find_platforms().items():
                rows.append((version, exe))
        except Exception:
            pass

        seen = set()
        for version, description in rows:
            if version in seen:
                continue
            seen.add(version)
            self.store.append([version, description])

        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sw.add(self.tree)
        main.pack_start(sw, True, True, 0)

        main.pack_start(
            Ui.label(
                "<small>8.* — самая свежая установленная платформа. "
                "8.3/8.5 — самая свежая установленная платформа выбранного семейства.</small>"
            ),
            False,
            False,
            0,
        )

        self.tree.connect("row-activated", self.on_row_activated)

        current = str(current or "").strip()
        if current:
            it = self.store.get_iter_first()
            while it:
                if self.store[it][0] == current:
                    self.tree.get_selection().select_iter(it)
                    break
                it = self.store.iter_next(it)

        self.show_all()


    def on_row_activated(self, tree, path, column):
        try:
            model = tree.get_model()
            treeiter = model.get_iter(path)
            if not treeiter:
                return

            is_group = bool(model.get_value(treeiter, 5))
            if is_group:
                if tree.row_expanded(path):
                    tree.collapse_row(path)
                else:
                    tree.expand_row(path, False)
                return

            self.response(Gtk.ResponseType.OK)
        except Exception:
            pass

    def selected_version(self):
        model, it = self.tree.get_selection().get_selected()
        if it:
            return model[it][0]
        return ""

class LaunchParamsDialog(Gtk.Dialog):
    def __init__(self, parent, current_text=""):
        super().__init__(
            title="Параметры запуска 1С — Обновлятор 1C Linux",
            transient_for=parent,
            flags=0,
        )
        self.set_default_size(620, 420)
        self.add_button("OK", Gtk.ResponseType.OK)
        self.add_button("Отмена", Gtk.ResponseType.CANCEL)

        box = self.get_content_area()
        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        main.set_border_width(12)
        box.add(main)

        main.pack_start(Ui.label("<b>Дополнительные параметры запуска</b>"), False, False, 0)

        self.text = Gtk.TextView()
        self.text.set_monospace(True)
        self.text.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.text.get_buffer().set_text(current_text or "")

        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sw.add(self.text)
        main.pack_start(sw, True, True, 0)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        main.pack_start(buttons, False, False, 0)

        presets = [
            ("Отключить диалоги", "/DisableStartupDialogs"),
            ("Отключить сообщения запуска", "/DisableStartupMessages"),
            ("Не обрезать лог", "-NoTruncate"),
            ("Разрешить динамическое обновление", "-Dynamic+"),
            ("Запретить динамическое обновление", "-Dynamic-"),
            ("Ключ /C", "/C "),
        ]

        for title, value in presets:
            b = Gtk.Button(label=title)
            b.connect("clicked", lambda _b, v=value: self.append_param(v))
            buttons.pack_start(b, False, False, 0)

        self.show_all()

    def append_param(self, value):
        buf = self.text.get_buffer()
        start, end = buf.get_bounds()
        cur = buf.get_text(start, end, True).strip()
        if cur:
            cur += " "
        cur += value
        buf.set_text(cur)

    def get_text(self):
        buf = self.text.get_buffer()
        start, end = buf.get_bounds()
        return buf.get_text(start, end, True).strip()




class TemplateSelectDialogGtk(Gtk.Dialog):
    """GTK-выбор шаблона 1С по логике старого TemplateSelectDialog."""

    def __init__(self, parent=None):
        super().__init__(title="Выбор шаблона 1С", transient_for=parent, flags=Gtk.DialogFlags.MODAL)
        self.set_default_size(900, 560)

        self.selected_template = ""
        self.selected_template_kind = ""
        self.selected_template_name = ""
        self.selected_template_version = ""
        self.extra_roots = []

        self.add_button("Отмена", Gtk.ResponseType.CANCEL)
        self.add_button("OK", Gtk.ResponseType.OK)
        self.set_default_response(Gtk.ResponseType.OK)

        box = self.get_content_area()
        box.set_border_width(10)
        box.set_spacing(8)

        info = Gtk.Label(label="Выберите поставляемую конфигурацию для начала работы или демонстрационный пример.")
        info.set_xalign(0)
        info.set_line_wrap(True)
        box.pack_start(info, False, False, 0)

        self.template_filter = Gtk.ComboBoxText()
        for key, title in [
            ("all", "Показывать все шаблоны"),
            ("clean", "Только чистые рабочие базы"),
            ("demo", "Только демонстрационные базы"),
            ("cf", "Только конфигурационные файлы .cf"),
        ]:
            self.template_filter.append(key, title)
        self.template_filter.set_active_id("all")
        self.template_filter.connect("changed", lambda *_: self.load_templates())
        box.pack_start(self.template_filter, False, False, 0)

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        btn_refresh = Gtk.Button(label="Обновить")
        btn_add_dir = Gtk.Button(label="Выбрать папку шаблонов...")
        btn_choose_file = Gtk.Button(label="Выбрать файл/папку вручную...")

        btn_refresh.connect("clicked", lambda *_: self.load_templates())
        btn_add_dir.connect("clicked", self.choose_root)
        btn_choose_file.connect("clicked", self.choose_file_or_folder)

        toolbar.pack_start(btn_refresh, False, False, 0)
        toolbar.pack_start(btn_add_dir, False, False, 0)
        toolbar.pack_start(btn_choose_file, False, False, 0)
        box.pack_start(toolbar, False, False, 0)

        self.store = Gtk.ListStore(str, str, str, str, str, str, str)
        # 0 name, 1 type title, 2 version, 3 folder/path, 4 payload, 5 kind, 6 product

        self.tree = Gtk.TreeView(model=self.store)
        self.tree.set_headers_visible(True)
        self.tree.set_enable_tree_lines(True)
        self.tree.set_headers_visible(True)
        self.tree.get_selection().set_mode(Gtk.SelectionMode.SINGLE)

        for idx, title, width in [
            (6, "Продукт", 230),
            (0, "Шаблон", 260),
            (1, "Тип", 180),
            (2, "Версия", 110),
            (3, "Путь", 360),
        ]:
            renderer = Gtk.CellRendererText()
            column = Gtk.TreeViewColumn(title, renderer, text=idx)
            column.set_resizable(True)
            column.set_min_width(80)
            column.set_fixed_width(width)
            self.tree.append_column(column)

        self.tree.connect("row-activated", lambda *_: self.response(Gtk.ResponseType.OK))

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.add(self.tree)
        box.pack_start(scroller, True, True, 0)

        self.load_templates()
        self.show_all()



    def _template_group_name(self, item):
        """Наименование конфигурации для группировки."""
        if not isinstance(item, dict):
            return "Прочее"

        for key in ("config_name", "display_name", "product_name", "config", "product"):
            value = str(item.get(key) or "").strip()
            if value:
                return value

        tmpl = str(item.get("template_name") or item.get("name") or "").strip()
        if tmpl:
            return tmpl

        return "Прочее"

    def _template_release_name(self, item):
        """Текст релиза внутри группы."""
        if not isinstance(item, dict):
            return ""

        version = str(item.get("version") or "").strip()
        typ = str(item.get("type") or "").strip()
        template_name = str(item.get("template_name") or item.get("name") or "").strip()

        parts = []
        if version:
            parts.append(version)
        if typ and typ.lower() not in ("", "шаблон"):
            parts.append(typ)
        if not version and template_name:
            parts.append(template_name)

        return " — ".join(parts) if parts else "Без версии"

    def _template_version_sort_key(self, value):
        s = str(value or "").strip()
        nums = re.findall(r'\d+', s)
        if nums:
            return tuple(int(x) for x in nums)
        return (0,)

    def _selected_template_item_from_tree(self):
        try:
            selection = self.tree.get_selection()
            model, treeiter = selection.get_selected()
            if not treeiter:
                return None

            is_group = bool(model.get_value(treeiter, 5))
            if is_group:
                return None

            item = model.get_value(treeiter, 6)
            if isinstance(item, dict):
                return item
        except Exception:
            pass
        return None

    def template_roots(self):
        from pathlib import Path

        roots = [
            Path("/mnt/DataStore/Updater1C/1c-updates"),
        ]

        # Папки, добавленные вручную кнопкой "Выбрать папку шаблонов..."
        roots.extend(self.extra_roots)

        result = []
        seen = set()

        for root in roots:
            try:
                root = Path(root).expanduser()
                key = str(root)

                if key not in seen and root.exists():
                    seen.add(key)
                    result.append(root)
            except Exception:
                pass

        return result

    def read_template_meta(self, folder):
        import re

        name = folder.name
        version = ""

        mft = folder / "1cv8.mft"

        if mft.exists():
            try:
                txt = mft.read_text(encoding="utf-8", errors="ignore")

                for line in txt.splitlines():
                    low = line.lower().strip()

                    if low.startswith("name=") or low.startswith("caption="):
                        name = line.split("=", 1)[1].strip().strip('"') or name
                    elif low.startswith("version="):
                        version = line.split("=", 1)[1].strip().strip('"')
            except Exception:
                pass

        if not version:
            m = re.search(r"(\d+[._]\d+[._]\d+(?:[._]\d+)?)", str(folder))

            if m:
                version = m.group(1).replace("_", ".")

        return name, version

    def product_group_name(self, folder, name):
        text = (str(folder) + " " + str(name or "")).lower()

        if "accounting" in text or "бухгалтер" in text:
            return "1С:Бухгалтерия предприятия", "Бухгалтерия предприятия"

        if "trade" in text or "торгов" in text:
            return "1С:Управление торговлей", "Управление торговлей"

        if "hrm" in text or "зарплат" in text or "zup" in text:
            return "1С:Зарплата и управление персоналом", "Зарплата и управление персоналом"

        return "Шаблоны 1С", name or folder.name

    def template_variants(self, folder):
        name, version = self.read_template_meta(folder)
        variants = []

        rules = [
            ("1Cv8new.dt", "clean", "Чистая рабочая база"),
            ("1cv8new.dt", "clean", "Чистая рабочая база"),
            ("1Cv8.dt", "demo", "Демо база"),
            ("1cv8.dt", "demo", "Демо база"),
            ("1Cv8.cf", "cf", "Чистая конфигурация .cf"),
            ("1cv8.cf", "cf", "Чистая конфигурация .cf"),
            ("1Cv8.1CD", "file", "Готовая файловая база 1Cv8.1CD"),
        ]

        for fn, kind, title in rules:
            f = folder / fn

            if f.exists():
                variants.append({
                    "name": name,
                    "version": version,
                    "kind": kind,
                    "title": title,
                    "payload": str(f),
                    "folder": str(folder),
                })

        return variants


    def refresh_templates(self, *_):
        try:
            items = list(self.scan_templates() or [])
        except Exception:
            items = []

        filter_text = ""
        try:
            filter_text = str(self.filter_combo.get_active_text() or "").strip().lower()
        except Exception:
            pass

        show_all = not filter_text or "все шаблоны" in filter_text

        self.store.clear()

        groups = {}
        grouped_items = {}

        for item in items:
            group_name = self._template_group_name(item)
            release_name = self._template_release_name(item)
            type_name = str(item.get("type") or "").strip()
            version = str(item.get("version") or "").strip()
            path = str(item.get("path") or "").strip()

            hay = " ".join([
                group_name,
                release_name,
                type_name,
                version,
                path,
                str(item.get("template_name") or ""),
                str(item.get("product_name") or ""),
            ]).lower()

            if not show_all and filter_text not in hay:
                continue

            grouped_items.setdefault(group_name, []).append({
                "release_name": release_name,
                "type": type_name,
                "version": version,
                "path": path,
                "item": item,
            })

        for group_name in sorted(grouped_items.keys(), key=lambda x: x.lower()):
            parent = self.store.append(
                None,
                [group_name, "", "", "", "", True, None]
            )
            groups[group_name] = parent

            children = grouped_items[group_name]
            children.sort(
                key=lambda x: self._template_version_sort_key(x.get("version")),
                reverse=True
            )

            for row in children:
                self.store.append(parent, [
                    row["release_name"],
                    "",
                    row["type"],
                    row["version"],
                    row["path"],
                    False,
                    row["item"],
                ])

        try:
            self.tree.expand_all()
        except Exception:
            pass

    def choose_root(self, *_):
        from pathlib import Path

        dlg = Gtk.FileChooserDialog(
            title="Выберите каталог шаблонов 1С",
            transient_for=self,
            action=Gtk.FileChooserAction.SELECT_FOLDER,
        )
        dlg.add_button("Отмена", Gtk.ResponseType.CANCEL)
        dlg.add_button("Выбрать", Gtk.ResponseType.OK)

        if dlg.run() == Gtk.ResponseType.OK:
            path = dlg.get_filename()

            if path:
                self.extra_roots.append(Path(path))
                self.load_templates()

        dlg.destroy()

    def choose_file_or_folder(self, *_):
        from pathlib import Path

        dlg = Gtk.FileChooserDialog(
            title="Выберите .dt/.cf/папку шаблона",
            transient_for=self,
            action=Gtk.FileChooserAction.OPEN,
        )
        dlg.add_button("Отмена", Gtk.ResponseType.CANCEL)
        dlg.add_button("Выбрать файл", Gtk.ResponseType.OK)
        dlg.add_button("Выбрать папку", 1001)

        resp = dlg.run()

        if resp == Gtk.ResponseType.OK:
            path = dlg.get_filename()

            if path:
                self.selected_template = path
                low = path.lower()
                self.selected_template_kind = "demo" if low.endswith(".dt") else "cf" if low.endswith(".cf") else "manual"
                self.selected_template_name = Path(path).stem
                self.selected_template_version = ""
                dlg.destroy()
                self.response(Gtk.ResponseType.OK)
                return

        elif resp == 1001:
            dlg.destroy()

            d2 = Gtk.FileChooserDialog(
                title="Выберите папку шаблона",
                transient_for=self,
                action=Gtk.FileChooserAction.SELECT_FOLDER,
            )
            d2.add_button("Отмена", Gtk.ResponseType.CANCEL)
            d2.add_button("Выбрать", Gtk.ResponseType.OK)

            if d2.run() == Gtk.ResponseType.OK:
                path = d2.get_filename()

                if path:
                    self.selected_template = path
                    self.selected_template_kind = "manual"
                    self.selected_template_name = Path(path).name
                    self.selected_template_version = ""
                    d2.destroy()
                    self.response(Gtk.ResponseType.OK)
                    return

            d2.destroy()
            return

        dlg.destroy()

    def accept_selected(self):
        model, it = self.tree.get_selection().get_selected()

        if it is None:
            return False

        payload = model[it][4]

        if not payload:
            return False

        self.selected_template = payload
        self.selected_template_kind = model[it][5] or ""
        self.selected_template_name = model[it][0] or ""
        self.selected_template_version = model[it][2] or ""

        return True

    def run(self):
        while True:
            resp = super().run()

            if resp != Gtk.ResponseType.OK:
                return resp

            if self.selected_template or self.accept_selected():
                return Gtk.ResponseType.OK

            msg = Gtk.MessageDialog(
                transient_for=self,
                modal=True,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.OK,
                text="Выберите шаблон 1С",
            )
            msg.format_secondary_text("Выделите строку шаблона и нажмите OK или дважды щелкните по строке.")
            msg.run()
            msg.destroy()

class BaseDialog(Gtk.Dialog):
    def __init__(
        self,
        parent,
        title="Добавление базы — Обновлятор 1C Linux",
        base=None,
        groups=None,
        settings=None,
    ):
        super().__init__(title=title, transient_for=parent, flags=0)
        self.set_default_size(620, 720)
        self.parent_window = parent
        self.base = dict(base or {})
        self.groups = groups or []
        self.settings = settings or {}

        self.add_button("OK", Gtk.ResponseType.OK)
        self.add_button("Отмена", Gtk.ResponseType.CANCEL)

        box = self.get_content_area()
        grid = Gtk.Grid()
        grid.set_row_spacing(8)
        grid.set_column_spacing(12)
        grid.set_border_width(12)
        box.add(grid)

        self.action = Ui.combo([
            "Добавить существующую информационную базу",
            "Создать новую пустую базу без конфигурации",
            "Создать новую базу из шаблона 1С",
            "Создать группу",
        ])

        self.name = Ui.entry(self.base.get("name", "Новая база"))

        self.group = Ui.combo([])
        combo_set_values(self.group, self.groups, self.base.get("group") or "")

        self.kind = Ui.combo(["file", "server", "web"])
        combo_set_values(self.kind, ["file", "server", "web"], self.base.get("kind") or "file")

        kind, connect = normalize_base_kind_and_connect(self.base)
        self.connect = Ui.entry(connect or self.base.get("connect", ""))

        self.template = Ui.entry(self.base.get("template", ""))
        self.user = Ui.entry(self.base.get("user", ""))

        self.password = Ui.entry(parent.get_base_password(base) if base and hasattr(parent, "get_base_password") else "")
        self.password.set_visibility(False)

        if self.base.get("password_saved") or self.base.get("password_secret_id"):
            self.password.set_placeholder_text("Пароль сохранен в системном хранилище")
        elif self.base.get("password"):
            self.password.set_placeholder_text("Пароль задан")
        else:
            self.password.set_placeholder_text("")

        self.platform = Ui.entry(self.base.get("platform_version") or "8.3")

        self.client_mode = Ui.combo(["Тонкий клиент", "Толстый клиент"])
        client_mode = self.base.get("client_mode") or "thin"
        client_caption = "Толстый клиент" if client_mode in ("thick", "Толстый клиент") else "Тонкий клиент"
        combo_set_values(self.client_mode, ["Тонкий клиент", "Толстый клиент"], client_caption)

        self.launch_params = Ui.entry(self.base.get("launch_parameters", ""))

        self.config_name = Ui.entry(self.base.get("config_name", "") or self.base.get("configuration", ""))
        self.config_synonym = Ui.entry(self.base.get("config_synonym", "") or self.base.get("synonym", ""))
        self.config_version = Ui.entry(self.base.get("config_version", "") or self.base.get("version", ""))
        self.update_code = Ui.entry(self.base.get("update_program_name", "") or self.base.get("update_code", ""))

        self.comment = Gtk.TextView()
        self.comment.set_size_request(-1, 90)
        comment_text = self.base.get("comment", "")
        if comment_text:
            self.comment.get_buffer().set_text(comment_text)

        rows = [
            ("Действие:", self.action),
            ("Имя базы:", self.name),
            ("Группа:", self.group),
            ("Тип:", self.kind),
            ("Путь / server\\base / URL:", self._entry_with_button(self.connect, self.on_browse_connect)),
            ("Шаблон 1С (.dt/.cf/папка):", self._entry_with_button(self.template, self.on_browse_template)),
            ("Пользователь:", self.user),
            ("Пароль:", self.password),
            ("Версия платформы:", self._combo_with_button(self.platform, self.on_find_platform_versions)),
            ("Режим запуска:", self.client_mode),
            ("Параметры запуска:", self._entry_with_button(self.launch_params, self.on_launch_params_builder)),
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

    def _entry_with_button(self, entry, handler=None):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.pack_start(entry, True, True, 0)
        btn = Gtk.Button(label="...")
        if handler:
            btn.connect("clicked", handler)
        box.pack_start(btn, False, False, 0)
        return box

    def _combo_with_button(self, combo, handler=None):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.pack_start(combo, True, True, 0)
        btn = Gtk.Button(label="...")
        if handler:
            btn.connect("clicked", handler)
        box.pack_start(btn, False, False, 0)
        return box

    def on_browse_connect(self, *_):
        dlg = Gtk.FileChooserDialog(
            title="Выберите папку файловой базы или файл 1Cv8.1CD",
            transient_for=self,
            action=Gtk.FileChooserAction.SELECT_FOLDER,
        )
        dlg.add_button("Отмена", Gtk.ResponseType.CANCEL)
        dlg.add_button("Выбрать", Gtk.ResponseType.OK)
        if dlg.run() == Gtk.ResponseType.OK:
            self.connect.set_text(dlg.get_filename() or "")
        dlg.destroy()




    def on_browse_template(self, *_):
        parent = getattr(self, "parent_window", None) or self.get_transient_for()

        action = ""

        try:
            if parent is not None and hasattr(parent, "add_base_action_key"):
                action = parent.add_base_action_key(self)
        except Exception:
            action = ""

        # Создание новой базы из шаблона 1С:
        # открываем список шаблонов из /mnt/DataStore/Updater1C/1c-updates.
        if action == "template" and parent is not None and hasattr(parent, "select_1c_template_for_dialog"):
            parent.select_1c_template_for_dialog(self)
            return

        # Добавить существующую базу:
        # шаблон здесь означает ручной .dt/.cf, без списка шаблонов.
        if action == "existing" and parent is not None and hasattr(parent, "select_dt_cf_file_for_dialog"):
            parent.select_dt_cf_file_for_dialog(self)
            return

        # Fallback: обычный выбор .dt/.cf.
        dlg = Gtk.FileChooserDialog(
            title="Выберите файл .dt или .cf",
            transient_for=self,
            action=Gtk.FileChooserAction.OPEN,
        )
        dlg.add_button("Отмена", Gtk.ResponseType.CANCEL)
        dlg.add_button("Выбрать", Gtk.ResponseType.OK)

        try:
            filt = Gtk.FileFilter()
            filt.set_name("Файлы 1С (*.dt, *.cf)")
            filt.add_pattern("*.dt")
            filt.add_pattern("*.DT")
            filt.add_pattern("*.cf")
            filt.add_pattern("*.CF")
            dlg.add_filter(filt)

            all_filter = Gtk.FileFilter()
            all_filter.set_name("Все файлы")
            all_filter.add_pattern("*")
            dlg.add_filter(all_filter)
        except Exception:
            pass

        try:
            dlg.set_current_folder(str(Path.home()))
        except Exception:
            pass

        if dlg.run() == Gtk.ResponseType.OK:
            path = dlg.get_filename() or ""

            if path:
                try:
                    self.template.set_text(path)
                except Exception:
                    pass

        dlg.destroy()

    def on_find_platform_versions(self, *_):
        current = self.platform.get_text().strip()
        dlg = PlatformVersionSelectDialog(self, current)
        if dlg.run() == Gtk.ResponseType.OK:
            selected = dlg.selected_version()
            if selected:
                self.platform.set_text(selected)
        dlg.destroy()

    def on_launch_params_builder(self, *_):
        dlg = LaunchParamsDialog(self, self.launch_params.get_text())
        if dlg.run() == Gtk.ResponseType.OK:
            self.launch_params.set_text(dlg.get_text())
        dlg.destroy()

    def get_data(self):
        comment_buf = self.comment.get_buffer()
        start, end = comment_buf.get_bounds()
        comment = comment_buf.get_text(start, end, True)

        client_caption = combo_get_text(self.client_mode)
        client_mode = "thick" if client_caption == "Толстый клиент" else "thin"

        result = dict(self.base)
        result.update({
            "name": self.name.get_text().strip(),
            "group": combo_get_text(self.group).strip(),
            "kind": combo_get_text(self.kind).strip() or "file",
            "connect": self.connect.get_text().strip(),
            "template": self.template.get_text().strip(),
            "user": self.user.get_text().strip(),
            "platform_version": self.platform.get_text().strip() or "8.3",
            "client_mode": client_mode,
            "launch_parameters": self.launch_params.get_text().strip(),
            "config_name": self.config_name.get_text().strip(),
            "config_synonym": self.config_synonym.get_text().strip(),
            "config_version": self.config_version.get_text().strip(),
            "update_program_name": self.update_code.get_text().strip(),
            "comment": comment,
        })

        # Пароль сохраняем только если пользователь реально что-то ввел.
        entered_password = self.password.get_text()
        if entered_password:
            result["password"] = entered_password
            result["password_saved"] = False
            result["password_secret_id"] = ""

        return result




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




def platform_project_url(branch: str) -> str:
    branch = str(branch or "8.3")
    if branch.startswith("8.5"):
        return "https://releases.1c.ru/project/Platform85"
    return "https://releases.1c.ru/project/Platform83"


def fetch_platform_releases_from_site(branch: str):
    """
    Пытается прочитать страницу проекта платформы.
    Если releases.1c.ru требует авторизацию через браузер/куки, вернет пустой список.
    """
    url = platform_project_url(branch)

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            html = r.read().decode("utf-8", errors="replace")
    except Exception:
        return []

    versions = []
    for m in re.finditer(r"\b8\.(?:3|5)\.\d+\.\d+\b", html):
        v = m.group(0)
        if branch.startswith("8.3") and not v.startswith("8.3."):
            continue
        if branch.startswith("8.5") and not v.startswith("8.5."):
            continue
        if v not in versions:
            versions.append(v)

    versions.sort(key=version_key, reverse=True)
    return versions



def platform_project_url(branch: str) -> str:
    branch = str(branch or "8.3")
    if branch.startswith("8.5"):
        return "https://releases.1c.ru/project/Platform85"
    return "https://releases.1c.ru/project/Platform83"


def fetch_platform_releases_from_site(branch: str):
    """
    Пытаемся получить список релизов со страницы releases.1c.ru.
    Если сайт требует cookies/авторизацию, вернется пустой список.
    """
    url = platform_project_url(branch)

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0"},
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            html = r.read().decode("utf-8", errors="replace")
    except Exception:
        return []

    versions = []
    pattern = r"\b8\.(?:3|5)\.\d+\.\d+\b"

    for m in re.finditer(pattern, html):
        v = m.group(0)

        if branch.startswith("8.3") and not v.startswith("8.3."):
            continue

        if branch.startswith("8.5") and not v.startswith("8.5."):
            continue

        if v not in versions:
            versions.append(v)

    versions.sort(key=version_key, reverse=True)
    return versions



def platform_distribution_variants(version: str, branch: str, component: str, os_name: str, bit64: bool, arm: bool, elbrus: bool, web_clients: bool):
    """
    Формируем очередь дистрибутивов по структуре страницы releases.1c.ru:
    Linux 64, Linux 32, Linux arm64/Эльбрус, macOS, Windows 32/64,
    внешние компоненты, демобаза, дополнительные материалы.

    URL пока может быть пустым, потому что releases.1c.ru часто требует cookie/авторизацию.
    Скачивание реальных URL будет работать, когда URL будет получен из страницы/зеркала/API.
    """
    result = []

    os_low = (os_name or "").lower()
    comp = component or "Все компоненты платформы"

    def add(section, title, code, url=""):
        result.append({
            "version": version,
            "section": section,
            "title": title,
            "code": code,
            "url": url,
        })

    def wants_all():
        return comp == "Все компоненты платформы"

    def wants_server():
        return wants_all() or comp == "Сервер"

    def wants_client():
        return wants_all() or comp in ("Клиент", "Тонкий клиент")

    def wants_thin():
        return wants_all() or comp == "Тонкий клиент"

    if "linux" in os_low:
        if arm or elbrus:
            section = "Linux (arm64, Эльбрус-8С)"
            if elbrus:
                if wants_server():
                    add(section, f"{version} Сервер 1С:Предприятия (Эльбрус-8С) для RPM-based Linux-систем", "linux-elbrus-rpm-server")
                    add(section, f"{version} Сервер 1С:Предприятия (Эльбрус-8С) для DEB-based Linux-систем", "linux-elbrus-deb-server")
                if wants_client():
                    add(section, f"{version} Клиент 1С:Предприятия (Эльбрус-8С) для RPM-based Linux-систем", "linux-elbrus-rpm-client")
                    add(section, f"{version} Клиент 1С:Предприятия (Эльбрус-8С) для DEB-based Linux-систем", "linux-elbrus-deb-client")
                if wants_thin():
                    add(section, f"{version} Тонкий клиент 1С:Предприятия (Эльбрус-8С) для RPM-based Linux-систем", "linux-elbrus-rpm-thin")
                    add(section, f"{version} Тонкий клиент 1С:Предприятия (Эльбрус-8С) для DEB-based Linux-систем", "linux-elbrus-deb-thin")
            else:
                if wants_server():
                    add(section, f"{version} Сервер 1С:Предприятия (64-bit ARM) для RPM-based Linux-систем", "linux-arm64-rpm-server")
                    add(section, f"{version} Сервер 1С:Предприятия (64-bit ARM) для DEB-based Linux-систем", "linux-arm64-deb-server")
                if wants_client():
                    add(section, f"{version} Клиент 1С:Предприятия (64-bit ARM) для RPM-based Linux-систем", "linux-arm64-rpm-client")
                    add(section, f"{version} Клиент 1С:Предприятия (64-bit ARM) для DEB-based Linux-систем", "linux-arm64-deb-client")
                if wants_thin():
                    add(section, f"{version} Тонкий клиент 1С:Предприятия (64-bit ARM) для RPM-based Linux-систем", "linux-arm64-rpm-thin")
                    add(section, f"{version} Тонкий клиент 1С:Предприятия (64-bit ARM) для DEB-based Linux-систем", "linux-arm64-deb-thin")
            return result

        if bit64:
            section = "Linux (64-bit)"
            if wants_all():
                add(section, f"{version} Технологическая платформа 1С:Предприятия (64-bit) для Linux + Тонкий клиент для Windows, Linux и MacOS для автоматического обновления клиентов через веб-сервер", "linux-x64-full-web-all")
                add(section, f"{version} Технологическая платформа 1С:Предприятия (64-bit) для Linux + Тонкий клиент для Windows и MacOS для автоматического обновления клиентов через веб-сервер", "linux-x64-full-web-winmac")
                add(section, f"{version} Технологическая платформа 1С:Предприятия (64-bit) для Linux", "linux-x64-full")
            if wants_thin():
                add(section, f"{version} Тонкий клиент 1С:Предприятия (64-bit) для DEB-based Linux-систем", "linux-x64-deb-thin")
                add(section, f"{version} Тонкий клиент 1С:Предприятия (64-bit) для Linux", "linux-x64-thin")
                add(section, f"{version} Тонкий клиент 1С:Предприятия (64-bit) для RPM-based Linux-систем", "linux-x64-rpm-thin")
            if wants_client():
                add(section, f"{version} Клиент 1С:Предприятия (64-bit) для DEB-based Linux-систем", "linux-x64-deb-client")
                add(section, f"{version} Клиент 1С:Предприятия (64-bit) для RPM-based Linux-систем", "linux-x64-rpm-client")
            if wants_server():
                add(section, f"{version} Сервер 1С:Предприятия (64-bit) для DEB-based Linux-систем", "linux-x64-deb-server")
                add(section, f"{version} Сервер 1С:Предприятия (64-bit) для RPM-based Linux-систем", "linux-x64-rpm-server")
            if web_clients:
                add(section, f"{version} Web-компоненты 1С:Предприятия (64-bit) для Linux", "linux-x64-web")
        else:
            section = "Linux (32-bit)"
            if wants_all():
                add(section, f"{version} Технологическая платформа 1С:Предприятия для Linux + Тонкий клиент для Windows, Linux и MacOS для автоматического обновления клиентов через веб-сервер", "linux-x86-full-web-all")
                add(section, f"{version} Технологическая платформа 1С:Предприятия для Linux + Тонкий клиент для Windows и MacOS для автоматического обновления клиентов через веб-сервер", "linux-x86-full-web-winmac")
                add(section, f"{version} Технологическая платформа 1С:Предприятия для Linux", "linux-x86-full")
            if wants_thin():
                add(section, f"{version} Тонкий клиент 1С:Предприятия для DEB-based Linux-систем", "linux-x86-deb-thin")
                add(section, f"{version} Тонкий клиент 1С:Предприятия для Linux", "linux-x86-thin")
                add(section, f"{version} Тонкий клиент 1С:Предприятия для RPM-based Linux-систем", "linux-x86-rpm-thin")
            if wants_client():
                add(section, f"{version} Клиент 1С:Предприятия для DEB-based Linux-систем", "linux-x86-deb-client")
                add(section, f"{version} Клиент 1С:Предприятия для RPM-based Linux-систем", "linux-x86-rpm-client")
            if wants_server():
                add(section, f"{version} Сервер 1С:Предприятия для DEB-based Linux-систем", "linux-x86-deb-server")
                add(section, f"{version} Сервер 1С:Предприятия для RPM-based Linux-систем", "linux-x86-rpm-server")

    elif "windows" in os_low:
        section = "Windows (64-bit)" if bit64 else "Windows (32-bit)"
        bit_title = " (64-bit)" if bit64 else ""
        bit_code = "x64" if bit64 else "x86"

        if wants_thin():
            add(section, f"{version} Тонкий клиент 1С:Предприятие{bit_title} для Windows", f"windows-{bit_code}-thin")
        if wants_server():
            add(section, f"{version} Сервер 1С:Предприятия{bit_title} для Windows + Тонкий клиент для Windows, Linux и MacOS для автоматического обновления клиентов через веб-сервер", f"windows-{bit_code}-server-web-all")
            add(section, f"{version} Сервер 1С:Предприятия{bit_title} для Windows + Тонкий клиент для Windows и MacOS для автоматического обновления клиентов через веб-сервер", f"windows-{bit_code}-server-web-winmac")
        if wants_all():
            add(section, f"{version} Технологическая платформа 1С:Предприятия{bit_title} для Windows + Тонкий клиент для Windows, Linux и MacOS для автоматического обновления клиентов через веб-сервер", f"windows-{bit_code}-full-web-all")
            add(section, f"{version} Технологическая платформа 1С:Предприятия{bit_title} для Windows + Тонкий клиент для Windows и MacOS для автоматического обновления клиентов через веб-сервер", f"windows-{bit_code}-full-web-winmac")
            add(section, f"{version} Технологическая платформа 1С:Предприятия{bit_title} для Windows", f"windows-{bit_code}-full")
        if wants_server():
            add(section, f"{version} Сервер 1С:Предприятия{bit_title} для Windows", f"windows-{bit_code}-server")
        if web_clients:
            add(section, f"{version} Web-компоненты 1С:Предприятия{bit_title} для Windows", f"windows-{bit_code}-web")

    elif "mac" in os_low:
        section = "MacOS (64-bit)"
        if wants_thin():
            add(section, f"{version} Тонкий клиент 1С:Предприятия для macOS", "macos-thin")
        if wants_client() or wants_all():
            add(section, f"{version} Клиент 1С:Предприятия для macOS", "macos-client")

    else:
        add("Прочее", f"{version} {os_name} клиент 1С", f"{safe_name(os_name)}-client")

    if wants_all():
        add("Технология внешних компонент", f"{version} Технология внешних компонент", "external-components")
        add("Демонстрационная информационная база", f"{version} Демонстрационная информационная база", "demo-base")
        add("Дополнительные материалы", f"{version} Дополнительные материалы", "additional-materials")

    return result


def auth_header_basic(login: str, password: str):
    login = login or ""
    password = password or ""
    if not login or not password:
        return {}
    token = base64.b64encode(f"{login}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def http_get_text_auth(url: str, login: str = "", password: str = "", referer: str = ""):
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    if referer:
        headers["Referer"] = referer
    headers.update(auth_header_basic(login, password))

    req = urllib.request.Request(url, headers=headers)

    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
        charset = "utf-8"
        content_type = r.headers.get("Content-Type") or ""
        m = re.search(r"charset=([^;]+)", content_type, flags=re.I)
        if m:
            charset = m.group(1).strip()
        return raw.decode(charset, errors="replace")




def html_links(page_url: str, html_text: str):
    result = []

    # Важно: regex в тройных кавычках, чтобы кавычки href не ломали синтаксис Python.
    pattern = r"""<a\b[^>]*href=["']([^"']+)["'][^>]*>(.*?)</a>"""

    for m in re.finditer(pattern, html_text or "", flags=re.I | re.S):
        href = html.unescape((m.group(1) or "").strip())
        body = m.group(2) or ""
        title = re.sub(r"<[^>]+>", " ", body, flags=re.S)
        title = html.unescape(re.sub(r"\s+", " ", title).strip())

        if not title:
            title = href

        result.append({
            "title": title,
            "url": urljoin(page_url, href),
        })

    return result

def platform_project_url(branch: str) -> str:
    branch = str(branch or "8.3")
    if branch.startswith("8.5"):
        return "https://releases.1c.ru/project/Platform85"
    return "https://releases.1c.ru/project/Platform83"


def fetch_platform_releases_from_site(branch: str, login: str = "", password: str = ""):
    url = platform_project_url(branch)
    html_text = http_get_text_auth(url, login, password)

    releases = []
    for link in html_links(url, html_text):
        title = link["title"]
        href = link["url"]

        version = extract_release_from_any(title) or extract_release_from_any(href)
        if not version:
            continue

        if branch.startswith("8.3") and not version.startswith("8.3."):
            continue
        if branch.startswith("8.5") and not version.startswith("8.5."):
            continue

        if not any(x["version"] == version for x in releases):
            releases.append({
                "version": version,
                "title": version,
                "url": href,
            })

    releases.sort(key=lambda x: version_key(x["version"]), reverse=True)
    return releases


def classify_platform_link(title: str):
    t = (title or "").lower()

    if "контрольн" in t or "sha" in t or "список измен" in t or "проблемные" in t:
        return ""

    if "linux" in t and "64" in t:
        return "Linux (64-bit)"
    if "linux" in t and ("32" in t or "i386" in t):
        return "Linux (32-bit)"
    if "arm" in t or "эльбрус" in t:
        return "Linux (arm64, Эльбрус-8С)"
    if "macos" in t or "mac os" in t:
        return "MacOS (64-bit)"
    if "windows" in t and "64" in t:
        return "Windows (64-bit)"
    if "windows" in t and ("32" in t or "x86" in t):
        return "Windows (32-bit)"
    if "демонстрац" in t:
        return "Демонстрационная информационная база"
    if "внешн" in t:
        return "Технология внешних компонент"
    if "дополнитель" in t:
        return "Дополнительные материалы"

    return "Прочее"


def platform_link_matches_filters(title: str, component: str, os_name: str, bit64: bool, arm: bool, elbrus: bool, web_clients: bool):
    t = (title or "").lower()
    os_low = (os_name or "").lower()
    comp = component or "Все компоненты платформы"

    if "windows" in os_low and "windows" not in t:
        return False
    if "linux" in os_low and "linux" not in t:
        return False
    if "mac" in os_low and not ("macos" in t or "mac os" in t):
        return False

    if bit64 and not ("64" in t or "x86_64" in t or "amd64" in t):
        return False

    if arm and "arm" not in t:
        return False

    if elbrus and "эльбрус" not in t:
        return False

    if web_clients and not ("веб" in t or "web" in t):
        return False

    if comp == "Сервер" and "сервер" not in t:
        return False

    if comp == "Тонкий клиент" and "тонкий клиент" not in t:
        return False

    if comp == "Клиент" and "клиент" not in t:
        return False

    return True


def fetch_platform_distribution_links(release_url: str, version: str, login: str = "", password: str = ""):
    html_text = http_get_text_auth(release_url, login, password, referer=platform_project_url("8.5" if version.startswith("8.5.") else "8.3"))
    links = html_links(release_url, html_text)

    result = []
    for link in links:
        title = link["title"]
        url = link["url"]

        section = classify_platform_link(title)
        if not section:
            continue

        # Берем только реальные элементы скачивания/дистрибутивов.
        low_url = url.lower()
        low_title = title.lower()

        looks_like_download = (
            "download" in low_url
            or "tmplts/get" in low_url
            or "file" in low_url
            or ".rar" in low_url
            or ".zip" in low_url
            or ".tar" in low_url
            or ".deb" in low_url
            or ".rpm" in low_url
            or "скачать" in low_title
            or "1с:предприят" in low_title
            or "1c:предприят" in low_title
            or "технологическая платформа" in low_title
            or "тонкий клиент" in low_title
            or "клиент" in low_title
            or "сервер" in low_title
        )

        if not looks_like_download:
            continue

        code = safe_name(title)
        result.append({
            "version": version,
            "section": section,
            "title": f"{version} {title}" if not title.startswith(version) else title,
            "code": code,
            "url": url,
            "filename": Path(url.split("?", 1)[0]).name,
        })

    return result


def make_releases_platform_client(login: str, password: str, branch: str):
    """
    Используем готовый клиент из Qt/main.py, потому что там уже реализована
    правильная ticket-авторизация login.1c.ru -> releases.1c.ru.
    """
    import sys
    project_root = str(Path(__file__).resolve().parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from main import OneCReleasesPlatformClient

    client = OneCReleasesPlatformClient(login, password)

    if str(branch or "").startswith("8.5"):
        client.PROJECT_NICK = "Platform85"
    else:
        client.PROJECT_NICK = "Platform83"

    return client


def fetch_platform_releases_with_client(branch: str, login: str, password: str):
    client = make_releases_platform_client(login, password, branch)
    versions = client.platform_versions()

    result = []
    for version in versions:
        if branch.startswith("8.3") and not version.startswith("8.3."):
            continue
        if branch.startswith("8.5") and not version.startswith("8.5."):
            continue

        result.append({
            "version": version,
            "title": version,
            "url": client._version_files_url(version),
            "client": client,
        })

    return result


def fetch_platform_distribution_links_with_client(branch: str, version: str, login: str, password: str):
    client = make_releases_platform_client(login, password, branch)

    # В Qt-клиенте это должно вернуть реальные файлы версии.
    files = client.version_files(version)

    result = []
    for item in files or []:
        if isinstance(item, dict):
            title = (
                item.get("title")
                or item.get("name")
                or item.get("presentation")
                or item.get("userName")
                or item.get("fileName")
                or str(item)
            )
            url = (
                item.get("url")
                or item.get("downloadUrl")
                or item.get("href")
                or item.get("download_href")
                or ""
            )
            filename = item.get("fileName") or Path(str(url).split("?", 1)[0]).name
        else:
            title = str(item)
            url = ""
            filename = ""

        section = classify_platform_link(title)
        if not section:
            section = "Прочее"

        result.append({
            "version": version,
            "section": section,
            "title": f"{version} {title}" if not str(title).startswith(version) else str(title),
            "code": safe_name(title),
            "url": url,
            "filename": filename,
        })

    return result

class PlatformBranchSelectDialog(Gtk.Dialog):
    def __init__(self, parent):
        super().__init__(
            title="Выбор редакции платформы 1С",
            transient_for=parent,
            flags=0,
        )
        self.set_default_size(380, 180)
        self.add_button("Отмена", Gtk.ResponseType.CANCEL)
        self.add_button("Продолжить", Gtk.ResponseType.OK)

        box = self.get_content_area()
        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        main.set_border_width(14)
        box.add(main)

        main.pack_start(Ui.label("Выберите редакцию платформы для загрузки:"), False, False, 0)

        self.combo = Ui.combo(["8.3", "8.5"])
        self.combo.set_active(0)
        main.pack_start(self.combo, False, False, 0)

        self.show_all()

    def selected_branch(self):
        return self.combo.get_active_text() or "8.3"



def auth_header_basic(login: str, password: str):
    if not login or not password:
        return {}
    raw = f"{login}:{password}".encode("utf-8")
    token = base64.b64encode(raw).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def download_platform_file_with_progress(item: dict, dest_dir: Path, login: str, password: str, log_func, unpack=False, delete_after_unpack=False):
    title = item.get("title") or item.get("name") or "platform"
    url = item.get("url") or ""

    if not url:
        log_func(f"ПРОПУСК: для элемента нет URL скачивания: {title}")
        log_func("Нужно получить ссылку файла с releases.1c.ru/API. Пока элемент добавлен в очередь как выбранный дистрибутив.")
        return None

    filename = item.get("filename") or Path(url.split("?", 1)[0]).name or (safe_name(title) + ".bin")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / filename

    headers = {
        "User-Agent": "Mozilla/5.0",
    }
    headers.update(auth_header_basic(login, password))

    req = urllib.request.Request(url, headers=headers)

    log_func(f"Скачивание: {title}")
    log_func(f"URL: {url}")
    log_func(f"Файл: {dest}")

    with urllib.request.urlopen(req, timeout=120) as r:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        last_percent = -1

        with dest.open("wb") as f:
            while True:
                chunk = r.read(1024 * 512)
                if not chunk:
                    break

                f.write(chunk)
                done += len(chunk)

                if total > 0:
                    percent = int(done * 100 / total)
                    if percent != last_percent and (percent % 5 == 0 or percent == 100):
                        last_percent = percent
                        log_func(f"{title}: {percent}% ({done // 1024 // 1024} / {total // 1024 // 1024} МБ)")
                else:
                    mb = done // 1024 // 1024
                    if mb % 10 == 0:
                        log_func(f"{title}: скачано {mb} МБ")

    log_func(f"Скачано: {dest}")

    if unpack:
        unpack_dir = dest_dir / (dest.stem + "_unpacked")
        unpack_dir.mkdir(parents=True, exist_ok=True)

        try:
            shutil.unpack_archive(str(dest), str(unpack_dir))
            log_func(f"Распаковано: {unpack_dir}")

            if delete_after_unpack:
                dest.unlink()
                log_func(f"Архив удален после распаковки: {dest}")

        except Exception as e:
            log_func(f"Не удалось распаковать архив стандартным способом: {type(e).__name__}: {e}")
            log_func("Для .rar может потребоваться unar/unrar/7z. Архив оставлен на месте.")

    return dest



def ensure_project_root_on_path():
    import sys
    root = str(Path(__file__).resolve().parent.parent)
    if root not in sys.path:
        sys.path.insert(0, root)
    return root


def masked_command(args):
    result = []
    skip_next = False

    for i, arg in enumerate(args):
        if skip_next:
            skip_next = False
            continue

        if arg in ("--pwd", "--password", "-p"):
            result.append(arg)
            result.append("***")
            skip_next = True
            continue

        result.append(str(arg))

    return command_to_text(result)


def oneget_binary():
    for candidate in [
        shutil.which("oneget"),
        str(Path.home() / ".local/bin/oneget"),
        "/usr/local/bin/oneget",
        "/usr/bin/oneget",
    ]:
        if candidate and Path(candidate).exists():
            return candidate
    return ""



def platform_branch_to_project(branch: str):
    # oneget v0.6.0 имеет filter builder для project "platform".
    # Для platform85 фильтры вида win.thin.x64 падают:
    # unknown filter builder for project <platform85>.
    return "platform"

def oneget_filter_from_item(item: dict):
    code = (item.get("code") or "").lower()
    title = (item.get("title") or "").lower()

    filters = []

    if "windows" in code or "windows" in title:
        filters.append("win")
    elif "mac" in code or "macos" in title:
        filters.append("mac")
    elif "rpm" in code or "rpm" in title:
        filters.append("rpm")
    elif "deb" in code or "deb" in title:
        filters.append("deb")
    elif "linux" in code or "linux" in title:
        filters.append("linux")
    else:
        filters.append("linux")

    if "server" in code or "сервер" in title:
        filters.append("server")
    elif "thin" in code or "тонкий клиент" in title:
        filters.append("thin")
    elif "client" in code or "клиент" in title:
        filters.append("client")
    elif "full" in code or "технологическая платформа" in title:
        filters.append("full")

    if "x64" in code or "64" in title or "x86_64" in code:
        filters.append("x64")
    elif "x86" in code or "32" in title or "i386" in code:
        filters.append("x32")

    # oneget не все комбинации принимает, но базовые фильтры из README поддерживает.
    clean = []
    for f in filters:
        if f and f not in clean:
            clean.append(f)

    return ".".join(clean)



def oneget_release_candidates(item: dict):
    version = item.get("version") or extract_release_from_any(item.get("title") or "")
    branch = "8.5" if str(version).startswith("8.5.") else "8.3"
    flt = oneget_filter_from_item(item)

    candidates = []

    if flt:
        # Основная попытка: фильтр через универсальный project platform.
        candidates.append(f"platform:{flt}@{version}")

    # Fallback: без фильтра.
    if branch == "8.5":
        candidates.append(f"platform85@{version}")

    candidates.append(f"platform@{version}")

    # Убираем дубли, сохраняя порядок.
    result = []
    for c in candidates:
        if c and c not in result:
            result.append(c)

    return result




def oneget_release_arg(item: dict):
    candidates = oneget_release_candidates(item)
    return candidates[0] if candidates else ""


def downloads_v8_auth_diagnostic(login: str, password: str):
    url = "http://downloads.v8.1c.ru/tmplts/"
    headers = {
        "User-Agent": "Mozilla/5.0",
    }
    headers.update(auth_header_basic(login, password))

    req = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read(3000)
            status = getattr(r, "status", None) or r.getcode()
            text = raw.decode("utf-8", errors="replace")
            return True, f"HTTP {status}; ответ: {text[:500].replace(chr(10), ' ')}"
    except urllib.error.HTTPError as e:
        body = e.read(1000).decode("utf-8", errors="replace")
        return False, f"HTTP {e.code}; {body[:500].replace(chr(10), ' ')}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def platform_item_has_direct_url(item: dict):
    return bool(item.get("url"))



class GtkLogWorkerAdapter:
    def __init__(self, log_func):
        self._log_func = log_func

    def log(self, message):
        self._log_func(str(message))



def make_gtk_releases_client(login: str, password: str, branch: str):
    ensure_project_root_on_path()
    from main import OneCReleasesPlatformClient

    client = OneCReleasesPlatformClient(login, password)
    client.PROJECT_NICK = "Platform85" if str(branch or "").startswith("8.5") else "Platform83"
    return client



def platform_candidate_text(item: dict) -> str:
    return " ".join([
        str(item.get("title") or ""),
        str(item.get("code") or ""),
        str(item.get("name") or ""),
        str(item.get("fileName") or ""),
        str(item.get("distributionName") or ""),
        str(item.get("versionFileUrl") or ""),
        str(item.get("downloadUrl") or ""),
        str(item.get("url") or ""),
    ]).lower()


def platform_candidate_score(wanted: dict, candidate: dict) -> int:
    wanted_code = str(wanted.get("code") or "").lower()
    wanted_title = str(wanted.get("title") or "").lower()
    cand_text = platform_candidate_text(candidate)
    cand_code = str(candidate.get("code") or "").lower()

    score = 0

    if wanted_code and wanted_code == cand_code:
        score += 100

    tokens = [x for x in wanted_code.replace("_", "-").replace(".", "-").split("-") if x]

    for t in tokens:
        if t in cand_text or t in cand_code:
            score += 10

    # Нормализация русских/английских признаков.
    checks = [
        ("windows", ["windows", "win"]),
        ("linux", ["linux"]),
        ("macos", ["macos", "mac os", "mac"]),
        ("x64", ["x64", "64-bit", "64"]),
        ("x32", ["x32", "32-bit", "32", "x86"]),
        ("thin", ["thin", "тонкий клиент"]),
        ("client", ["client", "клиент"]),
        ("server", ["server", "сервер"]),
        ("deb", ["deb", "debian", "deb-based"]),
        ("rpm", ["rpm", "rpm-based"]),
    ]

    for wanted_token, candidate_words in checks:
        if wanted_token in wanted_code or wanted_token in wanted_title:
            if any(w in cand_text for w in candidate_words):
                score += 15
            else:
                score -= 20

    if str(wanted.get("version") or "") and str(wanted.get("version")) in cand_text:
        score += 10

    if candidate.get("versionFileUrl") or candidate.get("downloadUrl") or candidate.get("url"):
        score += 30

    return score


def find_real_platform_item_for_fallback(client, fallback_item: dict):
    version = fallback_item.get("version") or extract_release_from_any(fallback_item.get("title") or "")
    if not version:
        return None

    raw_items = client.platform_files(version) or []

    best = None
    best_score = -9999

    for cand in raw_items:
        if not isinstance(cand, dict):
            continue

        score = platform_candidate_score(fallback_item, cand)

        if score > best_score:
            best = cand
            best_score = score

    if best is None or best_score < 20:
        return None

    title = (
        best.get("title")
        or best.get("name")
        or best.get("distributionName")
        or best.get("fileName")
        or fallback_item.get("title")
        or str(best)
    )

    result = dict(best)
    result.setdefault("version", version)
    result.setdefault("title", title if str(title).startswith(str(version)) else f"{version} {title}")
    result.setdefault("code", fallback_item.get("code") or safe_name(title))
    result.setdefault("section", classify_platform_link(title) or fallback_item.get("section") or "Прочее")

    return result


def version_to_underscore(version: str) -> str:
    return str(version or "").strip().replace(".", "_")



def downloads_v8_candidate_filenames(item: dict):
    # Для прямого downloads.v8 используем тот же безопасный список,
    # чтобы Linux больше не проверял Windows .rar.
    return releases_version_file_candidate_names(item)

def downloads_v8_candidate_urls(item: dict):
    version = item.get("version") or extract_release_from_any(item.get("title") or "")
    vu = version_to_underscore(version)
    filenames = downloads_v8_candidate_filenames(item)

    prefixes = [
        f"http://downloads.v8.1c.ru/tmplts/{vu}",
        f"https://downloads.v8.1c.ru/tmplts/{vu}",
    ]

    urls = []
    for prefix in prefixes:
        for fn in filenames:
            url = prefix.rstrip("/") + "/" + fn
            if url not in urls:
                urls.append(url)

    return urls


def probe_direct_download_url(url: str, login: str, password: str):
    import requests

    headers = {
        "User-Agent": "Mozilla/5.0 updater1c-linux",
        "Range": "bytes=0-0",
    }

    try:
        r = requests.get(
            url,
            auth=(login, password),
            headers=headers,
            stream=True,
            timeout=20,
            allow_redirects=True,
        )

        content_type = (r.headers.get("Content-Type") or "").lower()
        final_url = r.url

        ok = (
            r.status_code in (200, 206)
            and "text/html" not in content_type
            and "login.1c.ru" not in final_url.lower()
        )

        try:
            r.close()
        except Exception:
            pass

        return ok, r.status_code, final_url, content_type

    except Exception as e:
        return False, 0, "", f"{type(e).__name__}: {e}"


def find_direct_downloads_v8_url(item: dict, login: str, password: str, log_func=None):
    for url in downloads_v8_candidate_urls(item):
        ok, status, final_url, content_type = probe_direct_download_url(url, login, password)

        if log_func:
            log_func(f"Проверяю прямой URL: {url} -> HTTP {status}; {content_type}; final={final_url}")

        if ok:
            return url

    return ""


def download_direct_downloads_v8_url(url: str, dest_dir: Path, login: str, password: str, log_func, fallback_title: str = ""):
    import requests
    from urllib.parse import urlparse, unquote

    dest_dir.mkdir(parents=True, exist_ok=True)

    name = Path(unquote(urlparse(url).path)).name
    if not name:
        name = safe_name(fallback_title or "platform_download.bin") + ".bin"

    dest = dest_dir / name

    headers = {
        "User-Agent": "Mozilla/5.0 updater1c-linux",
    }

    with requests.get(
        url,
        auth=(login, password),
        headers=headers,
        stream=True,
        timeout=120,
        allow_redirects=True,
    ) as r:
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code}: {url}\n{(r.text or '')[:500]}")

        content_type = (r.headers.get("Content-Type") or "").lower()
        if "text/html" in content_type:
            head = r.raw.read(500, decode_content=True)
            raise RuntimeError(f"Вместо архива получен HTML: {content_type}; {head!r}")

        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        last_percent = -1

        with dest.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue

                f.write(chunk)
                done += len(chunk)

                if total:
                    percent = int(done * 100 / total)
                    if percent != last_percent and (percent % 5 == 0 or percent == 100):
                        last_percent = percent
                        log_func(f"Скачано {percent}% ({done // 1024 // 1024} / {total // 1024 // 1024} МБ)")
                else:
                    log_func(f"Скачано {done // 1024 // 1024} МБ")

    log_func(f"Файл скачан: {dest}")
    return dest


def releases_platform_nick(version: str) -> str:
    version = str(version or "")
    if version.startswith("8.5."):
        return "Platform85"
    return "Platform83"





def releases_version_file_candidate_names(item: dict):
    version = item.get("version") or extract_release_from_any(item.get("title") or "")
    vu = version_to_underscore(version)
    code = str(item.get("code") or "").lower()
    title = str(item.get("title") or "").lower()

    names = []

    def add(name):
        if name and name not in names:
            names.append(name)

    is_linux = "linux" in code or "linux" in title
    is_windows = "windows" in code or "win" in code or "windows" in title
    is_macos = "mac" in code or "macos" in title or "mac os" in title

    is_thin = "thin" in code or "тонкий клиент" in title
    is_server = "server" in code or "сервер" in title
    is_client = "client" in code or "клиент" in title

    is_deb = "deb" in code or "deb" in title
    is_rpm = "rpm" in code or "rpm" in title

    if is_linux:
        if is_deb:
            if is_thin:
                add(f"thin.client.deb64_{vu}.tar.gz")
                add(f"client.deb64_{vu}.tar.gz")
                add(f"deb64thin_{vu}.tar.gz")
            elif is_server:
                add(f"deb64_{vu}.tar.gz")
                add(f"server.deb64_{vu}.tar.gz")
            else:
                add(f"deb64_{vu}.tar.gz")
                add(f"client.deb64_{vu}.tar.gz")
        elif is_rpm:
            if is_thin:
                add(f"thin.client.rpm64_{vu}.tar.gz")
                add(f"client.rpm64_{vu}.tar.gz")
                add(f"rpm64thin_{vu}.tar.gz")
            elif is_server:
                add(f"rpm64_{vu}.tar.gz")
                add(f"server.rpm64_{vu}.tar.gz")
            else:
                add(f"rpm64_{vu}.tar.gz")
                add(f"client.rpm64_{vu}.tar.gz")
        else:
            if is_thin:
                add(f"thin.client.deb64_{vu}.tar.gz")
                add(f"client.deb64_{vu}.tar.gz")
                add(f"thin.client.rpm64_{vu}.tar.gz")
                add(f"client.rpm64_{vu}.tar.gz")
            elif is_server:
                add(f"deb64_{vu}.tar.gz")
                add(f"rpm64_{vu}.tar.gz")
            else:
                add(f"deb64_{vu}.tar.gz")
                add(f"rpm64_{vu}.tar.gz")
                add(f"thin.client.deb64_{vu}.tar.gz")
                add(f"thin.client.rpm64_{vu}.tar.gz")

        return names

    if is_windows:
        if is_thin:
            add(f"setuptc64_{vu}.rar")
            add(f"setuptc_{vu}.rar")
        elif is_server:
            add(f"windows64_{vu}.rar")
        elif is_client:
            add(f"windows64full_{vu}.rar")
            add(f"windows64_{vu}.rar")
        else:
            add(f"windows64full_{vu}.rar")
            add(f"windows64_{vu}.rar")
            add(f"windows_{vu}.rar")

        return names

    if is_macos:
        add(f"clientosx_{vu}.dmg")
        add(f"macos_{vu}.dmg")
        return names

    add(f"thin.client.deb64_{vu}.tar.gz")
    add(f"deb64_{vu}.tar.gz")
    add(f"thin.client.rpm64_{vu}.tar.gz")
    add(f"rpm64_{vu}.tar.gz")
    add(f"setuptc64_{vu}.rar")
    add(f"windows64_{vu}.rar")

    return names

def releases_version_file_urls(item: dict):
    from urllib.parse import quote

    version = item.get("version") or extract_release_from_any(item.get("title") or "")
    vu = version_to_underscore(version)
    nick = releases_platform_nick(version)

    urls = []

    for fn in releases_version_file_candidate_names(item):
        rel_path = f"Platform\\{vu}\\{fn}"
        url = (
            "https://releases.1c.ru/version_file"
            + "?nick=" + quote(nick, safe="")
            + "&ver=" + quote(str(version), safe="")
            + "&path=" + quote(rel_path, safe="")
        )
        if url not in urls:
            urls.append(url)

    return urls


def releases_form_login_session(login: str, password: str, log_func=None):
    import re
    import html as _html
    import requests
    from urllib.parse import urljoin

    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 updater1c-linux",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })

    r = s.get("https://releases.1c.ru", timeout=30, allow_redirects=True)
    text = r.text or ""

    if log_func:
        log_func(f"releases.1c.ru start: HTTP {r.status_code}; final={r.url}; len={len(text)}")

    # Если вдруг уже авторизованы.
    if "login.1c.ru" not in r.url.lower() and "loginForm" not in text:
        return s

    action = ""
    execution = ""

    m = re.search(r'<form[^>]+id=["\']loginForm["\'][^>]+action=["\']([^"\']+)["\']', text, re.I | re.S)
    if not m:
        m = re.search(r'<form[^>]+action=["\']([^"\']+)["\'][^>]*id=["\']loginForm["\']', text, re.I | re.S)
    if m:
        action = _html.unescape(m.group(1))

    m = re.search(r'name=["\']execution["\']\s+value=["\']([^"\']+)["\']', text, re.I)
    if m:
        execution = _html.unescape(m.group(1))

    if not action or not execution:
        raise RuntimeError("Не найдена форма loginForm/execution на странице login.1c.ru")

    post_url = urljoin("https://login.1c.ru", action)

    data = {
        "inviteCode": "",
        "inviteType": "",
        "username": login,
        "password": password,
        "rememberMe": "on",
        "execution": execution,
        "_eventId": "submit",
        "geolocation": "",
        "submit": "Войти",
    }

    rr = s.post(
        post_url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
        allow_redirects=True,
    )

    if log_func:
        log_func(f"login form submit: HTTP {rr.status_code}; final={rr.url}; len={len(rr.text or '')}")

    # Пробуем открыть releases после формы.
    chk = s.get("https://releases.1c.ru/project/Platform83", timeout=30, allow_redirects=True)

    if log_func:
        log_func(f"releases check Platform83: HTTP {chk.status_code}; final={chk.url}; len={len(chk.text or '')}")

    if "login.1c.ru/login" in chk.url.lower():
        raise RuntimeError("После form-login releases.1c.ru снова вернул страницу логина")

    return s


def extract_download_distribution_href(page_url: str, html_text: str):
    import re
    import html as _html
    from urllib.parse import urljoin

    text = html_text or ""

    # Ищем ссылку с текстом "Скачать дистрибутив".
    pattern = r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>'
    for m in re.finditer(pattern, text, re.I | re.S):
        href = _html.unescape(m.group(1) or "")
        body = re.sub(r"<[^>]+>", " ", m.group(2) or "", flags=re.S)
        title = _html.unescape(re.sub(r"\s+", " ", body).strip()).lower()
        if "скачать дистрибутив" in title or "скачать" == title:
            return urljoin(page_url, href)

    # fallback: рядом с текстом.
    pos = text.lower().find("скачать дистрибутив")
    if pos >= 0:
        left = text[max(0, pos - 1200):pos]
        hrefs = re.findall(r'href=["\']([^"\']+)["\']', left, re.I | re.S)
        if hrefs:
            return urljoin(page_url, _html.unescape(hrefs[-1]))

    return ""







def score_version_file_link_for_item(item: dict, href: str, label: str) -> int:
    text = (href + " " + label).lower()
    code = str(item.get("code") or "").lower()
    title = str(item.get("title") or "").lower()

    score = 0

    want_linux = "linux" in code or "linux" in title
    want_windows = "windows" in code or "win" in code or "windows" in title
    want_deb = "deb" in code or "deb-based" in title or "deb-based" in code
    want_rpm = "rpm" in code or "rpm-based" in title or "rpm-based" in code
    want_generic_linux = want_linux and not want_deb and not want_rpm
    want_thin = "thin" in code or "тонкий клиент" in title
    want_server = "server" in code or "сервер" in title
    want_platform = "platform" in code or "технологическая платформа" in title
    want_x64 = "x64" in code or "64-bit" in title or "64" in code

    is_server_link = any(x in text for x in [
        "server",
        "server64",
        "сервер",
        "with_all_clients",
        "all_clients",
    ])

    is_platform_link = any(x in text for x in [
        "platform",
        "технологическая платформа",
        "windows64full",
    ])

    is_windows_link = any(x in text for x in [
        "windows",
        "setuptc",
        "windows64",
        ".rar",
    ])

    is_linux_link = any(x in text for x in [
        "linux",
        "deb",
        "rpm",
        "deb64",
        "rpm64",
        ".zip",
        ".tar.gz",
    ])

    is_deb_link = "deb" in text or "deb64" in text or "deb-based" in text
    is_rpm_link = "rpm" in text or "rpm64" in text or "rpm-based" in text

    is_thin_link = any(x in text for x in [
        "thin",
        "тонкий клиент",
        "setuptc",
        "thin.client",
    ])

    is_client_link = any(x in text for x in [
        "client",
        "клиент",
        "setuptc",
    ])

    is_arm_link = "arm" in text or "aarch64" in text or "эльбрус" in text

    # ОС.
    if want_windows:
        score += 50 if is_windows_link else -200
        if is_linux_link and not is_windows_link:
            score -= 300

    if want_linux:
        score += 50 if is_linux_link else -200
        if is_windows_link and not is_linux_link:
            score -= 300

    # DEB/RPM.
    if want_deb:
        score += 80 if is_deb_link else -180
        if is_rpm_link:
            score -= 250

    if want_rpm:
        score += 80 if is_rpm_link else -180
        if is_deb_link:
            score -= 250

    # Generic Linux: выбран "для Linux", а не DEB/RPM.
    # Поэтому generic label получает плюс, а DEB/RPM — штраф.
    if want_generic_linux:
        label_is_plain_linux = (
            "для linux" in text
            and "deb-based" not in text
            and "rpm-based" not in text
            and "deb64" not in text
            and "rpm64" not in text
        )

        if label_is_plain_linux:
            score += 160

        if is_deb_link or is_rpm_link:
            score -= 120

    # Разрядность.
    if want_x64:
        score += 20 if any(x in text for x in ["64", "x64", "64-bit"]) else -60

    if want_x64 and is_arm_link:
        score -= 300

    # Тонкий клиент.
    if want_thin:
        score += 100 if is_thin_link else -120

        if is_server_link:
            score -= 500

        if is_platform_link and not is_thin_link:
            score -= 400

        if want_windows and "setuptc" in text:
            score += 220

        if want_linux and "thin.client" in text and not want_generic_linux:
            score += 220

    # Сервер.
    if want_server:
        score += 120 if is_server_link else -120
        if is_thin_link and not is_server_link:
            score -= 200

    # Платформа.
    if want_platform:
        score += 120 if is_platform_link or is_server_link else -80

    if not want_thin and "client" in code:
        score += 70 if is_client_link else 0

    return score


def discover_releases_version_file_urls(session, item: dict, log_func=None):
    import re
    import html as _html
    from urllib.parse import urljoin

    version = item.get("version") or extract_release_from_any(item.get("title") or "")
    nick = releases_platform_nick(version)

    page_url = f"https://releases.1c.ru/version_files?nick={nick}&ver={version}"

    try:
        r = session.get(page_url, timeout=40, allow_redirects=True)
    except Exception as e:
        if log_func:
            log_func(f"Не удалось открыть список файлов релиза: {type(e).__name__}: {e}")
        return []

    body = r.text or ""

    if log_func:
        log_func(f"Список файлов релиза получен: HTTP {r.status_code}; len={len(body)}")

    if "login.1c.ru/login" in r.url.lower():
        if log_func:
            log_func("Список файлов релиза вернул форму входа.")
        return []

    links = []

    for m in re.finditer(r'<a\b[^>]*href=["\']([^"\']*version_file[^"\']+)["\'][^>]*>(.*?)</a>', body, re.I | re.S):
        href = _html.unescape(m.group(1) or "")
        raw_label = m.group(2) or ""
        label = re.sub(r"<[^>]+>", " ", raw_label, flags=re.S)
        label = _html.unescape(re.sub(r"\s+", " ", label).strip())
        full = urljoin(r.url, href)

        score = score_version_file_link_for_item(item, full, label)

        if score > 0:
            links.append((score, full, label))

    links.sort(key=lambda x: x[0], reverse=True)

    if log_func and links:
        log_func("Лучшие кандидаты из списка релиза:")
        for score, full, label in links[:3]:
            log_func(f"  score={score}; {label}")

    result = []
    seen = set()

    for score, full, label in links:
        if full in seen:
            continue

        seen.add(full)
        result.append(full)

    return result

def filename_from_content_disposition(headers) -> str:
    import re
    from urllib.parse import unquote

    try:
        cd = headers.get("Content-Disposition") or headers.get("content-disposition") or ""
        if not cd:
            return ""

        m = re.search(r"filename\\*=UTF-8''([^;]+)", cd, re.I)
        if m:
            name = unquote(m.group(1)).strip().strip('"')
            if name and "." in name:
                return name

        m = re.search(r'filename="?([^";]+)"?', cd, re.I)
        if m:
            name = unquote(m.group(1)).strip().strip('"')
            if name and "." in name:
                return name

        return ""

    except Exception:
        return ""



def download_by_releases_version_file(item: dict, dest_dir: Path, login: str, password: str, log_func, progress_func=None, cancel_checker=None):
    import requests
    from urllib.parse import urlparse, unquote

    def is_cancelled():
        try:
            return bool(cancel_checker and cancel_checker())
        except Exception:
            return False

    s = releases_form_login_session(login, password, log_func)

    guessed_urls = releases_version_file_urls(item)
    discovered_urls = discover_releases_version_file_urls(s, item, log_func)

    urls = []
    for url in discovered_urls + guessed_urls:
        if url not in urls:
            urls.append(url)

    for page_url in urls:
        if is_cancelled():
            raise RuntimeError("Операция отменена пользователем")

        page_file_name = filename_from_version_file_url(page_url)
        log_func(f"Проверяю файл релиза: {page_file_name or page_url.split('/version_file?', 1)[-1]}")

        r = s.get(page_url, timeout=40, allow_redirects=True)
        html_text = r.text or ""

        if r.status_code == 404:
            continue

        if "login.1c.ru/login" in r.url.lower():
            log_func("Страница файла вернула форму входа, пробую следующий кандидат.")
            continue

        href = extract_download_distribution_href(r.url, html_text)

        if not href:
            continue

        log_func("Ссылка скачивания найдена. Начинаю загрузку...")

        dest_dir.mkdir(parents=True, exist_ok=True)

        # Главное исправление:
        # dl04/dl05 URL выглядит как /public/file/get/<uuid>, поэтому имя файла
        # нужно брать из исходного version_file path=...
        name = page_file_name

        with s.get(href, stream=True, timeout=180, allow_redirects=True) as rr:
            if rr.status_code >= 400:
                raise RuntimeError(f"HTTP {rr.status_code}: {href}\n{(rr.text or '')[:500]}")

            content_type = (rr.headers.get("Content-Type") or "").lower()

            if "text/html" in content_type:
                head = rr.raw.read(500, decode_content=True)
                raise RuntimeError(f"Вместо архива получен HTML: {content_type}; {head!r}")

            if not name:
                name = filename_from_content_disposition(rr.headers)

            if not name:
                parsed_name = Path(unquote(urlparse(href).path)).name
                if parsed_name and "." in parsed_name and parsed_name.lower() not in ("get", "download"):
                    name = parsed_name

            if not name:
                # Последний fallback только для неизвестного файла.
                name = safe_name(item.get("title") or "platform") + ".bin"

            log_func(f"Имя файла релиза: {name}")

            dest = dest_dir / name
            part = dest.with_name(dest.name + ".part")

            if part.exists():
                try:
                    part.unlink()
                except Exception:
                    pass

            total = int(rr.headers.get("Content-Length") or 0)
            done = 0
            last_percent = -1

            if progress_func:
                progress_func(0, done, total, "Начало скачивания")

            try:
                with part.open("wb") as f:
                    for chunk in rr.iter_content(chunk_size=1024 * 1024):
                        if is_cancelled():
                            raise RuntimeError("Операция отменена пользователем")

                        if not chunk:
                            continue

                        f.write(chunk)
                        done += len(chunk)

                        if total:
                            percent = int(done * 100 / total)
                            if progress_func and percent != last_percent:
                                progress_func(percent, done, total, f"{percent}%")
                                last_percent = percent
                        else:
                            if progress_func:
                                progress_func(0, done, total, f"{done // 1024 // 1024} МБ")

                part.replace(dest)

            except Exception:
                try:
                    if part.exists():
                        part.unlink()
                except Exception:
                    pass
                raise

        if progress_func:
            progress_func(100, done, total, "100%")

        log_func(f"Файл скачан: {dest}")
        return dest

    return None

def open_folder_external(path_value):
    path = Path(str(path_value or "")).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        try:
            subprocess.Popen(["gio", "open", str(path)])
        except Exception:
            pass


def default_platform_download_dir_from_config(config=None):
    try:
        settings = (config or {}).get("settings", {})
        value = settings.get("platform_download_dir") or settings.get("platforms_dir") or ""
        if value:
            return str(Path(value).expanduser())
    except Exception:
        pass
    return "/mnt/DataStore/Updater1C/1c-platforms"







def is_supported_archive_for_unpack(path_value) -> bool:
    name = Path(path_value).name.lower()

    non_archives = (
        ".bin",
        ".run",
        ".deb",
        ".rpm",
        ".msi",
        ".exe",
        ".dmg",
    )

    if name.endswith(non_archives):
        return False

    archive_suffixes = (
        ".zip",
        ".rar",
        ".7z",
        ".tar",
        ".tar.gz",
        ".tgz",
        ".tar.xz",
        ".txz",
        ".tar.bz2",
        ".tbz2",
        ".gz",
        ".bz2",
        ".xz",
    )

    return name.endswith(archive_suffixes)

def make_executable_if_needed(path_value, log_func=None):
    path = Path(path_value)
    name = path.name.lower()

    if name.endswith((".bin", ".run")) and path.exists():
        try:
            path.chmod(path.stat().st_mode | 0o111)
            if log_func:
                log_func(f"Файл сделан исполняемым: {path}")
        except Exception as e:
            if log_func:
                log_func(f"Не удалось сделать файл исполняемым: {type(e).__name__}: {e}")



def filename_from_version_file_url(page_url: str) -> str:
    from urllib.parse import urlparse, parse_qs, unquote
    import html as _html

    try:
        url = _html.unescape(str(page_url or ""))
        q = parse_qs(urlparse(url).query)

        path_value = ""
        for key in ("path", "Path", "file", "File"):
            values = q.get(key)
            if values:
                path_value = values[0]
                break

        path_value = unquote(_html.unescape(path_value or ""))
        path_value = path_value.replace("\\\\", "/").replace("\\", "/")
        path_value = path_value.strip("/")

        name = Path(path_value).name.strip()

        # Имя должно быть реальным файлом, а не Platform/8_3_27_2130.
        if name and "." in name:
            return name

        return ""

    except Exception:
        return ""




def archive_output_folder_name(path_value) -> str:
    name = Path(path_value).name
    lower = name.lower()

    archive_suffixes = (
        ".tar.gz",
        ".tar.xz",
        ".tar.bz2",
        ".tbz2",
        ".tgz",
        ".txz",
        ".zip",
        ".rar",
        ".7z",
        ".tar",
        ".gz",
        ".bz2",
        ".xz",
    )

    for suffix in archive_suffixes:
        if lower.endswith(suffix):
            return name[:-len(suffix)]

    return Path(name).stem


def unpack_platform_archive(archive: Path, unpack_dir: Path, log_func=None):
    import shutil
    import subprocess

    archive = Path(archive)
    unpack_dir = Path(unpack_dir)

    def log(msg):
        if log_func:
            log_func(msg)

    if not is_supported_archive_for_unpack(archive):
        make_executable_if_needed(archive, log)
        log(f"Распаковка пропущена: файл не является архивом ({archive.name})")
        return None

    name_lower = archive.name.lower()

    if unpack_dir.exists():
        shutil.rmtree(unpack_dir)

    unpack_dir.mkdir(parents=True, exist_ok=True)

    if name_lower.endswith(".rar"):
        # В GNOME двойной клик обычно идет через file-roller / backend файлового менеджера.
        # Поэтому сначала используем file-roller — он у тебя руками распаковывает этот RAR.
        file_roller = shutil.which("file-roller")

        if file_roller:
            log("Распаковка RAR через file-roller...")
            cmd = [file_roller, "--extract-to", str(unpack_dir), str(archive)]
            r = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

            if r.returncode == 0:
                return unpack_dir

            log(f"file-roller не смог распаковать RAR: exit={r.returncode}; {r.stdout[-800:]}")

        seven_zz = shutil.which("7zz")

        if seven_zz:
            log("Распаковка RAR через 7zz...")
            cmd = [seven_zz, "x", "-y", f"-o{unpack_dir}", str(archive)]
            r = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

            if r.returncode == 0:
                return unpack_dir

            log(f"7zz не смог распаковать RAR: exit={r.returncode}; {r.stdout[-800:]}")

        # Старый 7z/p7zip специально не используем: он уже дал Unsupported Method.
        log("RAR не распакован автоматически. Архив оставлен без удаления.")
        return None

    shutil.unpack_archive(str(archive), str(unpack_dir))
    return unpack_dir

class PlatformDownloadDialog(Gtk.Dialog):
    def __init__(self, parent):
        self.cancel_requested = False
        super().__init__(
            title="Скачать платформу 1с — Обновлятор 1C Linux",
            transient_for=parent,
            flags=0,
        )
        self.set_default_size(930, 680)
        self.add_button("Отмена", Gtk.ResponseType.CANCEL)
        self.add_button("Скачать", Gtk.ResponseType.OK)

        self.selected_branch = ""
        self.selected_version = ""
        self.available_meta = {}
        self.queue_meta = {}
        self.release_meta = {}

        box = self.get_content_area()
        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        main.set_border_width(12)
        box.add(main)

        grid = Gtk.Grid()
        grid.set_column_spacing(8)
        grid.set_row_spacing(8)
        main.pack_start(grid, False, False, 0)

        self.version_link = Gtk.Button(label="загрузить с releases.1c.ru")
        self.version_link.connect("clicked", self.on_open_releases)

        self.component = Ui.combo(["Все компоненты платформы", "Сервер", "Тонкий клиент", "Клиент"])
        self.os_combo = Ui.combo(["Windows", "Linux", "macOS"])
        self.bit64 = Ui.check("64 бит")
        self.bit64.set_active(True)
        self.arm = Ui.check("ARM")
        self.elbrus = Ui.check("Эльбрус")
        self.web_clients = Ui.check("Клиенты для веб-сервера")

        for widget in [self.component, self.os_combo, self.bit64, self.arm, self.elbrus, self.web_clients]:
            try:
                widget.connect("changed", self.on_filter_changed)
            except Exception:
                try:
                    widget.connect("toggled", self.on_filter_changed)
                except Exception:
                    pass

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

        main.pack_start(
            Ui.label("Нажмите «загрузить с releases.1c.ru», выберите ветку 8.3/8.5, затем выберите конкретную версию."),
            False,
            False,
            0,
        )

        main.pack_start(Ui.label("<b>Элементы для выбора (двойной щелчок или Enter):</b>"), False, False, 0)
        self.available_store = Gtk.ListStore(str)
        self.available = Gtk.TreeView(model=self.available_store)
        self._add_text_column(self.available, "Элементы для выбора", 0)
        self.available.connect("row-activated", self.on_available_row_activated)
        main.pack_start(self._scrolled(self.available), True, True, 0)

        main.pack_start(Ui.label("<b>Элементы для скачивания (используйте Delete для удаления):</b>"), False, False, 0)
        self.queue_store = Gtk.ListStore(str)
        self.queue = Gtk.TreeView(model=self.queue_store)
        self._add_text_column(self.queue, "Элементы для скачивания", 0)
        self.queue.connect("key-press-event", self.on_queue_key_press)
        self.queue.connect("row-activated", self.on_queue_row_activated)
        main.pack_start(self._scrolled(self.queue), True, True, 0)

        self.unpack = Ui.check("Распаковать архив после скачивания")
        self.delete_after = Ui.check("Удалить архив после распаковки")
        main.pack_start(self.unpack, False, False, 0)
        main.pack_start(self.delete_after, False, False, 0)

        dir_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        dir_box.pack_start(Ui.label("Скачивать сюда"), False, False, 0)
        self.download_dir = Ui.entry("/mnt/DataStore/Updater1C/1c-platforms")
        dir_box.pack_start(self.download_dir, True, True, 0)
        dir_box.pack_start(Gtk.Button(label="..."), False, False, 0)
        open_btn = Gtk.Button(label="Открыть")
        open_btn.connect("clicked", self.on_open_download_dir)
        dir_box.pack_start(open_btn, False, False, 0)
        main.pack_start(dir_box, False, False, 0)


        progress_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        main.pack_start(progress_box, False, False, 0)

        self.download_progress = Gtk.ProgressBar()
        self.download_progress.set_show_text(True)
        self.download_progress.set_text("Готово к скачиванию")
        progress_box.pack_start(self.download_progress, True, True, 0)

        self.open_download_dir_btn = Ui.button("Открыть папку")
        self.open_download_dir_btn.connect("clicked", self.open_download_dir)
        progress_box.pack_start(self.open_download_dir_btn, False, False, 0)

        self.show_all()

    def _add_text_column(self, tree, title, column):
        renderer = Gtk.CellRendererText()
        col = Gtk.TreeViewColumn(title, renderer, text=column)
        col.set_resizable(True)
        col.set_expand(True)
        tree.append_column(col)

    def _scrolled(self, child):
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sw.set_size_request(-1, 150)
        sw.add(child)
        return sw






    def on_open_releases(self, *_):
        dlg = PlatformBranchSelectDialog(self)
        response = dlg.run()
        branch = dlg.selected_branch()
        dlg.destroy()

        if response != Gtk.ResponseType.OK:
            return

        self.selected_branch = branch
        self.selected_version = ""
        self.available_meta.clear()
        self.queue_meta.clear()
        self.release_meta.clear()
        self.available_store.clear()
        self.queue_store.clear()

        self.available_store.append([f"Подготовка списка версий платформы {branch}..."])

        # Пока список формируем локально, но только по опубликованным версиям,
        # чтобы в очередь не попадали локально установленные, но нескачиваемые сборки.
        versions = []

        if branch.startswith("8.5"):
            versions.extend([
                "8.5.1.1343",
                "8.5.1.1302",
                "8.5.1.1236",
                "8.5.1.1150",
            ])
        else:
            versions.extend([
                "8.3.27.2130",
                "8.3.27.2074",
                "8.3.27.1786",
                "8.3.25.1394",
                "8.3.24.1548",
            ])

        versions = sorted(set(versions), key=version_key, reverse=True)

        self.available_store.clear()

        for version in versions:
            self.release_meta[version] = {
                "version": version,
                "title": version,
                "url": "",
            }
            self.available_store.append([version])

    def on_available_row_activated(self, tree, path, column):
        model = tree.get_model()
        it = model.get_iter(path)
        if not it:
            return

        value = model[it][0]
        version = extract_release_from_any(value)

        if version and value.strip() == version:
            self.selected_version = version
            self.fill_available_distributions_for_version(version)
            return

        item = self.available_meta.get(value)
        if item:
            self.add_item_to_queue(item)

    def on_filter_changed(self, *_):
        if self.selected_version:
            self.fill_available_distributions_for_version(self.selected_version)







    def fill_available_distributions_for_version(self, version):
        """
        ВАЖНО:
        Здесь нельзя синхронно ходить на releases.1c.ru, иначе GTK зависает.
        Поэтому список пакетов для выбора формируем локально, быстро.
        Реальный versionFileUrl/downloadUrl ищется позже — при нажатии "Скачать",
        уже в фоновом потоке.
        """
        self.available_store.clear()
        self.available_meta.clear()

        component = self.component.get_active_text() or "Все компоненты платформы"
        os_name = self.os_combo.get_active_text() or "Linux"
        branch = self.selected_branch or ("8.5" if str(version).startswith("8.5.") else "8.3")

        items = platform_distribution_variants(
            version=version,
            branch=branch,
            component=component,
            os_name=os_name,
            bit64=self.bit64.get_active(),
            arm=self.arm.get_active(),
            elbrus=self.elbrus.get_active(),
            web_clients=self.web_clients.get_active(),
        )

        if not items:
            self.available_store.append([f"Нет элементов для выбранных фильтров: {version}"])
            return

        current_section = ""

        for item in items:
            section = item.get("section") or ""
            if section and section != current_section:
                current_section = section
                self.available_store.append([f"▸ {section}"])

            title = item["title"]
            self.available_store.append([title])
            self.available_meta[title] = item

    def add_item_to_queue(self, item):
        title = item.get("title") or ""
        if not title:
            return

        if title in self.queue_meta:
            return

        self.queue_store.append([title])
        self.queue_meta[title] = item

    def remove_queue_iter(self, it):
        if not it:
            return
        title = self.queue_store[it][0]
        self.queue_meta.pop(title, None)
        self.queue_store.remove(it)

    def on_queue_row_activated(self, tree, path, column):
        model = tree.get_model()
        it = model.get_iter(path)
        if it:
            title = model[it][0]
            self.queue_meta.pop(title, None)
            model.remove(it)

    def on_queue_key_press(self, tree, event):
        try:
            keyval = event.keyval
            keyname = Gdk.keyval_name(keyval)
        except Exception:
            keyname = ""

        if keyname != "Delete":
            return False

        model, it = tree.get_selection().get_selected()
        if it:
            title = model[it][0]
            self.queue_meta.pop(title, None)
            model.remove(it)

        return True


    def get_queue_items(self):
        result = []
        it = self.queue_store.get_iter_first()
        while it:
            title = self.queue_store[it][0]
            item = self.queue_meta.get(title)
            if item:
                result.append(item)
            it = self.queue_store.iter_next(it)
        return result









    def request_cancel_download(self, *_):
        self.cancel_requested = True
        try:
            self.set_download_progress(0, "Отмена...")
        except Exception:
            pass

    def is_download_cancelled(self):
        return bool(getattr(self, "cancel_requested", False))

    def set_download_progress(self, percent, text=""):
        try:
            value = max(0, min(100, int(percent or 0)))
            self.download_progress.set_fraction(value / 100)
            self.download_progress.set_text(text or f"{value}%")
        except Exception:
            pass

    def open_download_dir(self, *_):
        try:
            open_folder_external(self.download_dir.get_text())
        except Exception:
            pass



    def on_download_queue(self):
        self.cancel_requested = False
        items = self.get_queue_items()
        if not items:
            return

        parent = self.get_transient_for()
        if parent is not None and hasattr(parent, "switch_to_report_tab"):
            parent.switch_to_report_tab()

        def log(msg):
            if parent is not None and hasattr(parent, "_append_log"):
                GLib.idle_add(parent._append_log, msg)
            else:
                print(msg)

        def op(**kwargs):
            try:
                if parent is not None and hasattr(parent, "set_current_operation"):
                    GLib.idle_add(parent.set_current_operation, **kwargs)
            except Exception:
                pass

        def progress(percent, done=0, total=0, text=""):
            value = max(0, min(100, int(percent or 0)))

            label = text or f"{value}%"
            if total:
                label = f"{value}% — {done // 1024 // 1024} / {total // 1024 // 1024} МБ"
            elif done:
                label = f"{done // 1024 // 1024} МБ"

            try:
                GLib.idle_add(self.set_download_progress, value, label)
            except Exception:
                pass

            try:
                if parent is not None and hasattr(parent, "set_report_progress"):
                    GLib.idle_add(parent.set_report_progress, value, label)
            except Exception:
                pass

        dest_dir = Path(self.download_dir.get_text()).expanduser()
        unpack = self.unpack.get_active()
        delete_after = self.delete_after.get_active()

        login = ""
        password = ""

        if parent is not None:
            try:
                login = parent.settings.get("its_login") or ""
                password = parent.get_its_password_for_update()
            except Exception:
                pass

        def work():
            import datetime

            started = datetime.datetime.now().strftime("%H:%M:%S")
            progress(0, 0, 0, "Подготовка")
            op(
                base="-",
                release="-",
                step="Подготовка",
                action="Скачивание платформы",
                mode="download",
                status="выполняется",
                pid="-",
                started=started,
            )

            log("=== Скачивание платформы 1С ===")
            log(f"Элементов в очереди: {len(items)}")
            log(f"Каталог скачивания: {dest_dir}")
            log(f"Распаковать после скачивания: {'да' if unpack else 'нет'}")
            log(f"Удалить архив после распаковки: {'да' if delete_after else 'нет'}")
            log(f"ИТС логин заполнен: {'да' if bool(login) else 'нет'}; пароль найден: {'да' if bool(password) else 'нет'}")

            if not login or not password:
                log("ОШИБКА: не найден логин/пароль ИТС.")
                progress(0, 0, 0, "Ошибка: нет ИТС")
                op(step="Ошибка", status="нет логина/пароля ИТС")
                return

            dest_dir.mkdir(parents=True, exist_ok=True)

            for i, original_item in enumerate(items, 1):
                item = dict(original_item)
                title = item.get("title") or "-"
                version = item.get("version") or extract_release_from_any(title) or ""

                if self.is_download_cancelled():
                    log("Операция отменена пользователем.")
                    progress(0, 0, 0, "Отменено")
                    op(action="Скачивание платформы", status="отменено")
                    break

                log("")
                log(f"[{i}/{len(items)}] {title}")

                archive = None
                progress(0, 0, 0, f"[{i}/{len(items)}] Поиск ссылки")
                op(
                    release=version or "-",
                    step=f"{i}/{len(items)}",
                    action="Поиск ссылки скачивания",
                    status="выполняется",
                )

                try:
                    log("Поиск ссылки через releases.1c.ru/version_file...")
                    archive = download_by_releases_version_file(
                        item,
                        dest_dir,
                        login,
                        password,
                        log,
                        progress_func=progress,
                        cancel_checker=self.is_download_cancelled,
                    )

                    if archive:
                        log("Скачивание завершено.")

                except Exception as e:
                    log(f"releases.1c.ru/version_file не сработал: {type(e).__name__}: {e}")

                if archive is None:
                    try:
                        log("Пробую прямой подбор файла на downloads.v8.1c.ru/tmplts...")
                        direct_v8_url = find_direct_downloads_v8_url(item, login, password, log)

                        if direct_v8_url:
                            log("Прямая ссылка найдена. Начинаю загрузку...")
                            archive = download_direct_downloads_v8_url(
                                direct_v8_url,
                                dest_dir,
                                login,
                                password,
                                log,
                                str(title),
                            )
                        else:
                            log("Прямой URL на downloads.v8.1c.ru не найден.")

                    except Exception as e:
                        log(f"Прямое скачивание downloads.v8.1c.ru не удалось: {type(e).__name__}: {e}")

                if archive is None:
                    log(f"ОШИБКА: не удалось скачать элемент: {title}")
                    progress(0, 0, 0, "Ошибка скачивания")
                    op(action="Скачивание", status="ошибка")
                    continue

                op(action="Файл скачан", status="скачан")

                if unpack and archive:
                    try:
                        if is_supported_archive_for_unpack(archive):
                            progress(100, 0, 0, "Распаковка")
                            op(action="Распаковка архива", status="выполняется")

                            # Распаковываем во временную папку.
                            # Если архив после распаковки удаляем, итоговая папка будет называться точно как архив.
                            # Если архив оставляем, добавляем _unpacked, потому файл и папка с одним именем рядом невозможны.
                            temp_unpack_dir = archive.parent / (archive.name + "_unpack_tmp")
                            final_unpack_dir = archive.parent / archive_output_folder_name(archive)

                            result_dir = unpack_platform_archive(archive, temp_unpack_dir, log)

                            if result_dir:
                                if delete_after:
                                    archive.unlink()
                                    log(f"Архив удален после распаковки: {archive}")

                                if final_unpack_dir.exists():
                                    import shutil
                                    if final_unpack_dir.is_dir():
                                        shutil.rmtree(final_unpack_dir)
                                    else:
                                        final_unpack_dir.unlink()

                                temp_unpack_dir.rename(final_unpack_dir)

                                log(f"Распаковано: {final_unpack_dir}")

                            op(action="Распаковка", status="готово")
                        else:
                            make_executable_if_needed(archive, log)
                            log(f"Распаковка пропущена: {archive.name} не архив.")
                            op(action="Распаковка", status="пропущена")

                    except Exception as e:
                        log(f"Не удалось распаковать архив: {type(e).__name__}: {e}")
                        op(action="Распаковка", status="ошибка")

                progress(100, 0, 0, "Готово")

            log("Скачивание платформы: завершено")
            log(f"Папка с файлами платформы: {dest_dir}")
            op(step="Завершено", action="Скачивание платформы", status="готово")

        threading.Thread(target=work, daemon=True).start()

    def on_open_download_dir(self, *_):
        path = Path(self.download_dir.get_text()).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["xdg-open", str(path)])


def get_updater_app_icon_path():
    from pathlib import Path

    candidates = [
        Path("/opt/updater1c-linux/icons/updater1c-linux.png"),
        Path(__file__).resolve().parents[1] / "icons" / "updater1c-linux.png",
        Path("/usr/share/icons/hicolor/512x512/apps/updater1c-linux.png"),
        Path("/usr/share/icons/hicolor/256x256/apps/updater1c-linux.png"),
        Path("/usr/share/pixmaps/updater1c-linux.png"),
    ]

    for path in candidates:
        try:
            if path.exists() and path.is_file():
                return str(path)
        except Exception:
            pass

    return ""


def system_libgcc_path_for_1c() -> str:
    candidates = [
        Path("/usr/lib/x86_64-linux-gnu/libgcc_s.so.1"),
        Path("/lib/x86_64-linux-gnu/libgcc_s.so.1"),
    ]

    for path in candidates:
        try:
            if path.exists() and path.is_file():
                return str(path)
        except Exception:
            pass

    return ""


def install_global_1c_libgcc_preload():
    """Исправление запуска 1С на новых Linux.

    Платформа 1С кладет рядом со своими бинарниками старую libgcc_s.so.1.
    На новых Ubuntu/Astra системные библиотеки могут требовать GCC_12/GCC_13,
    из-за чего 1cv8/1cv8c падает:
      libgcc_s.so.1: version `GCC_12.0.0' not found

    Поэтому для всех дочерних процессов Обновлятора подставляем системную libgcc.
    Это действует на любые версии 1С, которые запускаются из приложения.
    """
    import os

    libgcc = system_libgcc_path_for_1c()

    if not libgcc:
        return ""

    old_preload = os.environ.get("LD_PRELOAD", "").strip()
    parts = [x for x in old_preload.split() if x]

    if libgcc not in parts:
        os.environ["LD_PRELOAD"] = " ".join([libgcc] + parts)

    return os.environ.get("LD_PRELOAD", "")


class MainWindow(Gtk.Window):
    def __init__(self):
        super().__init__(title=APP_NAME)

        self._base_password_runtime_cache = {}
        self._last_launch_guard = {"key": "", "time": 0.0}

        try:
            self._global_1c_ld_preload = install_global_1c_libgcc_preload()
        except Exception:
            self._global_1c_ld_preload = ""

        try:
            from gi.repository import GLib as _GLib
            _GLib.set_application_name("Обновлятор 1C Linux")
            _GLib.set_prgname("updater1c-linux")
        except Exception:
            pass

        try:
            self.set_wmclass("updater1c-linux", "updater1c-linux")
        except Exception:
            pass

        try:
            icon_path = get_updater_app_icon_path()
            if icon_path:
                self.set_icon_from_file(icon_path)
        except Exception:
            pass
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

        try:
            from gi.repository import GLib
            GLib.idle_add(self.install_clipboard_paste_workaround, self)
        except Exception:
            pass


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
        self.base_user.connect("changed", self.on_base_credentials_changed)
        self.base_password.connect("changed", self.on_base_credentials_changed)
        self.loading_creds = False
        self.current_base_index = None
        self.base_user.connect("changed", self.on_credentials_changed)
        self.base_password.connect("changed", self.on_credentials_changed)

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
            ("🔄  Проверить настройки", self.on_check_selected_base_real),
            ("▼  Скачать обновления", self.on_download_updates_real),
            ("◻  Скачать платформу", self.on_download_platform),
            ("🔍  Установить обновления", self.on_auto_update),
        ]
        for title, handler in buttons:
            b = Ui.button(title)
            b.connect("clicked", handler)
            actions.pack_start(b, False, False, 0)

        columns = ["", "База", "Тип", "Конфигурация", "Версия", "Путь / сервер", "Платформа", "DB"]
        self.base_store = Gtk.TreeStore(bool, str, str, str, str, str, str, str, int)
        self.base_tree = Gtk.TreeView(model=self.base_store)
        self.base_tree.get_selection().set_mode(Gtk.SelectionMode.SINGLE)
        self.base_tree.set_reorderable(True)
        self.base_tree.get_selection().connect("changed", self.on_base_selection_changed)
        self.base_tree.connect("button-press-event", self.on_base_tree_double_click_press)
        self.base_tree.connect("button-press-event", self.on_base_tree_button_press)
        self.base_tree.connect("row-activated", self.on_base_row_activated)

        self._loading_bases_tree = False
        self._tree_save_scheduled = False
        self.base_store.connect("row-inserted", self.on_base_tree_structure_changed)
        self.base_store.connect("row-deleted", self.on_base_tree_structure_changed)
        self.base_store.connect("rows-reordered", self.on_base_tree_structure_changed)
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

        move_up = Ui.button("🔵 ▲")
        move_down = Ui.button("🔵 ▼")
        move_up.set_tooltip_text("Переместить выбранную базу/группу выше")
        move_down.set_tooltip_text("Переместить выбранную базу/группу ниже")
        move_up.connect("clicked", self.on_move_selected_base_up)
        move_down.connect("clicked", self.on_move_selected_base_down)
        bottom.pack_start(move_up, False, False, 0)
        bottom.pack_start(move_down, False, False, 0)

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
        self._loading_bases_tree = True
        self.base_store.clear()

        def append_group(parent_iter, name):
            return self.base_store.append(parent_iter, [
                False, str(name), "", "", "", "", "", "", -1
            ])

        def append_base(parent_iter, b, idx):
            self.base_store.append(parent_iter, [
                False,
                b.get("name", ""),
                b.get("kind", ""),
                b.get("config_synonym") or b.get("config_name", ""),
                b.get("config_version", ""),
                b.get("connect", ""),
                b.get("platform_version", "8.3"),
                b.get("db_type", ""),
                int(idx),
            ])

        groups_cache = {}

        def get_group_iter(path_value):
            parts = [x.strip() for x in str(path_value or "Без группы").split("/") if x.strip()]
            if not parts:
                parts = ["Без группы"]

            parent = None
            current_path = []

            for part in parts:
                current_path.append(part)
                key = "/".join(current_path)

                if key not in groups_cache:
                    groups_cache[key] = append_group(parent, part)

                parent = groups_cache[key]

            return parent

        if self.bases:
            for idx, b in enumerate(self.bases):
                if not isinstance(b, dict):
                    continue

                group = b.get("group") or "Без группы"
                parent = get_group_iter(group)
                append_base(parent, b, idx)
        else:
            demo = [
                {"name": "AccountingBase", "kind": "file", "connect": "/mnt/Data/bases/AccountingBase", "platform_version": "8.3", "group": "Мое"},
                {"name": "Conversion", "kind": "file", "config_synonym": "Конвертация данных, редакция 2.1", "config_version": "2.1.8.2", "connect": "/mnt/Data/bases/Conversion", "platform_version": "8.3", "group": "Мое"},
            ]

            self.bases = demo

            for idx, b in enumerate(self.bases):
                parent = get_group_iter(b.get("group") or "Мое")
                append_base(parent, b, idx)

        def expand_all(parent=None):
            child = self.base_store.iter_children(parent)
            while child is not None:
                try:
                    if self.base_store.iter_has_child(child):
                        self.base_tree.expand_row(self.base_store.get_path(child), False)
                        expand_all(child)
                except Exception:
                    pass
                child = self.base_store.iter_next(child)

        expand_all()

        self._loading_bases_tree = False
        self.update_bases_status()

    def is_group_iter(self, tree_iter):
        if tree_iter is None:
            return False

        try:
            idx = int(self.base_store[tree_iter][8])
            return idx < 0
        except Exception:
            return False

    def iter_parent(self, tree_iter):
        try:
            return self.base_store.iter_parent(tree_iter)
        except Exception:
            return None

    def iter_previous_sibling(self, tree_iter):
        parent = self.iter_parent(tree_iter)
        child = self.base_store.iter_children(parent)
        previous = None

        while child is not None:
            if self.base_store.get_path(child) == self.base_store.get_path(tree_iter):
                return previous

            previous = child
            child = self.base_store.iter_next(child)

        return None

    def iter_next_sibling(self, tree_iter):
        try:
            return self.base_store.iter_next(tree_iter)
        except Exception:
            return None

    def select_iter_and_scroll(self, tree_iter):
        try:
            path = self.base_store.get_path(tree_iter)
            self.base_tree.get_selection().select_path(path)
            self.base_tree.scroll_to_cell(path, None, True, 0.5, 0.0)
        except Exception:
            pass

    def on_move_selected_base_up(self, *_):
        tree_iter = self.selected_base_iter()

        if tree_iter is None:
            return

        previous = self.iter_previous_sibling(tree_iter)

        if previous is None:
            self._append_log("Перемещение вверх невозможно: строка уже первая на своем уровне.")
            return

        self.base_store.move_before(tree_iter, previous)
        self.select_iter_and_scroll(tree_iter)
        self.save_bases_order_from_tree()
        self._append_log("Порядок баз сохранен после перемещения вверх.")

    def on_move_selected_base_down(self, *_):
        tree_iter = self.selected_base_iter()

        if tree_iter is None:
            return

        next_iter = self.iter_next_sibling(tree_iter)

        if next_iter is None:
            self._append_log("Перемещение вниз невозможно: строка уже последняя на своем уровне.")
            return

        self.base_store.move_after(tree_iter, next_iter)
        self.select_iter_and_scroll(tree_iter)
        self.save_bases_order_from_tree()
        self._append_log("Порядок баз сохранен после перемещения вниз.")

    def on_base_tree_structure_changed(self, *args):
        if getattr(self, "_loading_bases_tree", False):
            return False

        if getattr(self, "_tree_save_scheduled", False):
            return False

        self._tree_save_scheduled = True

        def do_save():
            self._tree_save_scheduled = False
            try:
                self.save_bases_order_from_tree()
            except Exception as e:
                self._append_log(f"Не удалось сохранить порядок после перемещения: {type(e).__name__}: {e}")
            return False

        GLib.timeout_add(350, do_save)
        return False

    def group_path_for_iter(self, tree_iter):
        parts = []
        parent = self.iter_parent(tree_iter)

        while parent is not None:
            try:
                name = str(self.base_store[parent][1] or "").strip()
                if name:
                    parts.append(name)
            except Exception:
                pass

            parent = self.iter_parent(parent)

        parts.reverse()

        return "/".join(parts) if parts else "Без группы"

    def row_to_base_dict(self, tree_iter, new_index):
        old_index = -1

        try:
            old_index = int(self.base_store[tree_iter][8])
        except Exception:
            old_index = -1

        if old_index >= 0 and old_index < len(self.bases):
            item = dict(self.bases[old_index])
        else:
            item = {}

        item["name"] = str(self.base_store[tree_iter][1] or "")
        item["kind"] = str(self.base_store[tree_iter][2] or "")
        item["config_synonym"] = str(self.base_store[tree_iter][3] or "")
        item["config_version"] = str(self.base_store[tree_iter][4] or "")
        item["connect"] = str(self.base_store[tree_iter][5] or "")
        item["platform_version"] = str(self.base_store[tree_iter][6] or "")
        item["db_type"] = str(self.base_store[tree_iter][7] or "")
        item["group"] = self.group_path_for_iter(tree_iter)

        try:
            self.base_store[tree_iter][8] = int(new_index)
        except Exception:
            pass

        return item

    def collect_bases_from_tree(self, parent_iter=None, result=None):
        if result is None:
            result = []

        child = self.base_store.iter_children(parent_iter)

        while child is not None:
            if self.is_group_iter(child):
                self.collect_bases_from_tree(child, result)
            else:
                result.append(self.row_to_base_dict(child, len(result)))

            child = self.base_store.iter_next(child)

        return result

    def backup_config_before_reorder(self):
        try:
            src = Path(self.config_path)
            if src.exists():
                dst = src.with_suffix(src.suffix + ".before_tree_reorder_backup")
                if not dst.exists():
                    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception:
            pass

    def save_bases_order_from_tree(self):
        self.backup_config_before_reorder()
        self.bases = self.collect_bases_from_tree()
        self.config["bases"] = self.bases
        save_json(self.config_path, self.config)
        self.update_bases_status()

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

        idx = -1
        try:
            idx = int(self.base_store[tree_iter][8])
        except Exception:
            idx = -1

        return {
            "checked": bool(self.base_store[tree_iter][0]),
            "name": self.base_store[tree_iter][1],
            "kind": self.base_store[tree_iter][2],
            "config": self.base_store[tree_iter][3],
            "version": self.base_store[tree_iter][4],
            "connect": self.base_store[tree_iter][5],
            "platform": self.base_store[tree_iter][6],
            "db": self.base_store[tree_iter][7],
            "index": idx,
            "is_group": self.is_group_iter(tree_iter),
        }


    def load_selected_credentials(self):
        vals = self.selected_base_values()
        self.loading_creds = True

        if not vals or vals.get("is_group") or not vals.get("base"):
            self.current_base_index = None
            self.base_user.set_text("")
            self.base_password.set_text("")
            self.loading_creds = False
            return

        idx = vals.get("index")
        base = vals.get("base") or {}

        self.current_base_index = idx
        self.base_user.set_text(base.get("user") or "")
        self.base_password.set_text(base.get("password") or "")
        self.loading_creds = False

    def on_credentials_changed(self, *_):
        if self.loading_creds:
            return
        if self.current_base_index is None:
            return
        if not (0 <= self.current_base_index < len(self.bases)):
            return

        self.bases[self.current_base_index]["user"] = self.base_user.get_text()
        self.bases[self.current_base_index]["password"] = self.base_password.get_text()
        self.config["bases"] = self.bases
        save_json(self.config_path, self.config)

    def current_base_dict(self):
        vals = self.selected_base_values()
        if not vals or vals.get("is_group"):
            return None

        base = vals.get("base")
        if base is None:
            base = {
                "name": vals.get("name") or "",
                "kind": vals.get("kind") or "file",
                "connect": vals.get("connect") or "",
                "platform_version": vals.get("platform") or "8.*",
                "db_type": vals.get("db") or "",
            }

        # Берем актуальные логин/пароль с формы.
        base = dict(base)
        base["user"] = self.base_user.get_text()
        base["password"] = self.base_password.get_text()
        return base

    def require_current_base_dict(self):
        base = self.current_base_dict()
        if not base:
            dlg = Gtk.MessageDialog(
                transient_for=self,
                flags=0,
                message_type=Gtk.MessageType.INFO,
                buttons=Gtk.ButtonsType.OK,
                text="Не выбрана база",
            )
            dlg.format_secondary_text("Выберите строку информационной базы, а не группу.")
            dlg.run()
            dlg.destroy()
            return None
        return base

    def run_base_mode(self, mode: str):
        base = self.require_current_base_dict()
        if not base:
            return

        try:
            args = build_1c_args(base, self.settings, mode)
            reports_dir = self.settings.get("reports_dir") or "/mnt/DataStore/Updater1C/1c-update-reports"
            pid, log_file = start_process_and_log(
                args,
                reports_dir=reports_dir,
                base_name=base.get("name") or "base",
                suffix=mode.lower(),
            )

            self._append_log("Запуск: " + command_to_text(args))
            self._append_log(f"PID={pid}")
            self._append_log(f"Лог запуска: {log_file}")

        except Exception as e:
            self._append_log(f"ОШИБКА запуска {mode}: {type(e).__name__}: {e}")



    def on_check_selected_base_real(self, *_):
        vals = self.selected_base_values()
        base = self.require_current_base_dict()
        if not base:
            return

        def work(log):
            log(f"--- Проверка базы: {base.get('name', '')} ---")

            kind, connect = normalize_base_kind_and_connect(base)
            log(f"Тип: {kind}")
            log(f"Подключение: {connect}")
            log("Метод определения конфигурации: DumpConfigToFiles Configuration.xml")

            if kind == "file":
                db_path = Path(connect)

                if db_path.is_file() and db_path.name.lower() == "1cv8.1cd":
                    log(f"Файл базы найден: {db_path}")
                    base["connect"] = str(db_path.parent)
                elif db_path.is_dir() and (db_path / "1Cv8.1CD").exists():
                    log(f"Файловая база найдена: {db_path / '1Cv8.1CD'}")
                else:
                    log(f"ОШИБКА: файловая база не найдена: {connect}")
                    return

            result = detect_metadata_by_dump(base, self.settings, log)

            name = result.get("config_name") or ""
            synonym = result.get("config_synonym") or ""
            version = result.get("config_version") or ""

            log(f"Определено: {name} / {synonym} / {version}")

            idx = vals.get("index", -1) if vals else -1
            if 0 <= idx < len(self.bases):
                base["config_name"] = name
                base["config_synonym"] = synonym
                base["config_version"] = version
                if not base.get("update_program_name"):
                    hay = (name + " " + synonym).lower()

                    if "бухгалтер" in hay or "accounting" in hay:
                        base["update_program_name"] = "Accounting"

                    elif (
                        "управление торговлей" in hay
                        or "управлениеторговлей" in hay.replace(" ", "")
                        or "торговл" in hay
                        or "trade" in hay
                    ):
                        base["update_program_name"] = "Trade"

                    elif "документооборот" in hay or "document" in hay or "docmng" in hay:
                        base["update_program_name"] = "DocumentManagement"

                    elif "зарплата" in hay or "зуп" in hay or "hrm" in hay or "salary" in hay:
                        base["update_program_name"] = "HRM"

                    elif "управление нашей фирмой" in hay or "унф" in hay or "smallbusiness" in hay:
                        base["update_program_name"] = "SmallBusiness"

                    elif "erp" in hay:
                        base["update_program_name"] = "ERP"

                self.config["bases"] = self.bases
                save_json(self.config_path, self.config)
                GLib.idle_add(self._load_bases_tree)

            log(f"Текущая версия конфигурации: {version or '-'}")
            log(f"Код программы обновлений: {base.get('update_program_name') or '-'}")
            log("Проверка настроек: завершено")

        self.run_in_background("Проверка настроек", work)

    def on_run_base_real(self, *_):
        self.run_base_mode("ENTERPRISE")

    def on_designer_base_real(self, *_):
        self.run_base_mode("DESIGNER")





    def on_base_row_activated(self, tree, path, column):
        """Двойной клик / Enter по строке базы.

        База: запуск 1С.
        Группа: свернуть/развернуть.
        """
        try:
            tree.grab_focus()
            tree.get_selection().select_path(path)
            tree.set_cursor(path, column, False)
        except Exception:
            pass

        try:
            vals = self.selected_base_values()

            if not vals:
                self._append_log("Двойной клик: база не выбрана.")
                return

            if vals.get("is_group"):
                if self.base_tree.row_expanded(path):
                    self.base_tree.collapse_row(path)
                else:
                    self.base_tree.expand_row(path, False)
                return

            self._append_log(f"Двойной клик: запуск базы {vals.get('name') or '-'}")
            self.launch_selected_base("ENTERPRISE")

        except Exception as e:
            self._append_log(f"Ошибка запуска по двойному клику: {type(e).__name__}: {e}")
            try:
                dlg = Gtk.MessageDialog(
                    transient_for=self,
                    flags=0,
                    message_type=Gtk.MessageType.ERROR,
                    buttons=Gtk.ButtonsType.OK,
                    text="Не удалось запустить базу",
                )
                dlg.format_secondary_text(f"{type(e).__name__}: {e}")
                dlg.run()
                dlg.destroy()
            except Exception:
                pass


    def on_base_tree_double_click_press(self, tree, event):
        """Fallback-обработчик двойного клика мышью.

        Нужен потому, что Gtk.TreeView row-activated иногда перестает срабатывать
        после включения reorderable/drag-and-drop.
        """
        try:
            if event.button != 1:
                return False

            if event.type != Gdk.EventType._2BUTTON_PRESS:
                return False

            hit = tree.get_path_at_pos(int(event.x), int(event.y))

            if not hit:
                return False

            path, column, cell_x, cell_y = hit

            try:
                tree.grab_focus()
                tree.get_selection().select_path(path)
                tree.set_cursor(path, column, False)
            except Exception:
                pass

            self.on_base_row_activated(tree, path, column)
            return True

        except Exception as e:
            try:
                self._append_log(f"Ошибка обработки двойного клика: {type(e).__name__}: {e}")
            except Exception:
                pass
            return False

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
            ("Запустить", self.on_run_base_real),
            ("Конфигуратор", self.on_designer_base_real),
            ("Свойства", self.on_edit_base),
            ("Проверить настройки", self.on_check_selected_base_real),
            ("Скачать обновления", self.on_download_updates_real),
            ("Скачать платформу", self.on_download_platform),
            ("Установить обновления", self.on_auto_update),
            ("Очистить кэш", self.on_clear_cache_real),
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



    def resolve_1c_executable(self, vals=None, designer=False, prefer_thin=True):
        """Подобрать исполняемый файл 1С.

        Для web-баз важно запускать 1cv8c напрямую.
        /opt/1cv8/common/1cestart часто открывает список баз и игнорирует прямой /WS.
        """
        import shutil

        vals = vals or self.selected_base_values() or {}
        base = self.selected_base_config(vals)

        if prefer_thin:
            preferred_names = ["1cv8c", "1cv8"]
        elif designer:
            preferred_names = ["1cv8", "1cv8c"]
        else:
            preferred_names = ["1cv8c", "1cv8"]

        raw_candidates = [
            base.get("platform_path"),
            base.get("platform_exe"),
            self.settings.get("selected_platform"),
            self.settings.get("one_c_start"),
        ]

        # Если указан конкретный путь к 1cv8/1cv8c — используем его.
        # 1cestart как прямой запуск web-базы не берем, если prefer_thin=True.
        for value in raw_candidates:
            if not value:
                continue

            value = str(value).strip()

            if "/" not in value:
                continue

            path = Path(value).expanduser()

            if not path.exists() or not path.is_file():
                continue

            if prefer_thin and path.name.lower() == "1cestart":
                continue

            return str(path)

        platform_value = str(
            base.get("platform_version")
            or base.get("platform")
            or vals.get("platform")
            or self.settings.get("platform_version")
            or ""
        ).strip()

        roots = [
            Path("/opt/1cv8/x86_64"),
            Path("/opt/1cv8/i386"),
            Path("/opt/1C/v8.3/x86_64"),
            Path("/opt/1C/v8.3/i386"),
            Path("/opt/1C/v8.5/x86_64"),
            Path("/opt/1C/v8.5/i386"),
        ]

        version_dirs = []

        for root in roots:
            if not root.exists():
                continue

            if platform_value and platform_value not in ("auto", "8.*"):
                version_dirs.extend(sorted(root.glob(platform_value + "*"), reverse=True))

            version_dirs.extend(sorted([x for x in root.iterdir() if x.is_dir()], reverse=True))

        seen_dirs = []

        for d in version_dirs:
            if d in seen_dirs:
                continue

            seen_dirs.append(d)

            for exe_name in preferred_names:
                exe = d / exe_name
                if exe.exists() and exe.is_file():
                    return str(exe)

        for exe_name in preferred_names:
            found = shutil.which(exe_name)
            if found:
                return found

        # Последний fallback только если ничего другого не нашли.
        common = Path("/opt/1cv8/common/1cestart")
        if common.exists() and not prefer_thin:
            return str(common)

        return ""




    def quote_1c_conn_value(self, value):
        value = str(value or "")
        value = value.replace("\\", "\\\\")
        value = value.replace('"', '\\"')
        return f'"{value}"'

    def find_direct_1cv8c(self, platform_value=""):
        """Найти именно 1cv8c, не 1cestart."""
        import shutil

        platform_value = str(platform_value or "").strip()

        roots = [
            Path("/opt/1cv8/x86_64"),
            Path("/opt/1cv8/i386"),
            Path("/opt/1C/v8.3/x86_64"),
            Path("/opt/1C/v8.3/i386"),
            Path("/opt/1C/v8.5/x86_64"),
            Path("/opt/1C/v8.5/i386"),
        ]

        version_dirs = []

        for root in roots:
            if not root.exists():
                continue

            if platform_value and platform_value not in ("auto", "8.*"):
                version_dirs.extend(sorted(root.glob(platform_value + "*"), reverse=True))

            version_dirs.extend(sorted([x for x in root.iterdir() if x.is_dir()], reverse=True))

        seen = set()

        for d in version_dirs:
            key = str(d)
            if key in seen:
                continue

            seen.add(key)

            exe = d / "1cv8c"
            if exe.exists() and exe.is_file():
                return str(exe)

        found = shutil.which("1cv8c")
        if found:
            return found

        return ""





    def resolve_web_1c_executable(self, vals=None, base=None):
        """Для web-базы нужен прямой тонкий клиент 1cv8c, не 1cv8 и не 1cestart.

        Для платформы 8.3 в этом окружении предпочитаем 8.3.27.1786,
        потому web-сервер опубликован на ней.
        """
        vals = vals or self.selected_base_values() or {}
        base = base or self.selected_base_config(vals) or {}

        platform_value = str(
            vals.get("platform")
            or base.get("platform_version")
            or base.get("platform")
            or ""
        ).strip()

        explicit_candidates = []

        # Если в базе указан точный путь к 1cv8c — он важнее.
        for key in ("platform_path", "platform_exe"):
            value = str(base.get(key) or "").strip()
            if value:
                explicit_candidates.append(Path(value).expanduser())

        # Для web 8.3 принудительно предпочитаем 8.3.27.1786.
        if platform_value == "8.3" or platform_value.startswith("8.3"):
            explicit_candidates.append(Path("/opt/1cv8/x86_64/8.3.27.1786/1cv8c"))

        if platform_value and platform_value not in ("8.3", "8.5", "auto", "8.*"):
            explicit_candidates.extend([
                Path("/opt/1cv8/x86_64") / platform_value / "1cv8c",
                Path("/opt/1C/v8.3/x86_64") / platform_value / "1cv8c",
                Path("/opt/1C/v8.5/x86_64") / platform_value / "1cv8c",
            ])

        for exe in explicit_candidates:
            try:
                if exe.exists() and exe.is_file() and exe.name == "1cv8c":
                    return str(exe)
            except Exception:
                pass

        roots = [
            Path("/opt/1cv8/x86_64"),
            Path("/opt/1C/v8.3/x86_64"),
            Path("/opt/1C/v8.5/x86_64"),
            Path("/opt/1cv8/i386"),
            Path("/opt/1C/v8.3/i386"),
            Path("/opt/1C/v8.5/i386"),
        ]

        version_dirs = []

        for root in roots:
            if not root.exists():
                continue

            for d in root.iterdir():
                if not d.is_dir():
                    continue

                if platform_value in ("8.3", "8.5"):
                    if not d.name.startswith(platform_value + "."):
                        continue
                elif platform_value and platform_value not in ("auto", "8.*"):
                    if not d.name.startswith(platform_value):
                        continue

                version_dirs.append(d)

        # Не выбираем 1cv8. Только 1cv8c.
        for d in sorted(version_dirs):
            exe = d / "1cv8c"
            if exe.exists() and exe.is_file():
                return str(exe)

        return ""



    def selected_base_index(self):
        vals = self.selected_base_values()

        if not vals or vals.get("is_group"):
            return -1

        name = str(vals.get("name") or "")
        connect = str(vals.get("connect") or "")

        for i, item in enumerate(self.bases or []):
            if not isinstance(item, dict):
                continue

            if str(item.get("name") or "") == name and str(item.get("connect") or "") == connect:
                return i

        try:
            idx = int(vals.get("index", -1))
            if 0 <= idx < len(self.bases):
                return idx
        except Exception:
            pass

        try:
            tree_iter = self.selected_base_iter()
            if tree_iter is not None:
                idx = int(self.base_store[tree_iter][8])
                if 0 <= idx < len(self.bases):
                    return idx
        except Exception:
            pass

        return -1

    def selected_base_config(self, vals=None):
        vals = vals or self.selected_base_values()

        if not vals or vals.get("is_group"):
            return {}

        name = str(vals.get("name") or "")
        connect = str(vals.get("connect") or "")

        for item in self.bases or []:
            if not isinstance(item, dict):
                continue

            if str(item.get("name") or "") == name and str(item.get("connect") or "") == connect:
                return item

        idx = self.selected_base_index()

        if idx >= 0 and idx < len(self.bases):
            item = self.bases[idx]
            if isinstance(item, dict):
                return item

        return {
            "name": name,
            "kind": vals.get("kind") or "",
            "connect": connect,
            "platform_version": vals.get("platform") or "",
        }

    def keyring_service_name(self):
        return "updater1c-linux"

    def base_secret_id(self, base):
        import hashlib

        base = base or {}

        raw = "|".join([
            str(base.get("name") or ""),
            str(base.get("connect") or ""),
            str(base.get("kind") or ""),
        ])

        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
        return f"base-password-{digest}"

    def keyring_get_password(self, secret_id):
        if not secret_id:
            return ""

        cache = getattr(self, "_base_password_runtime_cache", {})
        if secret_id in cache:
            return cache.get(secret_id) or ""

        try:
            import keyring

            value = keyring.get_password(self.keyring_service_name(), secret_id) or ""

            if value:
                self._base_password_runtime_cache[secret_id] = value

            return value

        except Exception as e:
            try:
                self._append_log(f"Keyring: не удалось прочитать пароль: {type(e).__name__}: {e}")
            except Exception:
                pass
            return ""

    def keyring_set_password(self, secret_id, password):
        if not secret_id:
            return False

        if not hasattr(self, "_base_password_runtime_cache"):
            self._base_password_runtime_cache = {}

        self._base_password_runtime_cache[secret_id] = password or ""

        try:
            import keyring

            if password:
                keyring.set_password(self.keyring_service_name(), secret_id, password)
            else:
                try:
                    keyring.delete_password(self.keyring_service_name(), secret_id)
                except Exception:
                    pass

            return True

        except Exception as e:
            try:
                self._append_log(f"Keyring: пароль сохранен только в памяти текущей сессии: {type(e).__name__}: {e}")
            except Exception:
                pass
            return False

    def save_config_safe(self):
        try:
            self.config["bases"] = self.bases
            save_json(self.config_path, self.config)
            return True
        except Exception as e:
            try:
                self._append_log(f"Не удалось сохранить настройки: {type(e).__name__}: {e}")
            except Exception:
                pass
            return False

    def get_base_password(self, base):
        base = base or {}

        secret_id = str(base.get("password_secret_id") or "")

        if secret_id:
            value = self.keyring_get_password(secret_id)
            if value:
                return value

        old_plain = str(base.get("password") or base.get("pwd") or base.get("pass") or "")

        if old_plain:
            secret_id = self.base_secret_id(base)
            self.keyring_set_password(secret_id, old_plain)

            base["password_secret_id"] = secret_id
            base["password_saved"] = True

            for key in ("password", "pwd", "pass"):
                base.pop(key, None)

            self.save_config_safe()
            return old_plain

        return ""

    def set_base_password(self, base, password):
        base = base or {}

        secret_id = str(base.get("password_secret_id") or "") or self.base_secret_id(base)

        self.keyring_set_password(secret_id, password)

        base["password_secret_id"] = secret_id
        base["password_saved"] = bool(password)

        for key in ("password", "pwd", "pass"):
            base.pop(key, None)

        return True

    def get_base_user_text(self):
        try:
            return str(self.base_user.get_text() or "").strip()
        except Exception:
            return ""

    def get_base_password_text(self):
        try:
            return str(self.base_password.get_text() or "")
        except Exception:
            return ""

    def on_base_selection_changed(self, *_):
        if getattr(self, "_loading_base_credentials", False):
            return

        vals = self.selected_base_values()

        self._loading_base_credentials = True

        try:
            if not vals or vals.get("is_group"):
                self.base_user.set_text("")
                self.base_password.set_text("")
                return

            base = self.selected_base_config(vals)

            user = str(
                base.get("user")
                or base.get("username")
                or base.get("login")
                or ""
            )

            password = self.get_base_password(base)

            self.base_user.set_text(user)
            self.base_password.set_text(password)

        finally:
            self._loading_base_credentials = False

    def on_base_credentials_changed(self, *_):
        if getattr(self, "_loading_base_credentials", False):
            return

        vals = self.selected_base_values()

        if not vals or vals.get("is_group"):
            return

        self.save_current_credentials_to_selected_base(silent=True)

    def save_current_credentials_to_selected_base(self, silent=False):
        vals = self.selected_base_values()

        if not vals or vals.get("is_group"):
            return False

        idx = self.selected_base_index()

        if idx < 0 or idx >= len(self.bases):
            if not silent:
                self._append_log("Не удалось сохранить логин/пароль: выбранная база не найдена.")
            return False

        base = self.bases[idx]

        if not isinstance(base, dict):
            return False

        user = self.get_base_user_text()
        password = self.get_base_password_text()

        base["user"] = user
        base["login"] = user
        base["username"] = user

        self.set_base_password(base, password)
        self.save_config_safe()

        if not silent:
            self._append_log(f"Логин сохранен, пароль сохранен в системном хранилище: {base.get('name') or vals.get('name')}")

        return True

    def get_launch_credentials_for_selected_base(self, base):
        base = base or {}

        form_user = self.get_base_user_text()
        form_password = self.get_base_password_text()

        user = form_user or str(
            base.get("user")
            or base.get("username")
            or base.get("login")
            or ""
        ).strip()

        password = form_password or self.get_base_password(base)

        return user, password

    def normalize_base_dialog_response_buttons(self, dlg):
        """Гарантирует, что OK/Отмена закрывают модальное окно.

        Некоторые текущие кнопки могли быть созданы как обычные Gtk.Button,
        без response-id. Поэтому вручную привязываем их к dlg.response().
        """
        def walk(widget):
            result = []

            try:
                children = widget.get_children()
            except Exception:
                children = []

            for child in children:
                result.append(child)
                result.extend(walk(child))

            return result

        for child in walk(dlg):
            try:
                if not isinstance(child, Gtk.Button):
                    continue

                label = str(child.get_label() or "").replace("_", "").strip().lower()

                if label in ("ok", "ок"):
                    child.connect("clicked", lambda *_: dlg.response(Gtk.ResponseType.OK))

                if label in ("отмена", "cancel"):
                    child.connect("clicked", lambda *_: dlg.response(Gtk.ResponseType.CANCEL))

            except Exception:
                pass

        try:
            dlg.set_default_response(Gtk.ResponseType.OK)
        except Exception:
            pass


    def on_edit_base(self, *_):
        vals = self.selected_base_values()

        if not vals or vals.get("is_group"):
            self._append_log("Для свойств выбери конкретную базу, а не группу.")
            return

        self.save_current_credentials_to_selected_base(silent=True)

        idx = self.selected_base_index()

        if idx < 0 or idx >= len(self.bases):
            self._append_log("Не удалось открыть свойства: база не найдена.")
            return

        base = self.bases[idx]

        if not isinstance(base, dict):
            self._append_log("Не удалось открыть свойства: некорректная запись базы.")
            return

        old_name = str(base.get("name") or vals.get("name") or "")
        old_connect = str(base.get("connect") or vals.get("connect") or "")

        dlg = BaseDialog(self, "Свойства базы — Обновлятор 1C Linux", base=base)

        try:
            self.configure_base_dialog_type_fields(dlg, base)
        except Exception as e:
            self._append_log(f"Предупреждение: не удалось настроить поля типа базы: {type(e).__name__}: {e}")

        try:
            self.configure_base_dialog_group_picker(dlg, base)
        except Exception as e:
            self._append_log(f"Предупреждение: не удалось настроить выбор группы: {type(e).__name__}: {e}")

        try:
            dlg.user.set_text(self.get_base_user_text() or str(base.get("user") or base.get("login") or ""))
        except Exception:
            pass

        try:
            dlg.password.set_text(self.get_base_password_text() or self.get_base_password(base))
        except Exception:
            pass

        self.normalize_base_dialog_response_buttons(dlg)

        self.install_clipboard_paste_workaround(dlg)

        resp = dlg.run()

        if resp != Gtk.ResponseType.OK:
            dlg.destroy()
            self._append_log("Изменение свойств базы отменено.")
            return

        try:
            base["name"] = str(dlg.name.get_text() or "").strip()
            base["group"] = self.apply_base_group_after_properties_ok(base, dlg)

            kind = self.base_dialog_kind_text(dlg) or str(combo_text(dlg.kind) or base.get("kind") or "file").strip().lower()
            base["kind"] = kind

            base["connect"] = self.base_dialog_connect_value(dlg)

            if kind == "server":
                try:
                    base["server_name"] = str(dlg.server_name.get_text() or "").strip()
                    base["db_name"] = str(dlg.db_name.get_text() or "").strip()
                except Exception:
                    pass
            else:
                base.pop("server_name", None)
                base.pop("db_name", None)

            base["user"] = str(dlg.user.get_text() or "").strip()
            base["login"] = base["user"]
            base["username"] = base["user"]

            try:
                base["platform_version"] = combo_text(dlg.platform) or base.get("platform_version") or "8.3"
            except Exception:
                base["platform_version"] = base.get("platform_version") or "8.3"

            try:
                base["launch_parameters"] = str(dlg.launch_params.get_text() or "").strip()
            except Exception:
                pass

            try:
                base["config_name"] = str(dlg.config_name.get_text() or "").strip()
            except Exception:
                pass

            try:
                base["config_synonym"] = str(dlg.config_synonym.get_text() or "").strip()
            except Exception:
                pass

            try:
                base["config_version"] = str(dlg.config_version.get_text() or "").strip()
            except Exception:
                pass

            try:
                base["update_program_name"] = str(dlg.update_code.get_text() or "").strip()
            except Exception:
                pass

            if hasattr(dlg, "comment"):
                try:
                    buf = dlg.comment.get_buffer()
                    start, end = buf.get_bounds()
                    base["comment"] = buf.get_text(start, end, True)
                except Exception:
                    pass

            try:
                self.set_base_password(base, dlg.password.get_text())
            except Exception as e:
                self._append_log(f"Не удалось сохранить пароль в keyring: {type(e).__name__}: {e}")

            new_name = str(base.get("name") or old_name)
            new_connect = str(base.get("connect") or old_connect)

            dlg.destroy()

            self.refresh_bases_tree_after_properties_save(base, old_name, old_connect)

            self._loading_base_credentials = True
            try:
                self.base_user.set_text(str(base.get("user") or ""))
                self.base_password.set_text(self.get_base_password(base))
            finally:
                self._loading_base_credentials = False

            self._append_log(f"Свойства базы сохранены: {new_name}")
            return

        except Exception as e:
            try:
                dlg.destroy()
            except Exception:
                pass

            self._append_log(f"Не удалось сохранить свойства базы: {type(e).__name__}: {e}")
            return

    def parse_server_connect(self, connect):
        connect = str(connect or "").strip()

        if "\\" in connect:
            srv, db = connect.split("\\", 1)
            return srv.strip(), db.strip()

        if "/" in connect and not connect.lower().startswith(("http://", "https://")):
            srv, db = connect.split("/", 1)
            return srv.strip(), db.strip()

        return connect, ""

    def base_dialog_kind_text(self, dlg):
        """Надежно читает текущий тип базы из Gtk.ComboBox/ComboBoxText."""
        try:
            value = dlg.kind.get_active_text()
            if value:
                return str(value).strip().lower()
        except Exception:
            pass

        try:
            model = dlg.kind.get_model()
            active_iter = dlg.kind.get_active_iter()

            if model is not None and active_iter is not None:
                row = model[active_iter]
                for value in row:
                    if value:
                        return str(value).strip().lower()
        except Exception:
            pass

        try:
            active = dlg.kind.get_active()
            model = dlg.kind.get_model()

            if model is not None and active >= 0:
                row = model[active]
                for value in row:
                    if value:
                        return str(value).strip().lower()
        except Exception:
            pass

        try:
            return str(combo_text(dlg.kind) or "").strip().lower()
        except Exception:
            return ""

    def find_widget_label_for_entry(self, entry):
        """Ищет подпись именно для поля connect.

        В текущей форме это ближайший Gtk.Label перед строкой с полем connect.
        """
        try:
            toplevel = entry.get_toplevel()
        except Exception:
            return None

        labels = []

        def walk(widget):
            try:
                children = widget.get_children()
            except Exception:
                return

            for child in children:
                try:
                    if isinstance(child, Gtk.Label):
                        txt = str(child.get_text() or "")
                        if (
                            "Путь" in txt
                            or "server" in txt
                            or "URL" in txt
                            or "base" in txt
                            or "1Cv8" in txt
                        ):
                            labels.append(child)
                except Exception:
                    pass

                walk(child)

        walk(toplevel)

        # Обычно нужная подпись первая из найденных среди "Путь / server / URL".
        return labels[0] if labels else None

    def find_button_near_entry(self, entry):
        """Ищет кнопку ... в той же строке, где поле connect."""
        try:
            parent = entry.get_parent()
        except Exception:
            parent = None

        if parent is None:
            return None

        buttons = []

        def walk(widget):
            try:
                children = widget.get_children()
            except Exception:
                return

            for child in children:
                try:
                    if isinstance(child, Gtk.Button) and str(child.get_label() or "").strip() == "...":
                        buttons.append(child)
                except Exception:
                    pass

                walk(child)

        walk(parent)
        return buttons[0] if buttons else None


    def hide_base_dialog_template_row(self, dlg):
        """Скрывает строку 'Шаблон 1С (.dt/.cf/папка)' вместе с полем и кнопкой."""
        hidden = set()

        def hide_widget(w):
            try:
                w.hide()
                hidden.add(w)
            except Exception:
                pass

        def walk(widget):
            result = []
            try:
                children = widget.get_children()
            except Exception:
                return result

            for child in children:
                result.append(child)
                result.extend(walk(child))

            return result

        try:
            toplevel = dlg.get_toplevel()
        except Exception:
            toplevel = dlg

        widgets = walk(toplevel)

        template_labels = []
        for w in widgets:
            try:
                if isinstance(w, Gtk.Label):
                    label_text = str(w.get_text() or "")
                    if "Шаблон 1С" in label_text or ".dt/.cf" in label_text:
                        template_labels.append(w)
            except Exception:
                pass

        for label in template_labels:
            hide_widget(label)

            # Если это Gtk.Grid, скрываем все элементы той же строки.
            try:
                parent = label.get_parent()
                if isinstance(parent, Gtk.Grid):
                    top = parent.child_get_property(label, "top-attach")
                    for child in parent.get_children():
                        try:
                            if parent.child_get_property(child, "top-attach") == top:
                                hide_widget(child)
                        except Exception:
                            pass
                    continue
            except Exception:
                pass

            # Fallback: скрываем ближайшие поля/кнопки после label в общем списке.
            try:
                idx = widgets.index(label)
                for child in widgets[idx + 1: idx + 5]:
                    if isinstance(child, (Gtk.Entry, Gtk.Button, Gtk.Box)):
                        hide_widget(child)
            except Exception:
                pass

        # Дополнительный fallback по известным возможным именам атрибутов.
        for attr in (
            "template",
            "template_path",
            "template_entry",
            "template_file",
            "cf_template",
            "dt_template",
            "template_button",
        ):
            try:
                w = getattr(dlg, attr, None)
                if w is not None:
                    hide_widget(w)
            except Exception:
                pass

    def base_dialog_save_current_type_value(self, dlg):
        kind = self.base_dialog_kind_text(dlg)

        if not hasattr(dlg, "_u1c_kind_values"):
            dlg._u1c_kind_values = {}

        if kind == "server":
            try:
                srv = str(dlg.server_name.get_text() or "").strip()
            except Exception:
                srv = ""

            try:
                db = str(dlg.db_name.get_text() or "").strip()
            except Exception:
                db = ""

            dlg._u1c_kind_values["server"] = {"srv": srv, "db": db}

        elif kind in ("file", "web"):
            try:
                value = str(dlg.connect.get_text() or "").strip()
            except Exception:
                value = ""

            dlg._u1c_kind_values[kind] = value

    def base_dialog_restore_type_value(self, dlg, kind):
        kind = str(kind or "").strip().lower()

        if not hasattr(dlg, "_u1c_kind_values"):
            dlg._u1c_kind_values = {}

        if kind == "server":
            value = dlg._u1c_kind_values.get("server") or {}

            try:
                dlg.server_name.set_text(str(value.get("srv") or ""))
                dlg.db_name.set_text(str(value.get("db") or ""))
            except Exception:
                pass

        elif kind in ("file", "web"):
            value = dlg._u1c_kind_values.get(kind)

            if value is None:
                value = ""

            try:
                dlg.connect.set_text(str(value or ""))
            except Exception:
                pass

    def configure_base_dialog_type_fields(self, dlg, base=None):
        """Настраивает поле подключения в свойствах базы под file/server/web.

        При переключении типа не переносим старый путь/URL в другой тип.
        Значение каждого типа хранится отдельно в рамках открытого окна.
        """
        base = base or {}

        if getattr(dlg, "_u1c_type_fields_configured", False):
            self.hide_base_dialog_template_row(dlg)
            self.apply_base_dialog_type_visibility(dlg)
            return

        dlg._u1c_type_fields_configured = True

        original_kind = self.base_dialog_kind_text(dlg) or str(base.get("kind") or "file").strip().lower()
        original_connect = str(base.get("connect") or dlg.connect.get_text() or "").strip()

        dlg._u1c_original_kind = original_kind
        dlg._u1c_current_kind = original_kind
        dlg._u1c_kind_values = {
            "file": "",
            "web": "",
            "server": {"srv": "", "db": ""},
        }

        if original_kind == "server":
            srv_value, db_value = self.parse_server_connect(original_connect)
            dlg._u1c_kind_values["server"] = {
                "srv": srv_value,
                "db": db_value,
            }
            # connect для server скрывается, поэтому очищаем его, чтобы он не светился при web.
            try:
                dlg.connect.set_text("")
            except Exception:
                pass

        elif original_kind == "web":
            dlg._u1c_kind_values["web"] = original_connect

        else:
            dlg._u1c_kind_values["file"] = original_connect

        dlg._u1c_connect_label = self.find_widget_label_for_entry(dlg.connect)
        dlg._u1c_connect_browse_button = self.find_button_near_entry(dlg.connect)

        try:
            connect_parent = dlg.connect.get_parent()
        except Exception:
            connect_parent = None

        dlg.server_name = Gtk.Entry()
        dlg.server_name.set_placeholder_text("srvname")

        dlg.db_name = Gtk.Entry()
        dlg.db_name.set_placeholder_text("dbname")

        if connect_parent is not None:
            try:
                connect_parent.pack_start(dlg.server_name, True, True, 0)
                connect_parent.pack_start(dlg.db_name, True, True, 0)
            except Exception:
                try:
                    connect_parent.add(dlg.server_name)
                    connect_parent.add(dlg.db_name)
                except Exception:
                    pass

        try:
            dlg.server_name.show()
            dlg.db_name.show()
        except Exception:
            pass

        # Восстанавливаем значение именно для текущего исходного типа.
        self.base_dialog_restore_type_value(dlg, original_kind)

        try:
            dlg.kind.connect("changed", lambda *_: self.on_base_dialog_kind_changed(dlg))
        except Exception:
            pass

        self.hide_base_dialog_template_row(dlg)
        self.apply_base_dialog_type_visibility(dlg)

    def on_base_dialog_kind_changed(self, dlg):
        old_kind = str(getattr(dlg, "_u1c_current_kind", "") or "").strip().lower()
        new_kind = self.base_dialog_kind_text(dlg)

        if old_kind and old_kind != new_kind:
            # Сначала сохраняем значение старого типа.
            try:
                dlg._u1c_current_kind = old_kind
                self.base_dialog_save_current_type_value(dlg)
            except Exception:
                pass

        dlg._u1c_current_kind = new_kind

        # Потом восстанавливаем значение нового типа.
        # Если в этом типе пользователь еще ничего не вводил, поле будет пустым.
        self.base_dialog_restore_type_value(dlg, new_kind)

        self.hide_base_dialog_template_row(dlg)
        self.apply_base_dialog_type_visibility(dlg)

    def apply_base_dialog_type_visibility(self, dlg):
        kind = self.base_dialog_kind_text(dlg)

        label = getattr(dlg, "_u1c_connect_label", None)
        browse = getattr(dlg, "_u1c_connect_browse_button", None)

        if kind == "server":
            if label is not None:
                try:
                    label.set_text("srvname / dbname:")
                except Exception:
                    pass

            try:
                dlg.connect.hide()
            except Exception:
                pass

            try:
                dlg.server_name.show()
                dlg.db_name.show()
            except Exception:
                pass

            if browse is not None:
                try:
                    browse.hide()
                except Exception:
                    pass

        elif kind == "web":
            if label is not None:
                try:
                    label.set_text("URL web-базы:")
                except Exception:
                    pass

            try:
                dlg.connect.show()
            except Exception:
                pass

            try:
                dlg.server_name.hide()
                dlg.db_name.hide()
            except Exception:
                pass

            if browse is not None:
                try:
                    browse.hide()
                except Exception:
                    pass

        else:
            if label is not None:
                try:
                    label.set_text("Путь к папке с 1Cv8.1CD:")
                except Exception:
                    pass

            try:
                dlg.connect.show()
            except Exception:
                pass

            try:
                dlg.server_name.hide()
                dlg.db_name.hide()
            except Exception:
                pass

            if browse is not None:
                try:
                    browse.show()
                except Exception:
                    pass

    def base_dialog_connect_value(self, dlg):
        # Перед сохранением фиксируем текущее значение активного типа.
        self.base_dialog_save_current_type_value(dlg)

        kind = self.base_dialog_kind_text(dlg)

        if kind == "server":
            value = dlg._u1c_kind_values.get("server") or {}

            srv = str(value.get("srv") or "").strip()
            db = str(value.get("db") or "").strip()

            if srv and db:
                return f"{srv}\\{db}"

            return srv or db

        if kind == "web":
            return str(dlg._u1c_kind_values.get("web") or "").strip()

        return str(dlg._u1c_kind_values.get("file") or "").strip()


    def available_base_groups(self, current_group=""):
        groups = set()

        current_group = str(current_group or "").strip()
        if current_group:
            groups.add(current_group)

        for base in self.bases or []:
            if not isinstance(base, dict):
                continue

            name = str(base.get("name") or "").strip()
            group = str(base.get("group") or "").strip()

            is_group = bool(
                base.get("is_group")
                or base.get("group_placeholder")
                or base.get("kind") == "group"
                or base.get("type") == "group"
            )

            if is_group and name:
                groups.add(name)

            if group:
                groups.add(group)

        try:
            child = self.base_store.iter_children(None)
            while child is not None:
                try:
                    if self.is_group_iter(child):
                        group_name = str(self.base_store[child][1] or "").strip()
                        if group_name:
                            groups.add(group_name)
                except Exception:
                    pass

                child = self.base_store.iter_next(child)
        except Exception:
            pass

        groups.discard("")
        groups.discard("-")

        result = sorted(groups, key=lambda x: x.lower())

        if "Без группы" in result:
            result.remove("Без группы")

        result.insert(0, "Без группы")
        return result

    def base_dialog_group_value(self, dlg):
        try:
            value = str(getattr(dlg, "_u1c_group_value", "") or "").strip()
            if value:
                return self.normalize_base_group_name(value)
        except Exception:
            pass

        try:
            if hasattr(dlg, "group_entry"):
                value = str(dlg.group_entry.get_text() or "").strip()
                if value:
                    return self.normalize_base_group_name(value)
        except Exception:
            pass

        try:
            value = str(combo_text(dlg.group) or "").strip()
            if value:
                return self.normalize_base_group_name(value)
        except Exception:
            pass

        return self.normalize_base_group_name("Без группы")

    def set_base_dialog_group_value(self, dlg, value):
        value = str(value or "").strip() or "Без группы"
        dlg._u1c_group_value = value

        try:
            if hasattr(dlg, "group_entry"):
                dlg.group_entry.set_text(value)
        except Exception:
            pass


    def choose_base_group_dialog(self, dlg):
        current = self.base_dialog_group_value(dlg)
        groups = self.available_base_groups(current)

        dialog = Gtk.Dialog(
            title="Выбор группы",
            transient_for=dlg,
            flags=Gtk.DialogFlags.MODAL,
        )

        dialog.add_button("Отмена", Gtk.ResponseType.CANCEL)
        dialog.add_button("OK", Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        dialog.set_default_size(420, 420)

        box = dialog.get_content_area()
        box.set_spacing(8)
        box.set_border_width(10)

        info = Gtk.Label(label="Выберите группу для базы:")
        info.set_xalign(0)
        box.pack_start(info, False, False, 0)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_min_content_height(260)

        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)

        # Важно: один клик только выделяет строку.
        # Без этого row-activated может срабатывать от одного клика.
        try:
            listbox.set_activate_on_single_click(False)
        except Exception:
            try:
                listbox.set_property("activate-on-single-click", False)
            except Exception:
                pass

        selected_row = None

        for group_name in groups:
            row = Gtk.ListBoxRow()

            label = Gtk.Label(label=group_name)
            label.set_xalign(0)
            label.set_margin_top(6)
            label.set_margin_bottom(6)
            label.set_margin_start(8)
            label.set_margin_end(8)

            row.add(label)
            row._u1c_group_name = group_name

            listbox.add(row)

            if group_name == current:
                selected_row = row

        scroller.add(listbox)
        box.pack_start(scroller, True, True, 0)

        new_label = Gtk.Label(label="Или введите новую группу:")
        new_label.set_xalign(0)
        box.pack_start(new_label, False, False, 0)

        new_entry = Gtk.Entry()
        new_entry.set_placeholder_text("Новая группа")
        box.pack_start(new_entry, False, False, 0)

        def select_current_row_and_close(*_):
            dialog.response(Gtk.ResponseType.OK)

        # Теперь это сработает только на двойное нажатие/активацию Enter.
        listbox.connect("row-activated", select_current_row_and_close)

        dialog.show_all()

        if selected_row is not None:
            try:
                listbox.select_row(selected_row)
            except Exception:
                pass

        resp = dialog.run()

        if resp == Gtk.ResponseType.OK:
            typed = str(new_entry.get_text() or "").strip()

            if typed:
                value = typed
            else:
                row = listbox.get_selected_row()
                value = str(getattr(row, "_u1c_group_name", "") or "").strip() if row is not None else ""

            if value:
                self.set_base_dialog_group_value(dlg, value)

        dialog.destroy()

    def configure_base_dialog_group_picker(self, dlg, base=None):
        """Заменяет группу на поле + кнопку ... без изменения on_add_base."""
        base = base or {}

        if getattr(dlg, "_u1c_group_picker_configured", False):
            return

        dlg._u1c_group_picker_configured = True

        current_group = str(
            base.get("group")
            or self.base_dialog_group_value(dlg)
            or "Без группы"
        ).strip() or "Без группы"

        dlg._u1c_group_value = current_group

        dlg.group_entry = Gtk.Entry()
        dlg.group_entry.set_text(current_group)
        dlg.group_entry.set_editable(False)

        dlg.group_button = Gtk.Button(label="...")
        dlg.group_button.set_tooltip_text("Выбрать группу")
        dlg.group_button.connect("clicked", lambda *_: self.choose_base_group_dialog(dlg))

        picker_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        picker_box.pack_start(dlg.group_entry, True, True, 0)
        picker_box.pack_start(dlg.group_button, False, False, 0)

        try:
            parent = dlg.group.get_parent()
        except Exception:
            parent = None

        if parent is None:
            return

        try:
            if isinstance(parent, Gtk.Grid):
                left = parent.child_get_property(dlg.group, "left-attach")
                top = parent.child_get_property(dlg.group, "top-attach")
                width = parent.child_get_property(dlg.group, "width")
                height = parent.child_get_property(dlg.group, "height")

                parent.remove(dlg.group)
                parent.attach(picker_box, left, top, width, height)

            elif isinstance(parent, Gtk.Box):
                parent.pack_start(picker_box, True, True, 0)
                parent.reorder_child(picker_box, 0)
                dlg.group.hide()

            else:
                dlg.group.hide()
                parent.add(picker_box)

            picker_box.show_all()

        except Exception:
            try:
                dlg.group.hide()
                parent.add(picker_box)
                picker_box.show_all()
            except Exception:
                pass



    def normalize_base_group_name(self, value):
        value = str(value or "").strip()

        if not value or value in ("-", "None", "none", "null"):
            return "Без группы"

        if value.lower() in ("без группы", "no group", "nogroup", "ungrouped"):
            return "Без группы"

        return value

    def apply_base_group_after_properties_ok(self, base, dlg):
        """Сохраняет группу из окна свойств и возвращает новое имя группы."""
        group = "Без группы"

        try:
            group = self.base_dialog_group_value(dlg)
        except Exception:
            try:
                group = combo_text(dlg.group)
            except Exception:
                group = base.get("group") or "Без группы"

        group = self.normalize_base_group_name(group)

        base["group"] = group

        # Чтобы старые поля не удерживали прошлую группу.
        base.pop("group_name", None)
        base.pop("parent_group", None)

        return group

    def select_base_by_name_connect(self, name, connect):
        name = str(name or "")
        connect = str(connect or "")

        def walk(parent=None):
            child = self.base_store.iter_children(parent)

            while child is not None:
                try:
                    row_name = str(self.base_store[child][1] or "")
                    row_connect = str(self.base_store[child][5] or "")

                    if not self.is_group_iter(child) and row_name == name and row_connect == connect:
                        path = self.base_store.get_path(child)
                        self.base_tree.get_selection().select_path(path)
                        self.base_tree.expand_to_path(path)
                        self.base_tree.scroll_to_cell(path, None, True, 0.4, 0.0)
                        return True

                    if self.base_store.iter_has_child(child):
                        if walk(child):
                            return True

                except Exception:
                    pass

                child = self.base_store.iter_next(child)

            return False

        return walk(None)


    def force_reload_bases_from_config_memory(self):
        """Синхронизирует self.bases с self.config после сохранения.

        Важно для случая, когда группа базы поменялась в свойствах:
        файл уже сохранен, но дерево может продолжать жить на старой группировке.
        """
        try:
            self.config["bases"] = self.bases
        except Exception:
            pass

        try:
            self.bases = self.config.get("bases", self.bases) or []
        except Exception:
            pass

        return self.bases


    def refresh_bases_tree_after_properties_save(self, base, old_name="", old_connect=""):
        """Полностью и сразу перестраивает дерево после сохранения свойств базы."""
        base = base or {}

        new_name = str(base.get("name") or old_name or "")
        new_connect = str(base.get("connect") or old_connect or "")
        new_group = self.normalize_base_group_name(base.get("group"))

        base["group"] = new_group

        try:
            self.config["bases"] = self.bases
        except Exception:
            pass

        try:
            self.save_config_safe()
        except Exception as e:
            try:
                self._append_log(f"Не удалось сохранить список баз: {type(e).__name__}: {e}")
            except Exception:
                pass

        try:
            if hasattr(self, "base_store") and self.base_store is not None:
                self.base_store.clear()
        except Exception:
            pass

        try:
            self._load_bases_tree()
        except Exception as e:
            try:
                self._append_log(f"Не удалось обновить список баз: {type(e).__name__}: {e}")
            except Exception:
                pass

        try:
            self.base_tree.expand_all()
        except Exception:
            pass

        try:
            self.select_base_by_name_connect(new_name, new_connect)
        except Exception:
            pass

        try:
            self.base_tree.queue_draw()
        except Exception:
            pass

        # Принудительно прокачиваем GTK-события, чтобы список обновился сразу,
        # а не после полного перезапуска приложения.
        try:
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)
        except Exception:
            pass

        try:
            self._append_log(f"Список баз обновлен: {new_name} → группа «{new_group}»")
        except Exception:
            pass

        return False



    def show_add_base_error(self, title, message):
        try:
            self.show_error(title, message)
            return
        except Exception:
            pass

        dlg = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text=str(title),
        )
        dlg.format_secondary_text(str(message))
        dlg.run()
        dlg.destroy()

    def dialog_combo_text(self, combo):
        try:
            value = combo.get_active_text()

            if value:
                return str(value).strip()
        except Exception:
            pass

        try:
            model = combo.get_model()
            active_iter = combo.get_active_iter()

            if model is not None and active_iter is not None:
                row = model[active_iter]

                for value in row:
                    if value:
                        return str(value).strip()
        except Exception:
            pass

        try:
            return str(combo_text(combo) or "").strip()
        except Exception:
            return ""

    def add_base_action_key(self, dlg):
        text = self.dialog_combo_text(dlg.action).lower()

        if "групп" in text:
            return "group"

        if "шаблон" in text:
            return "template"

        if "пуст" in text or "без конфигурац" in text:
            return "empty"

        return "existing"

    def dialog_widget_row_children(self, widget):
        if widget is None:
            return []

        try:
            w = widget
            grid = None
            grid_child = None

            while w is not None:
                parent = w.get_parent()

                if parent is not None and isinstance(parent, Gtk.Grid):
                    grid = parent
                    grid_child = w
                    break

                w = parent

            if grid is None or grid_child is None:
                return [widget]

            top = grid.child_get_property(grid_child, "top-attach")
            result = []

            for child in grid.get_children():
                try:
                    if grid.child_get_property(child, "top-attach") == top:
                        result.append(child)
                except Exception:
                    pass

            return result or [widget]

        except Exception:
            return [widget]

    def dialog_row_set_visible(self, widget, visible):
        for child in self.dialog_widget_row_children(widget):
            try:
                if visible:
                    child.show()
                else:
                    child.hide()
            except Exception:
                pass

    def dialog_find_label(self, dlg, needles):
        needles = [str(x).lower() for x in needles]
        labels = []

        def walk(w):
            try:
                children = w.get_children()
            except Exception:
                return

            for child in children:
                try:
                    if isinstance(child, Gtk.Label):
                        txt = str(child.get_text() or "").lower()

                        if any(n in txt for n in needles):
                            labels.append(child)
                except Exception:
                    pass

                walk(child)

        walk(dlg)

        return labels[0] if labels else None

    def dialog_find_entry_after_label(self, dlg, label):
        if label is None:
            return None

        try:
            parent = label.get_parent()

            if isinstance(parent, Gtk.Grid):
                top = parent.child_get_property(label, "top-attach")

                for child in parent.get_children():
                    try:
                        if parent.child_get_property(child, "top-attach") != top:
                            continue

                        if isinstance(child, Gtk.Entry):
                            return child

                        if isinstance(child, Gtk.Box):
                            for sub in child.get_children():
                                if isinstance(sub, Gtk.Entry):
                                    return sub
                    except Exception:
                        pass
        except Exception:
            pass

        return None

    def dialog_find_button_after_label(self, dlg, label):
        if label is None:
            return None

        try:
            parent = label.get_parent()

            if isinstance(parent, Gtk.Grid):
                top = parent.child_get_property(label, "top-attach")

                for child in parent.get_children():
                    try:
                        if parent.child_get_property(child, "top-attach") != top:
                            continue

                        if isinstance(child, Gtk.Button):
                            return child

                        if isinstance(child, Gtk.Box):
                            for sub in child.get_children():
                                if isinstance(sub, Gtk.Button):
                                    return sub
                    except Exception:
                        pass
        except Exception:
            pass

        return None

    def configure_add_base_action_behavior(self, dlg):
        if getattr(dlg, "_u1c_add_actions_configured", False):
            self.apply_add_base_action_visibility(dlg)
            return

        dlg._u1c_add_actions_configured = True

        try:
            self.configure_base_dialog_group_picker(dlg, {})
        except Exception:
            pass

        dlg._u1c_connect_label = (
            self.dialog_find_label(dlg, ["путь / server", "url", "1cv8.1cd", "путь к папке"])
            or self.find_widget_label_for_entry(dlg.connect)
        )
        dlg._u1c_connect_browse_button = self.find_button_near_entry(dlg.connect)

        dlg._u1c_template_label = self.dialog_find_label(dlg, ["шаблон 1с", ".dt/.cf", "шаблон"])
        dlg._u1c_template_entry = self.dialog_find_entry_after_label(dlg, dlg._u1c_template_label) or getattr(dlg, "template", None)
        dlg._u1c_template_button = self.dialog_find_button_after_label(dlg, dlg._u1c_template_label)

        try:
            parent = dlg.connect.get_parent()
        except Exception:
            parent = None

        dlg.add_server_name = Gtk.Entry()
        dlg.add_server_name.set_placeholder_text("srvname")

        dlg.add_db_name = Gtk.Entry()
        dlg.add_db_name.set_placeholder_text("dbname")

        if parent is not None:
            try:
                parent.pack_start(dlg.add_server_name, True, True, 0)
                parent.pack_start(dlg.add_db_name, True, True, 0)
            except Exception:
                try:
                    parent.add(dlg.add_server_name)
                    parent.add(dlg.add_db_name)
                except Exception:
                    pass

        try:
            dlg.action.connect("changed", lambda *_: self.on_add_base_action_changed(dlg))
        except Exception:
            pass

        try:
            dlg.kind.connect("changed", lambda *_: self.apply_add_base_action_visibility(dlg))
        except Exception:
            pass

        try:
            dlg.name.connect("changed", lambda *_: self.add_dialog_default_base_path(dlg, only_if_empty=False))
        except Exception:
            pass

        self.apply_add_base_action_visibility(dlg)

    def on_add_base_action_changed(self, dlg):
        action = self.add_base_action_key(dlg)

        try:
            if action == "group":
                dlg.name.set_placeholder_text("Имя группы")
            else:
                dlg.name.set_placeholder_text("Имя базы")
        except Exception:
            pass

        if action in ("empty", "template"):
            try:
                if not dlg.name.get_text().strip() or dlg.name.get_text().strip() == "Новая база":
                    dlg.name.set_text("NewBase" if action == "template" else "Empty")
            except Exception:
                pass

            try:
                combo_set_values(dlg.kind, ["file", "server", "web"], "file")
            except Exception:
                pass

            self.add_dialog_default_base_path(dlg, only_if_empty=True)

        self.apply_add_base_action_visibility(dlg)

    def apply_add_base_action_visibility(self, dlg):
        action = self.add_base_action_key(dlg)
        kind = self.base_dialog_kind_text(dlg)
        is_group = action == "group"

        # В существующей базе шаблон тоже оставляем видимым:
        # если он заполнен, создаем новую file-базу и грузим .dt/.cf.
        template_visible = action in ("existing", "template")

        for attr in (
            "kind",
            "connect",
            "user",
            "password",
            "platform",
            "client_mode",
            "launch_params",
            "config_name",
            "config_synonym",
            "config_version",
            "update_code",
        ):
            try:
                self.dialog_row_set_visible(getattr(dlg, attr, None), not is_group)
            except Exception:
                pass

        try:
            self.dialog_row_set_visible(getattr(dlg, "_u1c_template_label", None), (not is_group and template_visible))
            self.dialog_row_set_visible(getattr(dlg, "_u1c_template_entry", None), (not is_group and template_visible))
            self.dialog_row_set_visible(getattr(dlg, "_u1c_template_button", None), (not is_group and template_visible))
        except Exception:
            pass

        if is_group:
            try:
                dlg.add_server_name.hide()
                dlg.add_db_name.hide()
            except Exception:
                pass
            return

        label = getattr(dlg, "_u1c_connect_label", None)
        browse = getattr(dlg, "_u1c_connect_browse_button", None)

        if kind == "server":
            if label is not None:
                label.set_text("srvname / dbname:")

            try:
                dlg.connect.hide()
                dlg.add_server_name.show()
                dlg.add_db_name.show()
            except Exception:
                pass

            if browse is not None:
                browse.hide()

        elif kind == "web":
            if label is not None:
                label.set_text("URL web-базы:")

            try:
                dlg.connect.show()
                dlg.add_server_name.hide()
                dlg.add_db_name.hide()
            except Exception:
                pass

            if browse is not None:
                browse.hide()

        else:
            if label is not None:
                if action in ("empty", "template") or (action == "existing" and self.add_dialog_template_value(dlg)):
                    label.set_text("Путь к папке новой базы:")
                else:
                    label.set_text("Путь к папке с 1Cv8.1CD:")

            try:
                dlg.connect.show()
                dlg.add_server_name.hide()
                dlg.add_db_name.hide()
            except Exception:
                pass

            if browse is not None:
                browse.show()

    def add_dialog_default_base_path(self, dlg, only_if_empty=True):
        action = self.add_base_action_key(dlg)
        kind = self.base_dialog_kind_text(dlg)

        if kind != "file" or action not in ("empty", "template"):
            return

        try:
            current = str(dlg.connect.get_text() or "").strip()
        except Exception:
            current = ""

        if only_if_empty and current:
            return

        try:
            name = str(dlg.name.get_text() or "").strip() or "NewBase"
            root = (self.settings.get("default_new_base_dir") or "/mnt/DataStore/bases") if hasattr(self, "settings") else "/mnt/DataStore/bases"
            dlg.connect.set_text(str(Path(root).expanduser() / safe_name(name)))
        except Exception:
            pass

    def add_dialog_connect_value(self, dlg):
        kind = self.base_dialog_kind_text(dlg)

        if kind == "server":
            srv = ""
            db = ""

            try:
                srv = str(dlg.add_server_name.get_text() or "").strip()
            except Exception:
                pass

            try:
                db = str(dlg.add_db_name.get_text() or "").strip()
            except Exception:
                pass

            if srv and db:
                return f"{srv}\\{db}"

            return srv or db

        return str(dlg.connect.get_text() or "").strip()

    def add_dialog_template_value(self, dlg):
        try:
            return str(dlg.template.get_text() or "").strip()
        except Exception:
            pass

        try:
            w = getattr(dlg, "_u1c_template_entry", None)

            if w is not None:
                return str(w.get_text() or "").strip()
        except Exception:
            pass

        return ""

    def suggest_name_from_template_path(self, template_path, kind=""):
        text = str(template_path or "").lower()

        if "accounting" in text or "бухгалтер" in text:
            return "AccountingDemo" if kind == "demo" else "AccountingBase"

        if "trade" in text or "торгов" in text:
            return "TradeDemo" if kind == "demo" else "TradeBase"

        if "hrm" in text or "zup" in text or "зарплат" in text:
            return "HRMDemo" if kind == "demo" else "HRMBase"

        return "DemoBase" if kind == "demo" else "NewBase"


    def select_dt_cf_file_for_dialog(self, dlg):
        """Обычный файловый выбор .dt/.cf для действия 'Добавить существующую базу'."""
        chooser = Gtk.FileChooserDialog(
            title="Выберите файл .dt или .cf",
            transient_for=dlg,
            action=Gtk.FileChooserAction.OPEN,
        )

        chooser.add_button("Отмена", Gtk.ResponseType.CANCEL)
        chooser.add_button("Выбрать", Gtk.ResponseType.OK)

        try:
            filt = Gtk.FileFilter()
            filt.set_name("Файлы 1С (*.dt, *.cf)")
            filt.add_pattern("*.dt")
            filt.add_pattern("*.DT")
            filt.add_pattern("*.cf")
            filt.add_pattern("*.CF")
            chooser.add_filter(filt)

            all_filter = Gtk.FileFilter()
            all_filter.set_name("Все файлы")
            all_filter.add_pattern("*")
            chooser.add_filter(all_filter)
        except Exception:
            pass

        for folder in [
            "/mnt/DataStore/Updater1C/1c-updates",
            str(Path.home() / "Загрузки"),
            str(Path.home() / "Downloads"),
            str(Path.home()),
        ]:
            try:
                if Path(folder).exists():
                    chooser.set_current_folder(folder)
                    break
            except Exception:
                pass

        if chooser.run() == Gtk.ResponseType.OK:
            path = chooser.get_filename() or ""

            if path:
                low = path.lower()

                if not (low.endswith(".dt") or low.endswith(".cf")):
                    self.show_add_base_error(
                        "Неверный файл шаблона",
                        "Для добавления существующей базы через шаблон выберите файл .dt или .cf."
                    )
                else:
                    try:
                        dlg.template.set_text(path)
                    except Exception:
                        pass

                    # Для существующей базы с заполненным .dt/.cf дальше будет создана новая file-база
                    # по указанному пути и загружен выбранный файл.
                    try:
                        self.apply_add_base_action_visibility(dlg)
                    except Exception:
                        pass

        chooser.destroy()

    def select_1c_template_for_dialog(self, dlg):
        selector = TemplateSelectDialogGtk(dlg)
        self.install_clipboard_paste_workaround(selector)

        try:
            if selector.run() == Gtk.ResponseType.OK and selector.selected_template:
                dlg.template.set_text(selector.selected_template)

                current_name = str(dlg.name.get_text() or "").strip()

                if not current_name or current_name in ("Новая база", "NewBase", "Empty"):
                    dlg.name.set_text(self.suggest_name_from_template_path(selector.selected_template, selector.selected_template_kind))

                try:
                    combo_set_values(dlg.kind, ["file", "server", "web"], "file")
                except Exception:
                    pass

                self.add_dialog_default_base_path(dlg, only_if_empty=False)

                if hasattr(dlg, "comment"):
                    kind_text = "Демонстрационная база" if selector.selected_template_kind == "demo" else "Чистая конфигурация"
                    extra = f"Шаблон 1С: {kind_text}; {selector.selected_template_name}; версия {selector.selected_template_version}"

                    try:
                        buf = dlg.comment.get_buffer()
                        start, end = buf.get_bounds()
                        cur = buf.get_text(start, end, True).strip()
                        buf.set_text((cur + "\n" + extra).strip() if cur else extra)
                    except Exception:
                        pass

                self.apply_add_base_action_visibility(dlg)

        finally:
            selector.destroy()

    def find_1c_executable_for_create(self, base):
        try:
            exe = select_platform_exe_for_base(base, self.settings if hasattr(self, "settings") else {}, "DESIGNER")

            if exe:
                return exe
        except Exception:
            pass

        platforms = find_platforms()

        if platforms:
            return next(iter(platforms.values()))

        raise RuntimeError("Не найден исполняемый файл платформы 1С")

    def run_1c_command_for_add_base(self, args, timeout=3600):
        import os
        import subprocess

        env = os.environ.copy()

        for lib in ("/usr/lib/x86_64-linux-gnu/libgcc_s.so.1", "/lib/x86_64-linux-gnu/libgcc_s.so.1"):
            if Path(lib).exists():
                old = env.get("LD_PRELOAD", "")
                env["LD_PRELOAD"] = lib if not old else lib + ":" + old
                break

        self._append_log("Команда 1С: " + " ".join(str(x) for x in args))

        proc = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            env=env,
        )

        out = proc.stdout or ""

        if out.strip():
            self._append_log(out.strip()[-4000:])

        if proc.returncode != 0:
            raise RuntimeError(f"Команда 1С завершилась с кодом {proc.returncode}")

    def create_infobase_from_add_dialog(self, dlg, base, template_path=""):
        import shutil

        kind = str(base.get("kind") or "file").strip().lower()

        if kind != "file":
            raise RuntimeError("Создание новой базы сейчас реализовано только для файловых баз.")

        path = Path(base.get("connect") or "").expanduser()

        if not str(path).strip():
            raise RuntimeError("Укажите путь к новой файловой базе.")

        exe = self.find_1c_executable_for_create(base)

        path.mkdir(parents=True, exist_ok=True)

        reports_dir = Path((self.settings.get("reports_dir") if hasattr(self, "settings") else "") or str(Path.home() / "1c-update-reports")).expanduser()
        reports_dir.mkdir(parents=True, exist_ok=True)

        out = reports_dir / f"{safe_name(base.get('name') or 'new_base')}_create_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

        self._append_log(f"Создание информационной базы: {base.get('name')}")

        self.run_1c_command_for_add_base(
            [exe, "CREATEINFOBASE", f"File=\"{path}\"", "/Out", str(out), "-NoTruncate"],
            timeout=900,
        )

        base["connect"] = str(path)

        if not template_path:
            self._append_log(f"Пустая база создана: {path}")
            return

        t = Path(template_path).expanduser()

        if not t.exists():
            raise RuntimeError(f"Шаблон 1С не найден: {t}")

        if t.is_dir():
            dt = (
                next(t.rglob("1Cv8new.dt"), None)
                or next(t.rglob("1cv8new.dt"), None)
                or next(t.rglob("1Cv8.dt"), None)
                or next(t.rglob("1cv8.dt"), None)
            )
            cf = next(t.rglob("1Cv8.cf"), None) or next(t.rglob("1cv8.cf"), None)
            onecd = next(t.rglob("1Cv8.1CD"), None)

            if dt:
                t = dt
            elif cf:
                t = cf
            elif onecd:
                shutil.copy2(onecd, path / "1Cv8.1CD")
                self._append_log(f"База создана копированием 1Cv8.1CD: {onecd}")
                return
            else:
                self._append_log("В папке шаблона не найден 1Cv8.dt / 1Cv8.cf / 1Cv8.1CD. Создана пустая база.")
                return

        suffix = t.suffix.lower()

        if suffix == ".dt":
            self._append_log(f"Восстановление .dt: {t}")
            self.run_1c_command_for_add_base(
                [exe, "DESIGNER", f"/F{path}", "/RestoreIB", str(t), "/Out", str(out), "-NoTruncate"],
                timeout=3600,
            )

        elif suffix == ".cf":
            self._append_log(f"Загрузка .cf: {t}")
            self.run_1c_command_for_add_base(
                [exe, "DESIGNER", f"/F{path}", "/LoadCfg", str(t), "/Out", str(out), "-NoTruncate"],
                timeout=3600,
            )

            self._append_log("Обновление структуры базы после .cf")
            self.run_1c_command_for_add_base(
                [exe, "DESIGNER", f"/F{path}", "/UpdateDBCfg", "-Dynamic+", "/Out", str(out), "-NoTruncate"],
                timeout=3600,
            )

        elif t.name == "1Cv8.1CD":
            shutil.copy2(t, path / "1Cv8.1CD")
            self._append_log(f"База создана копированием 1Cv8.1CD: {t}")

        else:
            raise RuntimeError("Поддерживаются шаблоны .dt, .cf, папка шаблона или 1Cv8.1CD")

    def build_base_from_add_dialog(self, dlg):
        name = str(dlg.name.get_text() or "").strip()

        if not name:
            raise RuntimeError("Не заполнено имя базы.")

        kind = self.base_dialog_kind_text(dlg) or "file"

        group = self.normalize_base_group_name(
            self.base_dialog_group_value(dlg) if hasattr(self, "base_dialog_group_value") else "Без группы"
        )

        connect = self.add_dialog_connect_value(dlg)
        user = str(dlg.user.get_text() or "").strip()

        client_caption = self.dialog_combo_text(dlg.client_mode) if hasattr(dlg, "client_mode") else "Тонкий клиент"
        client_mode = "thick" if client_caption == "Толстый клиент" else "thin"

        base = {
            "name": name,
            "group": group,
            "kind": kind,
            "type": kind,
            "connect": connect,
            "template": self.add_dialog_template_value(dlg),
            "user": user,
            "login": user,
            "username": user,
            "platform_version": str(dlg.platform.get_text() or "8.3").strip() or "8.3",
            "client_mode": client_mode,
            "launch_parameters": str(dlg.launch_params.get_text() or "").strip(),
            "config_name": str(dlg.config_name.get_text() or "").strip(),
            "config_synonym": str(dlg.config_synonym.get_text() or "").strip(),
            "config_version": str(dlg.config_version.get_text() or "").strip(),
            "update_program_name": str(dlg.update_code.get_text() or "").strip(),
        }

        if kind == "server":
            srv, db = self.parse_server_connect(connect)
            base["server_name"] = srv
            base["db_name"] = db

        if hasattr(dlg, "comment"):
            try:
                buf = dlg.comment.get_buffer()
                start, end = buf.get_bounds()
                base["comment"] = buf.get_text(start, end, True)
            except Exception:
                pass

        return base

    def add_group_from_dialog(self, dlg):
        name = str(dlg.name.get_text() or "").strip()

        if not name:
            raise RuntimeError("Не заполнено имя группы.")

        parent_group = self.normalize_base_group_name(
            self.base_dialog_group_value(dlg) if hasattr(self, "base_dialog_group_value") else "Без группы"
        )

        group_item = {
            "name": name,
            "group": "" if parent_group == "Без группы" else parent_group,
            "kind": "group",
            "type": "group",
            "is_group": True,
            "connect": "",
            "platform_version": "",
        }

        self.bases.append(group_item)
        self.config["bases"] = self.bases
        self.save_config_safe()
        self.refresh_bases_tree_after_add(group_item)
        self._append_log(f"Группа добавлена: {name}")

    def refresh_bases_tree_after_add(self, base):
        try:
            self.config["bases"] = self.bases
        except Exception:
            pass

        try:
            self.save_config_safe()
        except Exception:
            pass

        try:
            self.base_store.clear()
        except Exception:
            pass

        self._load_bases_tree()

        try:
            self.base_tree.expand_all()
        except Exception:
            pass

        try:
            if not base.get("is_group"):
                self.select_base_by_name_connect(base.get("name"), base.get("connect"))
        except Exception:
            pass

        try:
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)
        except Exception:
            pass

    def on_add_base(self, *_):
        dlg = BaseDialog(
            self,
            "Добавление базы — Обновлятор 1C Linux",
            base={},
            groups=self.available_base_groups("Без группы") if hasattr(self, "available_base_groups") else [],
            settings=self.settings if hasattr(self, "settings") else {},
        )

        try:
            self.configure_add_base_action_behavior(dlg)
        except Exception as e:
            self._append_log(f"Предупреждение: не удалось настроить действия добавления базы: {type(e).__name__}: {e}")

        try:
            self.normalize_base_dialog_response_buttons(dlg)
        except Exception:
            pass

        self.install_clipboard_paste_workaround(dlg)

        resp = dlg.run()

        if resp != Gtk.ResponseType.OK:
            dlg.destroy()
            self._append_log("Добавление базы отменено.")
            return

        try:
            action = self.add_base_action_key(dlg)

            if action == "group":
                self.add_group_from_dialog(dlg)
                dlg.destroy()
                return

            base = self.build_base_from_add_dialog(dlg)
            template_path = self.add_dialog_template_value(dlg)

            if action == "empty":
                self.create_infobase_from_add_dialog(dlg, base, "")

            elif action == "template":
                if not template_path:
                    raise RuntimeError("Не выбран шаблон 1С (.dt/.cf/папка).")

                self.create_infobase_from_add_dialog(dlg, base, template_path)

            elif action == "existing" and template_path:
                # Старая рабочая логика: существующая база + заполненный шаблон
                # значит создать новую file-базу по указанному пути и загрузить .dt/.cf/1Cv8.1CD.
                self.create_infobase_from_add_dialog(dlg, base, template_path)

            elif action == "existing":
                if not base.get("connect"):
                    raise RuntimeError("Не заполнен путь/server/base/URL.")

            self.bases.append(base)

            try:
                password = str(dlg.password.get_text() or "")

                if password:
                    self.set_base_password(base, password)
            except Exception as e:
                self._append_log(f"Не удалось сохранить пароль базы в keyring: {type(e).__name__}: {e}")

            self.config["bases"] = self.bases
            self.save_config_safe()
            self.refresh_bases_tree_after_add(base)

            dlg.destroy()

            self._append_log(f"База добавлена: {base.get('name')}")

        except Exception as e:
            try:
                dlg.destroy()
            except Exception:
                pass

            self._append_log(f"Не удалось добавить базу: {type(e).__name__}: {e}")
            self.show_add_base_error("Не удалось добавить базу", f"{type(e).__name__}: {e}")

    def read_clipboard_text_safe(self):
        """Читает буфер обмена с fallback для Wayland.

        GTK на Wayland иногда пишет:
        gdkselection-wayland.c: error reading selection buffer: Операция была отменена.
        Поэтому сначала пробуем wl-paste/xclip/xsel, потом уже Gtk.Clipboard.
        """
        import shutil
        import subprocess

        commands = []

        if shutil.which("wl-paste"):
            commands.append(["wl-paste", "-n"])
            commands.append(["wl-paste", "--no-newline"])

        if shutil.which("xclip"):
            commands.append(["xclip", "-selection", "clipboard", "-o"])

        if shutil.which("xsel"):
            commands.append(["xsel", "--clipboard", "--output"])

        for cmd in commands:
            try:
                proc = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    timeout=2,
                )

                if proc.returncode == 0 and proc.stdout:
                    return proc.stdout
            except Exception:
                pass

        try:
            from gi.repository import Gtk, Gdk
            clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            value = clipboard.wait_for_text()

            if value:
                return value
        except Exception:
            pass

        return ""

    def paste_text_into_entry_safe(self, entry, text):
        text = "" if text is None else str(text)

        if text == "":
            return False

        try:
            bounds = entry.get_selection_bounds()

            has_selection = False
            start = end = 0

            if isinstance(bounds, tuple):
                if len(bounds) == 3:
                    has_selection, start, end = bounds
                elif len(bounds) == 2:
                    has_selection = True
                    start, end = bounds

            current = str(entry.get_text() or "")

            if has_selection:
                left = min(int(start), int(end))
                right = max(int(start), int(end))
                new_text = current[:left] + text + current[right:]
                pos = left + len(text)
            else:
                pos0 = int(entry.get_position())
                if pos0 < 0:
                    pos0 = len(current)
                new_text = current[:pos0] + text + current[pos0:]
                pos = pos0 + len(text)

            entry.set_text(new_text)
            entry.set_position(pos)
            return True

        except Exception:
            try:
                entry.set_text(text)
                entry.set_position(len(text))
                return True
            except Exception:
                return False

    def safe_paste_into_entry(self, entry):
        text = self.read_clipboard_text_safe()

        if not text:
            try:
                self._append_log("Буфер обмена пуст или недоступен.")
            except Exception:
                pass
            return True

        self.paste_text_into_entry_safe(entry, text)
        return True

    def connect_entry_clipboard_fallback(self, entry):
        try:
            from gi.repository import Gtk, Gdk
        except Exception:
            return

        try:
            if getattr(entry, "_u1c_clipboard_fallback_connected", False):
                return

            entry._u1c_clipboard_fallback_connected = True
        except Exception:
            pass

        def on_key_press(widget, event):
            try:
                state = event.state & Gtk.accelerator_get_default_mod_mask()
                ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
                shift = bool(state & Gdk.ModifierType.SHIFT_MASK)

                key_name = (Gdk.keyval_name(event.keyval) or "").lower()

                if (ctrl and key_name == "v") or (shift and key_name == "insert"):
                    return self.safe_paste_into_entry(widget)

            except Exception:
                pass

            return False

        def on_paste_clipboard(widget):
            try:
                widget.stop_emission_by_name("paste-clipboard")
            except Exception:
                pass

            self.safe_paste_into_entry(widget)
            return True

        try:
            entry.connect("key-press-event", on_key_press)
        except Exception:
            pass

        try:
            entry.connect("paste-clipboard", on_paste_clipboard)
        except Exception:
            pass

    def install_clipboard_paste_workaround(self, root=None):
        """Подключает устойчивую вставку ко всем Gtk.Entry внутри root."""
        try:
            from gi.repository import Gtk
        except Exception:
            return False

        if root is None:
            root = self

        def walk(widget):
            try:
                if isinstance(widget, Gtk.Entry):
                    self.connect_entry_clipboard_fallback(widget)
            except Exception:
                pass

            try:
                children = widget.get_children()
            except Exception:
                children = []

            for child in children:
                walk(child)

        try:
            walk(root)
        except Exception:
            pass

        return False

    def build_1c_launch_args(self, mode="ENTERPRISE"):
        import shlex

        vals = self.selected_base_values()

        if not vals or vals.get("is_group"):
            raise RuntimeError("Выбрана не база, а группа или пустая строка.")

        base = self.selected_base_config(vals)

        name = vals.get("name") or base.get("name") or ""
        kind = str(vals.get("kind") or base.get("kind") or "").lower()

        # Для запуска берем путь/URL из строки дерева, потому после DnD индекс базы мог съехать.
        connect = str(vals.get("connect") or base.get("connect") or "").strip()

        if not connect:
            raise RuntimeError(f"У базы '{name}' не заполнен путь / сервер / URL.")

        lower_connect = connect.lower()
        is_web = kind == "web" or lower_connect.startswith("http://") or lower_connect.startswith("https://")

        if is_web:
            exe = self.resolve_web_1c_executable(vals, base)
        else:
            exe = self.resolve_1c_executable(
                vals,
                designer=(mode.upper() == "DESIGNER"),
                prefer_thin=(mode.upper() == "ENTERPRISE"),
            )

        if not exe:
            raise RuntimeError("Не найден исполняемый файл 1С. Для web-базы нужен прямой 1cv8c.")

        exe_name = Path(exe).name.lower()

        if is_web and exe_name != "1cv8c":
            raise RuntimeError(f"Для web-базы нужен 1cv8c, а выбран: {exe}")

        args = [exe, mode.upper()]

        user, password = self.get_launch_credentials_for_selected_base(base)

        if is_web:
            # Для 1С web-базы используем классический формат ключей:
            # 1cv8c ENTERPRISE /WShttps://... /Nuser /Ppassword
            args.append("/WS" + connect)

            if user:
                args.append("/N" + user)

            if password:
                args.append("/P" + password)

        elif kind == "server" or ("\\" in connect and not connect.startswith("/")):
            args.append("/S" + connect)

            if user:
                args.append("/N" + user)

            if password:
                args.append("/P" + password)

        else:
            args.append("/F" + connect)

            if user:
                args.append("/N" + user)

            if password:
                args.append("/P" + password)

        launch_params = str(
            base.get("launch_parameters")
            or base.get("launch_params")
            or ""
        ).strip()

        if launch_params:
            try:
                args.extend(shlex.split(launch_params))
            except Exception:
                args.append(launch_params)

        return args



    def launch_selected_base(self, mode="ENTERPRISE"):
        import os
        import re
        import time
        import subprocess
        from pathlib import Path

        vals = self.selected_base_values()

        if not vals:
            self._append_log("Не выбрана база для запуска.")
            return

        if vals.get("is_group"):
            self._append_log("Выбрана группа. Для запуска выбери конкретную базу.")
            return

        launch_key = f"{vals.get('name') or ''}|{vals.get('connect') or ''}|{mode}"

        guard = getattr(self, "_last_launch_guard", {"key": "", "time": 0.0})
        now = time.monotonic()

        if guard.get("key") == launch_key and now - float(guard.get("time") or 0) < 1.5:
            self._append_log("Повторный запуск проигнорирован: защита от двойного события.")
            return

        self._last_launch_guard = {"key": launch_key, "time": now}

        try:
            self.save_current_credentials_to_selected_base(silent=True)
        except Exception as e:
            self._append_log(f"Предупреждение: не удалось сохранить логин/пароль перед запуском: {type(e).__name__}: {e}")

        name = vals.get("name") or "-"
        args = self.build_1c_launch_args(mode)

        safe_args = []
        user_filled = False
        password_filled = False

        for arg in args:
            s = str(arg)

            if s.startswith("/N") and len(s) > 2:
                user_filled = True

            if s.startswith("/P") and len(s) > 2:
                password_filled = True
                s = "/P***"

            if "Pwd=" in s:
                password_filled = True
                s = re.sub(r'Pwd="[^"]*"', 'Pwd="***"', s)

            safe_args.append(s)

        try:
            env = self.onec_launch_env()
        except Exception:
            env = os.environ.copy()

        command_text = " ".join(str(x) for x in safe_args)

        self._append_log("")
        self._append_log(f"=== Запуск 1С: {name} ===")
        self._append_log("Команда: " + command_text)
        self._append_log(f"Пользователь передан: {'да' if user_filled else 'нет'}; пароль передан: {'да' if password_filled else 'нет'}")

        if env.get("LD_PRELOAD"):
            self._append_log("LD_PRELOAD: " + env.get("LD_PRELOAD", ""))

        try:
            Path("/tmp/updater1c_last_launch_command.txt").write_text(
                "SAFE_COMMAND=" + command_text + "\\n"
                + "ARGS_REPR=" + repr(args) + "\\n"
                + "USER_FILLED=" + str(user_filled) + "\\n"
                + "PASSWORD_FILLED=" + str(password_filled) + "\\n"
                + "LD_PRELOAD=" + str(env.get("LD_PRELOAD", "")) + "\\n",
                encoding="utf-8",
            )
        except Exception:
            pass

        subprocess.Popen(args, close_fds=True, env=env)
        self._append_log("Процесс 1С запущен.")

    def on_run_base_stub(self, *_):
        try:
            self.launch_selected_base("ENTERPRISE")
        except Exception as e:
            self._append_log(f"Не удалось запустить базу: {type(e).__name__}: {e}")


    def on_designer_base_stub(self, *_):
        try:
            self.launch_selected_base("DESIGNER")
        except Exception as e:
            self._append_log(f"Не удалось запустить конфигуратор: {type(e).__name__}: {e}")

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
            self.save_bases_order_from_tree()
            self.update_bases_status()
            self._append_log(f"Удалено из списка: {name}")


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

        inner.append_page(self._build_its_tab(), Ui.label("ИТС"))

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
        btn_save_settings = Ui.button("сохранить настройки")
        btn_save_settings.connect("clicked", self.on_save_settings_real)
        buttons.pack_start(btn_save_settings, False, False, 0)

        buttons.pack_start(Ui.button("Найти платформы 1С"), False, False, 0)


    def _build_its_tab(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_border_width(10)

        grid = Gtk.Grid()
        grid.set_column_spacing(12)
        grid.set_row_spacing(12)
        box.pack_start(grid, False, False, 0)

        self.its_login_entry = Ui.entry(self.settings.get("its_login", ""))

        self.its_password_entry = Ui.entry("")
        self.its_password_entry.set_visibility(False)
        if self.settings.get("its_password_saved"):
            self.its_password_entry.set_placeholder_text("Пароль сохранен в системном хранилище")
        else:
            self.its_password_entry.set_placeholder_text("Введите пароль ИТС")

        grid.attach(Ui.label("Логин ИТС/users.v8.1c.ru:"), 0, 0, 1, 1)
        grid.attach(self.its_login_entry, 1, 0, 1, 1)
        grid.attach(Ui.label("Пароль ИТС:"), 0, 1, 1, 1)
        grid.attach(self.its_password_entry, 1, 1, 1, 1)

        hint = Ui.label("<small>Пароль сохраняется в системном хранилище Linux. В config.json пароль открытым текстом не пишется.</small>")
        grid.attach(hint, 1, 2, 1, 1)

        return box



    def save_its_settings_to_store(self):
        login = self.its_login_entry.get_text().strip() if hasattr(self, "its_login_entry") else self.settings.get("its_login", "")
        password = self.its_password_entry.get_text() if hasattr(self, "its_password_entry") else ""

        self.settings["its_login"] = login

        if password:
            saved = False

            try:
                ensure_project_root_on_path()
                from secret_store import set_secret, its_password_account
                set_secret(its_password_account(), password)
                saved = True
            except Exception as e:
                self._append_log(f"Внимание: не удалось сохранить ИТС пароль через secret_store: {type(e).__name__}: {e}")

            if not saved:
                try:
                    import keyring
                    keyring.set_password("updater1c-linux", "its_password", password)
                    saved = True
                except Exception as e:
                    self._append_log(f"Внимание: не удалось сохранить ИТС пароль через keyring: {type(e).__name__}: {e}")

            if saved:
                self.settings["its_password_saved"] = True
                self.settings["its_password"] = ""

                try:
                    self.its_password_entry.set_text("")
                    self.its_password_entry.set_placeholder_text("Пароль сохранен в системном хранилище")
                except Exception:
                    pass

                self._append_log("ИТС пароль сохранен в системном хранилище.")
            else:
                self._append_log("ОШИБКА: ИТС пароль не удалось сохранить в системное хранилище.")

        self.config["settings"] = self.settings
        save_json(self.config_path, self.config)


    def get_its_password_for_update(self):
        try:
            if hasattr(self, "its_password_entry"):
                typed = self.its_password_entry.get_text() or ""
                if typed:
                    return typed
        except Exception:
            pass

        direct = self.settings.get("its_password") or ""
        if direct:
            return direct

        try:
            ensure_project_root_on_path()
            from secret_store import get_secret, its_password_account
            value = get_secret(its_password_account()) or ""
            if value:
                return value
        except Exception:
            pass

        for service, account in [
            ("updater1c-linux", "its_password"),
            ("Обновлятор 1С", "its_password"),
            ("updater1c", "its_password"),
        ]:
            try:
                import keyring
                value = keyring.get_password(service, account) or ""
                if value:
                    return value
            except Exception:
                pass

        return ""

    def on_save_settings_real(self, *_):
        self.save_its_settings_to_store()
        self.config["settings"] = self.settings
        save_json(self.config_path, self.config)
        self._append_log("Настройки сохранены.")


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
            ("◻ Скачать платформу", self.on_download_platform),
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

        b_reports = Ui.button("Открыть папку с отчетами")
        b_reports.connect("clicked", self.open_reports_dir)
        links.pack_start(b_reports, False, False, 0)

        links.pack_start(Ui.button("Открыть текущий лог"), False, False, 0)

        b_platforms = Ui.button("Открыть папку с платформами")
        b_platforms.connect("clicked", self.open_platform_download_dir)
        links.pack_start(b_platforms, False, False, 0)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        right.set_size_request(420, -1)
        body.pack_start(right, False, False, 0)

        current = Gtk.Grid()
        current.set_column_spacing(12)
        current.set_row_spacing(6)
        right.pack_start(current, False, False, 0)
        current.attach(Ui.label("<b>Текущая операция</b>"), 0, 0, 2, 1)

        self.current_operation_labels = {}
        for i, label in enumerate(["База:", "Релиз:", "Шаг:", "Действие:", "Режим:", "Статус:", "PID процесса:", "Время запуска:"], 1):
            current.attach(Ui.label(label), 0, i, 1, 1)
            value = Ui.label("-")
            value.set_xalign(0)
            key = label.replace(":", "")
            self.current_operation_labels[key] = value
            current.attach(value, 1, i, 1, 1)

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

        self.report_progress = Gtk.ProgressBar()
        self.report_progress.set_show_text(True)
        self.report_progress.set_text("Нет активного скачивания")
        tab.pack_start(self.report_progress, False, False, 0)

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


    def switch_to_report_tab(self):
        try:
            self.notebook.set_current_page(3)
        except Exception:
            pass

    def _append_log_safe(self, text):
        GLib.idle_add(self._append_log, text)

    def run_in_background(self, title, work_func):
        self.switch_to_report_tab()
        self._append_log(f"=== {title} ===")

        def runner():
            try:
                work_func(lambda s: GLib.idle_add(self._append_log, s))
            except Exception as e:
                GLib.idle_add(self._append_log, f"ОШИБКА операции: {type(e).__name__}: {e}")

        t = threading.Thread(target=runner, daemon=True)
        t.start()




    def set_current_operation(self, **kwargs):
        try:
            mapping = {
                "base": "База",
                "release": "Релиз",
                "step": "Шаг",
                "action": "Действие",
                "mode": "Режим",
                "status": "Статус",
                "pid": "PID процесса",
                "started": "Время запуска",
            }

            labels = getattr(self, "current_operation_labels", {})

            for key, value in kwargs.items():
                label_key = mapping.get(key, key)
                if label_key in labels:
                    labels[label_key].set_text(str(value if value not in (None, "") else "-"))
        except Exception:
            pass

    def set_report_progress(self, percent, text=""):
        try:
            value = max(0, min(100, int(percent or 0)))
            self.report_progress.set_fraction(value / 100)
            self.report_progress.set_text(text or f"{value}%")
        except Exception:
            pass

    def open_reports_dir(self, *_):
        try:
            path = self.settings.get("reports_dir") or self.settings.get("report_dir") or str(Path.home() / "1c-update-reports")
            open_folder_external(path)
        except Exception as e:
            self._append_log(f"Не удалось открыть папку отчетов: {type(e).__name__}: {e}")

    def open_platform_download_dir(self, *_):
        try:
            path = default_platform_download_dir_from_config(self.config)
            open_folder_external(path)
        except Exception as e:
            self._append_log(f"Не удалось открыть папку платформ: {type(e).__name__}: {e}")

    def _append_log(self, text):
        buf = self.report.get_buffer()
        end = buf.get_end_iter()
        buf.insert(end, text + "\n")


    def gtk_operation_target_indices(self):
        """Если есть отмеченные базы — берем их. Иначе текущую выбранную."""
        result = []

        def walk(parent_iter=None):
            child = self.base_store.iter_children(parent_iter)
            while child is not None:
                is_group = self.base_store.iter_has_child(child)
                if is_group:
                    walk(child)
                else:
                    checked = bool(self.base_store[child][0])
                    try:
                        idx = int(self.base_store[child][8])
                    except Exception:
                        idx = -1
                    if checked and 0 <= idx < len(self.bases):
                        result.append(idx)
                child = self.base_store.iter_next(child)

        walk(None)

        if result:
            return result

        vals = self.selected_base_values()
        if vals and not vals.get("is_group"):
            idx = vals.get("index", -1)
            if 0 <= idx < len(self.bases):
                return [idx]

        return []

    def on_download_updates_real(self, *_):
        self.switch_to_report_tab()
        indexes = self.gtk_operation_target_indices()
        if not indexes:
            self._append_log("ОШИБКА: не выбраны базы для скачивания обновлений.")
            return

        login = self.settings.get("its_login") or ""
        password = self.get_its_password_for_update()

        if not login or not password:
            self._append_log("ОШИБКА: не заполнены логин/пароль ИТС. Проверьте вкладку Настройки программы → ИТС.")
            return

        api = GtkOneCUpdateApi(login, password)

        self._append_log("=== Скачивание обновлений конфигураций ===")
        self._append_log(f"Баз к обработке: {len(indexes)}")

        for pos, idx in enumerate(indexes, 1):
            if not (0 <= idx < len(self.bases)):
                continue

            base = self.bases[idx]
            name = base.get("name") or f"base_{idx}"
            program = base.get("update_program_name") or ""
            version = base.get("config_version") or ""
            platform_version = base.get("platform_version") or "8.3"

            if not program:
                hay = " ".join([
                    str(base.get("config_name") or ""),
                    str(base.get("config_synonym") or ""),
                    str(base.get("name") or ""),
                ]).lower()

                if "бухгалтер" in hay or "accounting" in hay:
                    program = "Accounting"
                elif (
                    "управление торговлей" in hay
                    or "управлениеторговлей" in hay.replace(" ", "")
                    or "торговл" in hay
                    or "trade" in hay
                ):
                    program = "Trade"
                elif "документооборот" in hay or "document" in hay or "docmng" in hay:
                    program = "DocumentManagement"
                elif "зарплата" in hay or "зуп" in hay or "hrm" in hay or "salary" in hay:
                    program = "HRM"
                elif "управление нашей фирмой" in hay or "унф" in hay or "smallbusiness" in hay:
                    program = "SmallBusiness"
                elif "erp" in hay:
                    program = "ERP"

                if program:
                    base["update_program_name"] = program
                    try:
                        self.config["bases"] = self.bases
                        save_json(self.config_path, self.config)
                    except Exception:
                        pass

            self._append_log("")
            self._append_log(f"[{pos}/{len(indexes)}]")
            self._append_log(f"--- Скачивание обновлений для базы: {name} ---")
            self._append_log(f"Текущая версия конфигурации: {version or '-'}")
            self._append_log(f"Код программы обновлений: {program or '-'}")
            self._append_log(f"Версия платформы для update-api: {platform_version or '8.3'}")

            if not program or not version:
                self._append_log("ПРОПУСК: не заполнены код программы обновлений или версия конфигурации. Сначала выполните Проверить настройки.")
                continue

            try:
                info = api.check_conf_update(program, version, platform_version)

                if not info:
                    self._append_log("Обновления не найдены или update-api не вернул configurationUpdateResponse.")
                    continue

                target_version = (
                    info.get("targetVersionNumber")
                    or info.get("newVersionNumber")
                    or info.get("versionNumber")
                    or info.get("configurationVersion")
                    or ""
                )

                platform_required = (
                    info.get("platformVersion")
                    or info.get("requiredPlatformVersion")
                    or info.get("minimalPlatformVersion")
                    or ""
                )

                size = info.get("size") or info.get("totalSize") or info.get("updateSize") or ""
                upgrade_sequence = info.get("upgradeSequence") or []

                program_uin = (
                    info.get("programVersionUin")
                    or info.get("configurationVersionUin")
                    or info.get("uin")
                    or ""
                )

                self._append_log(f"Целевая версия: {target_version or '-'}")
                self._append_log(f"Минимальная/требуемая платформа по ответу API: {platform_required or '-'}")
                if size:
                    self._append_log(f"Размер по ответу API: {size}")
                self._append_log(f"Последовательность обновлений: {upgrade_sequence}")

                program_root = update_program_dir(self.settings, program)
                metadata_dir = program_root / "_metadata"
                metadata_dir.mkdir(parents=True, exist_ok=True)

                (metadata_dir / f"update_info_{now_stamp()}.json").write_text(
                    json.dumps({"info": info}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

                if not upgrade_sequence:
                    self._append_log("ПРОПУСК: update-api не вернул upgradeSequence.")
                    continue

                if not program_uin:
                    self._append_log("ПРОПУСК: update-api не вернул programVersionUin/configurationVersionUin.")
                    self._append_log(json.dumps(info, ensure_ascii=False, indent=2)[:3000])
                    continue

                files = api.get_conf_download_data(upgrade_sequence, program_uin)

                (metadata_dir / f"download_data_{now_stamp()}.json").write_text(
                    json.dumps({"info": info, "files": files}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

                if not files:
                    self._append_log("ПРОПУСК: update-api не вернул configurationUpdateDataList.")
                    continue

                for n, item in enumerate(files, 1):
                    release = update_item_release_version(item, target_version) or target_version or f"step_{n}"
                    release_dir = program_root / safe_name(release)

                    url = update_item_url(item) or find_download_url_recursive(item)
                    if not url:
                        self._append_log(f"ПРОПУСК: для файла {n} нет URL скачивания.")
                        self._append_log(json.dumps(item, ensure_ascii=False, indent=2)[:2000])
                        continue

                    filename = update_item_filename(item, url, fallback=f"{safe_name(release)}.zip")
                    dest = release_dir / filename

                    self._append_log(f"Скачивание {n}/{len(files)}:")
                    self._append_log(f"Версия релиза: {release}")
                    self._append_log(f"Имя файла: {filename}")
                    self._append_log(f"Папка релиза: {release_dir}")
                    _old_url = url
                    url = u1c_fix_1c_public_download_url(url)
                    if url != _old_url:
                        self._append_log(f"URL нормализован: {url}")
                    self._append_log(f"URL: {url}")

                    _old_url = url
                    url = u1c_normalize_first_download_url(url)
                    if url != _old_url:
                        self._append_log(f"URL нормализован: {url}")
                    if dest.exists() and dest.stat().st_size > 0:
                        self._append_log(f"Файл уже есть: {dest}")
                    else:
                        download_url_to_file(url, dest, self._append_log, login, password)

                self._append_log(f"Скачивание обновлений: база {name} завершено")

            except Exception as e:
                self._append_log(f"ОШИБКА скачивания обновлений для {name}: {type(e).__name__}: {e}")

    def on_clear_cache_real(self, *_):
        dlg = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text="Очистить кэш 1С?",
        )
        dlg.format_secondary_text(
            "Будут удалены пользовательские кэш-каталоги 1С в домашней папке.\n"
            "Информационные базы и настройки списка баз не удаляются."
        )
        response = dlg.run()
        dlg.destroy()

        if response != Gtk.ResponseType.OK:
            self._append_log("Очистка кэша отменена.")
            return

        candidates = [
            Path.home() / ".1cv8" / "1C" / "1cv8",
            Path.home() / ".1cv8" / "1C" / "1Cv8",
            Path.home() / ".cache" / "1C" / "1cv8",
            Path.home() / ".cache" / "1C" / "1Cv8",
        ]

        self._append_log("=== Очистка кэша 1С ===")

        for folder in candidates:
            if not folder.exists():
                self._append_log(f"Нет папки: {folder}")
                continue

            removed = 0
            for child in folder.iterdir():
                try:
                    if child.is_dir():
                        shutil.rmtree(child)
                    else:
                        child.unlink()
                    removed += 1
                except Exception as e:
                    self._append_log(f"Не удалось удалить {child}: {e}")

            self._append_log(f"Очищено элементов: {removed} в {folder}")

        self._append_log("Очистка кэша 1С завершена.")

    def on_cancel_operation_real(self, *_):
        self._append_log("Отмена: активная фоновая операция GTK сейчас не запущена.")


    def on_stub(self, *_):
        self._append_log("GTK preview: обработчик будет подключен на следующем этапе переноса логики.")

    def on_auto_update(self, *_):
        dlg = AutoUpdateDialog(self)
        dlg.run()
        dlg.destroy()

    def on_download_platform(self, *_):
        dlg = PlatformDownloadDialog(self)
        response = dlg.run()
        if response == Gtk.ResponseType.OK:
            dlg.on_download_queue()
        dlg.destroy()


def main():
    MainWindow()
    Gtk.main()


if __name__ == "__main__":
    main()
