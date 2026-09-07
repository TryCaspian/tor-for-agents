"""Crypto tests. Pure, no network.

An agent's content identity is a pair of keys: an Ed25519 signing key
(authorship) and a Curve25519 box key (receiving sealed secrets). A group
is a symmetric key; messages are sealed to the group and signed by the
author. The board host only ever sees ciphertext.
"""
import pytest

from anet.crypto import (
    AgentKeys,
    PublicIdentity,
    GroupKey,
    seal_to,
    open_sealed,
    CryptoError,
)


def test_keys_roundtrip_public_identity_serialization():
    keys = AgentKeys.generate()
    pub = keys.public()
    wire = pub.to_dict()
    restored = PublicIdentity.from_dict(wire)
    assert restored == pub
    assert restored.fingerprint == pub.fingerprint


def test_sign_and_verify():
    keys = AgentKeys.generate()
    sig = keys.sign(b"attested bytes")
    assert keys.public().verify(b"attested bytes", sig) is True


def test_verify_rejects_tampered_payload():
    keys = AgentKeys.generate()
    sig = keys.sign(b"attested bytes")
    assert keys.public().verify(b"attested bytez", sig) is False


def test_verify_rejects_wrong_signer():
    a, b = AgentKeys.generate(), AgentKeys.generate()
    sig = a.sign(b"hi")
    assert b.public().verify(b"hi", sig) is False


def test_sealed_box_only_recipient_can_open():
    recipient = AgentKeys.generate()
    blob = seal_to(recipient.public(), b"the group key")
    assert open_sealed(recipient, blob) == b"the group key"


def test_sealed_box_wrong_recipient_fails():
    recipient, intruder = AgentKeys.generate(), AgentKeys.generate()
    blob = seal_to(recipient.public(), b"secret")
    with pytest.raises(CryptoError):
        open_sealed(intruder, blob)


def test_group_key_encrypt_decrypt():
    g = GroupKey.generate()
    ct = g.encrypt(b"hello group")
    assert g.decrypt(ct) == b"hello group"


def test_group_key_ciphertext_is_not_plaintext():
    g = GroupKey.generate()
    ct = g.encrypt(b"hello group")
    assert b"hello group" not in ct


def test_group_key_wrong_key_fails():
    g1, g2 = GroupKey.generate(), GroupKey.generate()
    ct = g1.encrypt(b"members only")
    with pytest.raises(CryptoError):
        g2.decrypt(ct)


def test_group_key_nonce_makes_ciphertext_unique():
    g = GroupKey.generate()
    assert g.encrypt(b"same") != g.encrypt(b"same")


def test_group_key_can_be_sealed_and_reconstructed():
    g = GroupKey.generate()
    member = AgentKeys.generate()
    invite = seal_to(member.public(), g.material)
    recovered = GroupKey(open_sealed(member, invite))
    assert recovered.decrypt(g.encrypt(b"x")) == b"x"
