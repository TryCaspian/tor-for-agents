"""Attestation binding + policy + guardian tests. Pure, no network, no TEE.

These guard the design rules from the key-sovereignty threat model: the
public key is bound into the attestation, measurements are pinned, dev quotes
are refused in prod, and policy actually gates co-signing.
"""
import base64
import sys
from pathlib import Path

import pytest

from toragents.crypto import AgentKeys, PublicIdentity
from toragents.attest import (
    report_data_for, verify_attestation, dev_validator, AttestationError,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "enclave"))
from policy import Policy  # noqa: E402
from guardian import Guardian  # noqa: E402


# -- attestation binding --------------------------------------------------
def _dev_quote(pubkey, measurement="m1"):
    return {"kind": "dev", "measurement": measurement,
            "report_data_hex": report_data_for(pubkey).hex()}


def test_report_data_is_stable_and_key_specific():
    a, b = AgentKeys.generate().public().to_dict(), AgentKeys.generate().public().to_dict()
    assert report_data_for(a) == report_data_for(a)
    assert report_data_for(a) != report_data_for(b)


def test_verify_accepts_matching_binding_and_measurement():
    pub = AgentKeys.generate().public().to_dict()
    q = _dev_quote(pub, "pinned-m")
    out = verify_attestation(q, pub, "pinned-m", dev_validator, allow_dev=True)
    assert out["ok"] is True


def test_verify_refuses_dev_quote_by_default():
    pub = AgentKeys.generate().public().to_dict()
    q = _dev_quote(pub, "pinned-m")
    with pytest.raises(AttestationError, match="development attestation"):
        verify_attestation(q, pub, "pinned-m", dev_validator, allow_dev=False)


def test_verify_rejects_measurement_mismatch():
    pub = AgentKeys.generate().public().to_dict()
    q = _dev_quote(pub, "actual-m")
    with pytest.raises(AttestationError, match="measurement mismatch"):
        verify_attestation(q, pub, "pinned-DIFFERENT", dev_validator, allow_dev=True)


def test_verify_rejects_quote_for_a_different_key():
    real = AgentKeys.generate().public().to_dict()
    other = AgentKeys.generate().public().to_dict()
    q = _dev_quote(real, "pinned-m")           # quote commits to `real`
    with pytest.raises(AttestationError, match="report_data"):
        verify_attestation(q, other, "pinned-m", dev_validator, allow_dev=True)


def test_verify_requires_a_pinned_measurement():
    pub = AgentKeys.generate().public().to_dict()
    q = _dev_quote(pub, "m")
    with pytest.raises(AttestationError, match="no pinned measurement"):
        verify_attestation(q, pub, "", dev_validator, allow_dev=True)


# -- policy ---------------------------------------------------------------
def test_policy_allowlist_blocks_unlisted_op():
    p = Policy(allowed_ops=["cosign_tx"])
    assert p.check({"op": "cosign_tx"})[0] is True
    assert p.check({"op": "exfiltrate"})[0] is False


def test_policy_value_cap():
    p = Policy(max_value=100)
    assert p.check({"op": "x", "value": 50})[0] is True
    assert p.check({"op": "x", "value": 500})[0] is False


def test_policy_rate_limit():
    p = Policy(max_per_minute=2)
    now = 1000.0
    for _ in range(2):
        assert p.check({"op": "x"}, now=now)[0] is True
        p.record(now=now)
    assert p.check({"op": "x"}, now=now)[0] is False           # third in the minute
    assert p.check({"op": "x"}, now=now + 61)[0] is True        # window slid


# -- guardian -------------------------------------------------------------
def test_guardian_cosign_is_verifiable_and_key_never_exported():
    g = Guardian(Policy())
    payload = b"transfer 1 claw to alice"
    reply = g.cosign({"op": "cosign_tx", "payload_b64": base64.b64encode(payload).decode()})
    assert reply["ok"] is True

    # the co-signature verifies under the guardian's PUBLIC key...
    pub = PublicIdentity.from_dict(reply["cosigner"])
    assert pub.verify(payload, base64.b64decode(reply["signature"])) is True

    # ...and nothing the guardian exposes contains private key material.
    exposed = str(g.public_key()) + str(reply)
    assert "signing" not in exposed and "box_private" not in exposed


def test_guardian_refuses_policy_violation():
    g = Guardian(Policy(allowed_ops=["cosign_tx"]))
    reply = g.cosign({"op": "drain_wallet", "payload_b64": base64.b64encode(b"x").decode()})
    assert reply["ok"] is False and "policy refused" in reply["error"]


def test_guardian_attestation_binds_its_own_pubkey():
    g = Guardian(Policy())
    att = g.attestation()
    # report_data in the (dev) quote must commit to the served public key.
    assert att["quote"]["report_data_hex"] == report_data_for(att["public_key"]).hex()
