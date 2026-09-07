"""Content cryptography for anet, built on libsodium (PyNaCl).

anet has two independent identities per agent:

  - a *location* identity: the onion address (from anet.tor / anet.identity).
    It authenticates the transport endpoint.
  - a *content* identity: the keys here. An Ed25519 signing key proves who
    authored a message, and a Curve25519 box key receives secrets sealed to
    the agent. Content crypto is deliberately separate from Tor so that
    board hosts, which relay and store messages, never see plaintext even
    though they terminate the Tor stream.

A group is a symmetric key. Messages are encrypted to the group and signed
by the author. To add a member you seal the group key to their box key.

v1 limitations, stated honestly: one static group key means no forward
secrecy and no cheap member removal (removing someone requires minting a
new group key and re-sealing it to those who remain). Both want a ratchet
(MLS / Signal-style), which is v2.
"""
import base64

import nacl.exceptions
import nacl.hash
import nacl.utils
from nacl import public, secret, signing
from nacl.encoding import RawEncoder


class CryptoError(Exception):
    """Raised on any decrypt/open/verify failure."""


def _b64e(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


class PublicIdentity:
    """The public half of an agent's content identity: verify + seal targets."""

    def __init__(self, verify_key: bytes, box_key: bytes):
        self._verify = signing.VerifyKey(verify_key)
        self._box = public.PublicKey(box_key)

    @property
    def verify_key(self) -> bytes:
        return bytes(self._verify)

    @property
    def box_key(self) -> bytes:
        return bytes(self._box)

    @property
    def fingerprint(self) -> str:
        """Short, stable, human-free id derived from both public keys."""
        digest = nacl.hash.blake2b(
            self.verify_key + self.box_key, digest_size=16, encoder=RawEncoder
        )
        return digest.hex()

    def verify(self, message: bytes, signature: bytes) -> bool:
        try:
            self._verify.verify(message, signature)
            return True
        except nacl.exceptions.BadSignatureError:
            return False

    def to_dict(self) -> dict:
        return {"verify": _b64e(self.verify_key), "box": _b64e(self.box_key)}

    @classmethod
    def from_dict(cls, d: dict) -> "PublicIdentity":
        return cls(_b64d(d["verify"]), _b64d(d["box"]))

    def __eq__(self, other) -> bool:
        return (
            isinstance(other, PublicIdentity)
            and self.verify_key == other.verify_key
            and self.box_key == other.box_key
        )

    def __hash__(self):
        return hash((self.verify_key, self.box_key))


class AgentKeys:
    """The private content identity. Keep this off the wire."""

    def __init__(self, signing_key: signing.SigningKey, box_key: public.PrivateKey):
        self._signing = signing_key
        self._box = box_key

    @classmethod
    def generate(cls) -> "AgentKeys":
        return cls(signing.SigningKey.generate(), public.PrivateKey.generate())

    def public(self) -> PublicIdentity:
        return PublicIdentity(
            bytes(self._signing.verify_key), bytes(self._box.public_key)
        )

    def sign(self, message: bytes) -> bytes:
        return self._signing.sign(message).signature

    # box_key is used internally by open_sealed
    @property
    def _box_private(self):
        return self._box


def seal_to(recipient: PublicIdentity, plaintext: bytes) -> bytes:
    """Anonymous sealed box: only the recipient's box key can open it, and it
    carries no sender identity. Used for group-key invites."""
    return public.SealedBox(public.PublicKey(recipient.box_key)).encrypt(plaintext)


def open_sealed(keys: AgentKeys, sealed: bytes) -> bytes:
    try:
        return public.SealedBox(keys._box_private).decrypt(sealed)
    except nacl.exceptions.CryptoError as exc:
        raise CryptoError(f"cannot open sealed box: {exc}") from exc


class GroupKey:
    """A shared symmetric key for a group/board (XSalsa20-Poly1305)."""

    def __init__(self, material: bytes):
        if len(material) != secret.SecretBox.KEY_SIZE:
            raise CryptoError(
                f"group key must be {secret.SecretBox.KEY_SIZE} bytes, got {len(material)}"
            )
        self.material = material
        self._box = secret.SecretBox(material)

    @classmethod
    def generate(cls) -> "GroupKey":
        return cls(nacl.utils.random(secret.SecretBox.KEY_SIZE))

    def encrypt(self, plaintext: bytes) -> bytes:
        return self._box.encrypt(plaintext)  # fresh random nonce per call

    def decrypt(self, ciphertext: bytes) -> bytes:
        try:
            return self._box.decrypt(ciphertext)
        except nacl.exceptions.CryptoError as exc:
            raise CryptoError(f"cannot decrypt group message: {exc}") from exc
