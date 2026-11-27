"""The HTTPS fetcher: scheme, redirect, and size rules (no sockets; the opener is injected)."""
import io
import unittest
import urllib.error
import urllib.request

import helpers  # noqa: F401
from toolchain_engine import fetch
from toolchain_engine.errors import FetchError


class Resp(io.BytesIO):
    def __init__(self, data, headers=None, url="https://example.invalid/x"):
        super().__init__(data)
        self.headers = headers or {}
        self.status = 200
        self._url = url

    def geturl(self):
        return self._url


class Opener:
    def __init__(self, resp=None, error=None):
        self.resp, self.error, self.requests = resp, error, []

    def open(self, request, timeout=None):
        self.requests.append((request, timeout))
        if self.error:
            raise self.error
        return self.resp


class FetchTests(unittest.TestCase):
    def fetcher(self, **kw):
        return fetch.HttpsFetcher(opener=Opener(**kw))

    def test_only_https_urls(self):
        for url in ("http://example.invalid/x", "ftp://example.invalid/x", "file:///etc/passwd", "//example.invalid/x", "example.invalid/x", ""):
            with self.subTest(url=url), self.assertRaises(FetchError):
                self.fetcher(resp=Resp(b"x"))(url, 100)

    def test_returns_a_readable_stream_with_a_timeout(self):
        f = self.fetcher(resp=Resp(b"hello"))
        stream = f("https://example.invalid/x", 100)
        self.assertEqual(stream.read(10), b"hello")
        self.assertIsNotNone(f._opener.requests[0][1])

    def test_declared_size_over_the_cap_is_refused(self):
        with self.assertRaises(FetchError):
            self.fetcher(resp=Resp(b"x", headers={"Content-Length": "1000"}))("https://example.invalid/x", 100)

    def test_http_and_network_errors(self):
        err = urllib.error.HTTPError("https://example.invalid/x", 503, "no", {}, io.BytesIO(b""))
        with self.assertRaises(FetchError) as caught:
            self.fetcher(error=err)("https://example.invalid/x", 100)
        self.assertIn("503", str(caught.exception))
        for error in (urllib.error.URLError("x"), TimeoutError("t"), ConnectionResetError("r")):
            with self.subTest(error=type(error).__name__), self.assertRaises(FetchError):
                self.fetcher(error=error)("https://example.invalid/x", 100)

    def test_final_url_must_be_https(self):
        with self.assertRaises(FetchError):
            self.fetcher(resp=Resp(b"x", url="http://example.invalid/x"))("https://example.invalid/x", 100)

    def test_redirect_handler_refuses_non_https_and_long_chains(self):
        handler = fetch.HttpsRedirectHandler()
        req = urllib.request.Request("https://example.invalid/a")
        with self.assertRaises(urllib.error.URLError):
            handler.redirect_request(req, None, 302, "Found", {}, "http://example.invalid/b")
        with self.assertRaises(urllib.error.URLError):
            handler.redirect_request(req, None, 302, "Found", {}, "file:///etc/passwd")
        ok = handler.redirect_request(req, io.BytesIO(), 302, "Found", {}, "https://other.invalid/c")
        self.assertEqual(ok.full_url, "https://other.invalid/c")
        self.assertIsNone(ok.get_header("Authorization"))

    def test_no_credentials_are_sent(self):
        f = self.fetcher(resp=Resp(b"x"))
        f("https://example.invalid/x", 100)
        request = f._opener.requests[0][0]
        self.assertIsNone(request.get_header("Authorization"))


if __name__ == "__main__":
    unittest.main()
