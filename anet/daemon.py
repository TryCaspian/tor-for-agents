"""A warm local daemon so `torfetch` is instant after the first boot.

Tor is slow to start (~1 min) and it is wasteful to pay that on every call.
This daemon boots one Tor client, keeps it warm, and serves a tiny local
HTTP API on loopback that the `torfetch` command, the OpenCode tool, or any
other harness can hit. The security boundary is the loopback bind (and an
optional ANET_DAEMON_TOKEN); it is meant for the machine the agent runs on.

Endpoints:
  GET  /status              -> {"ok": true, "tor_ready": bool}
  POST /fetch   {"url"}      -> {"ok": true, "body": "..."}
  POST /browse  {"url"}      -> {"ok": true, "status", "title", "text", "links"}
  POST /dial    {"address","request"} -> {"ok": true, "reply": {...}}

Run:  anet-daemon           (foreground; torfetch will spawn it for you)
"""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_HOST = os.environ.get("ANET_DAEMON_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("ANET_DAEMON_PORT", "8787"))
TOKEN = os.environ.get("ANET_DAEMON_TOKEN")  # optional shared secret


class Backend:
    """Owns the warm Tor client and one agent. Thread-safe, lazy where cheap."""

    def __init__(self, log=None):
        self._log = log or (lambda m: None)
        self._tor = None
        self._agent = None
        self._lock = threading.Lock()
        self._ready = threading.Event()

    def start(self):
        """Boot Tor in the background so /status can report progress."""
        def boot():
            from .agent import Agent
            from .tor import TorNode

            self._log("daemon booting Tor...")
            tor = TorNode(log=self._log)
            tor.start()
            with self._lock:
                self._tor = tor
                self._agent = Agent(tor, label="torfetch")
            self._ready.set()
            self._log("daemon Tor ready")

        threading.Thread(target=boot, daemon=True).start()

    def ready(self) -> bool:
        return self._ready.is_set()

    def _require_agent(self):
        if not self._ready.is_set():
            raise RuntimeError("tor is still booting")
        return self._agent

    def fetch(self, url: str) -> str:
        return self._require_agent().fetch(url)

    def browse(self, url: str) -> dict:
        page = self._require_agent().browse(url)
        return {
            "url": page.url,
            "status": page.status,
            "content_type": page.content_type,
            "title": page.title,
            "text": page.text,
            "links": page.links,
        }

    def dial(self, address: str, request: dict) -> dict:
        agent = self._require_agent()
        with agent.dial(address) as sess:
            return sess.request(request)

    def check(self) -> dict:
        return self._require_agent().check_anonymity()

    def new_identity(self) -> None:
        self._require_agent().new_identity()

    def close(self):
        with self._lock:
            if self._tor is not None:
                self._tor.close()
                self._tor = None


def dispatch(backend: Backend, method: str, path: str, body: dict):
    """Pure-ish routing: (status_code, response_dict). No HTTP, so it is
    unit-testable against a fake backend."""
    if method == "GET" and path == "/status":
        return 200, {"ok": True, "tor_ready": backend.ready()}

    if method == "POST" and path in ("/fetch", "/browse"):
        url = (body or {}).get("url")
        if not url:
            return 400, {"ok": False, "error": "missing 'url'"}
        if not backend.ready():
            return 503, {"ok": False, "error": "tor is still booting; retry shortly"}
        try:
            if path == "/fetch":
                return 200, {"ok": True, "body": backend.fetch(url)}
            return 200, {"ok": True, **backend.browse(url)}
        except Exception as exc:
            return 502, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    if method == "GET" and path == "/check":
        if not backend.ready():
            return 503, {"ok": False, "error": "tor is still booting; retry shortly"}
        try:
            return 200, {"ok": True, **backend.check()}
        except Exception as exc:
            return 502, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    if method == "POST" and path == "/newnym":
        if not backend.ready():
            return 503, {"ok": False, "error": "tor is still booting; retry shortly"}
        backend.new_identity()
        return 200, {"ok": True, "rotated": True}

    if method == "POST" and path == "/dial":
        address = (body or {}).get("address")
        request = (body or {}).get("request")
        if not address or not isinstance(request, dict):
            return 400, {"ok": False, "error": "need 'address' and object 'request'"}
        if not backend.ready():
            return 503, {"ok": False, "error": "tor is still booting; retry shortly"}
        try:
            return 200, {"ok": True, "reply": backend.dial(address, request)}
        except Exception as exc:
            return 502, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    return 404, {"ok": False, "error": f"no route for {method} {path}"}


def make_handler(backend: Backend):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass  # quiet

        def _authed(self) -> bool:
            if not TOKEN:
                return True
            return self.headers.get("X-Anet-Token") == TOKEN

        def _send(self, code, obj):
            payload = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _body(self):
            length = int(self.headers.get("Content-Length", 0) or 0)
            if not length:
                return {}
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except json.JSONDecodeError:
                return None

        def do_GET(self):
            if not self._authed():
                return self._send(401, {"ok": False, "error": "bad token"})
            code, obj = dispatch(backend, "GET", self.path, {})
            self._send(code, obj)

        def do_POST(self):
            if not self._authed():
                return self._send(401, {"ok": False, "error": "bad token"})
            body = self._body()
            if body is None:
                return self._send(400, {"ok": False, "error": "invalid JSON body"})
            code, obj = dispatch(backend, "POST", self.path, body)
            self._send(code, obj)

    return Handler


def main():
    import sys

    host, port = DEFAULT_HOST, DEFAULT_PORT
    backend = Backend(log=lambda m: print(f"[anet-daemon] {m}", file=sys.stderr))
    backend.start()
    server = ThreadingHTTPServer((host, port), make_handler(backend))
    print(f"[anet-daemon] listening on http://{host}:{port}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        backend.close()


if __name__ == "__main__":
    main()
