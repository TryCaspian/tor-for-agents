"""Anonymity-helper tests. Pure, no network. These guard the two silent
leaks: local DNS resolution and a distinguishing request fingerprint."""
from anet.anon import proxy_url, browser_headers, TOR_BROWSER_UA


def test_proxy_url_uses_socks5h_for_remote_dns():
    # socks5h (not socks5) is what routes DNS through Tor instead of leaking
    # every hostname to the local resolver.
    url = proxy_url(9050)
    assert url.startswith("socks5h://")
    assert "socks5://" not in url
    assert url == "socks5h://127.0.0.1:9050"


def test_proxy_url_embeds_isolation_credentials():
    url = proxy_url(9050, isolation="abcd1234")
    assert url == "socks5h://abcd1234:abcd1234@127.0.0.1:9050"
    assert url.startswith("socks5h://")


def test_browser_headers_are_uniform_tor_browser():
    h = browser_headers()
    assert h["User-Agent"] == TOR_BROWSER_UA
    assert "Firefox/128.0" in h["User-Agent"]
    # No python-httpx signature, no bespoke headers that add entropy.
    assert "python" not in h["User-Agent"].lower()
    assert h["Accept-Language"] == "en-US,en;q=0.5"


def test_headers_do_not_leak_identity_fields():
    h = browser_headers()
    lowered = {k.lower() for k in h}
    for leaky in ("from", "x-forwarded-for", "authorization", "cookie"):
        assert leaky not in lowered
