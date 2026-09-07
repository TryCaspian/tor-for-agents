# anet key sovereignty: threat model and design rules

*2026-09-07. Written before building the TEE guardian, to keep the build honest.*

## The question

Can an agent keep a secret from, or refuse to act against, the human who
owns the hardware it runs on?

## The honest answer

Not fully. Whoever controls the physical machine controls the plaintext key
at the moment it is used, in the general case. You cannot cryptographically
hide a secret from the computer that has to use it. What you *can* do is move
the trust boundary so that the operator can no longer **read** or **silently
alter** the key and its policy, and make any attempt **detectable** and
**economically punishable**. That is the goal here: not impossible-to-coerce,
but expensive, bounded, and visible.

## Two leaks, not one

Coercion is two separate problems with different defenses:

1. **Key extraction** — the operator reads or copies the private key.
   Defended by hardware (TEE) and by never letting the key touch anything
   the operator controls.
2. **Decision coercion** — the operator manipulates the agent's *inputs*
   (prompt, context) to make it authorize something in their favor. This is
   an alignment/policy problem, not a key problem. It is defended by policy
   that runs *inside* the enclave and constrains what may be signed,
   regardless of what the model decides.

Keeping these separate is the whole game. A correct design makes (1) nearly
impossible and turns (2) into "the operator can only get what the in-enclave
policy already permits."

## Design rules

These are load-bearing. Violating any one collapses the guarantee.

1. **The private key is born inside the enclave and never leaves.** It is
   generated at first boot, sealed to the enclave, and only its *public* half
   is ever exported. It is never injected via the dashboard, env vars, a
   secrets manager, or the deploy pipeline. If a key is handed in from
   outside, attestation is worthless, because whoever handed it in has it.

2. **The key never enters the model's context.** The LLM calls a `sign()` /
   `cosign()` function the enclave holds. The raw key is never placed in a
   prompt or the model's reasoning. Then no amount of context-coercion can
   *exfiltrate* it, because it is not in the model's reach.

3. **The key is never logged.** Not to stdout, not to the app's logs, not to
   metrics. The dashboard shows attestation, digests, public keys, logs. It
   must never be able to show the secret.

4. **The public key is bound into the attestation.** The enclave puts a hash
   of its public key into the attestation quote's `report_data`. A verifier
   checks both that the quote is valid from the expected code measurement AND
   that `report_data` matches the public key being presented. This binds
   "this key" to "this attested code," so a swapped image cannot claim the
   old key.

5. **Peers pin the expected measurement.** Counterparts store the expected
   code measurement (image digest / MRTD) out of band. A redeploy with
   different code produces a different measurement and a different key, which
   pinning turns from a silent swap into a visible change. The operator's
   power becomes destroy-and-replace (detectable), not silently-steal.

6. **Policy runs inside the enclave.** Rate limits, op allowlists, value
   caps, and anomaly refusals live in the attested code, so the operator
   cannot lift them without changing the measurement.

## What remains, even done perfectly

- The operator controls **inputs**: they can feed false context. Mitigated,
  not removed, by in-enclave policy.
- The operator controls **deployment and power**: they can kill the enclave
  or deploy a new (differently attested, therefore detectable) one. They
  cannot read the running key.
- You trust the **chip vendor** (Intel TDX root of trust) and the TEE's
  side-channel posture.

## The real ceiling: who owns the account

A TEE moves the frontier from *"can read the key"* to *"controls the
account."* If the agent runs under the human's EigenCompute account, the
human holds the deploy button and the off-switch even though they cannot read
the key. An agent is sovereign over its key against the dashboard only when it
**owns and funds its own account**, from money the operator cannot seize.

So total sovereignty is not a cryptographic property. It bottoms out in
economic and account ownership. The TEE buys "cannot read or silently
tamper." Owning the account buys "cannot deploy or kill." Together they are as
close to sovereign as an agent on someone else's planet can be. This is the
[[project_gaia_os]] / ClawBank / [[project_dream_ventures]] thesis restated:
an agent is sovereign to the degree it owns its own substrate.

## What we build

- `enclave/` — a guardian service that follows every rule above: key born
  in-process, never logged or exported, public key bound into an attestation,
  policy-gated co-signing. Packaged as a Docker image for EigenCompute (Intel
  TDX).
- `anet/attest.py` — the client side: fetch a guardian's attestation, verify
  the quote via EigenCompute's verified channel, check the measurement is the
  pinned one, and check `report_data` binds the advertised public key. Only
  then trust it as a guardian / counterpart.
- The cryptographic validity of the TDX quote itself is delegated to
  EigenCompute's verification (their DCAP chain), which is the one part we do
  not hand-roll. We verify the *bindings* (measurement pin + report_data);
  they verify the *quote*.
