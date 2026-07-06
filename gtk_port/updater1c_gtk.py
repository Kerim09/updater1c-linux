
# Подключается рано: скрывает только технические пакетные команды 1С через xvfb-run.
import u1c_headless
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

# UPDATER1C_DATETIME_COMPAT_BEGIN
# Совместимость: поддерживает и datetime.now(), и datetime.datetime.now().
import datetime as _u1c_datetime_module

class _U1CDateTimeCompat:
    datetime = _u1c_datetime_module.datetime
    date = _u1c_datetime_module.date
    time = _u1c_datetime_module.time
    timedelta = _u1c_datetime_module.timedelta
    timezone = _u1c_datetime_module.timezone

    def now(self, *args, **kwargs):
        return _u1c_datetime_module.datetime.now(*args, **kwargs)

    def today(self, *args, **kwargs):
        return _u1c_datetime_module.datetime.today(*args, **kwargs)

    def strptime(self, *args, **kwargs):
        return _u1c_datetime_module.datetime.strptime(*args, **kwargs)

    def fromtimestamp(self, *args, **kwargs):
        return _u1c_datetime_module.datetime.fromtimestamp(*args, **kwargs)

    def __getattr__(self, name):
        if hasattr(_u1c_datetime_module.datetime, name):
            return getattr(_u1c_datetime_module.datetime, name)
        return getattr(_u1c_datetime_module, name)

datetime = _U1CDateTimeCompat()
# UPDATER1C_DATETIME_COMPAT_END






APP_NAME = "Обновлятор 1C Linux"
APP_VERSION = "1.2.8"
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
    """Не меняем URL, который вернул update-api.

    Для обновлений конфигураций 1С update-api может вернуть:
      /public/file/tmplts/get/<uuid>

    Это правильный endpoint для шаблонов/обновлений.
    Нельзя принудительно менять его на:
      /public/file/get/<uuid>

    Иначе dl04.1c.ru может вернуть HTML-страницу вместо файла обновления.
    """
    return str(url or "").strip()



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
    """Возвращаем исходный URL без подмены endpoint.

    Раньше здесь принудительно предпочитался /public/file/get/,
    из-за этого ссылка /public/file/tmplts/get/ превращалась в неправильную.
    """
    return str(url or "").strip()



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




def download_url_to_file(
    url: str,
    dest: Path,
    log_func,
    login: str = "",
    password: str = "",
    progress_func=None,
    progress_label: str = "",
):
    """Скачать файл обновления 1С через requests с Basic Auth.

    Проценты не пишем в текстовый лог. Прогресс передаём в progress_func,
    чтобы GTK показывал его через ProgressBar.
    """
    import requests
    import time

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    tmp = dest.with_suffix(dest.suffix + ".part")
    if tmp.exists():
        tmp.unlink()

    headers = {
        "User-Agent": "1C+Enterprise/8.3",
        "Accept": "*/*",
    }

    auth = (login, password) if (login or password) else None

    def progress(percent: int, done: int = 0, total: int = 0, text: str = ""):
        if progress_func:
            try:
                progress_func(percent, done, total, text)
            except Exception:
                pass

    log_func(f"Скачивание через requests: {url}")
    progress(0, 0, 0, progress_label or "Скачивание: подготовка")

    with requests.get(
        url,
        headers=headers,
        auth=auth,
        timeout=120,
        stream=True,
        allow_redirects=True,
    ) as r:
        content_type = (r.headers.get("Content-Type") or "").lower()
        total = int(r.headers.get("Content-Length") or 0)

        log_func(f"HTTP статус: {r.status_code}")
        log_func(f"Итоговый URL: {r.url}")
        log_func(f"Content-Type: {content_type or '-'}")
        log_func(f"Content-Length: {total or '-'}")

        if r.status_code >= 400:
            head = ""
            try:
                head = r.text[:1000]
            except Exception:
                pass
            progress(0, 0, total, "Ошибка скачивания")
            raise RuntimeError(
                f"HTTP ошибка скачивания: {r.status_code} {r.reason}\n"
                f"URL: {url}\n"
                f"Итоговый URL: {r.url}\n"
                f"Начало ответа:\n{head}"
            )

        if "text/html" in content_type:
            head = ""
            try:
                head = next(r.iter_content(1000), b"").decode("utf-8", errors="replace")
            except Exception:
                pass
            progress(0, 0, total, "Ошибка: получена HTML-страница")
            raise RuntimeError(
                "Вместо файла обновления сервер вернул HTML-страницу. "
                f"Content-Type: {content_type}\n"
                f"URL: {url}\n"
                f"Итоговый URL: {r.url}\n"
                f"Начало ответа:\n{head}"
            )

        done = 0
        last_percent = -1
        last_ui_update = 0.0

        with tmp.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue

                f.write(chunk)
                done += len(chunk)

                if total:
                    percent = int(done * 100 / total)
                else:
                    percent = 0

                now = time.monotonic()

                # В UI обновляем часто, но без спама в лог.
                if percent != last_percent and (now - last_ui_update >= 0.15 or percent >= 100):
                    mb_done = done / 1024 / 1024
                    mb_total = total / 1024 / 1024 if total else 0

                    if total:
                        text = f"{progress_label or 'Скачивание'}: {percent}% — {mb_done:.1f}/{mb_total:.1f} МБ"
                    else:
                        text = f"{progress_label or 'Скачивание'}: {mb_done:.1f} МБ"

                    progress(percent, done, total, text)
                    last_percent = percent
                    last_ui_update = now

    tmp.rename(dest)

    validate_downloaded_update_file(dest)

    progress(100, dest.stat().st_size, dest.stat().st_size, f"{progress_label or 'Скачивание'}: файл получен")
    log_func(f"Файл скачан: {dest}")
    return dest


def u1c_config_version_folder(version: str) -> str:
    return str(version or "").strip().replace(".", "_").replace("-", "_")


def u1c_config_template_paths(item: dict, program: str, release: str):
    result = []

    def add(path_value):
        path_value = str(path_value or "").strip().replace("\\", "/").strip("/")
        if not path_value:
            return

        path_value = re.sub(r"^tmplts/", "", path_value, flags=re.I)
        path_value = re.sub(r"^1[cс]/", "1c/", path_value, flags=re.I)

        if not path_value.lower().startswith("1c/"):
            path_value = "1c/" + path_value

        if path_value not in result:
            result.append(path_value)

    for key in ("templatePath", "path", "folder", "catalog", "catalogPath"):
        if isinstance(item, dict):
            add(item.get(key))

    program = safe_name(program or "").strip()
    release_folder = u1c_config_version_folder(release)

    if program and release_folder:
        add(f"1c/{program}/{release_folder}")

    return result


def u1c_config_candidate_filenames(item: dict):
    result = []

    def add(name):
        name = str(name or "").strip().replace("\\", "/").split("/")[-1]
        if not name:
            return
        if "." not in name:
            return
        low = name.lower()
        if low in ("get", "download"):
            return
        if name not in result:
            result.append(name)

    if isinstance(item, dict):
        for key in ("updateFileName", "fileName", "name", "filename"):
            add(item.get(key))

    for name in ("1cv8.cfu", "1Cv8.cfu", "1CV8.CFU", "1cv8.zip", "1Cv8.zip", "1CV8.ZIP"):
        add(name)

    return result


def u1c_config_download_candidate_urls(item: dict, program: str, release: str, primary_url: str):
    result = []

    def add(url):
        url = str(url or "").strip()
        if url and url not in result:
            result.append(url)

    # Сначала пробуем URL из update-api и варианты endpoint.
    for url in u1c_download_url_variants(primary_url):
        add(url)

    # Потом прямой старый механизм downloads.v8.1c.ru/tmplts.
    for template_path in u1c_config_template_paths(item, program, release):
        for filename in u1c_config_candidate_filenames(item):
            add(f"https://downloads.v8.1c.ru/tmplts/{template_path}/{filename}")
            add(f"http://downloads.v8.1c.ru/tmplts/{template_path}/{filename}")

    return result


def download_config_update_file_with_fallback(item: dict, program: str, release: str, primary_url: str, dest: Path, login: str, password: str, log_func, progress_func=None):
    import zipfile

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    errors = []
    candidates = u1c_config_download_candidate_urls(item, program, release, primary_url)

    if not candidates:
        raise RuntimeError("Нет URL-кандидатов для скачивания обновления.")

    log_func(f"Кандидатов скачивания: {len(candidates)}")

    for candidate in candidates:
        candidate_name = Path(candidate.split("?", 1)[0]).name
        if not candidate_name or candidate_name.lower() in ("get", "download"):
            candidate_name = dest.name

        attempt_dest = dest
        if candidate_name.lower().endswith((".zip", ".rar", ".7z", ".tar", ".tar.gz", ".tgz")):
            attempt_dest = dest.parent / candidate_name

        try:
            if attempt_dest.exists():
                try:
                    validate_downloaded_update_file(attempt_dest)
                    if attempt_dest.stat().st_size > 1024 * 1024:
                        log_func(f"Файл уже есть и выглядит нормальным: {attempt_dest}")
                        return attempt_dest
                    else:
                        log_func(f"Файл слишком маленький, перекачиваю: {attempt_dest}")
                        attempt_dest.unlink()
                except Exception:
                    try:
                        attempt_dest.unlink()
                    except Exception:
                        pass

            log_func(f"Пробую скачать: {candidate}")
            downloaded = download_url_to_file(
                candidate,
                attempt_dest,
                log_func,
                login,
                password,
                progress_func=progress_func,
                progress_label=f"{program} {release}",
            )
            if progress_func:
                try:
                    progress_func(100, 0, 0, f"{program} {release}: распаковка")
                    GLib.idle_add(
                        self.idle_set_current_operation,
                        {
                            "base": name,
                            "release": release,
                            "step": "Распаковка",
                            "action": "Распаковка архива обновления",
                            "mode": "UNPACK",
                            "status": f"{program} {release}: распаковка",
                            "pid": "-",
                            "started": getattr(self, "_download_operation_started", "-") or "-",
                        },
                    )
                except Exception:
                    pass

            downloaded = prepare_1c_downloaded_update_file(downloaded, dest, release, log_func)

            if progress_func:
                try:
                    progress_func(100, 0, 0, f"{program} {release}: готово")
                except Exception:
                    pass


            downloaded = prepare_1c_downloaded_update_file(downloaded, dest, release, log_func)

            validate_downloaded_update_file(downloaded)

            if downloaded.stat().st_size < 1024 * 1024:
                raise RuntimeError(f"Скачанный файл подозрительно маленький: {downloaded.stat().st_size} байт")

            log_func(f"Файл обновления скачан: {downloaded}")
            return downloaded

        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            errors.append(f"{candidate} -> {msg}")
            log_func(f"Не удалось скачать по этому URL: {msg}")

            try:
                if attempt_dest.exists() and attempt_dest.stat().st_size < 1024 * 1024:
                    attempt_dest.unlink()
            except Exception:
                pass

    raise RuntimeError("Не удалось скачать файл обновления ни по одному URL.\n" + "\n".join(errors[-10:]))


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




def tail_file(path: Path, max_lines=250) -> str:
    try:
        path = Path(path)
        if not path.exists():
            return ""
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-max_lines:])
    except Exception as e:
        return f"Не удалось прочитать лог {path}: {type(e).__name__}: {e}"


def looks_like_html_download_file(path: Path) -> bool:
    try:
        path = Path(path)
        if not path.exists() or not path.is_file():
            return False

        data = path.read_bytes()[:8192]
        if not data:
            return False

        raw = data.lstrip().lower()

        if raw.startswith(b"<!doctype html") or raw.startswith(b"<html"):
            return True

        text = data.decode("utf-8", errors="ignore").lower()

        html_markers = (
            "<!doctype html",
            "<html",
            "</html>",
            "<head",
            "<body",
            "login.1c.ru",
            "releases.1c.ru",
            "портал 1с",
            "авторизац",
        )

        return any(marker in text for marker in html_markers)

    except Exception:
        return False


def validate_downloaded_update_file(path: Path) -> None:
    path = Path(path)

    if not path.exists():
        raise RuntimeError(f"Файл обновления не найден: {path}")

    if path.stat().st_size <= 0:
        raise RuntimeError(f"Файл обновления пустой: {path}")

    if looks_like_html_download_file(path):
        head = path.read_text(encoding="utf-8", errors="replace")[:1000]
        raise RuntimeError(
            "Вместо файла обновления получена HTML-страница. "
            f"Файл нельзя использовать как обновление 1С: {path}\n"
            f"Начало файла:\n{head}"
        )


def is_uuid_value(value: str) -> bool:
    value = (value or "").strip()
    return bool(re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        value,
    ))


def is_valid_config_release(value: str) -> bool:
    value = (value or "").strip().replace("_", ".")
    if not value or is_uuid_value(value):
        return False
    return bool(re.fullmatch(r"\d+\.\d+\.\d+(?:\.\d+)?", value))


def release_version_from_text(value: str) -> str:
    text = str(value or "").strip()
    if not text or is_uuid_value(text):
        return ""

    text = text.replace("\\", "/")
    parts = re.split(r"[/\s]+", text)

    for part in reversed(parts):
        part = part.strip(" ._-")
        cand = part.replace("_", ".")
        if is_valid_config_release(cand):
            return cand

    for m in re.finditer(r"(?<!\d)(\d+[._]\d+[._]\d+(?:[._]\d+)?)(?!\d)", text):
        cand = m.group(1).replace("_", ".")
        if is_valid_config_release(cand):
            return cand

    return ""


def version_to_update_folder(version: str) -> str:
    return safe_name((version or "").strip().replace(".", "_"))


def update_release_folder(value: str, fallback: str = "release") -> str:
    v = (value or "").strip().replace("\\", "/")
    if "/" in v:
        v = v.rstrip("/").split("/")[-1]
    if not v:
        v = fallback
    return safe_name(v.replace(".", "_"))


def read_release_version_from_mft(folder: Path) -> str:
    mft = Path(folder) / "1cv8.mft"
    if not mft.exists():
        return ""

    text = mft.read_text(encoding="utf-8", errors="ignore")
    patterns = [
        r"Version\s*=\s*\"?([0-9]+(?:[._][0-9]+)+)\"?",
        r"VersionNumber\s*=\s*\"?([0-9]+(?:[._][0-9]+)+)\"?",
        r"Версия\s*=\s*\"?([0-9]+(?:[._][0-9]+)+)\"?",
    ]

    for pattern in patterns:
        m = re.search(pattern, text, flags=re.I)
        if m:
            return m.group(1).replace(".", "_")

    m = re.search(r"([0-9]+[._][0-9]+[._][0-9]+(?:[._][0-9]+)?)", text)
    if m:
        return m.group(1).replace(".", "_")

    return ""


def find_update_file_in_dir(folder: Path):
    folder = Path(folder)
    if not folder.exists():
        return None

    preferred = [folder / "1cv8.cfu", folder / "1CV8.CFU", folder / "1cv8.cf", folder / "1CV8.CF"]
    for item in preferred:
        if item.exists() and item.is_file():
            try:
                validate_downloaded_update_file(item)
                return item
            except Exception:
                continue

    files = []
    for pattern in ("*.cfu", "*.CFU", "*.cf", "*.CF"):
        files.extend([p for p in folder.rglob(pattern) if p.is_file()])

    valid_files = []
    for item in files:
        try:
            validate_downloaded_update_file(item)
            valid_files.append(item)
        except Exception:
            continue

    valid_files.sort(key=lambda p: (0 if p.name.lower() == "1cv8.cfu" else 1, len(str(p)), str(p)))
    return valid_files[0] if valid_files else None


def update_item_release_version(item, fallback: str = "") -> str:
    keys = [
        "versionNumber", "releaseVersion", "release_version", "version",
        "newVersion", "targetVersion", "targetVersionNumber",
        "templatePath", "updateTemplatePath", "updateFileName",
        "fileName", "name", "title",
    ]
    values = []

    if isinstance(item, dict):
        for key in keys:
            if key in item:
                values.append(item.get(key))

        for nested_key in ("programVersion", "versionInfo", "release", "update"):
            nested = item.get(nested_key)
            if isinstance(nested, dict):
                for key in keys:
                    if key in nested:
                        values.append(nested.get(key))
    else:
        values.append(item)

    values.append(fallback)

    for value in values:
        ver = release_version_from_text(str(value or ""))
        if ver:
            return ver

    return ""


def release_version_for_downloaded_folder(folder: Path, fallback: str = "") -> str:
    ver = read_release_version_from_mft(folder).replace("_", ".")
    if is_valid_config_release(ver):
        return ver

    ver = release_version_from_text(Path(folder).name)
    if ver:
        return ver

    ver = release_version_from_text(fallback)
    if ver:
        return ver

    return ""


def load_latest_update_metadata(program_dir: Path) -> dict:
    program_dir = Path(program_dir)
    metadata_dir = program_dir / "_metadata"
    result = {}

    candidates = []
    if (program_dir / "latest_update_info.json").exists():
        candidates.append(program_dir / "latest_update_info.json")
    if metadata_dir.exists():
        candidates.extend(sorted(metadata_dir.glob("download_data_*.json"), key=lambda p: p.stat().st_mtime, reverse=True))
        candidates.extend(sorted(metadata_dir.glob("update_info_*.json"), key=lambda p: p.stat().st_mtime, reverse=True))

    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue

        if isinstance(data, dict):
            # Предпочитаем файл download_data: в нём есть и info, и files.
            if data.get("files") or data.get("configurationUpdateDataList"):
                return data
            if not result:
                result = data

    return result


def unpack_update_archives_in_folder(folder: Path, log_func=None) -> None:
    folder = Path(folder)
    if find_update_file_in_dir(folder):
        return

    archives = []
    for pattern in ("*.zip", "*.rar", "*.7z", "*.tar", "*.tar.gz", "*.tgz"):
        archives.extend([p for p in folder.rglob(pattern) if p.is_file()])

    for archive in sorted(archives, key=lambda p: len(str(p))):
        if find_update_file_in_dir(folder):
            return

        target = archive.parent / (archive_output_folder_name(archive) + "_unpacked")
        if target.exists() and find_update_file_in_dir(target):
            continue

        try:
            if log_func:
                log_func(f"Распаковка архива обновления: {archive}")
            unpack_platform_archive(archive, target, log_func)
        except Exception as e:
            if log_func:
                log_func(f"Не удалось распаковать {archive}: {type(e).__name__}: {e}")


def find_downloaded_update_steps(settings: dict, program: str, current_version: str = "", log_func=None):
    root = update_program_dir(settings, program)
    if not root.exists():
        return []

    info = load_latest_update_metadata(root)
    seq = []
    data_list = []

    if isinstance(info, dict):
        info_block = info.get("info") if isinstance(info.get("info"), dict) else info
        if isinstance(info_block, dict):
            seq = info_block.get("upgradeSequence") or []
        data_list = info.get("files") or info.get("configurationUpdateDataList") or []

    result = []
    used = set()

    def add_folder(folder: Path, fallback: str = "") -> bool:
        if not folder.exists() or not folder.is_dir():
            return False

        if "_metadata" in folder.parts or "_archives" in folder.parts:
            return False

        unpack_update_archives_in_folder(folder, log_func)

        key = str(folder.resolve())
        if key in used:
            return False

        update_file = find_update_file_in_dir(folder)
        if not update_file:
            return False

        rel_ver = release_version_for_downloaded_folder(update_file.parent, fallback)
        if not rel_ver:
            rel_ver = release_version_for_downloaded_folder(folder, fallback)
        if not rel_ver:
            if is_uuid_value(folder.name) or is_uuid_value(str(fallback)):
                return False
            rel_ver = folder.name.replace("_", ".")

        if not is_valid_config_release(rel_ver):
            return False

        used.add(key)
        result.append((rel_ver, update_file, update_file.parent))
        return True

    def add_release(value: str) -> bool:
        rel_ver = release_version_from_text(value)
        if not rel_ver:
            return False

        folder_name = version_to_update_folder(rel_ver)
        candidates = [root / folder_name, root / rel_ver]
        candidates.extend([p for p in root.rglob(folder_name) if p.is_dir()])
        candidates.extend([p for p in root.rglob(rel_ver) if p.is_dir()])

        for folder in candidates:
            if add_folder(folder, rel_ver):
                return True
        return False

    if isinstance(data_list, list):
        for n, item in enumerate(data_list):
            fallback = seq[n] if n < len(seq) else ""
            rel_ver = update_item_release_version(item, str(fallback))
            if rel_ver:
                add_release(rel_ver)

    for item in seq:
        rel_ver = update_item_release_version(item, str(item))
        if rel_ver:
            add_release(rel_ver)

    if result:
        result.sort(key=lambda x: version_key(x[0]))
        return result

    for folder in sorted([p for p in root.iterdir() if p.is_dir() and not p.name.startswith("_")], key=lambda p: p.name):
        add_folder(folder, "")

    result.sort(key=lambda x: version_key(x[0]))
    return result




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
            ("1cv8.1CD", "file", "Готовая файловая база 1Cv8.1CD"),
            ("1cv8.1cd", "file", "Готовая файловая база 1Cv8.1CD"),
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


    def load_templates(self):
        """Загружает локальные шаблоны 1С в плоскую ListStore-модель.

        В 1.2.2-1.2.4 вызовы self.load_templates() остались,
        а сам метод мог пропасть после незавершенной группировки шаблонов.
        Плоская модель совместима с текущим accept_selected().
        """
        self.store.clear()

        wanted = "all"
        try:
            wanted = self.template_filter.get_active_id() or "all"
        except Exception:
            pass

        folders = set()
        masks = [
            "1cv8.mft",
            "1Cv8new.dt", "1cv8new.dt",
            "1Cv8.dt", "1cv8.dt",
            "1Cv8.cf", "1cv8.cf",
            "1Cv8.1CD", "1cv8.1CD", "1cv8.1cd",
        ]

        for root in self.template_roots():
            try:
                for mask in masks:
                    for marker in root.rglob(mask):
                        folders.add(marker.parent)
            except Exception:
                pass

        for folder in sorted(folders, key=lambda x: str(x).lower()):
            try:
                variants = self.template_variants(folder)
            except Exception:
                variants = []

            for v in variants:
                kind = str(v.get("kind") or "")
                if wanted != "all" and kind != wanted:
                    continue

                try:
                    product, conf = self.product_group_name(folder, v.get("name") or "")
                except Exception:
                    product, conf = "Шаблоны 1С", v.get("name") or folder.name

                self.store.append([
                    str(conf or ""),
                    str(v.get("title") or ""),
                    str(v.get("version") or ""),
                    str(v.get("folder") or folder),
                    str(v.get("payload") or ""),
                    kind,
                    str(product or ""),
                ])

    def refresh_templates(self, *_):
        """Совместимость со старыми обработчиками кнопок/патчами."""
        return self.load_templates()

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
            "Последовательность: /UpdateCfg конкретного 1cv8.cfu → /UpdateDBCfg → ENTERPRISE /C ВыполнитьОбновлениеИЗавершитьРаботу."
        )
        grid.attach(info, 0, 0, 2, 1)

        self.backup_mode = Ui.combo(["Сделать резервную копию перед обновлением", "Не делать резервную копию"])
        self.backup_type = Ui.combo([
            "Авто: файловая=архив 1Cv8.1CD, серверная=.dt",
            "Файловый архив 1Cv8.1CD; для серверной будет .dt",
            "Выгрузка .dt через конфигуратор",
        ])
        self.restore_on_error = Ui.check("При ошибке попытаться автоматически откатить базу из созданной копии", True)
        self.update_db_cfg = Ui.check("После /UpdateCfg отдельной командой выполнять /UpdateDBCfg", True)
        self.dynamic_update = Ui.check("Для /UpdateDBCfg пробовать динамическое применение изменений (-Dynamic+)", True)
        self.server_update = Ui.check("Для серверной базы добавлять к /UpdateDBCfg ключ -Server", False)
        self.run_handlers = Ui.check("После каждого релиза запускать обработчики обновления в режиме 1С:Предприятие", True)
        self.stop_on_error = Ui.check("Остановить цепочку при первой ошибке", True)
        self.hidden_xvfb = Ui.check("Скрытый пакетный запуск через xvfb-run, если установлен", bool(shutil.which("xvfb-run")))
        self.timeout_minutes = Ui.entry("120")

        rows = [
            ("Резервная копия:", self.backup_mode),
            ("Тип резервной копии:", self.backup_type),
            ("", self.restore_on_error),
            ("", self.update_db_cfg),
            ("", self.dynamic_update),
            ("", self.server_update),
            ("", self.run_handlers),
            ("", self.stop_on_error),
            ("", self.hidden_xvfb),
            ("Таймаут одной операции, минут:", self.timeout_minutes),
        ]

        for i, (label, widget) in enumerate(rows, 1):
            grid.attach(Ui.label(label), 0, i, 1, 1)
            grid.attach(widget, 1, i, 1, 1)

        self.show_all()

    def get_values(self):
        try:
            timeout_minutes = int((self.timeout_minutes.get_text() or "120").strip())
        except Exception:
            timeout_minutes = 120

        timeout_minutes = max(1, timeout_minutes)

        return {
            "make_backup": self.backup_mode.get_active() == 0,
            "backup_type": self.backup_type.get_active(),
            "restore_on_error": self.restore_on_error.get_active(),
            "update_db_cfg": self.update_db_cfg.get_active(),
            "dynamic_update": self.dynamic_update.get_active(),
            "server_update": self.server_update.get_active(),
            "run_handlers": self.run_handlers.get_active(),
            "stop_on_error": self.stop_on_error.get_active(),
            "hidden_xvfb": self.hidden_xvfb.get_active(),
            "timeout_minutes": timeout_minutes,
            "timeout_seconds": timeout_minutes * 60,
        }



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
        self.set_default_size(1320, 810)
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
        b3.connect("clicked", self.on_sync_with_1c_list)

        b1.connect("clicked", self.on_check_all_bases)
        b2.connect("clicked", self.on_uncheck_all_bases)

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
        # UPDATER1C_DESIGNER_LAUNCH_FIX_20260626
        # Пункт "Конфигуратор" обязан передавать DESIGNER дальше без потери режима.
        mode = (mode or "ENTERPRISE").upper()
        if mode not in ("ENTERPRISE", "DESIGNER"):
            mode = "ENTERPRISE"
        return self.launch_selected_base(mode)

    def guess_update_program_name_from_metadata(self, config_name="", config_synonym="", base_name=""):
        """Определяет код программы обновлений 1С по имени/синониму конфигурации."""
        raw = " ".join(str(x or "") for x in [config_name, config_synonym, base_name])
        hay = raw.lower()
        compact = hay.replace(" ", "").replace("_", "").replace("-", "").replace(",", "")

        # Базовую бухгалтерию проверяем раньше обычной.
        if (
            "бухгалтерияпредприятиябазовая" in compact
            or "бухгалтерияпредприятиябаз" in compact
            or ("бухгалтер" in hay and "базов" in hay)
            or "accountingbase" in compact
        ):
            return "AccountingBase"

        if "бухгалтер" in hay or "accounting" in compact:
            return "Accounting"

        if (
            "управлениеторговлей" in compact
            or "торговл" in hay
            or "trade" in compact
        ):
            return "Trade"

        if "документооборот" in hay or "document" in compact or "docmng" in compact:
            return "DocumentManagement"

        if "зарплата" in hay or "зуп" in hay or "hrm" in compact or "salary" in compact:
            return "HRM"

        if "управлениенашейфирмой" in compact or "унф" in hay or "smallbusiness" in compact:
            return "SmallBusiness"

        if "erp" in compact:
            return "ERP"

        return ""

    def refresh_bases_tree_keep_selected(self, base):
        """Сохраняет конфиг и перерисовывает список баз после проверки настроек."""
        try:
            self.config["bases"] = self.bases
        except Exception:
            pass

        try:
            self.save_config_safe()
        except Exception:
            try:
                save_json(self.config_path, self.config)
            except Exception:
                pass

        try:
            self._load_bases_tree()
        except Exception:
            pass

        try:
            self.base_tree.expand_all()
        except Exception:
            pass

        try:
            self.select_base_by_name_connect(base.get("name"), base.get("connect"))
        except Exception:
            pass

        try:
            self.update_bases_status()
        except Exception:
            pass

    def cf_export_file_name(self, base):
        """Имя .cf: база + конфигурация + релиз, если есть + дата."""
        base_name = safe_name(base.get("name") or "base")

        config_name = (
            base.get("config_name")
            or base.get("configuration_name")
            or base.get("config_synonym")
            or base.get("configuration_synonym")
            or ""
        )
        config_name = safe_name(config_name) if config_name else ""

        release = (
            base.get("config_version")
            or base.get("configuration_version")
            or base.get("version")
            or ""
        )
        release = str(release or "").strip()

        parts = [base_name]

        if config_name:
            parts.append(config_name)

        if release:
            parts.append(safe_name(release))

        parts.append(now_stamp())

        return "_".join([x for x in parts if x]) + ".cf"


    def find_real_base_for_checked_metadata(self, base):
        """Находит настоящий словарь базы в self.bases для записи результата проверки.

        В некоторых местах GTK-форма работает с копией строки, поэтому простое base["config_version"]
        не обновляет таблицу и свойства.
        """
        if not isinstance(base, dict):
            return None

        # 1. Если это тот же объект — отлично.
        for item in self.bases:
            if item is base:
                return item

        name = str(base.get("name") or "").strip()
        connect = str(base.get("connect") or "").strip()
        kind = str(base.get("kind") or base.get("type") or "").strip().lower()
        group = str(base.get("group") or "").strip()

        # 2. Лучшее совпадение: тип + подключение.
        if connect:
            for item in self.bases:
                if not isinstance(item, dict):
                    continue
                if item.get("is_group") or item.get("kind") == "group" or item.get("type") == "group":
                    continue

                item_connect = str(item.get("connect") or "").strip()
                item_kind = str(item.get("kind") or item.get("type") or "").strip().lower()

                if item_connect == connect and (not kind or item_kind == kind):
                    return item

        # 3. Имя + группа.
        if name:
            for item in self.bases:
                if not isinstance(item, dict):
                    continue
                if item.get("is_group") or item.get("kind") == "group" or item.get("type") == "group":
                    continue

                if str(item.get("name") or "").strip() != name:
                    continue

                if not group or str(item.get("group") or "").strip() == group:
                    return item

        # 4. Просто имя, если оно уникально.
        matches = []
        if name:
            for item in self.bases:
                if not isinstance(item, dict):
                    continue
                if str(item.get("name") or "").strip() == name:
                    matches.append(item)

        if len(matches) == 1:
            return matches[0]

        return None

    def apply_checked_metadata_to_real_base(self, base, config_name="", config_synonym="", config_version="", update_program_name=""):
        """Сохраняет результат Проверить настройки в base и в реальную строку self.bases."""
        real_base = self.find_real_base_for_checked_metadata(base) or base

        config_name = str(config_name or "").strip()
        config_synonym = str(config_synonym or "").strip()
        config_version = str(config_version or "").strip()
        update_program_name = str(update_program_name or "").strip()

        # Если код не передан — определяем здесь.
        if not update_program_name:
            try:
                update_program_name = self.guess_update_program_name_from_metadata(
                    config_name,
                    config_synonym,
                    real_base.get("name") or base.get("name"),
                )
            except Exception:
                update_program_name = ""

        targets = []

        if isinstance(base, dict):
            targets.append(base)

        if isinstance(real_base, dict) and real_base is not base:
            targets.append(real_base)

        for target in targets:
            if config_name:
                target["config_name"] = config_name
                target["configuration_name"] = config_name

            if config_synonym:
                target["config_synonym"] = config_synonym
                target["configuration_synonym"] = config_synonym

            if config_version:
                target["config_version"] = config_version
                target["configuration_version"] = config_version
                target["version"] = config_version

            if update_program_name:
                target["update_program_name"] = update_program_name
                target["update_code"] = update_program_name
                target["program_name"] = update_program_name
                target["program_code"] = update_program_name

        try:
            self.config["bases"] = self.bases
        except Exception:
            pass

        try:
            self.save_config_safe()
        except Exception:
            try:
                save_json(self.config_path, self.config)
            except Exception:
                pass

        # Перерисовываем дерево в UI-потоке.
        try:
            GLib.idle_add(self.refresh_bases_tree_keep_selected, real_base)
        except Exception:
            try:
                GLib.idle_add(self._load_bases_tree)
            except Exception:
                pass

        return real_base

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

                guessed_program = self.guess_update_program_name_from_metadata(name, synonym, base.get("name"))
                if guessed_program:
                    base["update_program_name"] = guessed_program

            checked_base = self.apply_checked_metadata_to_real_base(
                base,
                name,
                synonym,
                version,
                self.guess_update_program_name_from_metadata(name, synonym, base.get("name")),
            )

            log(f"Текущая версия конфигурации: {version or '-'}")
            log(f"Код программы обновлений: {base.get('update_program_name') or '-'}")
            GLib.idle_add(self.refresh_bases_tree_keep_selected, base)
            log("Проверка настроек: завершено")

        self.run_in_background("Проверка настроек", work)

    def on_run_base_real(self, *_):
        self.launch_selected_base("ENTERPRISE")

    def on_designer_base_real(self, *_):
        self.launch_selected_base("DESIGNER")

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


    def context_selected_1c_base(self):
        """Выбранная база для операций из контекстного меню."""
        try:
            self.save_current_credentials_to_selected_base(silent=True)
        except Exception:
            pass

        base = self.require_current_base_dict()

        if not base:
            return None

        kind, connect = normalize_base_kind_and_connect(base)

        if kind == "web":
            self._append_log("Операция недоступна для web-базы: DESIGNER по URL в этой операции не используется.")
            return None

        if not connect:
            self._append_log("У выбранной базы не заполнен путь / сервер / URL.")
            return None

        return base

    def onec_command_env_for_maintenance(self):
        import os

        env = os.environ.copy()

        try:
            libgcc = system_libgcc_path_for_1c()
        except Exception:
            libgcc = ""

        if not libgcc:
            for candidate in ("/usr/lib/x86_64-linux-gnu/libgcc_s.so.1", "/lib/x86_64-linux-gnu/libgcc_s.so.1"):
                if Path(candidate).exists():
                    libgcc = candidate
                    break

        if libgcc:
            old = env.get("LD_PRELOAD", "").strip()
            parts = [x for x in old.split() if x]

            if libgcc not in parts:
                parts.insert(0, libgcc)

            env["LD_PRELOAD"] = " ".join(parts)

        return env

    def mask_1c_command_for_log(self, args):
        import re
        safe = []

        for arg in args:
            s = str(arg)

            if s.startswith("/P") and len(s) > 2:
                s = "/P***"

            if "Pwd=" in s:
                s = re.sub(r'Pwd="[^"]*"', 'Pwd="***"', s)

            safe.append(s)

        try:
            return command_to_text(safe)
        except Exception:
            import shlex
            return " ".join(shlex.quote(str(x)) for x in safe)


    def u1c_walk_widgets(self, root=None):
        """Обходит GTK-виджеты окна, чтобы обновлять панель статуса без привязки к именам полей."""
        if root is None:
            root = self

        result = []

        def walk(widget):
            result.append(widget)

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

        return result

    def u1c_set_label_near_caption(self, caption, value):
        """Ищет Label 'База:'/'Статус:' и меняет соседний Label справа в той же строке Gtk.Grid."""
        try:
            from gi.repository import Gtk
        except Exception:
            return False

        wanted = str(caption or "").strip().lower().rstrip(":")
        value = "-" if value is None or str(value) == "" else str(value)

        for widget in self.u1c_walk_widgets(self):
            try:
                if not isinstance(widget, Gtk.Label):
                    continue

                text = str(widget.get_text() or "").strip().lower().rstrip(":")

                if text != wanted:
                    continue

                parent = widget.get_parent()

                if not isinstance(parent, Gtk.Grid):
                    continue

                top = parent.child_get_property(widget, "top-attach")
                left = parent.child_get_property(widget, "left-attach")

                candidates = []

                for child in parent.get_children():
                    try:
                        if child is widget:
                            continue

                        if parent.child_get_property(child, "top-attach") != top:
                            continue

                        child_left = parent.child_get_property(child, "left-attach")

                        if child_left <= left:
                            continue

                        if isinstance(child, Gtk.Label):
                            candidates.append((child_left, child))
                    except Exception:
                        pass

                if candidates:
                    candidates.sort(key=lambda x: x[0])
                    candidates[0][1].set_text(value)
                    return True

            except Exception:
                pass

        return False

    def u1c_set_all_progressbars(self, fraction=None, text="", pulse=False):
        try:
            from gi.repository import Gtk
        except Exception:
            return False

        changed = False

        for widget in self.u1c_walk_widgets(self):
            try:
                if not isinstance(widget, Gtk.ProgressBar):
                    continue

                widget.set_show_text(True)

                if text:
                    widget.set_text(str(text))

                if pulse:
                    widget.pulse()
                elif fraction is not None:
                    f = max(0.0, min(1.0, float(fraction)))
                    widget.set_fraction(f)

                changed = True

            except Exception:
                pass

        return changed

    def u1c_operation_pulse_tick(self):
        try:
            if not getattr(self, "_u1c_maintenance_pulse_active", False):
                return False

            self.u1c_set_all_progressbars(text=getattr(self, "_u1c_maintenance_pulse_text", "Выполняется..."), pulse=True)
            return True

        except Exception:
            return False

    def u1c_set_operation_panel(
        self,
        base=None,
        operation="",
        release="-",
        step="-",
        action="-",
        mode="DESIGNER",
        status="выполняется",
        pid="-",
        started="-",
        progress=None,
        pulse=False,
    ):
        """Обновляет правую панель 'Текущая операция' и progress bar из любого фонового потока."""
        try:
            from gi.repository import GLib
        except Exception:
            GLib = None

        def apply():
            try:
                base_name = "-"

                if isinstance(base, dict):
                    base_name = base.get("name") or "-"

                self.u1c_set_label_near_caption("База", base_name)
                self.u1c_set_label_near_caption("Релиз", release)
                self.u1c_set_label_near_caption("Шаг", step)
                self.u1c_set_label_near_caption("Действие", action or operation or "-")
                self.u1c_set_label_near_caption("Режим", mode or "-")
                self.u1c_set_label_near_caption("Статус", status or "-")
                self.u1c_set_label_near_caption("PID процесса", pid)
                self.u1c_set_label_near_caption("Время запуска", started)

                text = status or operation or "Выполняется..."

                if progress is not None:
                    self.u1c_set_all_progressbars(progress, text=text, pulse=False)
                elif pulse:
                    self.u1c_set_all_progressbars(text=text, pulse=True)

            except Exception:
                pass

            return False

        if GLib is not None:
            GLib.idle_add(apply)
        else:
            apply()

    def u1c_begin_maintenance_operation(self, base, operation, action=None):
        from datetime import datetime

        try:
            from gi.repository import GLib
        except Exception:
            GLib = None

        self._u1c_maintenance_pulse_active = True
        self._u1c_maintenance_pulse_text = operation or "Выполняется..."

        started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        self.u1c_set_operation_panel(
            base=base,
            operation=operation,
            step="1/1",
            action=action or operation,
            mode="DESIGNER",
            status="запуск",
            pid="-",
            started=started,
            progress=0.02,
            pulse=False,
        )

        if GLib is not None:
            GLib.timeout_add(250, self.u1c_operation_pulse_tick)

        return started

    def u1c_finish_maintenance_operation(self, base, operation, ok=True, error_text=""):
        self._u1c_maintenance_pulse_active = False

        if ok:
            status = "завершено"
            progress = 1.0
        else:
            status = "ошибка" + (": " + str(error_text)[:180] if error_text else "")
            progress = 1.0

        self.u1c_set_operation_panel(
            base=base,
            operation=operation,
            step="1/1",
            action=operation,
            mode="DESIGNER",
            status=status,
            progress=progress,
            pulse=False,
        )


    def run_1c_maintenance_command(self, log, title, base, extra_args, timeout=7200):
        import subprocess
        from datetime import datetime

        args = build_1c_args(base, self.settings, "DESIGNER")
        args.extend(extra_args)

        env = self.onec_command_env_for_maintenance()

        started = self.u1c_begin_maintenance_operation(base, title, action=title)

        log("")
        log(f"=== {title}: {base.get('name') or '-'} ===")
        log("Команда: " + self.mask_1c_command_for_log(args))

        if env.get("LD_PRELOAD"):
            log("LD_PRELOAD: " + env.get("LD_PRELOAD", ""))

        proc = None

        try:
            proc = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
            )

            self.u1c_set_operation_panel(
                base=base,
                operation=title,
                step="1/1",
                action=title,
                mode="DESIGNER",
                status="выполняется",
                pid=str(proc.pid),
                started=started,
                pulse=True,
            )

            try:
                out, _ = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except Exception:
                    pass

                out, _ = proc.communicate()
                raise RuntimeError(f"Таймаут операции 1С: {timeout} сек.")

            out = out or ""

            if out.strip():
                log(out.strip()[-6000:])

            if proc.returncode != 0:
                raise RuntimeError(f"1С завершилась с кодом {proc.returncode}")

            self.u1c_finish_maintenance_operation(base, title, ok=True)
            return True

        except Exception as e:
            self.u1c_finish_maintenance_operation(base, title, ok=False, error_text=f"{type(e).__name__}: {e}")
            raise

    def default_maintenance_dir(self, base):
        root = (
            self.settings.get("backup_dir")
            or self.settings.get("backups_dir")
            or "/mnt/DataStore/Updater1C/1c-backups"
        )

        name = safe_name(base.get("name") or "base")
        path = Path(root).expanduser() / name
        path.mkdir(parents=True, exist_ok=True)

        return path

    def archive_selected_base_dt(self, base):
        """Архивирование ИБ через /DumpIB в .dt."""
        out_dir = self.default_maintenance_dir(base)
        dest = out_dir / f"{safe_name(base.get('name') or 'base')}_{now_stamp()}.dt"

        reports_dir = Path(
            self.settings.get("reports_dir")
            or self.settings.get("report_dir")
            or "/mnt/DataStore/Updater1C/1c-update-reports"
        ).expanduser()
        reports_dir.mkdir(parents=True, exist_ok=True)

        log_file = reports_dir / f"{safe_name(base.get('name') or 'base')}_DumpIB_{now_stamp()}.log"

        def work(log):
            self.run_1c_maintenance_command(
                log,
                "Архивирование базы в .dt",
                base,
                ["/DumpIB", str(dest), "/Out", str(log_file), "-NoTruncate"],
                timeout=14400,
            )

            log(f"Архив создан: {dest}")

            if log_file.exists():
                try:
                    tail = log_file.read_text(encoding="utf-8", errors="replace")[-6000:]
                    if tail.strip():
                        log("Хвост лога 1С:")
                        log(tail)
                except Exception:
                    pass

        self.run_in_background("Архивирование базы", work)


    def save_selected_config_cf(self, base):
        """Выгрузка конфигурации через /DumpCfg в .cf."""
        from pathlib import Path

        out_dir = self.default_maintenance_dir(base)
        dest = out_dir / self.cf_export_file_name(base)

        reports_dir = Path(
            self.settings.get("reports_dir")
            or self.settings.get("report_dir")
            or "/mnt/DataStore/Updater1C/1c-update-reports"
        ).expanduser()
        reports_dir.mkdir(parents=True, exist_ok=True)

        log_file = reports_dir / f"{safe_name(base.get('name') or 'base')}_DumpCfg_{now_stamp()}.log"

        def work(log):
            self.run_1c_maintenance_command(
                log,
                "Сохранение конфигурации в .cf",
                base,
                ["/DumpCfg", str(dest), "/Out", str(log_file), "-NoTruncate"],
                timeout=14400,
            )

            log(f"Файл конфигурации создан: {dest}")

            if log_file.exists():
                try:
                    tail = log_file.read_text(encoding="utf-8", errors="replace")[-6000:]

                    if tail.strip():
                        log("Хвост лога 1С:")
                        log(tail)
                except Exception:
                    pass

        self.run_in_background("Сохранение конфигурации в cf", work)

    def choose_cf_file_for_load(self):
        dlg = Gtk.FileChooserDialog(
            title="Выберите файл конфигурации .cf",
            transient_for=self,
            action=Gtk.FileChooserAction.OPEN,
        )

        dlg.add_button("Отмена", Gtk.ResponseType.CANCEL)
        dlg.add_button("Выбрать", Gtk.ResponseType.OK)

        try:
            filt = Gtk.FileFilter()
            filt.set_name("Файлы конфигурации 1С (*.cf)")
            filt.add_pattern("*.cf")
            filt.add_pattern("*.CF")
            dlg.add_filter(filt)

            all_filter = Gtk.FileFilter()
            all_filter.set_name("Все файлы")
            all_filter.add_pattern("*")
            dlg.add_filter(all_filter)
        except Exception:
            pass

        for folder in [
            self.settings.get("backup_dir") or "",
            "/mnt/DataStore/Updater1C/1c-backups",
            str(Path.home() / "Загрузки"),
            str(Path.home() / "Downloads"),
            str(Path.home()),
        ]:
            try:
                if folder and Path(folder).expanduser().exists():
                    dlg.set_current_folder(str(Path(folder).expanduser()))
                    break
            except Exception:
                pass

        result = ""

        if dlg.run() == Gtk.ResponseType.OK:
            result = dlg.get_filename() or ""

        dlg.destroy()

        return result

    def load_selected_config_from_cf(self, base, cf_file):
        """Загрузка .cf через /LoadCfg и применение /UpdateDBCfg -Dynamic+."""
        cf_path = Path(cf_file).expanduser()

        if not cf_path.exists() or not cf_path.is_file():
            self._append_log(f"Файл .cf не найден: {cf_path}")
            return

        if cf_path.suffix.lower() != ".cf":
            self._append_log(f"Выбран не .cf файл: {cf_path}")
            return

        reports_dir = Path(
            self.settings.get("reports_dir")
            or self.settings.get("report_dir")
            or "/mnt/DataStore/Updater1C/1c-update-reports"
        ).expanduser()
        reports_dir.mkdir(parents=True, exist_ok=True)

        log_load = reports_dir / f"{safe_name(base.get('name') or 'base')}_LoadCfg_{now_stamp()}.log"
        log_update = reports_dir / f"{safe_name(base.get('name') or 'base')}_UpdateDBCfg_{now_stamp()}.log"

        def work(log):
            self.run_1c_maintenance_command(
                log,
                "Загрузка конфигурации из .cf",
                base,
                ["/LoadCfg", str(cf_path), "/Out", str(log_load), "-NoTruncate"],
                timeout=14400,
            )

            if log_load.exists():
                try:
                    tail = log_load.read_text(encoding="utf-8", errors="replace")[-6000:]
                    if tail.strip():
                        log("Хвост лога LoadCfg:")
                        log(tail)
                except Exception:
                    pass

            self.run_1c_maintenance_command(
                log,
                "Обновление конфигурации базы данных",
                base,
                ["/UpdateDBCfg", "-Dynamic+", "/Out", str(log_update), "-NoTruncate"],
                timeout=14400,
            )

            if log_update.exists():
                try:
                    tail = log_update.read_text(encoding="utf-8", errors="replace")[-6000:]
                    if tail.strip():
                        log("Хвост лога UpdateDBCfg:")
                        log(tail)
                except Exception:
                    pass

            log("Загрузка .cf и обновление конфигурации БД завершены.")

        self.run_in_background("Загрузка конфигурации из cf", work)

    def on_context_archive_base(self, *_):
        base = self.context_selected_1c_base()

        if not base:
            return

        self.archive_selected_base_dt(base)

    def on_context_save_config_cf(self, *_):
        base = self.context_selected_1c_base()

        if not base:
            return

        self.save_selected_config_cf(base)

    def on_context_load_config_cf(self, *_):
        base = self.context_selected_1c_base()

        if not base:
            return

        cf_file = self.choose_cf_file_for_load()

        if not cf_file:
            self._append_log("Загрузка конфигурации из .cf отменена.")
            return

        dlg = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text="Загрузить конфигурацию из .cf?",
        )
        dlg.format_secondary_text(
            "База: "
            + str(base.get("name") or "-")
            + "\nФайл: "
            + str(cf_file)
            + "\n\nОперация изменит конфигурацию базы. Перед загрузкой рекомендуется сделать архив."
        )

        resp = dlg.run()
        dlg.destroy()

        if resp != Gtk.ResponseType.OK:
            self._append_log("Загрузка конфигурации из .cf отменена пользователем.")
            return

        self.load_selected_config_from_cf(base, cf_file)


    def is_group_base_item(self, base):
        if not isinstance(base, dict):
            return True

        kind = str(base.get("kind") or base.get("type") or "").strip().lower()

        return bool(base.get("is_group")) or kind == "group"

    def is_web_base_item(self, base):
        if not isinstance(base, dict):
            return False

        try:
            kind, connect = normalize_base_kind_and_connect(base)
            return str(kind or "").strip().lower() == "web"
        except Exception:
            kind = str(base.get("kind") or base.get("type") or "").strip().lower()
            return kind == "web"

    def context_menu_archive_cf_items_disabled(self):
        """Для групп и web-баз архив/cf операции в контекстном меню недоступны."""
        try:
            base = self.require_current_base_dict()
        except Exception:
            base = None

        if not base:
            return True

        if self.is_group_base_item(base):
            return True

        if self.is_web_base_item(base):
            return True

        return False

    def show_base_context_menu(self, event):
        _u1c_archive_cf_restricted_labels = {
            "Архивировать",
            "Сохранить конфигурацию в cf",
            "Загрузить конфигурацию из cf",
        }
        _u1c_archive_cf_disabled = self.context_menu_archive_cf_items_disabled()

        vals = self.selected_base_values()

        menu = Gtk.Menu()

        if vals:
            title = vals.get("name") or "База"
            item_title = Gtk.MenuItem(label=f"База: {title}")
            try:
                _u1c_menu_label_text = str(f"База: {title}")
                if _u1c_menu_label_text in _u1c_archive_cf_restricted_labels and _u1c_archive_cf_disabled:
                    item_title.set_sensitive(False)
            except Exception:
                pass
            item_title.set_sensitive(False)
            menu.append(item_title)

            menu.append(Gtk.SeparatorMenuItem())

        items = [
            ("Запустить", self.on_run_base_real),
            ("Конфигуратор", self.on_designer_base_real),
            ("Свойства", self.on_edit_base),
            ("Проверить настройки", self.on_check_selected_base_real),
            ("Архивировать", self.on_context_archive_base),
            ("Сохранить конфигурацию в cf", self.on_context_save_config_cf),
            ("Загрузить конфигурацию из cf", self.on_context_load_config_cf),
            ("Скачать обновления", self.on_download_updates_real),
            ("Скачать платформу", self.on_download_platform),
            ("Установить обновления", self.on_auto_update),
            ("Очистить кэш", self.on_clear_cache_real),
        ]

        for label, handler in items:
            item = Gtk.MenuItem(label=label)
            try:
                _u1c_menu_label_text = str(label)
                if _u1c_menu_label_text in _u1c_archive_cf_restricted_labels and _u1c_archive_cf_disabled:
                    item.set_sensitive(False)
            except Exception:
                pass
            item.connect("activate", lambda _item, h=handler: h())
            menu.append(item)

        menu.append(Gtk.SeparatorMenuItem())

        item_check = Gtk.MenuItem(label="Отметить выбранную строку/группу")

        try:

            _u1c_menu_label_text = str("Отметить выбранную строку/группу")

            if _u1c_menu_label_text in _u1c_archive_cf_restricted_labels and _u1c_archive_cf_disabled:

                item_check.set_sensitive(False)

        except Exception:

            pass
        item_check.connect("activate", lambda *_: self.set_selected_base_checked(True))
        menu.append(item_check)

        item_uncheck = Gtk.MenuItem(label="Снять отметку с выбранной строки/группы")

        try:

            _u1c_menu_label_text = str("Снять отметку с выбранной строки/группы")

            if _u1c_menu_label_text in _u1c_archive_cf_restricted_labels and _u1c_archive_cf_disabled:

                item_uncheck.set_sensitive(False)

        except Exception:

            pass
        item_uncheck.connect("activate", lambda *_: self.set_selected_base_checked(False))
        menu.append(item_uncheck)

        menu.append(Gtk.SeparatorMenuItem())

        item_delete = Gtk.MenuItem(label="Удалить из списка")

        try:

            _u1c_menu_label_text = str("Удалить из списка")

            if _u1c_menu_label_text in _u1c_archive_cf_restricted_labels and _u1c_archive_cf_disabled:

                item_delete.set_sensitive(False)

        except Exception:

            pass
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


    def ibases_v8i_candidate_paths(self):
        """Ищет стандартные файлы списка информационных баз 1С."""
        home = Path.home()

        candidates = [
            home / ".1C" / "1cestart" / "ibases.v8i",
            home / ".1C" / "1CEStart" / "ibases.v8i",
            home / ".1cv8" / "1C" / "1CEStart" / "ibases.v8i",
            home / ".config" / "1C" / "1CEStart" / "ibases.v8i",
            home / ".config" / "1C" / "1cestart" / "ibases.v8i",
        ]

        try:
            xdg = Path(os.environ.get("XDG_CONFIG_HOME") or (home / ".config"))
            candidates.extend([
                xdg / "1C" / "1CEStart" / "ibases.v8i",
                xdg / "1C" / "1cestart" / "ibases.v8i",
            ])
        except Exception:
            pass

        # Иногда 1С кладет файл глубже; fallback ограничиваем домашней папкой 1C.
        for root in [home / ".1C", home / ".1cv8"]:
            try:
                if root.exists():
                    candidates.extend(root.rglob("ibases.v8i"))
            except Exception:
                pass

        result = []
        seen = set()

        for path in candidates:
            try:
                path = Path(path).expanduser()
                key = str(path)

                if key not in seen and path.exists() and path.is_file():
                    seen.add(key)
                    result.append(path)
            except Exception:
                pass

        return result

    def read_ibases_v8i_text(self, path):
        """Читает ibases.v8i с учетом типовых кодировок."""
        path = Path(path)

        data = path.read_bytes()

        for enc in ("utf-8-sig", "utf-16", "utf-16le", "cp1251"):
            try:
                return data.decode(enc)
            except Exception:
                pass

        return data.decode("utf-8", errors="replace")

    def v8i_unquote(self, value):
        value = str(value or "").strip()

        if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
            value = value[1:-1]

        value = value.replace('""', '"')
        value = value.replace("\\\\", "\\")

        return value.strip()

    def v8i_get_quoted(self, text, key):
        """Достает Key="..." из строки подключения 1С."""
        text = str(text or "")
        rx = re.compile(r'(?i)(?:^|;)\s*' + re.escape(key) + r'\s*=\s*"((?:[^"]|"")*)"')
        m = rx.search(text)

        if not m:
            return ""

        return self.v8i_unquote('"' + m.group(1) + '"')

    def v8i_parse_connect(self, connect):
        connect = str(connect or "").strip()

        if not connect:
            return "file", ""

        file_path = self.v8i_get_quoted(connect, "File")
        if file_path:
            return "file", file_path

        srv = self.v8i_get_quoted(connect, "Srvr")
        ref = self.v8i_get_quoted(connect, "Ref")
        if srv or ref:
            return "server", (srv + "\\" + ref).strip("\\")

        for web_key in ("ws", "http", "https"):
            url = self.v8i_get_quoted(connect, web_key)
            if url:
                return "web", url

        # Иногда web может лежать без стандартного ключа.
        m = re.search(r'(https?://[^";]+)', connect, flags=re.I)
        if m:
            return "web", m.group(1)

        return "file", connect

    def parse_ibases_v8i_file(self, path):
        """Парсит ibases.v8i в список словарей баз."""
        text = self.read_ibases_v8i_text(path)

        sections = []
        current = None

        for raw in text.splitlines():
            line = raw.strip()

            if not line or line.startswith(";") or line.startswith("#"):
                continue

            if line.startswith("[") and line.endswith("]"):
                name = line[1:-1].strip()

                current = {
                    "name": name,
                    "_source_v8i": str(path),
                }
                sections.append(current)
                continue

            if current is None:
                continue

            if "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()

            current[key] = self.v8i_unquote(value)

        imported = []

        for sec in sections:
            item = self.normalize_imported_base_from_v8i(sec)

            if item:
                imported.append(item)

        return imported

    def normalize_imported_base_from_v8i(self, sec):
        name = str(sec.get("name") or "").strip()

        if not name:
            return None

        connect_raw = str(sec.get("Connect") or sec.get("connect") or "").strip()

        # Секции-группы в ibases.v8i могут быть без Connect — их не добавляем как базу.
        if not connect_raw:
            return None

        kind, connect = self.v8i_parse_connect(connect_raw)

        folder = (
            sec.get("Folder")
            or sec.get("folder")
            or sec.get("Каталог")
            or ""
        )

        group = str(folder or "").replace("\\", "/").strip().strip("/")

        if not group:
            group = "Без группы"

        # Для file-базы 1С иногда хранит путь с File="..."
        if kind == "file":
            connect = str(Path(connect).expanduser()) if connect else ""

        item = {
            "name": name,
            "group": group,
            "kind": kind,
            "type": kind,
            "connect": connect,
            "platform_version": "8.3",
            "source": "ibases.v8i",
            "_source_v8i": str(sec.get("_source_v8i") or ""),
        }

        # Если логин есть в строке подключения, переносим.
        user = self.v8i_get_quoted(connect_raw, "Usr")
        if user:
            item["user"] = user
            item["login"] = user
            item["username"] = user

        if kind == "server":
            try:
                srv, db = self.parse_server_connect(connect)
                item["server_name"] = srv
                item["db_name"] = db
            except Exception:
                pass

        return item

    def base_sync_key(self, base):
        kind = str(base.get("kind") or base.get("type") or "").strip().lower()
        connect = str(base.get("connect") or "").strip()

        if kind and connect:
            return (kind, connect.lower())

        name = str(base.get("name") or "").strip().lower()
        group = str(base.get("group") or "").strip().lower()

        return ("name", group, name)

    def merge_imported_1c_bases(self, imported):
        existing_by_key = {}

        for base in self.bases or []:
            if not isinstance(base, dict):
                continue

            if base.get("is_group") or base.get("kind") == "group" or base.get("type") == "group":
                continue

            key = self.base_sync_key(base)
            existing_by_key[key] = base

        added = 0
        updated = 0
        skipped = 0

        for item in imported:
            key = self.base_sync_key(item)
            existing = existing_by_key.get(key)

            if existing is not None:
                # Обновляем только безопасные поля. Пароли/секреты не трогаем.
                for field in ("name", "group", "kind", "type", "connect", "server_name", "db_name"):
                    value = item.get(field)

                    if value:
                        existing[field] = value

                if not existing.get("platform_version"):
                    existing["platform_version"] = item.get("platform_version") or "8.3"

                if item.get("user") and not (existing.get("user") or existing.get("login") or existing.get("username")):
                    existing["user"] = item.get("user")
                    existing["login"] = item.get("user")
                    existing["username"] = item.get("user")

                existing["source"] = item.get("source") or existing.get("source") or "ibases.v8i"
                existing["_source_v8i"] = item.get("_source_v8i") or existing.get("_source_v8i") or ""

                updated += 1
                continue

            self.bases.append(item)
            existing_by_key[key] = item
            added += 1

        return added, updated, skipped

    def on_sync_with_1c_list(self, *_):
        """Синхронизация с ibases.v8i 1С."""
        self._append_log("=== Синхронизация со списком баз 1С ===")

        try:
            paths = self.ibases_v8i_candidate_paths()

            if not paths:
                self._append_log("Файлы ibases.v8i не найдены.")
                self._append_log("Проверенные места: ~/.1C/1cestart, ~/.1C/1CEStart, ~/.1cv8/1C/1CEStart")
                return

            all_imported = []

            for path in paths:
                try:
                    items = self.parse_ibases_v8i_file(path)
                    self._append_log(f"Найден список 1С: {path}")
                    self._append_log(f"Баз в файле: {len(items)}")
                    all_imported.extend(items)
                except Exception as e:
                    self._append_log(f"Не удалось прочитать {path}: {type(e).__name__}: {e}")

            # Убираем дубли между несколькими ibases.v8i.
            unique = {}
            for item in all_imported:
                unique[self.base_sync_key(item)] = item

            imported = list(unique.values())

            if not imported:
                self._append_log("В ibases.v8i не найдено баз с заполненной строкой Connect.")
                return

            added, updated, skipped = self.merge_imported_1c_bases(imported)

            self.config["bases"] = self.bases

            try:
                self.save_config_safe()
            except Exception:
                save_json(self.config_path, self.config)

            self._load_bases_tree()

            try:
                self.base_tree.expand_all()
            except Exception:
                pass

            try:
                self.update_bases_status()
            except Exception:
                pass

            self._append_log(f"Синхронизация завершена. Добавлено: {added}; обновлено: {updated}; пропущено: {skipped}.")

        except Exception as e:
            self._append_log(f"ОШИБКА синхронизации со списком баз 1С: {type(e).__name__}: {e}")

            try:
                self.show_error("Ошибка синхронизации", f"{type(e).__name__}: {e}")
            except Exception:
                pass

    def build_1c_launch_args(self, mode="ENTERPRISE"):
        # UPDATER1C_DESIGNER_LAUNCH_FIX_20260626
        mode = (mode or "ENTERPRISE").upper()
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

        # UPDATER1C_DESIGNER_LAUNCH_FIX_20260626
        # Защита от старых веток кода: при запуске конфигуратора не допускаем ENTERPRISE.
        launch_mode = (mode or "ENTERPRISE").upper()
        if launch_mode == "DESIGNER" and args:
            if len(args) == 1:
                args.append("DESIGNER")
            else:
                first_mode = str(args[1]).upper()
                if first_mode in ("ENTERPRISE", "DESIGNER"):
                    args[1] = "DESIGNER"
                else:
                    args.insert(1, "DESIGNER")

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

    def set_download_operation_progress(
        self,
        percent,
        text="",
        base="-",
        release="-",
        step="-",
        action="Скачивание файла обновления",
    ):
        """Обновляет нижний progressbar и правую панель 'Текущая операция'."""
        try:
            from datetime import datetime

            if not getattr(self, "_download_operation_started", ""):
                self._download_operation_started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            value = max(0, min(100, int(percent or 0)))
            status_text = text or f"{value}%"

            self.set_report_progress(value, status_text)

            self.set_current_operation(
                base=base or "-",
                release=release or "-",
                step=step or "-",
                action=action or "Скачивание файла обновления",
                mode="DOWNLOAD",
                status=status_text,
                pid="-",
                started=getattr(self, "_download_operation_started", "-") or "-",
            )

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

        self._download_operation_started = ""
        GLib.idle_add(
            self.set_download_operation_progress,
            0,
            "Скачивание обновлений: подготовка",
            "-",
            "-",
            "Подготовка",
            "Скачивание обновлений",
        )
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

                if ("бухгалтер" in hay and "базов" in hay) or "accountingbase" in hay.replace(" ", "").replace("_", "").replace("-", ""):
                    program = "AccountingBase"
                elif "бухгалтер" in hay or "accounting" in hay:
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
                    release_dir = update_release_dir(self.settings, program, release)

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
                        try:
                            validate_downloaded_update_file(dest)
                            if dest.stat().st_size > 1024 * 1024:
                                self._append_log(f"Файл уже есть: {dest}")
                            else:
                                self._append_log(f"Файл обновления слишком маленький, удаляю: {dest}")
                                dest.unlink()
                        except Exception as e:
                            self._append_log(f"Найден битый файл обновления, удаляю: {dest}")
                            self._append_log(str(e))
                            try:
                                dest.unlink()
                            except Exception:
                                pass

                    if not dest.exists():
                        download_config_update_file_with_fallback(
                            item=item,
                            program=program,
                            release=release,
                            primary_url=url,
                            dest=dest,
                            login=login,
                            password=password,
                            log_func=self._append_log,
                            progress_func=lambda percent, done=0, total=0, text="": GLib.idle_add(
                                self.set_download_operation_progress,
                                percent,
                                text or f"{program} {release}: {percent}%",
                                name,
                                release,
                                "Скачивание",
                                "Скачивание файла обновления",
                            ),
                        )
                self._append_log(f"Скачивание обновлений: база {name} завершено")
                GLib.idle_add(
                    self.idle_set_current_operation,
                    {
                        "base": name,
                        "release": release if "release" in locals() else "-",
                        "step": "Готово",
                        "action": "Скачивание обновлений",
                        "mode": "DOWNLOAD",
                        "status": "Скачивание завершено",
                        "pid": "-",
                        "started": getattr(self, "_download_operation_started", "-") or "-",
                    },
                )
                GLib.idle_add(self.set_report_progress, 100, f"Скачивание обновлений: {name} завершено")

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

    def ask_yes_no(self, title, message, default_no=True):
        dlg = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.YES_NO,
            text=title,
        )
        dlg.format_secondary_text(message)
        try:
            dlg.set_default_response(Gtk.ResponseType.NO if default_no else Gtk.ResponseType.YES)
        except Exception:
            pass
        response = dlg.run()
        dlg.destroy()
        return response == Gtk.ResponseType.YES

    def prepared_base_for_update(self, idx: int) -> dict:
        base = dict(self.bases[idx])
        user = str(base.get("user") or base.get("username") or base.get("login") or "").strip()
        password = self.get_base_password(self.bases[idx])
        base["user"] = user
        base["password"] = password
        return base

    def guess_update_program_for_base(self, base: dict) -> str:
        program = str(base.get("update_program_name") or base.get("program") or "").strip()
        if program:
            return program
        return self.guess_update_program_name_from_metadata(
            base.get("config_name") or "",
            base.get("config_synonym") or "",
            base.get("name") or "",
        )

    def update_base_version_after_release(self, idx: int, release_version: str, log):
        new_version = (release_version or "").replace("_", ".").strip()
        if not new_version:
            return
        try:
            if 0 <= idx < len(self.bases) and isinstance(self.bases[idx], dict):
                self.bases[idx]["config_version"] = new_version
                self.config["bases"] = self.bases
                save_json(self.config_path, self.config)
                GLib.idle_add(self.refresh_bases_tree_keep_selected, self.bases[idx])
                log(f"Версия конфигурации в карточке базы обновлена: {new_version}")
            else:
                log(f"Не удалось сохранить версию {new_version}: индекс базы не найден.")
        except Exception as e:
            log(f"Не удалось сохранить новую версию конфигурации {new_version}: {type(e).__name__}: {e}")

    def run_1c_update_batch(self, log, base: dict, mode: str, extra_args: list, timeout: int, hidden_xvfb: bool = False):
        args = build_1c_args(base, self.settings, mode)
        args.extend(extra_args)

        final_args = list(args)
        if hidden_xvfb:
            xvfb = shutil.which("xvfb-run")
            if xvfb:
                final_args = [xvfb, "-a", "-s", "-screen 0 1280x1024x24"] + final_args
                log("Скрытый запуск через xvfb-run включен.")
            else:
                log("xvfb-run не найден. Запуск будет обычным. Установить можно: sudo apt install xvfb -y")

        env = self.onec_command_env_for_maintenance()
        log("Команда: " + self.mask_1c_command_for_log(final_args))
        if env.get("LD_PRELOAD"):
            log("LD_PRELOAD: " + env.get("LD_PRELOAD", ""))

        proc = subprocess.Popen(
            final_args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            env=env,
        )
        try:
            out, _ = proc.communicate(timeout=max(1, int(timeout)))
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except Exception:
                pass
            out, _ = proc.communicate()
            if out and out.strip():
                log(out.strip()[-6000:])
            raise RuntimeError(f"Таймаут операции 1С: {timeout} сек.")

        if out and out.strip():
            log(out.strip()[-6000:])

        log(f"Код завершения: {proc.returncode}")
        if proc.returncode != 0:
            raise RuntimeError(f"Команда завершилась с кодом {proc.returncode}")

    def file_base_1cd_path(self, base: dict) -> Path:
        kind, connect = normalize_base_kind_and_connect(base)
        if kind != "file":
            raise RuntimeError("Файловый архив 1Cv8.1CD доступен только для файловой базы.")
        base_dir = Path(connect).expanduser()
        one_cd = base_dir / "1Cv8.1CD"
        if not one_cd.exists():
            raise RuntimeError(f"Файл базы не найден: {one_cd}")
        return one_cd

    def make_file_1cd_backup_for_update(self, log, base: dict, release: str) -> Path:
        import tarfile
        one_cd = self.file_base_1cd_path(base)
        backup_root = Path(self.settings.get("backup_dir") or self.settings.get("backups_dir") or "/mnt/DataStore/Updater1C/1c-backups").expanduser()
        out_dir = backup_root / safe_name(base.get("name") or "base")
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / f"{safe_name(base.get('name') or 'base')}_before_{safe_name(release)}_{now_stamp()}_1Cv8.1CD.tar.gz"
        log("=== Архивирование файловой базы 1Cv8.1CD ===")
        log(f"Источник: {one_cd}")
        log(f"Архив: {dest}")
        with tarfile.open(dest, "w:gz") as tar:
            tar.add(one_cd, arcname="1Cv8.1CD")
        log(f"Архив создан: {dest}")
        return dest

    def make_dt_backup_for_update(self, log, base: dict, release: str, vals: dict) -> Path:
        backup_root = Path(self.settings.get("backup_dir") or self.settings.get("backups_dir") or "/mnt/DataStore/Updater1C/1c-backups").expanduser()
        out_dir = backup_root / safe_name(base.get("name") or "base")
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / f"{safe_name(base.get('name') or 'base')}_before_{safe_name(release)}_{now_stamp()}.dt"
        reports_dir = Path(self.settings.get("reports_dir") or self.settings.get("report_dir") or "/mnt/DataStore/Updater1C/1c-update-reports").expanduser()
        reports_dir.mkdir(parents=True, exist_ok=True)
        log_file = reports_dir / f"{safe_name(base.get('name') or 'base')}_DumpIB_before_{safe_name(release)}_{now_stamp()}.log"
        log("=== Архивирование базы в .dt ===")
        self.run_1c_update_batch(
            log,
            base,
            "DESIGNER",
            ["/DumpIB", str(dest), "/Out", str(log_file), "-NoTruncate"],
            timeout=vals.get("timeout_seconds", 7200),
            hidden_xvfb=vals.get("hidden_xvfb", False),
        )
        if tail_file(log_file).strip():
            log("Хвост лога DumpIB:")
            log(tail_file(log_file))
        log(f"Архив .dt создан: {dest}")
        return dest

    def make_pre_update_backup(self, log, base: dict, release: str, vals: dict):
        if not vals.get("make_backup"):
            log("Резервная копия перед обновлением отключена пользователем.")
            return None

        kind, _connect = normalize_base_kind_and_connect(base)
        backup_type = int(vals.get("backup_type", 0))

        if backup_type == 2:
            return self.make_dt_backup_for_update(log, base, release, vals)

        if backup_type == 1:
            if kind == "file":
                return self.make_file_1cd_backup_for_update(log, base, release)
            log("Для серверной базы файловый архив 1Cv8.1CD недоступен, будет создана .dt.")
            return self.make_dt_backup_for_update(log, base, release, vals)

        if kind == "file":
            return self.make_file_1cd_backup_for_update(log, base, release)

        if kind == "server":
            return self.make_dt_backup_for_update(log, base, release, vals)

        log("Для этого типа базы резервная копия не создается.")
        return None

    def restore_file_1cd_backup_for_update(self, log, base: dict, backup_file: Path):
        import tarfile
        one_cd = self.file_base_1cd_path(base)
        backup_file = Path(backup_file)
        if not backup_file.exists():
            raise RuntimeError(f"Архив отката не найден: {backup_file}")
        failed = one_cd.with_name(f"1Cv8.1CD.failed_{now_stamp()}")
        log("=== Откат файловой базы из архива ===")
        log(f"Текущий файл базы будет сохранен как: {failed}")
        shutil.move(str(one_cd), str(failed))
        with tarfile.open(backup_file, "r:gz") as tar:
            member = tar.getmember("1Cv8.1CD")
            tar.extract(member, path=str(one_cd.parent))
        if not one_cd.exists():
            raise RuntimeError("После распаковки файл 1Cv8.1CD не появился.")
        log("Откат файловой базы выполнен.")

    def restore_pre_update_backup(self, log, base: dict, backup_file, vals: dict):
        if not backup_file:
            log("Откат невозможен: резервная копия не создавалась.")
            return
        backup_file = Path(backup_file)
        if backup_file.name.lower().endswith(".tar.gz"):
            self.restore_file_1cd_backup_for_update(log, base, backup_file)
            return
        if backup_file.suffix.lower() == ".dt":
            reports_dir = Path(self.settings.get("reports_dir") or self.settings.get("report_dir") or "/mnt/DataStore/Updater1C/1c-update-reports").expanduser()
            reports_dir.mkdir(parents=True, exist_ok=True)
            log_file = reports_dir / f"{safe_name(base.get('name') or 'base')}_RestoreIB_{now_stamp()}.log"
            log(f"Откат через /RestoreIB из {backup_file}")
            self.run_1c_update_batch(
                log,
                base,
                "DESIGNER",
                ["/RestoreIB", str(backup_file), "/Out", str(log_file), "-NoTruncate"],
                timeout=vals.get("timeout_seconds", 7200),
                hidden_xvfb=vals.get("hidden_xvfb", False),
            )
            if tail_file(log_file).strip():
                log("Хвост лога RestoreIB:")
                log(tail_file(log_file))
            return
        log(f"Автооткат для типа копии {backup_file} не выполняется автоматически.")

    def run_update_handlers_for_base(self, log, base: dict, release_name: str, vals: dict):
        reports_dir = Path(self.settings.get("reports_dir") or self.settings.get("report_dir") or "/mnt/DataStore/Updater1C/1c-update-reports").expanduser()
        reports_dir.mkdir(parents=True, exist_ok=True)
        log_file = reports_dir / f"{safe_name(base.get('name') or 'base')}_03_Handlers_{safe_name(release_name)}_{now_stamp()}.log"
        extra = [
            "/DisableStartupDialogs",
            "/DisableStartupMessages",
            "/C", "ВыполнитьОбновлениеИЗавершитьРаботу",
            "/Out", str(log_file),
            "-NoTruncate",
        ]
        log("Шаг 3. Запуск обработчиков обновления в режиме 1С:Предприятие...")
        self.run_1c_update_batch(
            log,
            base,
            "ENTERPRISE",
            extra,
            timeout=vals.get("timeout_seconds", 7200),
            hidden_xvfb=True,
        )
        if tail_file(log_file).strip():
            log("Хвост лога обработчиков:")
            log(tail_file(log_file))
        log(f"Лог обработчиков: {log_file}")

    def on_auto_update(self, *_):
        indexes = self.gtk_operation_target_indices()
        if not indexes:
            self.switch_to_report_tab()
            self._append_log("ОШИБКА: не выбраны базы для установки обновлений.")
            return

        dlg = AutoUpdateDialog(self)
        response = dlg.run()
        vals = dlg.get_values() if response == Gtk.ResponseType.OK else None
        dlg.destroy()

        if response != Gtk.ResponseType.OK:
            self.switch_to_report_tab()
            self._append_log("Автообновление отменено.")
            return

        if vals.get("make_backup"):
            if not self.ask_yes_no("Подтверждение обновления", "Перед обновлением будет создана резервная копия. Продолжить?"):
                self.switch_to_report_tab()
                self._append_log("Автообновление отменено пользователем.")
                return
        else:
            if not self.ask_yes_no("Без резервной копии", "Запустить автообновление без резервной копии? Это рискованно."):
                self.switch_to_report_tab()
                self._append_log("Автообновление отменено пользователем.")
                return

        def work(log):
            log(f"Баз к автообновлению: {len(indexes)}")
            log("Режим: полностью пакетный. Ручной выбор релиза и ручное нажатие кнопок не используется.")
            log(f"Таймаут одной операции: {vals.get('timeout_seconds', 7200)} сек.")

            for pos, idx in enumerate(indexes, 1):
                if not (0 <= idx < len(self.bases)):
                    continue

                base = self.prepared_base_for_update(idx)
                name = base.get("name") or f"base_{idx}"
                program = self.guess_update_program_for_base(base)
                current_version = str(base.get("config_version") or "").strip()
                backup_file = None

                log("")
                log(f"[{pos}/{len(indexes)}] Автообновление базы: {name}")
                log(f"Код программы обновлений: {program or '<не заполнен>'}")
                log(f"Текущая версия в карточке базы: {current_version or '<не заполнена>'}")

                try:
                    kind, _connect = normalize_base_kind_and_connect(base)
                    if kind == "web":
                        log("ПРОПУСК: web-базы через DESIGNER по URL не обновляются.")
                        continue

                    if not program:
                        log("ПРОПУСК: не заполнен код программы обновлений 1С. Сначала выполните Проверить настройки.")
                        continue

                    # Проверяем платформу заранее, чтобы ошибка была понятной до резервного копирования.
                    _ = select_platform_exe_for_base(base, self.settings, "DESIGNER")
                    if not _:
                        log("ОШИБКА: платформа 1С не найдена.")
                        continue

                    steps = find_downloaded_update_steps(self.settings, program, current_version, log)
                    if not steps:
                        log(f"Не найдены скачанные релизы в {update_program_dir(self.settings, program)}. Сначала нажми «Скачать обновления».")
                        continue

                    if current_version:
                        filtered = []
                        for rel, cfu, folder in steps:
                            rel_ver = rel.replace("_", ".")
                            if version_key(rel_ver) > version_key(current_version):
                                filtered.append((rel, cfu, folder))
                            else:
                                log(f"Пропуск уже примененного/старого релиза: {rel} <= {current_version}")
                        steps = filtered

                    if not steps:
                        log("Нет релизов новее текущей версии базы.")
                        continue

                    log("Последовательность пакетного обновления:")
                    for rel, cfu, _folder in steps:
                        log(f"  {rel}: {cfu}")

                    backup_file = self.make_pre_update_backup(log, base, steps[0][0], vals)
                    last_release = current_version

                    reports_dir = Path(self.settings.get("reports_dir") or self.settings.get("report_dir") or "/mnt/DataStore/Updater1C/1c-update-reports").expanduser()
                    reports_dir.mkdir(parents=True, exist_ok=True)

                    for rel, cfu, _folder in steps:
                        rel_safe = safe_name(rel)
                        log("")
                        log(f"=== Релиз {rel} ===")

                        log_update_cfg = reports_dir / f"{safe_name(name)}_01_UpdateCfg_{rel_safe}_{now_stamp()}.log"
                        extra_update = [
                            "/DisableStartupDialogs",
                            "/DisableStartupMessages",
                            "/UpdateCfg", str(cfu),
                            "/Out", str(log_update_cfg),
                            "-NoTruncate",
                        ]
                        log("Шаг 1. Пакетное обновление конфигурации /UpdateCfg без ручного выбора релиза...")
                        self.run_1c_update_batch(
                            log,
                            base,
                            "DESIGNER",
                            extra_update,
                            timeout=vals.get("timeout_seconds", 7200),
                            hidden_xvfb=vals.get("hidden_xvfb", False),
                        )
                        if tail_file(log_update_cfg).strip():
                            log("Хвост лога /UpdateCfg:")
                            log(tail_file(log_update_cfg))
                        log(f"Лог /UpdateCfg: {log_update_cfg}")

                        if vals.get("update_db_cfg"):
                            log_update_db = reports_dir / f"{safe_name(name)}_02_UpdateDBCfg_{rel_safe}_{now_stamp()}.log"
                            extra_db = [
                                "/DisableStartupDialogs",
                                "/DisableStartupMessages",
                                "/UpdateDBCfg",
                            ]
                            if vals.get("server_update") and kind == "server":
                                extra_db.append("-Server")
                            if vals.get("dynamic_update"):
                                extra_db.append("-Dynamic+")
                            extra_db.extend(["/Out", str(log_update_db), "-NoTruncate"])

                            log("Шаг 2. Пакетное обновление конфигурации базы данных /UpdateDBCfg...")
                            self.run_1c_update_batch(
                                log,
                                base,
                                "DESIGNER",
                                extra_db,
                                timeout=vals.get("timeout_seconds", 7200),
                                hidden_xvfb=vals.get("hidden_xvfb", False),
                            )
                            if tail_file(log_update_db).strip():
                                log("Хвост лога /UpdateDBCfg:")
                                log(tail_file(log_update_db))
                            log(f"Лог /UpdateDBCfg: {log_update_db}")
                        else:
                            log("Шаг 2 пропущен: /UpdateDBCfg отключен в диалоге.")

                        if vals.get("run_handlers"):
                            self.run_update_handlers_for_base(log, base, rel, vals)
                        else:
                            log("Шаг 3 пропущен: обработчики обновления отключены в диалоге.")

                        last_release = rel.replace("_", ".")
                        self.update_base_version_after_release(idx, last_release, log)
                        log(f"Релиз {rel} применен. Версия в карточке обновлена на {last_release}.")

                    log(f"Автообновление базы {name} завершено. Последний примененный релиз: {last_release}")

                except Exception as e:
                    log(f"ОШИБКА автообновления базы {name}: {type(e).__name__}: {e}")
                    if vals.get("restore_on_error"):
                        try:
                            log("Пробую выполнить откат из резервной копии...")
                            self.restore_pre_update_backup(log, base, backup_file, vals)
                        except Exception as rollback_error:
                            log(f"ОШИБКА отката: {type(rollback_error).__name__}: {rollback_error}")
                    if vals.get("stop_on_error"):
                        raise

        self.run_in_background("Автообновление", work)

    def on_download_platform(self, *_):
        dlg = PlatformDownloadDialog(self)
        response = dlg.run()
        if response == Gtk.ResponseType.OK:
            dlg.on_download_queue()
        dlg.destroy()


def main():
    MainWindow()
    Gtk.main()



# === Canonical 1C updates storage layout ===
#
# /mnt/DataStore/Updater1C/1c-updates/
# └── 1c/
#     └── AccountingBase/
#         └── 3_0_199_13/
#             ├── 1cv8.cfu
#             ├── 1cv8.mft
#             ├── ReadMe.txt
#             └── ...
#
# Важно:
# - vendor хранится отдельной первой папкой: 1c
# - код конфигурации хранится латиницей: AccountingBase
# - версия релиза хранится через "_": 3_0_199_13
# - ZIP от 1С распаковывается прямо в папку релиза
# - русские имена внутри ZIP восстанавливаются через CP866


def update_storage_root_dir(settings: dict) -> Path:
    return Path(settings.get("updates_dir") or "/mnt/DataStore/Updater1C/1c-updates")


def update_vendor_folder(vendor: str = "1c") -> str:
    value = str(vendor or "1c").strip()

    low = value.lower().replace("с", "c")

    if low in ("1c", "1с", "фирма 1c", 'фирма "1c"', 'фирма "1с"'):
        return "1c"

    return safe_name(value)


def update_program_dir(settings: dict, program: str, vendor: str = "1c") -> Path:
    root = update_storage_root_dir(settings)
    return root / update_vendor_folder(vendor) / safe_name(program or "UnknownProgram")


def update_release_folder(version: str) -> str:
    value = str(version or "").strip().replace("\\", "/")

    if "/" in value:
        value = value.rstrip("/").split("/")[-1]

    value = value.replace(".", "_").replace("-", "_").strip("_")

    return safe_name(value or "unknown_release")


def version_to_update_folder(version: str) -> str:
    return update_release_folder(version)


def update_release_dir(settings: dict, program: str, release: str, vendor: str = "1c") -> Path:
    return update_program_dir(settings, program, vendor) / update_release_folder(release)


def decode_1c_zip_member_name(info) -> str:
    """В ZIP от 1С русские имена часто лежат в DOS/CP866 без UTF-8-флага."""
    name = info.filename or ""

    if getattr(info, "flag_bits", 0) & 0x800:
        return name.replace("\\", "/")

    try:
        raw = name.encode("cp437")
    except Exception:
        return name.replace("\\", "/")

    candidates = []

    for enc in ("cp866", "cp1251", "utf-8"):
        try:
            candidates.append(raw.decode(enc))
        except Exception:
            pass

    def score(value: str) -> int:
        cyr = sum(1 for ch in value if "А" <= ch <= "я" or ch in "Ёё")
        bad = sum(1 for ch in value if ch in "╨╩╬╧╠╡░▒▓")
        return cyr * 10 - bad * 5

    if candidates:
        return max(candidates, key=score).replace("\\", "/")

    return name.replace("\\", "/")


def safe_join_zip_path(root: Path, member_name: str) -> Path:
    import posixpath

    root = Path(root).resolve()
    member_name = str(member_name or "").replace("\\", "/")
    member_name = posixpath.normpath(member_name)

    if member_name in ("", "."):
        raise RuntimeError("Пустое имя файла в ZIP")

    if member_name.startswith("/") or member_name.startswith("../") or "/../" in member_name:
        raise RuntimeError(f"Опасный путь в ZIP: {member_name}")

    target = (root / member_name).resolve()

    if target != root and not str(target).startswith(str(root) + "/"):
        raise RuntimeError(f"Выход за пределы папки распаковки ZIP: {member_name}")

    return target


def unpack_1c_zip_preserve_encoding(zip_path: Path, target_dir: Path, log_func=None) -> None:
    import shutil
    import zipfile

    zip_path = Path(zip_path)
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    if log_func:
        log_func(f"Распаковка ZIP 1С в папку релиза: {zip_path}")
        log_func(f"Папка релиза: {target_dir}")

    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            member_name = decode_1c_zip_member_name(info)
            target = safe_join_zip_path(target_dir, member_name)

            if info.is_dir() or member_name.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
                continue

            target.parent.mkdir(parents=True, exist_ok=True)

            with zf.open(info, "r") as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)

    if log_func:
        log_func("ZIP 1С распакован. Русские имена восстановлены.")


def prepare_1c_downloaded_update_file(downloaded: Path, dest: Path, release: str, log_func=None) -> Path:
    """Если dlXX.1c.ru вернул ZIP, распаковываем его прямо в папку релиза."""
    import shutil
    import zipfile

    downloaded = Path(downloaded)
    dest = Path(dest)
    release_dir = dest.parent

    if not zipfile.is_zipfile(downloaded):
        return downloaded

    if log_func:
        log_func(f"Скачанный файл оказался ZIP-архивом: {downloaded}")

    archive_dir = release_dir / "_archives"
    archive_dir.mkdir(parents=True, exist_ok=True)

    archive_path = archive_dir / f"downloaded_{update_release_folder(release)}.zip"

    if archive_path.exists():
        archive_path.unlink()

    shutil.move(str(downloaded), str(archive_path))

    unpack_1c_zip_preserve_encoding(archive_path, release_dir, log_func)

    found = find_update_file_in_dir(release_dir)

    if not found:
        raise RuntimeError(f"После распаковки ZIP не найден файл .cfu/.cf: {archive_path}")

    found = Path(found)

    if found.resolve() != dest.resolve():
        if dest.exists():
            dest.unlink()
        shutil.copy2(found, dest)
        found = dest

    validate_downloaded_update_file(found)

    if log_func:
        log_func(f"Файл обновления готов: {found}")

    return found



# === Compatibility binding: MainWindow.idle_set_current_operation ===
# GLib.idle_add не принимает keyword-аргументы, поэтому текущую операцию
# обновляем через dict, переданный позиционным аргументом.
def _updater1c_idle_set_current_operation(self, data):
    try:
        data = data or {}

        self.set_current_operation(
            base=data.get("base", "-"),
            release=data.get("release", "-"),
            step=data.get("step", "-"),
            action=data.get("action", "-"),
            mode=data.get("mode", "-"),
            status=data.get("status", "-"),
            pid=data.get("pid", "-"),
            started=data.get("started", "-"),
        )

    except Exception:
        pass

    return False


try:
    MainWindow.idle_set_current_operation = _updater1c_idle_set_current_operation
except NameError:
    pass


# UPDATER1C_DT_AUTOFILL_NO_COMBO_LOCK_BEGIN
# Автозаполнение .dt/.cf без вмешательства в поле "Действие".
# Важно: этот патч НЕ меняет combo "Действие", чтобы пользователь мог выбрать пункт сам.
try:
    import re as _u1c_re
    import json as _u1c_json
    from pathlib import Path as _u1c_Path

    try:
        from gi.repository import Gtk as _u1c_Gtk, GLib as _u1c_GLib
        Gtk = globals().get("Gtk") or _u1c_Gtk
        GLib = globals().get("GLib") or _u1c_GLib
    except Exception:
        Gtk = globals().get("Gtk")
        GLib = globals().get("GLib")

    def _u1c_norm(text):
        return _u1c_re.sub(r"\s+", " ", (text or "").replace(":", "")).strip().lower()

    def _u1c_clean_name(path_text):
        name = _u1c_Path((path_text or "").strip()).name
        low = name.lower()

        for ext in (".dt", ".cf", ".cfu"):
            if low.endswith(ext):
                name = name[:-len(ext)]
                break

        name = _u1c_re.sub(r"\s+", "", name)
        name = _u1c_re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
        name = _u1c_re.sub(r"_+", "_", name).strip("._-")
        return name or "NewBase"

    def _u1c_bases_dir():
        names = (
            "bases_dir",
            "base_dir",
            "bases_path",
            "base_path",
            "ibases_dir",
            "ibases_path",
            "default_bases_dir",
            "default_base_dir",
            "new_bases_dir",
            "local_bases_dir",
            "bases_root",
            "bases_root_dir",
            "infobases_dir",
            "onec_bases_dir",
        )

        try:
            cls = globals().get("AppConfig")
            if cls:
                cfg = cls()
                if hasattr(cfg, "load"):
                    cfg.load()

                for obj in (cfg, getattr(cfg, "settings", None)):
                    try:
                        d = vars(obj)
                    except Exception:
                        d = {}

                    for name in names:
                        val = d.get(name)
                        if isinstance(val, str) and val.startswith("/"):
                            return val
        except Exception:
            pass

        for cfg_path in (
            _u1c_Path.home() / ".config" / "updater1c-linux" / "config.json",
            _u1c_Path.home() / ".config" / "updater1c-linux" / "settings.json",
            _u1c_Path.home() / ".local" / "share" / "updater1c-linux" / "config.json",
            _u1c_Path.home() / ".local" / "share" / "updater1c-linux" / "settings.json",
        ):
            try:
                if not cfg_path.exists():
                    continue

                data = _u1c_json.loads(cfg_path.read_text(encoding="utf-8"))
                stack = [data]

                if isinstance(data, dict):
                    stack.extend(v for v in data.values() if isinstance(v, dict))

                for obj in stack:
                    for name in names:
                        val = obj.get(name) if isinstance(obj, dict) else None
                        if isinstance(val, str) and val.startswith("/"):
                            return val
            except Exception:
                pass

        for fallback in (
            "/mnt/DataStore/bases",
            "/mnt/Data/bases",
            "/mnt/DataStore/projects/bases",
            str(_u1c_Path.home() / "1c-bases"),
        ):
            try:
                if _u1c_Path(fallback).exists():
                    return fallback
            except Exception:
                pass

        return "/mnt/DataStore/bases"

    def _u1c_children(widget):
        result = []

        try:
            child = widget.get_first_child()
            while child is not None:
                result.append(child)
                child = child.get_next_sibling()
        except Exception:
            pass

        try:
            if hasattr(widget, "get_children"):
                result.extend(widget.get_children())
        except Exception:
            pass

        try:
            if hasattr(widget, "get_child"):
                child = widget.get_child()
                if child is not None:
                    result.append(child)
        except Exception:
            pass

        unique = []
        seen = set()
        for item in result:
            if item is not None and id(item) not in seen:
                seen.add(id(item))
                unique.append(item)

        return unique

    def _u1c_flatten(root):
        result = []
        seen = set()

        def walk(w):
            if w is None or id(w) in seen:
                return
            seen.add(id(w))
            result.append(w)
            for ch in _u1c_children(w):
                walk(ch)

        walk(root)
        return result

    def _u1c_text(w):
        for meth in ("get_text", "get_label", "get_title", "get_active_text"):
            try:
                if hasattr(w, meth):
                    val = getattr(w, meth)()
                    if val:
                        return str(val)
            except Exception:
                pass
        return ""

    def _u1c_is_entry(w):
        try:
            return hasattr(w, "get_text") and hasattr(w, "set_text") and "entry" in type(w).__name__.lower()
        except Exception:
            return False

    def _u1c_get(entry):
        try:
            return entry.get_text() or ""
        except Exception:
            return ""

    def _u1c_set(entry, value):
        try:
            entry.set_text(value or "")
            return True
        except Exception:
            return False

    def _u1c_pos(root, w):
        try:
            ok, x, y = w.translate_coordinates(root, 0, 0)
            if ok:
                a = w.get_allocation()
                return int(x), int(y), int(a.width), int(a.height)
        except Exception:
            pass

        try:
            a = w.get_allocation()
            return int(a.x), int(a.y), int(a.width), int(a.height)
        except Exception:
            return 0, 0, 0, 0

    def _u1c_find_label(root, fragments):
        fragments = [_u1c_norm(x) for x in fragments]

        for w in _u1c_flatten(root):
            txt = _u1c_norm(_u1c_text(w))
            if txt and any(f in txt for f in fragments):
                return w

        return None

    def _u1c_find_entry_on_label_row(root, fragments):
        label = _u1c_find_label(root, fragments)
        if label is None:
            return None

        lx, ly, lw, lh = _u1c_pos(root, label)
        lcy = ly + lh / 2

        best = None
        best_score = None

        for w in _u1c_flatten(root):
            if w is label or not _u1c_is_entry(w):
                continue

            x, y, ww, wh = _u1c_pos(root, w)
            cy = y + wh / 2

            if x <= lx:
                continue

            dy = abs(cy - lcy)
            if dy > max(18, lh, wh):
                continue

            score = dy * 1000 + abs(x - lx)

            if best is None or score < best_score:
                best = w
                best_score = score

        return best

    def _u1c_entries(root):
        return [w for w in _u1c_flatten(root) if _u1c_is_entry(w)]

    def _u1c_template_entry(root):
        for e in _u1c_entries(root):
            val = _u1c_get(e).strip().lower()
            if val.endswith((".dt", ".cf", ".cfu")):
                return e

        return _u1c_find_entry_on_label_row(root, ("шаблон 1с", "шаблон", ".dt", ".cf"))

    def _u1c_autofill(root):
        try:
            template = _u1c_template_entry(root)
            if template is None:
                return False

            dt_path = _u1c_get(template).strip()
            if not dt_path.lower().endswith((".dt", ".cf", ".cfu")):
                return False

            last = getattr(root, "_u1c_no_combo_last_dt_path", "")
            if last == dt_path:
                return True

            base_name = _u1c_clean_name(dt_path)
            base_path = str(_u1c_Path(_u1c_bases_dir()) / base_name)

            name_entry = _u1c_find_entry_on_label_row(root, ("имя базы",))
            folder_entry = _u1c_find_entry_on_label_row(root, ("путь к папке новой базы", "папке новой базы"))

            if name_entry is not None:
                _u1c_set(name_entry, base_name)

            if folder_entry is not None:
                _u1c_set(folder_entry, base_path)

            setattr(root, "_u1c_no_combo_last_dt_path", dt_path)
            return True

        except Exception as e:
            try:
                print("UPDATER1C_DT_AUTOFILL_NO_COMBO_LOCK_ERROR:", repr(e))
            except Exception:
                pass
            return False

    def _u1c_toplevels():
        try:
            return list(Gtk.Window.list_toplevels())
        except Exception:
            pass

        try:
            model = Gtk.Window.get_toplevels()
            return [model.get_item(i) for i in range(model.get_n_items())]
        except Exception:
            return []

    _u1c_connected = set()

    def _u1c_scan():
        try:
            if Gtk is None or GLib is None:
                return True

            for win in _u1c_toplevels():
                title = ""
                try:
                    title = win.get_title() or ""
                except Exception:
                    pass

                if "Добавление базы" not in title:
                    continue

                _u1c_autofill(win)

                if id(win) in _u1c_connected:
                    continue

                _u1c_connected.add(id(win))

                def changed_cb(_entry, _win=win):
                    try:
                        _u1c_autofill(_win)
                    except Exception as e:
                        try:
                            print("UPDATER1C_DT_AUTOFILL_CHANGED_ERROR:", repr(e))
                        except Exception:
                            pass

                # Подключаемся только к полям ввода. Combo "Действие" не трогаем.
                for e in _u1c_entries(win):
                    try:
                        e.connect("changed", changed_cb)
                    except Exception:
                        pass

                for w in _u1c_flatten(win):
                    txt = _u1c_norm(_u1c_text(w))
                    if txt in ("ok", "ок", "_ok"):
                        try:
                            w.connect("clicked", lambda _btn, _win=win: _u1c_autofill(_win))
                        except Exception:
                            pass

        except Exception as e:
            try:
                print("UPDATER1C_DT_AUTOFILL_SCAN_ERROR:", repr(e))
            except Exception:
                pass

        return True

    try:
        GLib.timeout_add(500, _u1c_scan)
    except Exception:
        pass

except Exception as e:
    try:
        print("UPDATER1C_DT_AUTOFILL_NO_COMBO_LOCK_INIT_ERROR:", repr(e))
    except Exception:
        pass
# UPDATER1C_DT_AUTOFILL_NO_COMBO_LOCK_END


# UPDATER1C_UPDATE_CHAIN_ZIP_SINGLE_PATCH_HOOK_BEGIN
try:
    import importlib.util as _u1c_update_chain_importlib_util
    from pathlib import Path as _u1c_update_chain_Path

    _u1c_update_chain_path = _u1c_update_chain_Path(__file__).with_name("updater1c_update_chain_patch.py")
    if _u1c_update_chain_path.exists():
        _u1c_update_chain_spec = _u1c_update_chain_importlib_util.spec_from_file_location(
            "updater1c_update_chain_patch",
            str(_u1c_update_chain_path),
        )
        _u1c_update_chain_mod = _u1c_update_chain_importlib_util.module_from_spec(_u1c_update_chain_spec)
        _u1c_update_chain_spec.loader.exec_module(_u1c_update_chain_mod)
        _u1c_update_chain_mod.install(globals())
except Exception as _u1c_update_chain_error:
    try:
        print("UPDATER1C_UPDATE_CHAIN_ZIP_SINGLE_PATCH_HOOK_ERROR:", repr(_u1c_update_chain_error))
    except Exception:
        pass
# UPDATER1C_UPDATE_CHAIN_ZIP_SINGLE_PATCH_HOOK_END

# UPDATER1C_BACKUP_PLUGIN_HOOK_BEGIN
# Подключение дополнительного модуля резервного копирования и имени .cf с релизом.
try:
    import importlib.util as _u1c_backup_importlib_util
    from pathlib import Path as _u1c_backup_Path

    _u1c_backup_plugin_path = _u1c_backup_Path(__file__).with_name("updater1c_backup_plugin.py")
    if _u1c_backup_plugin_path.exists():
        _u1c_backup_spec = _u1c_backup_importlib_util.spec_from_file_location(
            "updater1c_backup_plugin",
            str(_u1c_backup_plugin_path),
        )
        _u1c_backup_mod = _u1c_backup_importlib_util.module_from_spec(_u1c_backup_spec)
        _u1c_backup_spec.loader.exec_module(_u1c_backup_mod)
        _u1c_backup_mod.install(globals())
except Exception as _u1c_backup_plugin_error:
    try:
        print("UPDATER1C_BACKUP_PLUGIN_HOOK_ERROR:", repr(_u1c_backup_plugin_error))
    except Exception:
        pass
# UPDATER1C_BACKUP_PLUGIN_HOOK_END


# UPDATER1C_DBMS_PROFILES_PLUGIN_HOOK_BEGIN
# Подключение отдельного списка профилей СУБД.
try:
    import importlib.util as _u1c_dbms_importlib_util
    from pathlib import Path as _u1c_dbms_Path

    _u1c_dbms_plugin_path = _u1c_dbms_Path(__file__).with_name("updater1c_dbms_profiles_plugin.py")
    if _u1c_dbms_plugin_path.exists():
        _u1c_dbms_spec = _u1c_dbms_importlib_util.spec_from_file_location(
            "updater1c_dbms_profiles_plugin",
            str(_u1c_dbms_plugin_path),
        )
        _u1c_dbms_mod = _u1c_dbms_importlib_util.module_from_spec(_u1c_dbms_spec)
        _u1c_dbms_spec.loader.exec_module(_u1c_dbms_mod)
        _u1c_dbms_mod.install(globals())
except Exception as _u1c_dbms_plugin_error:
    try:
        print("UPDATER1C_DBMS_PROFILES_PLUGIN_HOOK_ERROR:", repr(_u1c_dbms_plugin_error))
    except Exception:
        pass
# UPDATER1C_DBMS_PROFILES_PLUGIN_HOOK_END


# UPDATER1C_BACKUP_HOTFIX_HOOK_BEGIN
# Hotfix формы архивирования: явный запуск, лог старта, правильное имя ZIP.
try:
    import importlib.util as _u1c_backup_hotfix_importlib_util
    from pathlib import Path as _u1c_backup_hotfix_Path

    _u1c_backup_hotfix_path = _u1c_backup_hotfix_Path(__file__).with_name("updater1c_backup_hotfix.py")
    if _u1c_backup_hotfix_path.exists():
        _u1c_backup_hotfix_spec = _u1c_backup_hotfix_importlib_util.spec_from_file_location(
            "updater1c_backup_hotfix",
            str(_u1c_backup_hotfix_path),
        )
        _u1c_backup_hotfix_mod = _u1c_backup_hotfix_importlib_util.module_from_spec(_u1c_backup_hotfix_spec)
        _u1c_backup_hotfix_spec.loader.exec_module(_u1c_backup_hotfix_mod)
        _u1c_backup_hotfix_mod.install(globals())
except Exception as _u1c_backup_hotfix_error:
    try:
        print("UPDATER1C_BACKUP_HOTFIX_HOOK_ERROR:", repr(_u1c_backup_hotfix_error))
    except Exception:
        pass
# UPDATER1C_BACKUP_HOTFIX_HOOK_END


# UPDATER1C_RESTORE_PLUGIN_HOOK_BEGIN
# Контекстное меню базы: восстановление из .dt/.zip архива; удаление "Скачать платформу" из меню строки.
try:
    import importlib.util as _u1c_restore_importlib_util
    from pathlib import Path as _u1c_restore_Path

    _u1c_restore_plugin_path = _u1c_restore_Path(__file__).with_name("updater1c_restore_plugin.py")
    if _u1c_restore_plugin_path.exists():
        _u1c_restore_spec = _u1c_restore_importlib_util.spec_from_file_location(
            "updater1c_restore_plugin",
            str(_u1c_restore_plugin_path),
        )
        _u1c_restore_mod = _u1c_restore_importlib_util.module_from_spec(_u1c_restore_spec)
        _u1c_restore_spec.loader.exec_module(_u1c_restore_mod)
        _u1c_restore_mod.install(globals())
except Exception as _u1c_restore_plugin_error:
    try:
        print("UPDATER1C_RESTORE_PLUGIN_HOOK_ERROR:", repr(_u1c_restore_plugin_error))
    except Exception:
        pass
# UPDATER1C_RESTORE_PLUGIN_HOOK_END


# UPDATER1C_OPS_UI_BRIDGE_HOOK_BEGIN
# Единый bridge для отчета, статуса, прогрессбара и кнопки восстановления.
try:
    import importlib.util as _u1c_ops_bridge_importlib_util
    from pathlib import Path as _u1c_ops_bridge_Path

    _u1c_ops_bridge_path = _u1c_ops_bridge_Path(__file__).with_name("updater1c_ops_ui_bridge.py")
    if _u1c_ops_bridge_path.exists():
        _u1c_ops_bridge_spec = _u1c_ops_bridge_importlib_util.spec_from_file_location(
            "updater1c_ops_ui_bridge",
            str(_u1c_ops_bridge_path),
        )
        _u1c_ops_bridge_mod = _u1c_ops_bridge_importlib_util.module_from_spec(_u1c_ops_bridge_spec)
        _u1c_ops_bridge_spec.loader.exec_module(_u1c_ops_bridge_mod)
        _u1c_ops_bridge_mod.install(globals())
except Exception as _u1c_ops_bridge_error:
    try:
        print("UPDATER1C_OPS_UI_BRIDGE_HOOK_ERROR:", repr(_u1c_ops_bridge_error))
    except Exception:
        pass
# UPDATER1C_OPS_UI_BRIDGE_HOOK_END


# UPDATER1C_BACKUP_COMBO_BRIDGE_HOOK_BEGIN
# Заполнение и принудительное обновление списка "Резервные копии (для отката)" файлами .dt/.zip выбранной базы.
try:
    import importlib.util as _u1c_backup_combo_importlib_util
    from pathlib import Path as _u1c_backup_combo_Path

    _u1c_backup_combo_path = _u1c_backup_combo_Path(__file__).with_name("updater1c_backup_combo_bridge.py")
    if _u1c_backup_combo_path.exists():
        _u1c_backup_combo_spec = _u1c_backup_combo_importlib_util.spec_from_file_location(
            "updater1c_backup_combo_bridge",
            str(_u1c_backup_combo_path),
        )
        _u1c_backup_combo_mod = _u1c_backup_combo_importlib_util.module_from_spec(_u1c_backup_combo_spec)
        _u1c_backup_combo_spec.loader.exec_module(_u1c_backup_combo_mod)
        _u1c_backup_combo_mod.install(globals())
except Exception as _u1c_backup_combo_error:
    try:
        print("UPDATER1C_BACKUP_COMBO_BRIDGE_HOOK_ERROR:", repr(_u1c_backup_combo_error))
    except Exception:
        pass
# UPDATER1C_BACKUP_COMBO_BRIDGE_HOOK_END

# UPDATER1C_ADAPTIVE_LIGHT_CONTENT_THEME_PATCH_BEGIN
# Адаптивная светлая рабочая область + принудительная перекраска заголовков Gtk.TreeViewColumn.
try:
    import html as _u1c_theme_html
    import gi as _u1c_theme_gi

    try:
        _u1c_theme_gi.require_version("Gtk", "3.0")
    except Exception:
        pass

    from gi.repository import Gtk as _u1c_theme_Gtk
    from gi.repository import Gdk as _u1c_theme_Gdk
    from gi.repository import GLib as _u1c_theme_GLib

    _U1C_ADAPTIVE_LIGHT_CONTENT_THEME_DONE = False

    def _u1c_theme_is_dark():
        try:
            settings = _u1c_theme_Gtk.Settings.get_default()
            if settings is None:
                return False

            try:
                if bool(settings.get_property("gtk-application-prefer-dark-theme")):
                    return True
            except Exception:
                pass

            try:
                theme_name = str(settings.get_property("gtk-theme-name") or "").lower()
                return ("dark" in theme_name) or ("black" in theme_name)
            except Exception:
                pass
        except Exception:
            pass

        return False

    def _u1c_rgba(color):
        rgba = _u1c_theme_Gdk.RGBA()
        rgba.parse(color)
        return rgba

    def _u1c_force_widget_colors(widget, bg="#e7e7e7", fg="#000000"):
        if widget is None:
            return

        bg_rgba = _u1c_rgba(bg)
        fg_rgba = _u1c_rgba(fg)

        states = [
            _u1c_theme_Gtk.StateFlags.NORMAL,
            _u1c_theme_Gtk.StateFlags.ACTIVE,
            _u1c_theme_Gtk.StateFlags.PRELIGHT,
            _u1c_theme_Gtk.StateFlags.SELECTED,
            _u1c_theme_Gtk.StateFlags.INSENSITIVE,
            _u1c_theme_Gtk.StateFlags.BACKDROP,
        ]

        for state in states:
            try:
                widget.override_background_color(state, bg_rgba)
            except Exception:
                pass

            try:
                widget.override_color(state, fg_rgba)
            except Exception:
                pass

        try:
            ctx = widget.get_style_context()
            ctx.add_class("u1c-force-light-header")
        except Exception:
            pass

        try:
            widget.queue_draw()
        except Exception:
            pass

    def _u1c_widget_children(widget):
        children = []

        try:
            children.extend(widget.get_children())
        except Exception:
            pass

        try:
            child = widget.get_child()
            if child is not None and child not in children:
                children.append(child)
        except Exception:
            pass

        try:
            tmp = []
            widget.foreach(lambda child, data: data.append(child), tmp)
            for child in tmp:
                if child not in children:
                    children.append(child)
        except Exception:
            pass

        return children

    def _u1c_collect_widgets(root):
        result = []
        stack = [root]
        seen = set()

        while stack:
            widget = stack.pop()
            if widget is None:
                continue

            ident = id(widget)
            if ident in seen:
                continue

            seen.add(ident)
            result.append(widget)

            for child in _u1c_widget_children(widget):
                stack.append(child)

        return result

    def _u1c_find_parent_button(widget):
        current = widget

        for _ in range(8):
            try:
                current = current.get_parent()
            except Exception:
                return None

            if current is None:
                return None

            try:
                if isinstance(current, _u1c_theme_Gtk.Button):
                    return current
            except Exception:
                pass

        return None

    def _u1c_force_tree_headers():
        try:
            windows = _u1c_theme_Gtk.Window.list_toplevels()
        except Exception:
            return True

        for win in windows:
            try:
                widgets = _u1c_collect_widgets(win)
            except Exception:
                widgets = []

            for tree in widgets:
                try:
                    if not isinstance(tree, _u1c_theme_Gtk.TreeView):
                        continue
                except Exception:
                    continue

                try:
                    tree.set_headers_visible(True)
                except Exception:
                    pass

                try:
                    columns = tree.get_columns()
                except Exception:
                    columns = []

                for column in columns:
                    try:
                        title = str(column.get_title() or " ")
                    except Exception:
                        title = " "

                    try:
                        current_widget = column.get_widget()
                    except Exception:
                        current_widget = None

                    need_replace = True
                    try:
                        if current_widget is not None and current_widget.get_name() == "u1c_tree_header_fixed":
                            need_replace = False
                    except Exception:
                        pass

                    if need_replace:
                        try:
                            label = _u1c_theme_Gtk.Label()
                            label.set_markup(
                                '<span foreground="#000000" weight="bold">'
                                + _u1c_theme_html.escape(title)
                                + '</span>'
                            )
                            label.set_xalign(0.0)
                            label.set_yalign(0.5)
                            label.set_margin_start(4)
                            label.set_margin_end(4)
                            label.set_margin_top(2)
                            label.set_margin_bottom(2)
                            label.set_name("u1c_tree_header_fixed_label")

                            event_box = _u1c_theme_Gtk.EventBox()
                            event_box.set_visible_window(True)
                            event_box.set_name("u1c_tree_header_fixed")
                            event_box.add(label)

                            _u1c_force_widget_colors(event_box, "#e7e7e7", "#000000")
                            _u1c_force_widget_colors(label, "#e7e7e7", "#000000")

                            column.set_widget(event_box)
                            event_box.show_all()
                        except Exception:
                            pass

                    try:
                        header_widget = column.get_widget()
                    except Exception:
                        header_widget = None

                    _u1c_force_widget_colors(header_widget, "#e7e7e7", "#000000")

                    # Главное: красим родительскую кнопку заголовка, потому что именно она оставалась тёмной.
                    button = None

                    try:
                        if hasattr(column, "get_button"):
                            button = column.get_button()
                    except Exception:
                        button = None

                    if button is None and header_widget is not None:
                        button = _u1c_find_parent_button(header_widget)

                    _u1c_force_widget_colors(button, "#e7e7e7", "#000000")

                    try:
                        for child in _u1c_collect_widgets(button):
                            _u1c_force_widget_colors(child, "#e7e7e7", "#000000")
                    except Exception:
                        pass

                try:
                    tree.queue_draw()
                except Exception:
                    pass

        return True

    def _u1c_apply_adaptive_light_content_theme():
        global _U1C_ADAPTIVE_LIGHT_CONTENT_THEME_DONE

        if _U1C_ADAPTIVE_LIGHT_CONTENT_THEME_DONE:
            return False

        dark = _u1c_theme_is_dark()

        app_bg = "#30343a" if dark else "#eeeeee"
        app_fg = "#f0f0f0" if dark else "#000000"

        css = f"""
        * {{
            text-shadow: none;
            -gtk-icon-shadow: none;
            -gtk-icon-effect: none;
        }}

        window,
        dialog,
        .background {{
            background-color: {app_bg};
            color: {app_fg};
        }}

        box,
        paned,
        grid,
        overlay,
        viewport,
        scrolledwindow,
        frame {{
            background-color: {app_bg};
            color: {app_fg};
        }}

        label,
        checkbutton,
        radiobutton {{
            color: {app_fg};
            opacity: 1;
        }}

        button,
        button.flat,
        button.text-button,
        button.image-button,
        combobox button {{
            background-image: none;
            background-color: #f5f5f5;
            color: #000000;
            border: 1px solid #b8b8b8;
            border-radius: 4px;
            box-shadow: none;
            opacity: 1;
        }}

        button *,
        button label,
        button box,
        button image,
        button:disabled *,
        button:disabled label,
        button:insensitive *,
        button:insensitive label,
        button:backdrop *,
        button:backdrop label {{
            color: #000000;
            opacity: 1;
        }}

        button:hover {{
            background-image: none;
            background-color: #e6e6e6;
            color: #000000;
        }}

        button:active,
        button:checked {{
            background-image: none;
            background-color: #ddb1f2;
            color: #000000;
        }}

        button:disabled,
        button:insensitive,
        button:backdrop {{
            background-image: none;
            background-color: #f1f1f1;
            color: #000000;
            border-color: #c7c7c7;
            opacity: 1;
        }}

        notebook,
        notebook > header,
        notebook > stack {{
            background-color: {app_bg};
            color: {app_fg};
        }}

        notebook tab,
        notebook tab:backdrop,
        notebook tab:disabled,
        notebook tab:insensitive {{
            background-image: none;
            background-color: #efefef;
            color: #000000;
            border: 1px solid #c7c7c7;
            opacity: 1;
        }}

        notebook tab label,
        notebook tab:checked label,
        notebook tab:disabled label,
        notebook tab:insensitive label {{
            color: #000000;
            opacity: 1;
        }}

        notebook tab:checked {{
            background-color: #ffffff;
            color: #000000;
        }}

        entry,
        textview,
        textview text,
        spinbutton,
        combobox,
        combobox box,
        combobox entry {{
            background-color: #f3f3f3;
            color: #000000;
            border-color: #bdbdbd;
            opacity: 1;
        }}

        entry *,
        textview *,
        spinbutton *,
        combobox * {{
            color: #000000;
            opacity: 1;
        }}

        treeview,
        treeview.view,
        .view {{
            background-color: #f0f0f0;
            color: #000000;
            opacity: 1;
        }}

        treeview.view *,
        treeview * {{
            color: #000000;
            opacity: 1;
        }}

        treeview header,
        treeview header button,
        treeview header button *,
        treeview.view header,
        treeview.view header button,
        treeview.view header button * {{
            background-image: none;
            background-color: #e7e7e7;
            color: #000000;
            border-color: #bdbdbd;
            box-shadow: none;
            opacity: 1;
        }}

        .u1c-force-light-header,
        .u1c-force-light-header *,
        #u1c_tree_header_fixed,
        #u1c_tree_header_fixed *,
        #u1c_tree_header_fixed_label {{
            background-image: none;
            background-color: #e7e7e7;
            color: #000000;
            opacity: 1;
        }}

        treeview.view:selected,
        treeview.view:selected:focus,
        treeview:selected,
        treeview:selected:focus,
        row:selected {{
            background-color: #dda0f2;
            color: #000000;
            opacity: 1;
        }}

        treeview.view:selected *,
        treeview:selected *,
        row:selected *,
        row:selected label {{
            color: #000000;
            opacity: 1;
        }}

        checkbutton check,
        radiobutton radio {{
            background-color: #f2f2f2;
            color: #000000;
            border: 1px solid #b8b8b8;
            opacity: 1;
        }}

        checkbutton check:checked,
        radiobutton radio:checked {{
            background-color: #dca2f2;
            color: #000000;
            border: 1px solid #9a55b8;
        }}

        menu,
        menuitem,
        popover,
        popover box {{
            background-color: #f3f3f3;
            color: #000000;
        }}

        menuitem *,
        popover * {{
            color: #000000;
            opacity: 1;
        }}
        """

        provider = _u1c_theme_Gtk.CssProvider()
        provider.load_from_data(css.encode("utf-8"))

        priority = 30000
        applied = False

        try:
            screen = _u1c_theme_Gdk.Screen.get_default()
            if screen is not None:
                _u1c_theme_Gtk.StyleContext.add_provider_for_screen(screen, provider, priority)
                applied = True
        except Exception:
            pass

        try:
            display = _u1c_theme_Gdk.Display.get_default()
            if display is not None and hasattr(_u1c_theme_Gtk.StyleContext, "add_provider_for_display"):
                _u1c_theme_Gtk.StyleContext.add_provider_for_display(display, provider, priority)
                applied = True
        except Exception:
            pass

        try:
            _u1c_force_tree_headers()
            _u1c_theme_GLib.timeout_add(500, _u1c_force_tree_headers)
            _u1c_theme_GLib.timeout_add(1500, _u1c_force_tree_headers)
            _u1c_theme_GLib.timeout_add(3000, _u1c_force_tree_headers)
        except Exception:
            pass

        _U1C_ADAPTIVE_LIGHT_CONTENT_THEME_DONE = applied
        return False

    try:
        _u1c_theme_GLib.idle_add(_u1c_apply_adaptive_light_content_theme)
        _u1c_theme_GLib.timeout_add(400, _u1c_apply_adaptive_light_content_theme)
        _u1c_theme_GLib.timeout_add(1500, _u1c_apply_adaptive_light_content_theme)
    except Exception:
        pass

except Exception:
    pass
# UPDATER1C_ADAPTIVE_LIGHT_CONTENT_THEME_PATCH_END

# UPDATER1C_REPORT_TAB_LAYOUT_V2_PATCH_BEGIN
# Вкладка "Отчет":
# - скрыть кнопки обновления, привязанные к выбранной базе;
# - верхний ряд: Открыть папку с отчетами / Открыть текущий лог / Очистить лог / Сохранить лог;
# - Скачать платформу перенести вниз;
# - Отменить перенести над аварийным прерыванием;
# - Прервать процесс сейчас переименовать в Аварийно прервать процесс сейчас.
try:
    import os as _u1c_report_os
    import re as _u1c_report_re
    import subprocess as _u1c_report_subprocess
    import datetime as _u1c_report_datetime
    from pathlib import Path as _u1c_report_Path

    import gi as _u1c_report_gi
    try:
        _u1c_report_gi.require_version("Gtk", "3.0")
    except Exception:
        pass

    from gi.repository import Gtk as _u1c_report_Gtk
    from gi.repository import GLib as _u1c_report_GLib
    from gi.repository import Gio as _u1c_report_Gio

    _U1C_REPORT_LAYOUT_DONE = False
    _U1C_REPORT_PROXY_NAMES = {
        "u1c_report_top_actions_row",
        "u1c_report_bottom_actions_row",
        "u1c_report_cancel_above_emergency",
    }

    def _u1c_report_widget_children(widget):
        children = []

        if widget is None:
            return children

        try:
            children.extend(widget.get_children())
        except Exception:
            pass

        try:
            child = widget.get_child()
            if child is not None and child not in children:
                children.append(child)
        except Exception:
            pass

        try:
            tmp = []
            widget.foreach(lambda child, data: data.append(child), tmp)
            for child in tmp:
                if child not in children:
                    children.append(child)
        except Exception:
            pass

        return children

    def _u1c_report_collect_widgets(root):
        result = []
        stack = [root]
        seen = set()

        while stack:
            widget = stack.pop()
            if widget is None:
                continue

            ident = id(widget)
            if ident in seen:
                continue

            seen.add(ident)
            result.append(widget)

            for child in _u1c_report_widget_children(widget):
                stack.append(child)

        return result

    def _u1c_report_widget_text(widget, depth=0):
        if widget is None or depth > 5:
            return ""

        parts = []

        for meth in ("get_text", "get_label", "get_title"):
            try:
                value = getattr(widget, meth)()
                if value:
                    parts.append(str(value))
            except Exception:
                pass

        try:
            active_text = widget.get_active_text()
            if active_text:
                parts.append(str(active_text))
        except Exception:
            pass

        for child in _u1c_report_widget_children(widget):
            text = _u1c_report_widget_text(child, depth + 1)
            if text:
                parts.append(text)

        return " ".join(parts)

    def _u1c_report_norm(text):
        text = str(text or "").lower().replace("ё", "е")
        text = _u1c_report_re.sub(r"\s+", " ", text)
        return text.strip()

    def _u1c_report_button_text(button):
        return _u1c_report_norm(_u1c_report_widget_text(button))

    def _u1c_report_is_proxy(widget):
        try:
            name = str(widget.get_name() or "")
            return name.startswith("u1c_report_")
        except Exception:
            return False

    def _u1c_report_is_report_tab_text(text):
        text = _u1c_report_norm(text)
        return "отчет" in text or "отчеты" in text

    def _u1c_report_pages():
        pages = []

        try:
            windows = _u1c_report_Gtk.Window.list_toplevels()
        except Exception:
            windows = []

        for win in windows:
            try:
                widgets = _u1c_report_collect_widgets(win)
            except Exception:
                widgets = []

            for widget in widgets:
                try:
                    if not isinstance(widget, _u1c_report_Gtk.Notebook):
                        continue
                except Exception:
                    continue

                try:
                    count = widget.get_n_pages()
                except Exception:
                    count = 0

                for index in range(count):
                    try:
                        page = widget.get_nth_page(index)
                        tab = widget.get_tab_label(page)
                        tab_text = _u1c_report_widget_text(tab)
                    except Exception:
                        continue

                    if _u1c_report_is_report_tab_text(tab_text):
                        pages.append(page)

        return pages

    def _u1c_report_default_reports_dir():
        candidates = [
            _u1c_report_Path.home() / "Документы" / "Updater1C" / "1c-update-reports",
            _u1c_report_Path.home() / "Documents" / "Updater1C" / "1c-update-reports",
            _u1c_report_Path("/mnt/DataStore/Updater1C/1c-update-reports"),
        ]

        for path in candidates:
            try:
                if path.exists():
                    return path
            except Exception:
                pass

        path = _u1c_report_Path.home() / "Документы" / "Updater1C" / "1c-update-reports"
        try:
            path.mkdir(parents=True, exist_ok=True)
        except Exception:
            path = _u1c_report_Path.home()

        return path

    def _u1c_report_find_largest_textview(page):
        textviews = []

        try:
            widgets = _u1c_report_collect_widgets(page)
        except Exception:
            widgets = []

        for widget in widgets:
            try:
                if isinstance(widget, _u1c_report_Gtk.TextView):
                    buf = widget.get_buffer()
                    start = buf.get_start_iter()
                    end = buf.get_end_iter()
                    text = buf.get_text(start, end, True)
                    textviews.append((len(text or ""), widget, text or ""))
            except Exception:
                pass

        if not textviews:
            return None, ""

        textviews.sort(key=lambda x: x[0], reverse=True)
        return textviews[0][1], textviews[0][2]

    def _u1c_report_current_text(page):
        _, text = _u1c_report_find_largest_textview(page)
        return text or ""

    def _u1c_report_set_current_text(page, text):
        textview, _ = _u1c_report_find_largest_textview(page)

        if textview is None:
            return False

        try:
            textview.get_buffer().set_text(text or "")
            return True
        except Exception:
            return False

    def _u1c_report_find_current_log_file(text):
        paths = []

        for match in _u1c_report_re.findall(r"(/[^\s'\"<>]+?\.log)", text or ""):
            candidate = match.rstrip(".,;:)")
            try:
                p = _u1c_report_Path(candidate)
                if p.exists() and p.is_file():
                    paths.append(p)
            except Exception:
                pass

        if paths:
            return paths[-1]

        reports_dir = _u1c_report_default_reports_dir()

        try:
            logs = sorted(
                [p for p in reports_dir.glob("*.log") if p.is_file()],
                key=lambda p: p.stat().st_mtime,
            )
            if logs:
                return logs[-1]
        except Exception:
            pass

        return None

    def _u1c_report_open_path(path):
        try:
            path = _u1c_report_Path(path)
        except Exception:
            return False

        try:
            _u1c_report_Gio.AppInfo.launch_default_for_uri(path.as_uri(), None)
            return True
        except Exception:
            pass

        try:
            _u1c_report_subprocess.Popen(["xdg-open", str(path)])
            return True
        except Exception:
            return False

    def _u1c_report_message(parent, text):
        try:
            dialog = _u1c_report_Gtk.MessageDialog(
                transient_for=parent if isinstance(parent, _u1c_report_Gtk.Window) else None,
                flags=0,
                message_type=_u1c_report_Gtk.MessageType.INFO,
                buttons=_u1c_report_Gtk.ButtonsType.OK,
                text=str(text),
            )
            dialog.run()
            dialog.destroy()
        except Exception:
            pass

    def _u1c_report_parent(button):
        try:
            parent = button.get_toplevel()
            if isinstance(parent, _u1c_report_Gtk.Window):
                return parent
        except Exception:
            pass
        return None

    def _u1c_report_action_open_reports(button, page):
        reports_dir = _u1c_report_default_reports_dir()
        reports_dir.mkdir(parents=True, exist_ok=True)

        if not _u1c_report_open_path(reports_dir):
            _u1c_report_message(_u1c_report_parent(button), f"Не удалось открыть папку отчетов:\n{reports_dir}")

    def _u1c_report_action_open_current_log(button, page):
        text = _u1c_report_current_text(page)
        parent = _u1c_report_parent(button)
        path = _u1c_report_find_current_log_file(text)

        if path is None:
            try:
                reports_dir = _u1c_report_default_reports_dir()
                reports_dir.mkdir(parents=True, exist_ok=True)
                path = reports_dir / ("updater1c_current_log_" + _u1c_report_datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + ".log")
                path.write_text(text or "", encoding="utf-8", errors="replace")
            except Exception as e:
                _u1c_report_message(parent, f"Не удалось подготовить текущий лог:\n{e}")
                return

        if not _u1c_report_open_path(path):
            _u1c_report_message(parent, f"Не удалось открыть лог:\n{path}")

    def _u1c_report_action_clear_log(button, page):
        _u1c_report_set_current_text(page, "")

    def _u1c_report_action_save_log(button, page):
        text = _u1c_report_current_text(page)
        parent = _u1c_report_parent(button)
        default_dir = _u1c_report_default_reports_dir()
        name = "updater1c_report_" + _u1c_report_datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + ".log"

        try:
            dialog = _u1c_report_Gtk.FileChooserDialog(
                title="Сохранить лог",
                transient_for=parent,
                action=_u1c_report_Gtk.FileChooserAction.SAVE,
            )
            dialog.add_buttons(
                "Отмена",
                _u1c_report_Gtk.ResponseType.CANCEL,
                "Сохранить",
                _u1c_report_Gtk.ResponseType.ACCEPT,
            )
            dialog.set_do_overwrite_confirmation(True)
            dialog.set_current_folder(str(default_dir))
            dialog.set_current_name(name)

            response = dialog.run()
            filename = dialog.get_filename()
            dialog.destroy()

            if response != _u1c_report_Gtk.ResponseType.ACCEPT or not filename:
                return

            path = _u1c_report_Path(filename)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text or "", encoding="utf-8", errors="replace")
            _u1c_report_message(parent, f"Лог сохранен:\n{path}")

        except Exception as e:
            _u1c_report_message(parent, f"Не удалось сохранить лог:\n{e}")

    def _u1c_report_click_button(button):
        if button is None:
            return False

        try:
            button.emit("clicked")
            return True
        except Exception:
            pass

        try:
            button.clicked()
            return True
        except Exception:
            return False

    def _u1c_report_find_buttons_in_page(page):
        buttons = {}

        try:
            widgets = _u1c_report_collect_widgets(page)
        except Exception:
            widgets = []

        for widget in widgets:
            try:
                if not isinstance(widget, _u1c_report_Gtk.Button):
                    continue
            except Exception:
                continue

            if _u1c_report_is_proxy(widget):
                continue

            text = _u1c_report_button_text(widget)

            if "скачать платформ" in text:
                buttons.setdefault("download_platform", widget)
            elif text == "отменить" or " отменить" in text:
                buttons.setdefault("cancel", widget)
            elif "очистить лог" in text:
                buttons.setdefault("clear_log", widget)
            elif "сохранить лог" in text:
                buttons.setdefault("save_log", widget)
            elif "открыть папку с отчет" in text:
                buttons.setdefault("open_reports", widget)
            elif "открыть текущий лог" in text:
                buttons.setdefault("open_current_log", widget)
            elif "открыть папку с платформ" in text:
                buttons.setdefault("open_platforms", widget)

        return buttons

    def _u1c_report_hide_original_report_buttons(page, source_buttons):
        hide_keys = {
            "download_platform",
            "cancel",
            "clear_log",
            "save_log",
            "open_reports",
            "open_current_log",
            "open_platforms",
        }

        for key in hide_keys:
            button = source_buttons.get(key)
            if button is None:
                continue

            try:
                button.set_no_show_all(True)
                button.hide()
                button.set_visible(False)
            except Exception:
                pass

        # Эти действия привязаны к выбранной базе и во вкладке отчета не нужны.
        try:
            widgets = _u1c_report_collect_widgets(page)
        except Exception:
            widgets = []

        for widget in widgets:
            try:
                if not isinstance(widget, _u1c_report_Gtk.Button):
                    continue
            except Exception:
                continue

            if _u1c_report_is_proxy(widget):
                continue

            text = _u1c_report_button_text(widget)

            if (
                "запустить обновлен" in text
                or "скачать обновлен" in text
                or "установить обновлен" in text
            ):
                try:
                    widget.set_sensitive(False)
                    widget.set_no_show_all(True)
                    widget.hide()
                    widget.set_visible(False)
                except Exception:
                    pass

    def _u1c_report_make_button(name, label, callback, page):
        button = _u1c_report_Gtk.Button.new_with_label(label)
        button.set_name(name)
        button.set_visible(True)
        button.set_sensitive(True)
        button.set_no_show_all(False)
        button.set_margin_start(0)
        button.set_margin_end(6)
        button.set_margin_top(2)
        button.set_margin_bottom(2)

        try:
            button.connect("clicked", lambda btn, p=page: callback(btn, p))
        except Exception:
            pass

        return button

    def _u1c_report_find_named(page, name):
        try:
            for widget in _u1c_report_collect_widgets(page):
                try:
                    if str(widget.get_name() or "") == name:
                        return widget
                except Exception:
                    pass
        except Exception:
            pass

        return None

    def _u1c_report_clear_container(container):
        try:
            for child in list(container.get_children()):
                container.remove(child)
        except Exception:
            pass

    def _u1c_report_attach_row_to_page(page, row, position):
        try:
            if isinstance(page, _u1c_report_Gtk.Box):
                if position == "top":
                    page.pack_start(row, False, False, 0)
                    try:
                        page.reorder_child(row, 0)
                    except Exception:
                        pass
                else:
                    page.pack_end(row, False, False, 0)

                row.show_all()
                return True
        except Exception:
            pass

        try:
            if isinstance(page, _u1c_report_Gtk.Grid):
                page.attach(row, 0, 0 if position == "top" else 99, 1, 1)
                row.show_all()
                return True
        except Exception:
            pass

        return False

    def _u1c_report_get_or_create_row(page, name, position):
        row = _u1c_report_find_named(page, name)

        if row is not None:
            return row

        row = _u1c_report_Gtk.Box(
            orientation=_u1c_report_Gtk.Orientation.HORIZONTAL,
            spacing=4,
        )
        row.set_name(name)
        row.set_margin_start(8)
        row.set_margin_end(8)
        row.set_margin_top(6)
        row.set_margin_bottom(4)

        if not _u1c_report_attach_row_to_page(page, row, position):
            return None

        return row

    def _u1c_report_action_cancel(button, page):
        source_buttons = _u1c_report_find_buttons_in_page(page)
        cancel = source_buttons.get("cancel")

        if cancel is not None and _u1c_report_click_button(cancel):
            return

        _u1c_report_message(_u1c_report_parent(button), "Активный процесс для отмены не найден.")

    def _u1c_report_action_download_platform(button, page):
        source_buttons = _u1c_report_find_buttons_in_page(page)
        platform = source_buttons.get("download_platform")

        if platform is not None and _u1c_report_click_button(platform):
            return

        _u1c_report_message(_u1c_report_parent(button), "Кнопка скачивания платформы не найдена.")

    def _u1c_report_action_open_platforms(button, page):
        source_buttons = _u1c_report_find_buttons_in_page(page)
        open_platforms = source_buttons.get("open_platforms")

        if open_platforms is not None and _u1c_report_click_button(open_platforms):
            return

        # Fallback: открываем стандартную папку платформ, если штатная кнопка не найдена.
        candidates = [
            _u1c_report_Path("/mnt/DataStore/Updater1C/1c-platforms"),
            _u1c_report_Path.home() / "Документы" / "Updater1C" / "1c-platforms",
            _u1c_report_Path.home() / "Documents" / "Updater1C" / "1c-platforms",
            _u1c_report_Path.home() / "Документы" / "1c-platforms",
            _u1c_report_Path.home() / "Documents" / "1c-platforms",
        ]

        for path in candidates:
            try:
                if path.exists():
                    _u1c_report_open_path(path)
                    return
            except Exception:
                pass

        try:
            candidates[0].mkdir(parents=True, exist_ok=True)
            _u1c_report_open_path(candidates[0])
            return
        except Exception:
            pass

        _u1c_report_message(_u1c_report_parent(button), "Кнопка открытия папки платформ не найдена.")

    def _u1c_report_patch_report_page(page):
        source_buttons = _u1c_report_find_buttons_in_page(page)

        _u1c_report_hide_original_report_buttons(page, source_buttons)

        top_row = _u1c_report_get_or_create_row(page, "u1c_report_top_actions_row", "top")

        if top_row is not None:
            _u1c_report_clear_container(top_row)

            top_buttons = [
                _u1c_report_make_button(
                    "u1c_report_btn_open_reports",
                    "📂 Открыть папку с отчетами",
                    _u1c_report_action_open_reports,
                    page,
                ),
                _u1c_report_make_button(
                    "u1c_report_btn_open_current_log",
                    "📄 Открыть текущий лог",
                    _u1c_report_action_open_current_log,
                    page,
                ),
                _u1c_report_make_button(
                    "u1c_report_btn_clear_log",
                    "🧹 Очистить лог",
                    _u1c_report_action_clear_log,
                    page,
                ),
                _u1c_report_make_button(
                    "u1c_report_btn_save_log",
                    "💾 Сохранить лог",
                    _u1c_report_action_save_log,
                    page,
                ),
            ]

            for btn in top_buttons:
                try:
                    top_row.pack_start(btn, False, False, 0)
                except Exception:
                    pass

            top_row.show_all()

        bottom_row = _u1c_report_get_or_create_row(page, "u1c_report_bottom_actions_row", "bottom")

        if bottom_row is not None:
            _u1c_report_clear_container(bottom_row)

            btn_platform = _u1c_report_make_button(
                "u1c_report_btn_download_platform_bottom",
                "▣ Скачать платформу",
                _u1c_report_action_download_platform,
                page,
            )

            btn_open_platforms = _u1c_report_make_button(
                "u1c_report_btn_open_platforms_bottom",
                "📂 Открыть папку с платформами",
                _u1c_report_action_open_platforms,
                page,
            )

            try:
                bottom_row.pack_start(btn_platform, False, False, 0)
                bottom_row.pack_start(btn_open_platforms, False, False, 0)
            except Exception:
                pass

            bottom_row.show_all()

    def _u1c_report_rename_emergency_buttons():
        try:
            windows = _u1c_report_Gtk.Window.list_toplevels()
        except Exception:
            windows = []

        for win in windows:
            try:
                widgets = _u1c_report_collect_widgets(win)
            except Exception:
                widgets = []

            for widget in widgets:
                try:
                    if not isinstance(widget, _u1c_report_Gtk.Button):
                        continue
                except Exception:
                    continue

                if _u1c_report_is_proxy(widget):
                    continue

                text = _u1c_report_button_text(widget)

                if "прервать процесс" not in text:
                    continue

                try:
                    widget.set_label("Аварийно прервать процесс сейчас")
                    widget.set_sensitive(True)
                    widget.set_visible(True)
                except Exception:
                    pass

                _u1c_report_add_cancel_above_emergency(widget)

    def _u1c_report_add_cancel_above_emergency(emergency_button):
        if emergency_button is None:
            return

        parent = None

        try:
            parent = emergency_button.get_parent()
        except Exception:
            parent = None

        if parent is None:
            return

        # Проверяем, что кнопка уже добавлена.
        try:
            for child in _u1c_report_widget_children(parent):
                try:
                    if str(child.get_name() or "") == "u1c_report_cancel_above_emergency":
                        return
                except Exception:
                    pass
        except Exception:
            pass

        # Нужна любая открытая страница отчета для корректного обработчика отмены.
        pages = _u1c_report_pages()
        page = pages[0] if pages else None

        cancel_button = _u1c_report_Gtk.Button.new_with_label("Отменить")
        cancel_button.set_name("u1c_report_cancel_above_emergency")
        cancel_button.set_margin_top(2)
        cancel_button.set_margin_bottom(6)
        cancel_button.set_sensitive(True)
        cancel_button.set_visible(True)

        try:
            cancel_button.connect("clicked", lambda btn, p=page: _u1c_report_action_cancel(btn, p))
        except Exception:
            pass

        try:
            if isinstance(parent, _u1c_report_Gtk.Box):
                parent.pack_start(cancel_button, False, False, 0)

                try:
                    children = list(parent.get_children())
                    emergency_index = children.index(emergency_button)
                    parent.reorder_child(cancel_button, max(0, emergency_index))
                except Exception:
                    pass

                cancel_button.show_all()
                return
        except Exception:
            pass

        try:
            if isinstance(parent, _u1c_report_Gtk.Grid):
                parent.attach_next_to(
                    cancel_button,
                    emergency_button,
                    _u1c_report_Gtk.PositionType.TOP,
                    1,
                    1,
                )
                cancel_button.show_all()
                return
        except Exception:
            pass

    def _u1c_report_scan_layout():
        try:
            for page in _u1c_report_pages():
                _u1c_report_patch_report_page(page)

            _u1c_report_rename_emergency_buttons()
        except Exception:
            pass

        return True

    def _u1c_report_start_layout_patch():
        global _U1C_REPORT_LAYOUT_DONE

        try:
            _u1c_report_scan_layout()

            if not _U1C_REPORT_LAYOUT_DONE:
                _u1c_report_GLib.timeout_add(700, _u1c_report_scan_layout)
                _u1c_report_GLib.timeout_add(1500, _u1c_report_scan_layout)
                _u1c_report_GLib.timeout_add_seconds(3, _u1c_report_scan_layout)
                _U1C_REPORT_LAYOUT_DONE = True
        except Exception:
            pass

        return False

    try:
        _u1c_report_GLib.idle_add(_u1c_report_start_layout_patch)
        _u1c_report_GLib.timeout_add(500, _u1c_report_start_layout_patch)
    except Exception:
        pass

except Exception:
    pass
# UPDATER1C_REPORT_TAB_LAYOUT_V2_PATCH_END


if __name__ == "__main__":
    main()
