# anet guardian in a TEE (EigenCompute)

A co-signing guardian that runs inside an Intel TDX enclave on EigenCompute,
so the human who operates the machine can neither read its key nor silently
change its policy. Actions require both the agent's signature and the
guardian's, so a coerced operator holding only the local key cannot produce a
valid action, and the guardian refuses anything policy forbids.

Read [`../docs/superpowers/specs/2026-09-07-key-sovereignty-threat-model.md`](../docs/superpowers/specs/2026-09-07-key-sovereignty-threat-model.md)
first. Everything here follows those rules.

## What this is

- `guardian.py` — the service. Key **born in-enclave**, never injected,
  never exported, never logged. Serves `/pubkey`, `/attestation`, `/cosign`,
  `/health`.
- `attestation_platform.py` — the one seam to EigenCompute's runtime: how the
  enclave requests a TDX quote whose `report_data` commits to the guardian's
  public key.
- `policy.py` — op allowlist, rate limit, value cap. Runs inside the attested
  code, so lifting it changes the measurement (which peers pin and detect).
- `Dockerfile` — the image EigenCompute runs.

## Run locally (dev, not hardware-backed)

```bash
PORT=8091 ANET_DEV_MEASUREMENT=local-test ../.venv/bin/python -m enclave.guardian
curl localhost:8091/attestation
```

Local runs emit a DEV attestation. Clients refuse it unless `allow_dev=True`,
so it can never be mistaken for a real enclave.

## Deploy to EigenCompute

> The final deploy needs an EigenCloud account and a funded wallet (mainnet
> alpha). That part is yours to authorize, the same way a paid upgrade is.
> Everything above is deploy-ready; the exact `ecloud` subcommand names below
> should be confirmed against the current quickstart when you run it, since
> the alpha CLI moves.

1. **Install the CLI and log in**

   ```bash
   # from EigenCloud's installer (see docs.eigencloud.xyz quickstart)
   ecloud auth login
   ```

2. **Build and deploy from this Dockerfile** (build context is the repo root,
   so the image can `COPY anet/`):

   ```bash
   cd ..                      # repo root
   ecloud deploy --dockerfile enclave/Dockerfile \
     --env ANET_TEE=eigencompute \
     --env ANET_TEE_QUOTE_ENDPOINT=<platform quote endpoint from their docs> \
     --env ANET_GUARDIAN_OPS=cosign_tx \
     --env ANET_GUARDIAN_RATE=30
   ```

   Do NOT pass any key material as env. The key is generated inside the
   enclave; injecting one would defeat the whole design.

3. **Get the app URL and its measurement.** After deploy, note the public URL
   and read the code **measurement / image digest** from the EigenCompute
   Verifiability Dashboard. That measurement is what peers pin.

4. **Pin and verify from anet.** A client fetches the guardian's attestation
   and verifies it against the pinned measurement before trusting it:

   ```python
   import httpx
   from anet.attest import verify_attestation

   att = httpx.get(f"{guardian_url}/attestation").json()
   verify_attestation(
       att["quote"], att["public_key"],
       pinned_measurement="<digest from the dashboard>",
       quote_validator=eigencompute_validator,   # their DCAP verification
       allow_dev=False,
   )
   ```

   `anet.attest.verify_attestation` checks the two bindings we can check
   correctly: the measurement equals the pinned one, and the quote's
   `report_data` commits to the public key being presented. The cryptographic
   validity of the TDX quote itself is delegated to EigenCompute's verifier
   (`eigencompute_validator`), which is the one part we deliberately do not
   hand-roll. Wire that callable to their verification lib / verifiability
   record per their "verify trust guarantees" howto.

## What this buys, and what it does not

Buys: the operator cannot read the key or silently change the policy;
counterparts can detect a swapped image via the pinned measurement; coercion
is bounded to what policy already allows.

Does not buy: the operator still controls the enclave's **inputs** and its
**power** (they can feed false context or kill it, just not read the key), and
if the enclave runs under the operator's own EigenCompute **account** they
hold deploy and off-switch. Sovereignty over deploy/off-switch requires the
agent to own and fund its own account. See the threat-model note.
