"""Live integration over a real Tor instance.

Slow: bootstrapping Tor and publishing an onion service takes tens of
seconds. Skipped unless TORAGENTS_LIVE=1 so the fast unit suite stays fast.

    TORAGENTS_LIVE=1 pytest tests/test_integration.py -q -s
"""
import os

import pytest

from toragents import Agent, TorNode

pytestmark = pytest.mark.skipif(
    os.environ.get("TORAGENTS_LIVE") != "1",
    reason="set TORAGENTS_LIVE=1 to run the live Tor integration test",
)


@pytest.fixture(scope="module")
def tor():
    node = TorNode(log=lambda m: print("[tor]", m))
    node.start()
    yield node
    node.close()


def test_agent_to_agent_roundtrip(tor):
    server = Agent(tor, label="bob", log=lambda m: print("[bob]", m))

    def reverse_handler(req):
        if req.get("op") == "reverse":
            return {"ok": True, "result": req["s"][::-1]}
        return {"ok": False, "error": "unknown op"}

    addr = server.serve(reverse_handler)
    assert addr.address.endswith(".onion")

    try:
        client = Agent(tor, label="alice")
        with client.dial(addr) as sess:
            reply = sess.request({"op": "reverse", "s": "anonymous"})
        assert reply == {"ok": True, "result": "suomynona"}
    finally:
        server.stop()


def test_handler_fault_is_contained(tor):
    server = Agent(tor, label="faulty")

    def boom(req):
        raise ValueError("kaboom")

    addr = server.serve(boom)
    try:
        client = Agent(tor, label="caller")
        with client.dial(addr) as sess:
            reply = sess.request({"op": "anything"})
        assert reply["ok"] is False
        assert "kaboom" in reply["error"]
    finally:
        server.stop()


def test_outbound_origin_is_hidden(tor):
    client = Agent(tor, label="scout")
    local_ip = _my_clearnet_ip()
    tor_ip = client.fetch("https://api.ipify.org").strip()
    print(f"[scout] local={local_ip} tor-exit={tor_ip}")
    assert tor_ip
    assert tor_ip != local_ip  # the exit is not us


def _my_clearnet_ip():
    import httpx

    try:
        return httpx.get("https://api.ipify.org", timeout=15).text.strip()
    except Exception:
        return "unknown"
