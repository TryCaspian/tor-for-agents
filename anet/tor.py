"""Tor control: launch an isolated Tor, mint ephemeral onion services,
and expose a SOCKS port for anonymous outbound.

This is the only module that knows Tor exists. Everything above it deals
in onion addresses and streams, not circuits. We launch our own Tor
instance with a throwaway data dir so a demo never touches the user's
system Tor, and we create v3 onion services entirely over the control
port (ADD_ONION), so there is no torrc to edit and nothing left on disk.
"""
import os
import tempfile
import threading

import stem.process
from stem.control import Controller


def _free_port() -> int:
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TorNode:
    """A running Tor instance we control.

    Usage:
        with TorNode() as tor:
            onion = tor.create_onion(local_port=5000)  # -> "abc...xyz.onion"
            sock = tor.dial(onion, virtual_port=80)     # a connected socket
            ip = tor.socks_get("https://api.ipify.org")
    """

    def __init__(self, boot_timeout: float = 90.0, log=None):
        self._boot_timeout = boot_timeout
        self._log = log or (lambda m: None)
        self._data_dir = tempfile.mkdtemp(prefix="anet-tor-")
        self.socks_port = _free_port()
        self._control_port = _free_port()
        self._process = None
        self._controller = None
        self._onions = []
        self._lock = threading.Lock()

    # -- lifecycle -------------------------------------------------------
    def start(self) -> "TorNode":
        self._log(f"launching tor (socks={self.socks_port}) ...")
        self._process = stem.process.launch_tor_with_config(
            config={
                # IsolateSOCKSAuth: streams with different SOCKS credentials get
                # separate circuits, so one agent's traffic is not linkable to
                # another's by a shared exit. IsolateClientAddr is default-on.
                "SocksPort": f"{self.socks_port} IsolateSOCKSAuth",
                "ControlPort": str(self._control_port),
                "DataDirectory": self._data_dir,
                "CookieAuthentication": "1",
                # Keep the demo light: we do not run as a relay.
                "AvoidDiskWrites": "1",
            },
            init_msg_handler=self._on_boot_msg,
            timeout=self._boot_timeout,
            take_ownership=True,
        )
        self._controller = Controller.from_port(port=self._control_port)
        self._controller.authenticate()
        self._log("tor bootstrapped")
        return self

    def _on_boot_msg(self, line: str) -> None:
        if "Bootstrapped" in line:
            self._log(line.strip())

    def close(self) -> None:
        with self._lock:
            if self._controller is not None:
                try:
                    self._controller.close()
                except Exception:
                    pass
                self._controller = None
            if self._process is not None:
                try:
                    self._process.terminate()
                    self._process.wait(timeout=10)
                except Exception:
                    pass
                self._process = None
        try:
            import shutil

            shutil.rmtree(self._data_dir, ignore_errors=True)
        except Exception:
            pass

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()

    # -- onion services --------------------------------------------------
    def create_onion(self, local_port: int, virtual_port: int = 80) -> str:
        """Publish a v3 onion service pointing virtual_port -> 127.0.0.1:local_port.

        Returns the ".onion" address. The Ed25519 key Tor generates for the
        service is the agent's identity; it is discarded when this node dies
        (ephemeral), so each run is unlinkable to the last.
        """
        resp = self._controller.create_ephemeral_hidden_service(
            {virtual_port: local_port},
            await_publication=True,
            key_type="NEW",
            key_content="ED25519-V3",
        )
        addr = f"{resp.service_id}.onion"
        self._onions.append(resp.service_id)
        self._log(f"onion published: {addr}")
        return addr

    # -- outbound over Tor ----------------------------------------------
    def dial(
        self,
        onion_addr: str,
        virtual_port: int = 80,
        timeout: float = 60.0,
        attempts: int = 4,
        isolation: str | None = None,
    ):
        """Open a raw socket to an onion service through the SOCKS proxy.

        A freshly published onion can take a few tries before its descriptor
        is fetchable and a rendezvous builds, so we retry with backoff rather
        than fail the first slow attempt. Each attempt gets its own connect
        timeout; the total budget is roughly attempts * per_try.
        """
        import time

        import socks  # PySocks

        host = onion_addr[:-6] if onion_addr.endswith(".onion") else onion_addr
        per_try = max(timeout / attempts, 20.0)
        last_err = None
        for i in range(attempts):
            s = socks.socksocket()
            s.set_proxy(
                socks.SOCKS5, "127.0.0.1", self.socks_port, rdns=True,
                username=isolation, password=isolation,
            )
            s.settimeout(per_try)
            try:
                s.connect((f"{host}.onion", virtual_port))
                return s
            except (socks.GeneralProxyError, socks.ProxyConnectionError, OSError) as exc:
                last_err = exc
                try:
                    s.close()
                except Exception:
                    pass
                if i < attempts - 1:
                    self._log(f"dial retry {i + 1}/{attempts} for {host[:8]}…: {exc}")
                    time.sleep(2.0 * (i + 1))
        raise ConnectionError(f"could not reach {host}.onion after {attempts} tries: {last_err}")

    def http_client(self, timeout: float = 60.0, isolation: str | None = None):
        """An httpx client whose traffic exits through Tor.

        Uses socks5h (DNS resolved at the exit, never locally) and a uniform
        Tor-Browser header set so the request carries no distinguishing
        fingerprint. Pass an isolation token to pin traffic to its own
        circuit.
        """
        import httpx

        from .anon import proxy_url, browser_headers

        return httpx.Client(
            proxy=proxy_url(self.socks_port, isolation),
            timeout=timeout,
            headers=browser_headers(),
            follow_redirects=True,
        )

    def socks_get(self, url: str, timeout: float = 60.0, isolation: str | None = None) -> str:
        with self.http_client(timeout=timeout, isolation=isolation) as c:
            return c.get(url).text

    def new_identity(self):
        """Request fresh circuits (Tor's NEWNYM, the 'New Identity' signal).
        Subsequent connections build new paths through the network."""
        import stem

        with self._lock:
            if self._controller is not None:
                self._controller.signal(stem.Signal.NEWNYM)
