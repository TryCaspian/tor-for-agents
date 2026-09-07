"""Anonymity helpers: the small, pure pieces that keep outbound traffic from
leaking who or what an agent is. Kept here so they can be unit-tested without
a network.

Two things matter for origin anonymity on the open web, beyond routing:

  1. DNS must resolve at the Tor exit, not locally. A `socks5h://` proxy URL
     does remote resolution; a plain `socks5://` leaks every hostname to the
     local resolver (and the ISP). We always use socks5h.

  2. The request must not carry a distinguishing fingerprint. Tor Browser
     defeats this by making every user look identical: one uniform
     User-Agent (a Windows Firefox ESR string regardless of real OS) and a
     fixed, minimal header set. We send the same, so an anet agent looks like
     any Tor Browser user, not like "python-httpx/x.y".
"""

# Matches Tor Browser's uniform fingerprint (Firefox 128 ESR on Windows), sent
# by every Tor Browser user on every OS so the value carries no information.
TOR_BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; rv:128.0) Gecko/20100101 Firefox/128.0"


def browser_headers() -> dict:
    """A uniform, low-entropy header set for outbound web requests."""
    return {
        "User-Agent": TOR_BROWSER_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Upgrade-Insecure-Requests": "1",
    }


def proxy_url(port: int, isolation: str | None = None) -> str:
    """Build a Tor SOCKS proxy URL. Always socks5h (remote DNS, no leak).

    When isolation is given it becomes the SOCKS username/password, which,
    with Tor's IsolateSOCKSAuth, pins this traffic to its own circuit,
    unlinkable from traffic using a different token.
    """
    if isolation:
        return f"socks5h://{isolation}:{isolation}@127.0.0.1:{port}"
    return f"socks5h://127.0.0.1:{port}"
