import json
import sys
import types
import unittest
from urllib.parse import urlparse
from unittest.mock import patch

from core.onec_releases_client import OneCReleasesClient


class FakeResponse:
    def __init__(self, url, text="", status_code=200, payload=None):
        self.url = url
        self.text = text
        self.status_code = status_code
        self.headers = {"Content-Type": "text/html; charset=utf-8"}
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    def __init__(self):
        self.headers = {}
        self.posts = []
        self.gets = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return FakeResponse(url, payload={"ticket": "ticket-123"})

    def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        parsed = urlparse(url)
        if parsed.path == "/project/Accounting30":
            body = (
                '<a href="/version_files?nick=Accounting30&ver=3.0.100.1">'
                "Бухгалтерия 3.0.100.1</a>"
            )
        elif parsed.path == "/version_files":
            body = """
                <a href="/version_file?nick=Accounting30&ver=3.0.100.1&path=1cv8.cfu">Обновление</a>
                <a href="/version_file?nick=Accounting30&ver=3.0.100.1&path=1cv8.cf">Полный дистрибутив</a>
            """
        else:
            body = "ok"
        return FakeResponse(url, body)


class OneCReleasesClientTests(unittest.TestCase):
    def make_client(self):
        fake_requests = types.ModuleType("requests")
        fake_requests.Session = FakeSession
        with patch.dict(sys.modules, {"requests": fake_requests}):
            client = OneCReleasesClient("user", "password", "Accounting30")
        client.session = FakeSession()
        return client

    def test_version_files_extracts_update_and_full_distribution(self):
        client = self.make_client()
        session = FakeSession()
        client.session = session
        client._authenticated = True

        files = client.version_files("3.0.100.1")

        self.assertEqual([item["fileName"] for item in files], ["1cv8.cfu", "1cv8.cf"])
        self.assertEqual(files[1]["title"], "Полный дистрибутив")
        self.assertEqual(session.gets[0][0], "https://releases.1c.ru/version_files?nick=Accounting30&ver=3.0.100.1")

    def test_ticket_payload_uses_releases_service(self):
        client = self.make_client()
        session = FakeSession()
        client.session = session

        self.assertEqual(client._get_ticket("releases.1c.ru"), "ticket-123")
        url, kwargs = session.posts[0]
        self.assertEqual(url, "https://login.1c.ru/rest/public/ticket/get")
        self.assertEqual(json.loads(kwargs["data"])["serviceNick"], "releases.1c.ru")
        self.assertEqual(kwargs["auth"], ("user", "password"))


if __name__ == "__main__":
    unittest.main()
