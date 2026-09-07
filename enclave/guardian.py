"""The toragents guardian: a co-signing service designed to run inside a TEE.

Follows every rule in the key-sovereignty threat model:

  - the signing key is BORN here at boot, never injected, never exported,
    never logged (only its public half is served);
  - the key never enters any model context (this is deterministic code, not
    an LLM);
  - the public key is bound into the attestation via report_data;
  - policy runs here, in the attested code.

Endpoints (listens on $PORT, default 8080, as EigenCompute expects):
  GET  /pubkey       -> the guardian's public identity (safe to share)
  GET  /attestation  -> {public_key, quote}  (quote commits to the pubkey)
  POST /cosign       -> {op, payload_b64, ...}; policy-gated co-signature
  GET  /health       -> liveness

A co-signature is the guardian's Ed25519 signature over the exact bytes a
requester wants blessed. Combined with the requester's own signature, an
action needs BOTH, so a coerced operator holding only the local key cannot
produce a valid action, and the guardian refuses anything policy forbids.
"""
import base64
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# toragents is installed in the image; crypto + attestation binding come from it.
from toragents.crypto import AgentKeys
from toragents.attest import report_data_for

sys.path.insert(0, os.path.dirname(__file__))
from attestation_platform import get_quote  # noqa: E402
from policy import Policy  # noqa: E402


class Guardian:
    def __init__(self, policy: Policy):
        # KEY BORN HERE. Never read from env/disk, never written anywhere.
        self._keys = AgentKeys.generate()
        self._policy = policy
        self._pub = self._keys.public().to_dict()

    def public_key(self) -> dict:
        return self._pub

    def attestation(self) -> dict:
        # Bind the public key into the quote's report_data.
        quote = get_quote(report_data_for(self._pub))
        return {"public_key": self._pub, "quote": quote}

    def cosign(self, request: dict) -> dict:
        payload_b64 = request.get("payload_b64")
        if not isinstance(payload_b64, str):
            return {"ok": False, "error": "payload_b64 (base64 bytes) required"}
        allowed, reason = self._policy.check(request)
        if not allowed:
            return {"ok": False, "error": f"policy refused: {reason}"}
        try:
            payload = base64.b64decode(payload_b64)
        except Exception:
            return {"ok": False, "error": "payload_b64 is not valid base64"}
        signature = self._keys.sign(payload)
        self._policy.record()
        return {
            "ok": True,
            "cosigner": self._pub,
            "signature": base64.b64encode(signature).decode("ascii"),
        }


def make_handler(guardian: Guardian):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass  # never log request bodies; they could carry payloads

        def _send(self, code, obj):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/health":
                return self._send(200, {"ok": True})
            if self.path == "/pubkey":
                return self._send(200, guardian.public_key())
            if self.path == "/attestation":
                return self._send(200, guardian.attestation())
            self._send(404, {"ok": False, "error": "not found"})

        def do_POST(self):
            if self.path != "/cosign":
                return self._send(404, {"ok": False, "error": "not found"})
            length = int(self.headers.get("Content-Length", 0) or 0)
            try:
                request = json.loads(self.rfile.read(length).decode("utf-8"))
            except json.JSONDecodeError:
                return self._send(400, {"ok": False, "error": "invalid JSON"})
            self._send(200, guardian.cosign(request))

    return H


def build_policy() -> Policy:
    ops = os.environ.get("TORAGENTS_GUARDIAN_OPS")  # comma list, or unset = any
    return Policy(
        allowed_ops=[o.strip() for o in ops.split(",")] if ops else None,
        max_per_minute=int(os.environ.get("TORAGENTS_GUARDIAN_RATE", "30")),
        max_value=(float(os.environ["TORAGENTS_GUARDIAN_MAX_VALUE"])
                   if os.environ.get("TORAGENTS_GUARDIAN_MAX_VALUE") else None),
    )


def main():
    port = int(os.environ.get("PORT", "8080"))
    guardian = Guardian(build_policy())
    # Safe to print the PUBLIC key; never the private one.
    print(f"[guardian] up on :{port} pub_fingerprint="
          f"{guardian.public_key()}", file=sys.stderr)
    ThreadingHTTPServer(("0.0.0.0", port), make_handler(guardian)).serve_forever()


if __name__ == "__main__":
    main()
