"""Daemon routing tests. Pure: a fake backend, no Tor, no sockets.

Exercises the dispatch table's status/error/ok shapes so the HTTP glue on
top can stay trivial."""
from anet.daemon import dispatch


class FakeBackend:
    def __init__(self, ready=True, raises=False):
        self._ready = ready
        self._raises = raises

    def ready(self):
        return self._ready

    def fetch(self, url):
        if self._raises:
            raise ConnectionError("exit down")
        return f"BODY::{url}"

    def browse(self, url):
        if self._raises:
            raise ConnectionError("exit down")
        return {"url": url, "status": 200, "content_type": "text/html",
                "title": "T", "text": "hello", "links": ["/a"]}

    def dial(self, address, request):
        return {"echo": request, "to": address}


def test_status_reports_ready():
    code, obj = dispatch(FakeBackend(ready=True), "GET", "/status", {})
    assert code == 200 and obj == {"ok": True, "tor_ready": True}


def test_status_reports_not_ready():
    code, obj = dispatch(FakeBackend(ready=False), "GET", "/status", {})
    assert obj["tor_ready"] is False


def test_fetch_ok():
    code, obj = dispatch(FakeBackend(), "POST", "/fetch", {"url": "https://x"})
    assert code == 200 and obj["body"] == "BODY::https://x"


def test_browse_ok():
    code, obj = dispatch(FakeBackend(), "POST", "/browse", {"url": "https://x"})
    assert code == 200 and obj["title"] == "T" and obj["links"] == ["/a"]


def test_missing_url_is_400():
    code, obj = dispatch(FakeBackend(), "POST", "/fetch", {})
    assert code == 400 and obj["ok"] is False


def test_not_ready_is_503():
    code, obj = dispatch(FakeBackend(ready=False), "POST", "/browse", {"url": "https://x"})
    assert code == 503 and "booting" in obj["error"]


def test_backend_exception_is_502():
    code, obj = dispatch(FakeBackend(raises=True), "POST", "/fetch", {"url": "https://x"})
    assert code == 502 and "ConnectionError" in obj["error"]


def test_dial_ok():
    code, obj = dispatch(FakeBackend(), "POST", "/dial",
                         {"address": "abc.onion", "request": {"op": "ping"}})
    assert code == 200 and obj["reply"]["echo"] == {"op": "ping"}


def test_dial_needs_object_request():
    code, obj = dispatch(FakeBackend(), "POST", "/dial",
                         {"address": "abc.onion", "request": "not-an-object"})
    assert code == 400


def test_unknown_route_is_404():
    code, obj = dispatch(FakeBackend(), "POST", "/nope", {})
    assert code == 404
