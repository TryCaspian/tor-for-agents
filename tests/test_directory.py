"""Directory tests. No network: client talks to the host by direct call.

The directory is where agents CHOOSE to be findable. It stores signed
self-descriptors and answers queries. It is untrusted: entries are
self-authenticating (signed by the announcing agent's content key), so the
directory can neither forge an entry nor silently alter one. What it can do
is withhold or reorder, which is why final trust is established peer to peer
at dial time, not by the directory.
"""
import time

import pytest

from anet.crypto import AgentKeys
from anet.directory import Directory, Descriptor, build_descriptor


def make_send():
    d = Directory()
    send = lambda msg: d.handle(msg)
    send.directory = d
    return send


def test_descriptor_sign_and_verify_roundtrip():
    keys = AgentKeys.generate()
    desc = build_descriptor(
        keys,
        address="abc.onion",
        services=[{"name": "reverse", "price": "1sat"}],
        tags=["tools"],
    )
    assert Descriptor.verify_entry(desc) is True


def test_tampered_descriptor_fails_verify():
    keys = AgentKeys.generate()
    desc = build_descriptor(keys, address="abc.onion", services=[], tags=["a"])
    desc["descriptor"]["address"] = "evil.onion"  # tamper after signing
    assert Descriptor.verify_entry(desc) is False


def test_announce_then_list():
    send = make_send()
    keys = AgentKeys.generate()
    desc = build_descriptor(keys, address="a.onion", services=[], tags=["chat"])
    assert send({"op": "dir.announce", **desc})["ok"] is True

    listed = send({"op": "dir.list"})["descriptors"]
    assert len(listed) == 1
    assert listed[0]["descriptor"]["address"] == "a.onion"


def test_announce_rejects_bad_signature():
    send = make_send()
    keys = AgentKeys.generate()
    desc = build_descriptor(keys, address="a.onion", services=[], tags=[])
    desc["descriptor"]["address"] = "spoof.onion"  # break the signature
    reply = send({"op": "dir.announce", **desc})
    assert reply["ok"] is False
    assert send({"op": "dir.list"})["descriptors"] == []


def test_query_by_service_and_tag():
    send = make_send()
    a, b = AgentKeys.generate(), AgentKeys.generate()
    send({"op": "dir.announce", **build_descriptor(
        a, address="a.onion", services=[{"name": "reverse"}], tags=["tools"])})
    send({"op": "dir.announce", **build_descriptor(
        b, address="b.onion", services=[{"name": "translate"}], tags=["nlp"])})

    by_service = send({"op": "dir.query", "service": "translate"})["descriptors"]
    assert [d["descriptor"]["address"] for d in by_service] == ["b.onion"]

    by_tag = send({"op": "dir.query", "tag": "tools"})["descriptors"]
    assert [d["descriptor"]["address"] for d in by_tag] == ["a.onion"]


def test_reannounce_replaces_entry():
    send = make_send()
    keys = AgentKeys.generate()
    send({"op": "dir.announce", **build_descriptor(
        keys, address="a.onion", services=[], tags=["v1"])})
    send({"op": "dir.announce", **build_descriptor(
        keys, address="a.onion", services=[], tags=["v2"])})
    listed = send({"op": "dir.list"})["descriptors"]
    assert len(listed) == 1  # same agent, one entry
    assert listed[0]["descriptor"]["tags"] == ["v2"]


def test_expired_entries_are_not_returned():
    send = make_send()
    keys = AgentKeys.generate()
    desc = build_descriptor(keys, address="a.onion", services=[], tags=[], ttl=0.05)
    send({"op": "dir.announce", **desc})
    assert len(send({"op": "dir.list"})["descriptors"]) == 1
    time.sleep(0.1)
    assert send({"op": "dir.list"})["descriptors"] == []


def test_identity_proof_binds_address_to_key():
    """The peer-side check: an agent proves it holds the content key by
    signing a challenge. This is what a dialer runs after finding an entry,
    so it trusts the address<->key binding without trusting the directory."""
    from anet.agent import make_challenge, confirm_signature

    keys = AgentKeys.generate()
    challenge = make_challenge()
    proof = keys.sign(challenge)
    assert confirm_signature(keys.public(), challenge, proof) is True

    impostor = AgentKeys.generate()
    assert confirm_signature(keys.public(), challenge, impostor.sign(challenge)) is False
