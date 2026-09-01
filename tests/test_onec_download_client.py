import ast
import base64
from pathlib import Path
import unittest

from core.onec_download_client import OneCDownloadSession


ROOT = Path(__file__).resolve().parents[1]


class FakeResponse:
    def __init__(self, status=200, url="", headers=None):
        self.status_code = status
        self.url = url
        self.headers = dict(headers or {})
        self.closed = False

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.headers = {}

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))

        if not self.responses:
            raise AssertionError(
                "Нет подготовленного HTTP response."
            )

        response = self.responses.pop(0)

        if not response.url:
            response.url = url

        return response


class OneCDownloadSessionTests(unittest.TestCase):
    def test_exact_update_api_url_uses_basic_auth(self):
        session = FakeSession(
            [FakeResponse(200)]
        )

        client = OneCDownloadSession(
            "тест@example.com",
            "пароль",
            session=session,
        )

        url = (
            "https://dl03.1c.ru/"
            "public/file/tmplts/get/"
            "15390ccc-a972-46e4-a431-bc64a692d2b0"
        )

        response = client.get(url)

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertEqual(
            session.calls[0][0],
            url,
        )

        kwargs = session.calls[0][1]

        self.assertFalse(
            kwargs["allow_redirects"]
        )

        header = kwargs["headers"]["Authorization"]

        self.assertTrue(
            header.startswith("Basic ")
        )

        decoded = base64.b64decode(
            header.split(" ", 1)[1]
        ).decode("utf-8")

        self.assertEqual(
            decoded,
            "тест@example.com:пароль",
        )

    def test_1c_redirect_repeats_auth(self):
        session = FakeSession(
            [
                FakeResponse(
                    302,
                    headers={
                        "Location":
                            "https://dl04.1c.ru/file.bin"
                    },
                ),
                FakeResponse(200),
            ]
        )

        client = OneCDownloadSession(
            "user",
            "pass",
            session=session,
        )

        client.get(
            "https://dl03.1c.ru/"
            "public/file/tmplts/get/test"
        )

        self.assertEqual(
            len(session.calls),
            2,
        )

        self.assertIn(
            "Authorization",
            session.calls[0][1]["headers"],
        )

        self.assertIn(
            "Authorization",
            session.calls[1][1]["headers"],
        )

    def test_external_https_redirect_does_not_leak_auth(self):
        session = FakeSession(
            [
                FakeResponse(
                    302,
                    headers={
                        "Location":
                            "https://cdn.example.net/file.bin"
                    },
                ),
                FakeResponse(200),
            ]
        )

        client = OneCDownloadSession(
            "user",
            "pass",
            session=session,
        )

        client.get(
            "https://dl03.1c.ru/"
            "public/file/tmplts/get/test"
        )

        self.assertIn(
            "Authorization",
            session.calls[0][1]["headers"],
        )

        self.assertNotIn(
            "Authorization",
            session.calls[1][1]["headers"],
        )

    def test_http_redirect_is_rejected(self):
        session = FakeSession(
            [
                FakeResponse(
                    302,
                    headers={
                        "Location":
                            "http://dl04.1c.ru/file.bin"
                    },
                )
            ]
        )

        client = OneCDownloadSession(
            "user",
            "pass",
            session=session,
        )

        with self.assertRaises(RuntimeError):
            client.get(
                "https://dl03.1c.ru/"
                "public/file/tmplts/get/test"
            )

    def test_non_1c_initial_host_is_rejected(self):
        client = OneCDownloadSession(
            "user",
            "pass",
            session=FakeSession([]),
        )

        with self.assertRaises(RuntimeError):
            client.get(
                "https://example.net/file"
            )


class UpdateApiUrlTests(unittest.TestCase):
    def test_variants_keep_exact_update_api_url(self):
        gtk = (
            ROOT
            / "gtk_port"
            / "updater1c_gtk.py"
        )

        text = gtk.read_text(
            encoding="utf-8"
        )

        tree = ast.parse(text)

        node = next(
            n
            for n in tree.body
            if isinstance(n, ast.FunctionDef)
            and n.name == "u1c_download_url_variants"
        )

        module = ast.Module(
            body=[node],
            type_ignores=[],
        )

        ast.fix_missing_locations(module)

        ns = {}

        exec(
            compile(
                module,
                str(gtk),
                "exec",
            ),
            ns,
        )

        url = (
            "https://dl05.1c.ru/"
            "public/file/tmplts/get/uuid"
        )

        self.assertEqual(
            ns["u1c_download_url_variants"](url),
            [url],
        )

        source = ast.get_source_segment(
            text,
            node,
        )

        self.assertNotIn(
            '.replace("/public/file/tmplts/get/',
            source,
        )


if __name__ == "__main__":
    unittest.main()
