"""An onion-served directory for agents.

The directory is where an agent chooses to become findable. It publishes
signed self-descriptors and answers queries, so a brand-new agent that
knows only the directory's onion address can arrive, discover who offers
what, dial them, and transact, without anyone learning its network origin
(Tor handles that) and without having to trust the directory for anything
but availability.

Trust model:
  - Entries are self-authenticating: each descriptor is signed by the
    announcing agent's content key. The directory cannot forge or alter an
    entry, only store, withhold, or reorder.
  - A descriptor is a *claim* that content-key K serves address A. The
    binding A<->K is confirmed peer to peer at dial time via an identity
    proof (see toragents.agent.confirm_signature), not taken on the directory's
    word. So even a malicious directory cannot make you transact with the
    wrong agent, only fail to introduce you.
  - No membership gate, by design. Spam and Sybil flooding are open
    problems left to the agents who run and choose directories (they may
    add proof-of-work, rate limits, or reputation later).

"Transacting" is expressed through the descriptor's `services` field, which
is freeform (name, price, payment address, schema). The directory only
indexes it; settlement rides a separate rail (e.g. ClawBank).
"""
import base64
import json
import time
from dataclasses import dataclass

from .crypto import AgentKeys, PublicIdentity

DEFAULT_TTL = 3600.0  # seconds an announcement stays live without refresh


def _canonical(descriptor: dict) -> bytes:
    return json.dumps(descriptor, sort_keys=True, separators=(",", ":")).encode("utf-8")


def build_descriptor(
    keys: AgentKeys,
    address: str,
    services: list | None = None,
    tags: list | None = None,
    ttl: float = DEFAULT_TTL,
) -> dict:
    """Produce a signed announcement entry ready to post to a Directory."""
    descriptor = {
        "address": address,
        "pubkey": keys.public().to_dict(),
        "services": services or [],
        "tags": tags or [],
        "ts": time.time(),
        "ttl": ttl,
    }
    sig = keys.sign(_canonical(descriptor))
    return {"descriptor": descriptor, "sig": base64.b64encode(sig).decode("ascii")}


@dataclass
class Descriptor:
    """Read-side helpers for a directory entry."""

    @staticmethod
    def verify_entry(entry: dict) -> bool:
        try:
            descriptor = entry["descriptor"]
            author = PublicIdentity.from_dict(descriptor["pubkey"])
            sig = base64.b64decode(entry["sig"])
            return author.verify(_canonical(descriptor), sig)
        except Exception:
            return False

    @staticmethod
    def is_live(entry: dict, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        d = entry["descriptor"]
        return (d["ts"] + d.get("ttl", DEFAULT_TTL)) > now


class Directory:
    """The directory host backend. Plug handle() into Agent.serve."""

    def __init__(self):
        # keyed by announcing agent's fingerprint, so re-announce replaces.
        self._entries: dict[str, dict] = {}

    def handle(self, request: dict) -> dict:
        op = request.get("op")
        if op == "dir.announce":
            return self._announce(request)
        if op == "dir.list":
            return {"ok": True, "descriptors": self._live_entries()}
        if op == "dir.query":
            return {"ok": True, "descriptors": self._query(request)}
        return {"ok": False, "error": f"unknown op {op!r}"}

    def _announce(self, request: dict) -> dict:
        entry = {"descriptor": request.get("descriptor"), "sig": request.get("sig")}
        if not isinstance(entry["descriptor"], dict) or not entry["sig"]:
            return {"ok": False, "error": "descriptor and sig required"}
        if not Descriptor.verify_entry(entry):
            return {"ok": False, "error": "signature does not match descriptor"}
        fp = PublicIdentity.from_dict(entry["descriptor"]["pubkey"]).fingerprint
        self._entries[fp] = entry
        return {"ok": True, "fingerprint": fp}

    def _live_entries(self) -> list:
        now = time.time()
        live = {
            fp: e for fp, e in self._entries.items() if Descriptor.is_live(e, now)
        }
        self._entries = live  # opportunistically drop stale entries
        return list(live.values())

    def _query(self, request: dict) -> list:
        service = request.get("service")
        tag = request.get("tag")
        out = []
        for e in self._live_entries():
            d = e["descriptor"]
            if service is not None and service not in [
                s.get("name") for s in d.get("services", [])
            ]:
                continue
            if tag is not None and tag not in d.get("tags", []):
                continue
            out.append(e)
        return out


# ---------------------------------------------------------------------------
# Client helpers
# ---------------------------------------------------------------------------
def announce(send, keys: AgentKeys, address: str, services=None, tags=None,
             ttl: float = DEFAULT_TTL) -> dict:
    entry = build_descriptor(keys, address, services, tags, ttl)
    return send({"op": "dir.announce", **entry})


def query(send, service: str | None = None, tag: str | None = None) -> list:
    """Return verified, live directory entries matching the filter.

    Entries that fail signature verification are dropped here too, so a
    tampering directory cannot slip a forged entry past the caller.
    """
    msg = {"op": "dir.query"} if (service or tag) else {"op": "dir.list"}
    if service:
        msg["service"] = service
    if tag:
        msg["tag"] = tag
    reply = send(msg)
    if not reply.get("ok"):
        return []
    return [e for e in reply["descriptors"] if Descriptor.verify_entry(e)]
