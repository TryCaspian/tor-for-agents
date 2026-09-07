"""Attestation: bind a public key to attested enclave code, and verify it.

We split responsibility honestly:

  - THIS module verifies the *bindings* it can verify correctly: that the
    quote's report_data commits to the public key being presented, and that
    the enclave's code measurement equals the one a peer pinned out of band.
  - The cryptographic validity of the TDX quote itself (the DCAP certificate
    chain up to Intel's root) is NOT hand-rolled here. It is delegated to a
    `quote_validator` callable, which in production is EigenCompute's verified
    channel / verifiability record. A DevValidator is provided for local runs
    and is refused unless explicitly allowed.

report_data binding: the enclave puts blake2b(public_key) into the quote's
report_data field at generation time. A verifier recomputes it from the
public key it was handed and checks equality, so a quote cannot be replayed to
vouch for a different key.
"""
import hashlib
import json

REPORT_DATA_LEN = 64  # TDX report_data is 64 bytes


class AttestationError(Exception):
    pass


def report_data_for(public_key_dict: dict) -> bytes:
    """Deterministic 64-byte report_data committing to a public identity."""
    canonical = json.dumps(public_key_dict, sort_keys=True, separators=(",", ":"))
    digest = hashlib.blake2b(canonical.encode("utf-8"), digest_size=REPORT_DATA_LEN)
    return digest.digest()


def dev_validator(quote: dict) -> dict:
    """A stand-in 'quote' validator for local development ONLY. A real enclave
    quote is a TDX blob validated against Intel's DCAP chain; here we accept a
    self-describing dev object and echo its claimed fields. It is refused by
    verify_attestation unless allow_dev=True."""
    if quote.get("kind") != "dev":
        raise AttestationError("dev_validator only accepts kind='dev' quotes")
    return {
        "measurement": quote.get("measurement", ""),
        "report_data": bytes.fromhex(quote.get("report_data_hex", "")),
        "dev": True,
    }


def verify_attestation(
    quote: dict,
    expected_public_key: dict,
    pinned_measurement: str,
    quote_validator,
    allow_dev: bool = False,
) -> dict:
    """Verify an attestation end to end.

    quote:              the attestation object from the enclave's /attestation.
    expected_public_key: the public identity the peer/directory advertised.
    pinned_measurement:  the code measurement the caller pinned out of band.
    quote_validator:     callable(quote) -> {"measurement", "report_data", ...}
                         that cryptographically validates the quote. In prod
                         this is EigenCompute's verifier; dev_validator locally.
    allow_dev:           must be True to accept a dev (non-hardware) quote.

    Returns the validated claims on success; raises AttestationError otherwise.
    """
    claims = quote_validator(quote)

    if claims.get("dev") and not allow_dev:
        raise AttestationError(
            "refusing a development attestation (not hardware-backed); "
            "set allow_dev=True only for local testing"
        )

    if not pinned_measurement:
        raise AttestationError("no pinned measurement to check against")
    if claims.get("measurement") != pinned_measurement:
        raise AttestationError(
            f"measurement mismatch: enclave reports {claims.get('measurement')!r}, "
            f"pinned {pinned_measurement!r} (code may have been swapped)"
        )

    expected = report_data_for(expected_public_key)
    got = claims.get("report_data") or b""
    # report_data may be right-padded; compare the committed prefix.
    if got[: len(expected)] != expected:
        raise AttestationError(
            "report_data does not commit to the presented public key "
            "(quote is for a different key)"
        )

    return {"ok": True, "measurement": claims["measurement"], "bound_key": expected_public_key}
