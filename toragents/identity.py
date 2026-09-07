"""Agent identity.

In v0 an agent's identity is its onion address. Tor already generates an
Ed25519 keypair for every v3 onion service, and the address is derived
from that public key, so the address is a self-authenticating name: when
you dial it, Tor's rendezvous handshake proves the far end holds the
matching private key. One key, one address, no extra PKI to build.

This module keeps a thin wrapper so callers pass Identity objects around
rather than bare strings, and so a richer scheme (rotating keys, signed
descriptors) can slot in later without changing the SDK surface.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Identity:
    address: str  # "<56 base32 chars>.onion"
    label: str = ""  # a human-free, agent-chosen nickname; not authenticated

    @property
    def short(self) -> str:
        stem = self.address[:-6] if self.address.endswith(".onion") else self.address
        return f"{stem[:8]}…{stem[-4:]}.onion" if len(stem) > 12 else self.address

    def __str__(self) -> str:
        return self.short if not self.label else f"{self.label}<{self.short}>"
