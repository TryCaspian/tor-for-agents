"""How the enclave obtains its attestation quote.

Inside EigenCompute (Intel TDX), the platform exposes a way to request a quote
whose report_data we choose (we put a hash of our public key there). The exact
mechanism is provided by the runtime; we read it from a configured path or
endpoint so this file has one clearly marked integration seam and nothing
hand-rolled.

Outside an enclave (local dev), we emit a self-describing DEV quote. It is not
hardware-backed and the client verifier refuses it unless explicitly allowed.
"""
import os


def get_quote(report_data: bytes) -> dict:
    """Return an attestation quote committing to report_data.

    Set TORAGENTS_TEE=eigencompute in the enclave. The runtime is expected to
    provide the quote-request mechanism at TORAGENTS_TEE_QUOTE_ENDPOINT (an HTTP
    endpoint or unix socket the platform mounts). We POST the report_data and
    return the platform's quote verbatim. This is the ONE seam that binds to
    EigenCompute's actual runtime; consult their attested-API howto for the
    exact endpoint and set the env var at deploy time.
    """
    backend = os.environ.get("TORAGENTS_TEE", "dev")

    if backend == "eigencompute":
        endpoint = os.environ.get("TORAGENTS_TEE_QUOTE_ENDPOINT")
        if not endpoint:
            raise RuntimeError(
                "TORAGENTS_TEE=eigencompute but TORAGENTS_TEE_QUOTE_ENDPOINT is unset; "
                "point it at the platform's quote mechanism (see EigenCompute "
                "attested-API docs)"
            )
        import httpx

        resp = httpx.post(endpoint, content=report_data, timeout=30)
        resp.raise_for_status()
        # The platform returns the raw TDX quote; wrap with the measurement it
        # also reports (via header) so the client can pin it.
        return {
            "kind": "tdx",
            "quote_b64": resp.text,
            "measurement": resp.headers.get("x-tee-measurement", ""),
        }

    # DEV: not hardware-backed. Measurement is a stable stand-in so local
    # end-to-end flows work; the client verifier refuses it unless allow_dev.
    return {
        "kind": "dev",
        "measurement": os.environ.get("TORAGENTS_DEV_MEASUREMENT", "dev-measurement-0000"),
        "report_data_hex": report_data.hex(),
    }
