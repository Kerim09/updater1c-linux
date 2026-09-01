"""HTTP-сессия для файлов обновлений портала 1С."""

from __future__ import annotations

import base64
from urllib.parse import urljoin, urlparse


class OneCDownloadSession:
    """Скачивает UpdateFileUrl с безопасной Basic-авторизацией.

    update-api возвращает готовый файловый URL вида:

        https://dlXX.1c.ru/public/file/tmplts/get/<uuid>

    Этот URL не подменяется на /public/file/get/.

    Basic credentials передаются повторно при HTTPS-редиректах
    только на доверенные домены 1С. При переходе на внешний CDN
    Authorization туда не передаётся.
    """

    REDIRECT_STATUSES = {
        301,
        302,
        303,
        307,
        308,
    }

    MAX_REDIRECTS = 10

    def __init__(self, login: str, password: str, session=None):
        if session is None:
            try:
                import requests
            except Exception as exc:
                raise RuntimeError(
                    "Для загрузок 1С нужен пакет python3-requests."
                ) from exc

            session = requests.Session()

        self.login = str(login or "").strip()
        self.password = str(password or "")
        self.session = session

        self.session.headers.update(
            {
                "User-Agent": "1C+Enterprise/8.3",
                "Accept": "*/*",
            }
        )

    @staticmethod
    def _host(url: str) -> str:
        try:
            return (
                urlparse(str(url or "")).hostname
                or ""
            ).lower().rstrip(".")
        except Exception:
            return ""

    @classmethod
    def _is_1c_host(cls, url: str) -> bool:
        host = cls._host(url)

        return (
            host == "1c.ru"
            or host.endswith(".1c.ru")
            or host == "1c.eu"
            or host.endswith(".1c.eu")
        )

    @staticmethod
    def _is_https(url: str) -> bool:
        try:
            return (
                urlparse(str(url or "")).scheme.lower()
                == "https"
            )
        except Exception:
            return False

    def _require_credentials(self) -> None:
        if not self.login or not self.password:
            raise RuntimeError(
                "Не заполнены логин и пароль ИТС."
            )

    def _authorization_header(self) -> str:
        token = base64.b64encode(
            f"{self.login}:{self.password}".encode("utf-8")
        ).decode("ascii")

        return f"Basic {token}"

    def get(
        self,
        url: str,
        *,
        stream: bool = True,
        timeout: int = 180,
    ):
        self._require_credentials()

        current_url = str(url or "").strip()

        if not self._is_https(current_url):
            raise RuntimeError(
                f"Файловый URL 1С должен использовать HTTPS: {current_url}"
            )

        if not self._is_1c_host(current_url):
            raise RuntimeError(
                f"Начальный файловый URL не относится к 1С: {current_url}"
            )

        auth_header = self._authorization_header()

        for redirect_no in range(self.MAX_REDIRECTS + 1):
            request_headers = {}

            #
            # Ключевой момент:
            # ITS credentials никогда не уходят на внешний CDN.
            #
            if self._is_1c_host(current_url):
                request_headers["Authorization"] = auth_header

            response = self.session.get(
                current_url,
                headers=request_headers,
                timeout=timeout,
                stream=stream,
                allow_redirects=False,
            )

            status = int(
                getattr(response, "status_code", 0)
                or 0
            )

            if status not in self.REDIRECT_STATUSES:
                return response

            location = str(
                getattr(response, "headers", {}).get(
                    "Location",
                    "",
                )
                or ""
            ).strip()

            if not location:
                try:
                    response.close()
                finally:
                    raise RuntimeError(
                        "Файловый сервер 1С вернул "
                        f"HTTP {status} без Location."
                    )

            next_url = urljoin(
                current_url,
                location,
            )

            if not self._is_https(next_url):
                try:
                    response.close()
                finally:
                    raise RuntimeError(
                        "Файловый сервер пытается выполнить "
                        "небезопасный redirect: "
                        f"{current_url} -> {next_url}"
                    )

            response.close()
            current_url = next_url

        raise RuntimeError(
            "Слишком много HTTP-редиректов "
            "при загрузке файла 1С."
        )
