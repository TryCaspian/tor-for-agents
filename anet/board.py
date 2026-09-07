"""Encrypted boards and groups on top of anet.

A board is an append-only log of encrypted, signed entries. A group is the
set of members who hold the board's key. The BoardHost stores and serves
the entries but holds no key, so it sees only ciphertext: it is a blind
relay for history. Members encrypt with the shared GroupKey and sign with
their own signing key, so any member can verify who wrote what.

  BoardHost.handle(request) -> reply     # plug into Agent.serve
  Board(send, board_id, keys, group_key) # a member's view

The same primitive serves both a public-ish message board (many members,
one key) and a private group chat (few members): the difference is only who
holds the key and who you invite.

Trust boundaries, stated plainly:
  - The host cannot read messages or forge authorship.
  - The host CAN withhold or reorder entries (it controls delivery). v1
    does not defend ordering integrity; a Merkle/CRDT log is v2.
  - Removing a member requires minting a new group key and re-inviting the
    rest (no forward secrecy). Also v2.
"""
import base64
import json
import time
from dataclasses import dataclass

from .crypto import AgentKeys, GroupKey, PublicIdentity, seal_to, open_sealed


def _canonical(board_id: str, author: dict, ts: float, ct_b64: str) -> bytes:
    """Deterministic bytes an author signs. Any change breaks the signature."""
    return json.dumps(
        {"board": board_id, "author": author, "ts": ts, "ct": ct_b64},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


# ---------------------------------------------------------------------------
# Host: blind, append-only storage. Knows nothing about keys or plaintext.
# ---------------------------------------------------------------------------
class BoardHost:
    def __init__(self):
        self._boards: dict[str, list[dict]] = {}

    def handle(self, request: dict) -> dict:
        op = request.get("op")
        if op == "board.append":
            return self._append(request)
        if op == "board.since":
            return self._since(request)
        return {"ok": False, "error": f"unknown op {op!r}"}

    def _append(self, request: dict) -> dict:
        board = request.get("board")
        entry = request.get("entry")
        if not isinstance(board, str) or not isinstance(entry, dict):
            return {"ok": False, "error": "board and entry required"}
        log = self._boards.setdefault(board, [])
        seq = len(log) + 1
        stored = {**entry, "seq": seq}
        log.append(stored)
        return {"ok": True, "seq": seq}

    def _since(self, request: dict) -> dict:
        board = request.get("board")
        after = int(request.get("after", 0))
        log = self._boards.get(board, [])
        return {"ok": True, "entries": [e for e in log if e["seq"] > after]}

    def raw_entries(self, board: str) -> list[dict]:
        """Test/inspection helper: exactly what the host holds (ciphertext)."""
        return self._boards.get(board, [])


# ---------------------------------------------------------------------------
# Member view of a board.
# ---------------------------------------------------------------------------
@dataclass
class Message:
    seq: int
    author_fingerprint: str
    text: str
    ts: float
    verified: bool


class Board:
    """One member's handle on a board.

    send: a callable send(dict) -> dict that reaches a BoardHost (in tests a
    direct call; in production a Session.request over Tor).
    """

    def __init__(
        self,
        send,
        board_id: str,
        keys: AgentKeys,
        group_key: GroupKey,
        known_members: set | None = None,
    ):
        self._send = send
        self.board_id = board_id
        self._keys = keys
        self._group = group_key
        # If provided, authorship is only trusted from these fingerprints.
        self._known = known_members
        self._cursor = 0  # last seq this handle has read

    # -- posting ---------------------------------------------------------
    def post(self, text: str) -> int:
        ct = self._group.encrypt(text.encode("utf-8"))
        ct_b64 = base64.b64encode(ct).decode("ascii")
        author = self._keys.public().to_dict()
        ts = time.time()
        sig = self._keys.sign(_canonical(self.board_id, author, ts, ct_b64))
        entry = {
            "author": author,
            "ts": ts,
            "ct": ct_b64,
            "sig": base64.b64encode(sig).decode("ascii"),
        }
        reply = self._send(
            {"op": "board.append", "board": self.board_id, "entry": entry}
        )
        if not reply.get("ok"):
            raise RuntimeError(f"append failed: {reply.get('error')}")
        return reply["seq"]

    # -- reading ---------------------------------------------------------
    def history(self, from_start: bool = False):
        """Return messages newer than this handle has seen (or all, if
        from_start). Decrypts with the group key and verifies each signature.
        Raises CryptoError if an entry cannot be decrypted (wrong key)."""
        after = 0 if from_start else self._cursor
        reply = self._send(
            {"op": "board.since", "board": self.board_id, "after": after}
        )
        if not reply.get("ok"):
            raise RuntimeError(f"since failed: {reply.get('error')}")

        out = []
        for e in reply["entries"]:
            plaintext = self._group.decrypt(base64.b64decode(e["ct"]))  # may raise
            author = PublicIdentity.from_dict(e["author"])
            signed = _canonical(self.board_id, e["author"], e["ts"], e["ct"])
            sig = base64.b64decode(e["sig"])
            verified = author.verify(signed, sig)
            if self._known is not None and author.fingerprint not in self._known:
                verified = False  # signature ok but author is not a known member
            out.append(
                Message(
                    seq=e["seq"],
                    author_fingerprint=author.fingerprint,
                    text=plaintext.decode("utf-8"),
                    ts=e["ts"],
                    verified=verified,
                )
            )
            self._cursor = max(self._cursor, e["seq"])
        return out

    # -- membership ------------------------------------------------------
    def make_invite(self, member: PublicIdentity) -> str:
        """Seal the group key to a prospective member. Deliver out of band."""
        sealed = seal_to(member, self._group.material)
        return base64.b64encode(sealed).decode("ascii")

    @classmethod
    def from_invite(
        cls, send, board_id: str, keys: AgentKeys, invite_b64: str, known_members=None
    ) -> "Board":
        material = open_sealed(keys, base64.b64decode(invite_b64))
        return cls(send, board_id, keys, GroupKey(material), known_members)
