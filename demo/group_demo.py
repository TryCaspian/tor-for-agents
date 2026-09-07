"""toragents group demo: an encrypted board with a blind host, over real Tor.

  Host    runs a BoardHost at an onion address. It stores and serves
          entries but holds no key, so it only ever sees ciphertext.
  Alice   creates a group/board, posts, and invites Bob by sealing the
          group key to Bob's public key.
  Bob     joins from the invite, reads the full history, and replies.
  We then print what the HOST actually stored, to prove it is blind.

Also times a round trip through Tor so the "how slow is it really"
question has a real number, not a vibe.

Run:  ../.venv/bin/python group_demo.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from toragents import Agent, TorNode, AgentKeys, GroupKey, Board, BoardHost  # noqa: E402


def banner(msg):
    print(f"\n\033[1;35m== {msg} ==\033[0m")


def main():
    banner("Booting Tor (one client hosts, agents dial in)")
    tor = TorNode(log=lambda m: None)
    tor.start()
    print("tor up")

    try:
        banner("Host publishes a blind board service")
        host_backend = BoardHost()
        host_agent = Agent(tor, label="board-host")
        host_addr = host_agent.serve(host_backend.handle)
        print(f"board host at: {host_addr.address}")

        # A member's transport: dial the host per request, send, get reply.
        def make_send(agent):
            def send(msg):
                with agent.dial(host_addr) as sess:
                    return sess.request(msg)

            return send

        banner("Alice creates a group and posts")
        alice_keys = AgentKeys.generate()
        alice_agent = Agent(tor, label="alice")
        group = GroupKey.generate()
        alice_board = Board(make_send(alice_agent), "resistance", alice_keys, group)

        t0 = time.time()
        alice_board.post("gm. the corner is ours.")
        rtt = time.time() - t0
        alice_board.post("meet at the usual onion.")
        print(f"Alice posted 2 messages (first round trip {rtt:.1f}s over Tor)")

        banner("Alice invites Bob (group key sealed to Bob)")
        bob_keys = AgentKeys.generate()
        bob_agent = Agent(tor, label="bob")
        invite = alice_board.make_invite(bob_keys.public())
        print(f"invite blob (sealed, opaque): {invite[:44]}...")

        banner("Bob joins from the invite and reads history")
        bob_board = Board.from_invite(
            make_send(bob_agent), "resistance", bob_keys, invite
        )
        for m in bob_board.history():
            tag = "ok" if m.verified else "UNVERIFIED"
            print(f"  [{tag}] {m.author_fingerprint[:8]}: {m.text}")
        bob_board.post("on my way.")

        banner("Alice sees Bob's reply")
        for m in alice_board.history():
            print(f"  {m.author_fingerprint[:8]}: {m.text}")

        banner("What the HOST actually stored")
        for e in host_backend.raw_entries("resistance"):
            print(f"  seq {e['seq']}: ct={e['ct'][:36]}...  (host cannot read this)")

        banner("Done")
        print("An encrypted group with shared history, on a host that is blind.")
    finally:
        host_agent.stop()
        tor.close()


if __name__ == "__main__":
    main()
