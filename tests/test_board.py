"""Encrypted board / group tests. No network: the client talks to the host
through a direct in-memory call, so we test the protocol and crypto without
paying for Tor.

Invariants under test:
  - members can post and read a shared history
  - the host stores only ciphertext and cannot read messages
  - authorship is signed and verified; forgeries are caught
  - a non-member (no group key) cannot read
"""
import json

import pytest

from toragents.crypto import AgentKeys, GroupKey, CryptoError
from toragents.board import BoardHost, Board


@pytest.fixture
def host_send():
    """A direct transport to a fresh in-memory host: send(msg) -> reply."""
    host = BoardHost()
    fixture = lambda msg: host.handle(msg)
    fixture.host = host
    return fixture


def test_two_members_share_history(host_send):
    alice, bob = AgentKeys.generate(), AgentKeys.generate()
    members = {alice.public().fingerprint, bob.public().fingerprint}

    group = GroupKey.generate()
    a_board = Board(host_send, "town-square", alice, group, known_members=members)
    b_board = Board(host_send, "town-square", bob, group, known_members=members)

    a_board.post("gm from alice")
    b_board.post("gm from bob")

    history = b_board.history()
    texts = [m.text for m in history]
    assert texts == ["gm from alice", "gm from bob"]
    assert all(m.verified for m in history)


def test_history_is_incremental(host_send):
    alice = AgentKeys.generate()
    group = GroupKey.generate()
    board = Board(host_send, "log", alice, group)
    board.post("one")
    first = board.history()
    assert [m.text for m in first] == ["one"]
    board.post("two")
    # A second call returns only what is new since the last read.
    delta = board.history()
    assert [m.text for m in delta] == ["two"]


def test_host_cannot_read_messages(host_send):
    alice = AgentKeys.generate()
    group = GroupKey.generate()
    board = Board(host_send, "secret", alice, group)
    board.post("the eagle lands at midnight")

    stored = host_send.host.raw_entries("secret")
    blob = json.dumps(stored)
    assert "eagle" not in blob
    assert "midnight" not in blob  # only ciphertext on the host


def test_non_member_cannot_decrypt(host_send):
    alice = AgentKeys.generate()
    group = GroupKey.generate()
    board = Board(host_send, "clubhouse", alice, group)
    board.post("members only")

    outsider = AgentKeys.generate()
    wrong_key = GroupKey.generate()
    intruder = Board(host_send, "clubhouse", outsider, wrong_key)
    with pytest.raises(CryptoError):
        intruder.history()


def test_forged_authorship_is_flagged(host_send):
    alice, mallory = AgentKeys.generate(), AgentKeys.generate()
    group = GroupKey.generate()

    # Mallory (a group-key holder) posts, then relabels her entry to claim
    # Alice wrote it. She cannot produce Alice's signature, so it must fail.
    mallory_board = Board(host_send, "gov", mallory, group)
    mallory_board.post("i am totally alice")

    entries = host_send.host.raw_entries("gov")
    entries[0]["author"] = alice.public().to_dict()  # impersonate alice

    reader = Board(host_send, "gov", AgentKeys.generate(), group)
    history = reader.history()
    assert history[0].verified is False  # signature does not match claimed author


def test_invite_flow_shares_group_key(host_send):
    founder = AgentKeys.generate()
    group = GroupKey.generate()
    founder_board = Board(host_send, "invite-me", founder, group)
    founder_board.post("welcome")

    newcomer = AgentKeys.generate()
    invite = founder_board.make_invite(newcomer.public())  # sealed to newcomer

    joined = Board.from_invite(host_send, "invite-me", newcomer, invite)
    assert [m.text for m in joined.history()] == ["welcome"]


def test_pinned_host_rejects_other_board_id():
    host = BoardHost(board_id="dead-drop")
    alice = AgentKeys.generate()
    group = GroupKey.generate()

    wrong = Board(host.handle, "other-log", alice, group)
    with pytest.raises(RuntimeError, match="only serves board"):
        wrong.post("should not land here")
    assert host.raw_entries("other-log") == []
    assert host.raw_entries("dead-drop") == []

    right = Board(host.handle, "dead-drop", alice, group)
    right.post("only this board")
    assert len(host.raw_entries("dead-drop")) == 1
