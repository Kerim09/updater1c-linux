"""Минимальный GTK-совместимый клиент каталога releases.1c.ru.

Модуль намеренно не импортирует Qt и не зависит от ``main.py``. Он отвечает
только за ticket-авторизацию, получение версий и списка файлов релиза.
Скачивание выполняется отдельным кодом GTK, который умеет проверять HTML,
Content-Disposition, размер и временный ``.part``-файл.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlencode, urljoin, urlparse


def _application_version() -> str:
    try:
        version_file = Path(__file__).resolve().parents[1] / "VERSION"
        version = version_file.read_text(encoding="utf-8").strip()
        if version:
            return version
    except Exception:
        pass
    return "unknown"


class OneCReleasesClient:
    BASE_URL = "https://releases.1c.ru"
    LOGIN_URL = "https://login.1c.ru"

    def __init__(self, login: str = "", password: str = "", project_nick: str = "Platform83"):
        try:
            import requests
        except Exception as exc:  # pragma: no cover - зависит от системы
            raise RuntimeError("Для загрузок 1С нужен пакет python3-requests.") from exc

        self.login = str(login or "").strip()
        self.password = str(password or "")
        self.project_nick = str(project_nick or "Platform83")
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": f"updater1c-linux/{_application_version()}",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
        self._authenticated = False
        self._version_links: dict[str, str] = {}

    def _require_credentials(self) -> None:
        if not self.login or not self.password:
            raise RuntimeError("Не заполнены логин и пароль ИТС.")

    def _absolute(self, value: str) -> str:
        return urljoin(self.BASE_URL + "/", str(value or ""))

    @staticmethod
    def _is_login_page(response, body: str = "") -> bool:
        final_url = str(getattr(response, "url", "") or "").lower()
        low = str(body or "").lower()
        return (
            "login.1c.ru/login" in final_url
            or ("login.1c.ru" in final_url and "ticket/auth" not in final_url)
            or "login.1c.ru/login" in low
            or ("логин" in low and "пароль" in low and "releases.1c.ru" not in low)
        )

    def _get_ticket(self, service_nick: str) -> str:
        self._require_credentials()
        response = self.session.post(
            self.LOGIN_URL + "/rest/public/ticket/get",
            auth=(self.login, self.password),
            headers={"Content-Type": "application/json"},
            data=json.dumps({
                "login": self.login,
                "password": self.password,
                "serviceNick": service_nick,
            }, ensure_ascii=False).encode("utf-8"),
            timeout=60,
            allow_redirects=True,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"ticket/get: HTTP {response.status_code}: {response.text[:600]}")
        try:
            data = response.json()
        except Exception as exc:
            raise RuntimeError("login.1c.ru вернул не JSON при получении ticket.") from exc
        ticket = data.get("ticket") or data.get("Ticket") or data.get("token")
        if not ticket:
            raise RuntimeError(f"login.1c.ru не вернул ticket: {data}")
        return str(ticket)

    def _activate_ticket(self, service_url: str) -> None:
        ticket = self._get_ticket("releases.1c.ru")
        auth_url = self.LOGIN_URL + "/ticket/auth?" + urlencode({
            "token": ticket,
            "service": service_url,
        })
        response = self.session.get(
            auth_url,
            timeout=60,
            allow_redirects=True,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"ticket/auth: HTTP {response.status_code}: {response.text[:600]}")

        # Повторное открытие целевого сервиса нужно для установки cookie
        # releases.1c.ru после ticket/auth.
        probe = self.session.get(
            service_url,
            auth=(self.login, self.password),
            timeout=60,
            allow_redirects=True,
        )
        if probe.status_code >= 400:
            raise RuntimeError(f"releases.1c.ru после ticket/auth: HTTP {probe.status_code}")
        self._authenticated = not self._is_login_page(probe, probe.text)

    def _ensure_auth(self) -> None:
        if self._authenticated:
            return
        self._require_credentials()

        target = self.BASE_URL + "/project/" + self.project_nick
        try:
            probe = self.session.get(target, auth=(self.login, self.password), timeout=30, allow_redirects=True)
            if probe.status_code == 200 and not self._is_login_page(probe, probe.text):
                self._authenticated = True
                return
        except Exception:
            pass

        errors = []
        for service_url in (
            self.BASE_URL + "/public/security_check",
            target,
        ):
            try:
                self._activate_ticket(service_url)
                if self._authenticated:
                    return
            except Exception as exc:
                errors.append(f"{service_url}: {type(exc).__name__}: {exc}")

        raise RuntimeError(
            "Не удалось авторизоваться на releases.1c.ru через ticket. "
            + ("; ".join(errors[-2:]) if errors else "Проверьте доступ ИТС.")
        )

    def get(self, url: str, *, stream: bool = False):
        self._ensure_auth()
        absolute = self._absolute(url)
        response = self.session.get(
            absolute,
            auth=(self.login, self.password),
            timeout=180,
            allow_redirects=True,
            stream=stream,
        )
        body = "" if stream else (response.text or "")
        if response.status_code == 401 or self._is_login_page(response, body):
            self._authenticated = False
            self._activate_ticket(absolute)
            response = self.session.get(
                absolute,
                auth=(self.login, self.password),
                timeout=180,
                allow_redirects=True,
                stream=stream,
            )
            body = "" if stream else (response.text or "")
        if self._is_login_page(response, body):
            raise RuntimeError(f"releases.1c.ru вернул страницу авторизации: {absolute}")
        if response.status_code >= 400:
            raise RuntimeError(f"releases.1c.ru: HTTP {response.status_code}: {absolute}\n{body[:800]}")
        return response

    @staticmethod
    def _links(page_url: str, body: str) -> list[tuple[str, str]]:
        result = []
        pattern = r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>'
        for match in re.finditer(pattern, body or "", re.I | re.S):
            href = html.unescape(match.group(1) or "")
            title = re.sub(r"<[^>]+>", " ", match.group(2) or "")
            title = html.unescape(re.sub(r"\s+", " ", title)).strip()
            result.append((urljoin(page_url, href), title or href))
        return result

    def platform_versions(self) -> list[str]:
        self._ensure_auth()
        pages = [self.BASE_URL + "/project/" + self.project_nick, self.BASE_URL + "/total"]
        versions: set[str] = set()
        self._version_links = {}
        pattern = re.compile(r"\b(?:8\.3|8\.5)\.\d+\.\d+\b")

        for page in pages:
            try:
                response = self.get(page)
            except Exception:
                continue
            body = response.text or ""
            for href, title in self._links(response.url, body):
                combined = href + " " + title
                query = parse_qs(urlparse(href).query)
                candidates = list(query.get("ver", [])) + pattern.findall(combined)
                for version in candidates:
                    if version.startswith(("8.3.", "8.5.")):
                        versions.add(version)
                        if "version_files" in href or "version_file" in href:
                            self._version_links.setdefault(version, href)
            versions.update(pattern.findall(body))

        return sorted(versions, key=self._version_key, reverse=True)

    def version_files_url(self, version: str) -> str:
        version = str(version or "").strip()
        if version not in self._version_links:
            return self.BASE_URL + "/version_files?" + urlencode({"nick": self.project_nick, "ver": version})
        return self._version_links[version]

    def version_files(self, version: str) -> list[dict]:
        version = str(version or "").strip()
        response = self.get(self.version_files_url(version))
        result = []
        seen = set()

        for href, title in self._links(response.url, response.text or ""):
            low = href.lower()
            if "version_file" not in low:
                continue
            if href in seen:
                continue
            seen.add(href)
            query = parse_qs(urlparse(href).query)
            raw_path = unquote((query.get("path") or [""])[0]).replace("\\", "/")
            filename = Path(raw_path).name if raw_path else ""
            result.append({
                "version": version,
                "title": title or filename or href,
                "url": href,
                "versionFileUrl": href,
                "fileName": filename,
                "source": "releases.1c.ru",
            })
        return result

    def platform_files(self, version: str) -> list[dict]:
        return self.version_files(version)

    @staticmethod
    def _version_key(value: str) -> tuple[int, ...]:
        try:
            return tuple(int(part) for part in re.findall(r"\d+", str(value)))
        except Exception:
            return (0,)
