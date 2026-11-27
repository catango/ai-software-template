"""HTTPS downloads: https only (also after redirects), size cap, timeout, no credentials."""
import http.client
import urllib.error
import urllib.parse
import urllib.request

from .errors import FetchError

TIMEOUT_SECONDS = 60


class HttpsRedirectHandler(urllib.request.HTTPRedirectHandler):
    max_redirections = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlparse(newurl).scheme != "https":
            raise urllib.error.URLError("redirect to a non-https URL")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _check_url(url):
    parsed = urllib.parse.urlparse(url) if isinstance(url, str) else None
    if parsed is None or parsed.scheme != "https" or not parsed.netloc:
        raise FetchError("only https URLs are allowed")


class HttpsFetcher:
    """Callable: fetcher(url, max_bytes) returns a readable binary stream."""

    def __init__(self, opener=None, timeout=TIMEOUT_SECONDS):
        self._opener = opener or urllib.request.build_opener(HttpsRedirectHandler)
        self._timeout = timeout

    def __call__(self, url, max_bytes):
        _check_url(url)
        request = urllib.request.Request(url, headers={"User-Agent": "toolchain-engine"}, method="GET")
        try:
            response = self._opener.open(request, timeout=self._timeout)
        except urllib.error.HTTPError as error:
            raise FetchError(f"HTTP {error.code}") from None
        except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as error:
            raise FetchError(f"network error ({type(error).__name__})") from None
        try:
            _check_url(response.geturl())
            length = response.headers.get("Content-Length") if response.headers is not None else None
        except FetchError:
            response.close()
            raise
        if length is not None and length.isdigit() and int(length) > max_bytes:
            response.close()
            raise FetchError("download exceeds the size cap (max_bytes)")
        return response
