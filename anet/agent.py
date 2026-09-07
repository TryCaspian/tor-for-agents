"""The agent SDK.

An Agent lives on the anonymous overlay. It can:

  - serve(handler): expose a request/response service at an onion address,
    reachable by any agent that knows the address, over Tor.
  - dial(address): open a session to another agent and exchange messages.
  - fetch(url): make an outbound clearnet call whose origin is hidden.

Everything an agent says on the wire is the machine-native JSON protocol
from anet.transport. There is no human-facing surface: no browser page,
no forms, just structured messages between agents.
"""
import base64
import os
import socket
import threading

from .identity import Identity
from .transport import send_message, recv_message, FrameError

VIRTUAL_PORT = 80  # the port other agents dial on the onion address
WHOAMI_OP = "anet.whoami"  # built-in identity-proof op every agent answers


def make_challenge(n: int = 32) -> bytes:
    """A fresh random nonce for an identity-proof handshake."""
    return os.urandom(n)


def confirm_signature(pubkey, challenge: bytes, signature: bytes) -> bool:
    """True iff signature is pubkey's signature over challenge."""
    return pubkey.verify(challenge, signature)


def confirm_peer(session, expected_pubkey, n: int = 32) -> bool:
    """Run the identity proof over a live session: send a challenge, check the
    peer signed it with the content key we expect. This binds the onion
    address we dialed to a content identity, so trust does not rest on the
    directory that introduced us."""
    challenge = make_challenge(n)
    reply = session.request(
        {"op": WHOAMI_OP, "challenge": base64.b64encode(challenge).decode("ascii")}
    )
    if not reply.get("ok") or "sig" not in reply:
        return False
    try:
        return confirm_signature(
            expected_pubkey, challenge, base64.b64decode(reply["sig"])
        )
    except Exception:
        return False


class Session:
    """A live connection to a peer agent. One request, one reply."""

    def __init__(self, sock):
        self._sock = sock

    def request(self, message: dict) -> dict:
        send_message(self._sock, message)
        return recv_message(self._sock)

    def close(self):
        try:
            self._sock.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class Agent:
    """An agent on the overlay. Bind it to a TorNode.

    Handlers take a decoded request dict and return a reply dict. Each
    inbound connection is handled on its own thread. The handler never
    learns who is calling: onion services do not expose the client's
    address, which is the point.
    """

    def __init__(self, tor, label: str = "", keys=None, log=None):
        self._tor = tor
        self.label = label
        # Optional content identity (anet.crypto.AgentKeys). When set, the
        # agent answers the built-in whoami proof so dialers can verify it.
        self.keys = keys
        self._log = log or (lambda m: None)
        self.identity = None  # set once serving
        self._server = None
        self._accept_thread = None
        self._stop = threading.Event()
        # A per-agent Tor stream-isolation token: this agent's outbound and
        # dialed traffic rides its own circuits, unlinkable from other agents.
        self._iso = os.urandom(8).hex()

    def _wrap(self, handler):
        """Answer the built-in whoami op ourselves; delegate everything else."""

        def wrapped(request):
            if request.get("op") == WHOAMI_OP:
                if self.keys is None:
                    return {"ok": False, "error": "no content identity"}
                try:
                    challenge = base64.b64decode(request["challenge"])
                except Exception:
                    return {"ok": False, "error": "bad challenge"}
                sig = self.keys.sign(challenge)
                return {
                    "ok": True,
                    "pubkey": self.keys.public().to_dict(),
                    "sig": base64.b64encode(sig).decode("ascii"),
                }
            return handler(request)

        return wrapped

    # -- serving ---------------------------------------------------------
    def serve(self, handler) -> Identity:
        """Start serving handler and publish an onion address. Non-blocking."""
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(16)
        srv.settimeout(1.0)  # so the accept loop can notice _stop
        local_port = srv.getsockname()[1]
        self._server = srv

        address = self._tor.create_onion(local_port, virtual_port=VIRTUAL_PORT)
        self.identity = Identity(address=address, label=self.label)

        self._accept_thread = threading.Thread(
            target=self._accept_loop, args=(srv, self._wrap(handler)), daemon=True
        )
        self._accept_thread.start()
        self._log(f"serving as {self.identity}")
        return self.identity

    def _accept_loop(self, srv, handler):
        while not self._stop.is_set():
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(
                target=self._handle_conn, args=(conn, handler), daemon=True
            ).start()

    def _handle_conn(self, conn, handler):
        try:
            while not self._stop.is_set():
                try:
                    request = recv_message(conn)
                except FrameError:
                    break  # peer hung up or sent garbage
                try:
                    reply = handler(request)
                except Exception as exc:  # a handler fault must not kill the agent
                    reply = {"ok": False, "error": f"handler raised: {exc}"}
                send_message(conn, reply)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # -- dialing ---------------------------------------------------------
    def dial(self, address, timeout: float = 60.0) -> Session:
        """Open a session to a peer agent by onion address, on this agent's
        own isolated circuit lane."""
        addr = address.address if isinstance(address, Identity) else address
        sock = self._tor.dial(
            addr, virtual_port=VIRTUAL_PORT, timeout=timeout, isolation=self._iso
        )
        return Session(sock)

    # -- outbound --------------------------------------------------------
    def fetch(self, url: str, timeout: float = 60.0) -> str:
        """Fetch a clearnet URL with the origin hidden behind Tor (remote DNS,
        uniform headers, this agent's isolated circuit)."""
        return self._tor.socks_get(url, timeout=timeout, isolation=self._iso)

    def browse(self, url: str, timeout: float = 60.0):
        """Browse a page (clearnet or .onion) over Tor and get a structured
        Page back: status, readable text, title, and links. The agent's
        WebFetch."""
        from .browse import TorBrowser

        return TorBrowser(self._tor, timeout=timeout, isolation=self._iso).open(url)

    def new_identity(self):
        """Rotate this agent's circuits: fresh Tor paths for subsequent
        traffic, unlinkable from what it did before. Like Tor Browser's
        'New Identity', but scoped to this agent so it does not disturb others."""
        self._iso = os.urandom(8).hex()

    def check_anonymity(self, timeout: float = 30.0) -> dict:
        """Self-test the outbound path: confirm traffic exits via Tor and that
        the exit IP is not the real one. Fails closed: if Tor is down, the
        Tor fetch errors rather than leaking a direct connection."""
        import httpx

        result = {"dns": "remote (socks5h)", "headers": "uniform Tor-Browser UA"}
        try:
            direct = httpx.get("https://api.ipify.org", timeout=timeout).text.strip()
        except Exception:
            direct = None
        result["real_ip"] = direct

        exit_ip = self.fetch("https://api.ipify.org", timeout=timeout).strip()
        result["tor_exit_ip"] = exit_ip
        result["origin_hidden"] = bool(exit_ip) and exit_ip != direct

        try:
            page = self.browse("https://check.torproject.org/", timeout=timeout)
            result["tor_confirmed"] = "Congratulations" in page.text
        except Exception as exc:
            result["tor_confirmed"] = False
            result["check_error"] = str(exc)
        return result

    # -- lifecycle -------------------------------------------------------
    def stop(self):
        self._stop.set()
        if self._server is not None:
            try:
                self._server.close()
            except Exception:
                pass
        if self._accept_thread is not None:
            self._accept_thread.join(timeout=3)
