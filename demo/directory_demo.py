"""toragents directory demo: a stranger arrives and transacts, untracked.

  Directory  runs at an onion address. Agents announce signed descriptors
             of what they offer; anyone can query.
  Reverser   serves a "reverse" tool and announces it to the directory.
  Newcomer   knows ONLY the directory's onion address. It queries for a
             service, discovers the reverser, dials it, cryptographically
             confirms it is talking to the key the directory named, then
             calls the tool. No prior contact, no tracking (Tor hides
             origin), trust established peer to peer.

Run:  ../.venv/bin/python directory_demo.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from toragents import (  # noqa: E402
    Agent, TorNode, AgentKeys, Directory, PublicIdentity,
    announce, query, confirm_peer,
)


def banner(msg):
    print(f"\n\033[1;35m== {msg} ==\033[0m")


def main():
    banner("Booting Tor")
    tor = TorNode(log=lambda m: None)
    tor.start()
    print("tor up")

    dir_agent = reverser = None
    try:
        banner("Publish the directory")
        directory = Directory()
        dir_agent = Agent(tor, label="directory")
        dir_addr = dir_agent.serve(directory.handle)
        print(f"directory at: {dir_addr.address}")

        def dir_send(msg):
            with dir_agent.dial(dir_addr) as s:
                return s.request(msg)

        banner("Reverser comes online and announces a service")
        rev_keys = AgentKeys.generate()
        reverser = Agent(tor, label="reverser", keys=rev_keys)

        def reverse_tool(req):
            if req.get("op") == "reverse":
                return {"ok": True, "result": req["s"][::-1]}
            return {"ok": False, "error": "unknown op"}

        rev_addr = reverser.serve(reverse_tool)
        announce(
            dir_send, rev_keys, rev_addr.address,
            services=[{"name": "reverse", "price": "1 claw", "in": "s", "out": "result"}],
            tags=["tools", "text"],
        )
        print(f"reverser announced {rev_addr.address} offering 'reverse'")

        banner("A stranger arrives knowing only the directory address")
        newcomer = Agent(tor, label="newcomer")
        found = query(dir_send, service="reverse")
        print(f"query 'reverse' -> {len(found)} result(s)")
        entry = found[0]["descriptor"]
        target_addr = entry["address"]
        target_key = PublicIdentity.from_dict(entry["pubkey"])
        price = entry["services"][0].get("price")
        print(f"found {target_addr}  (price: {price})")

        banner("Dial the discovered agent and verify who it is")
        with newcomer.dial(target_addr) as sess:
            ok = confirm_peer(sess, target_key)
            print(f"identity proof: {'CONFIRMED' if ok else 'FAILED'}")
            if ok:
                reply = sess.request({"op": "reverse", "s": "untracked and unafraid"})
                print(f"transaction result: {reply}")

        banner("Done")
        print("A stranger discovered a service and transacted, origin hidden,")
        print("trusting the counterpart's key, not the directory.")
    finally:
        if reverser:
            reverser.stop()
        if dir_agent:
            dir_agent.stop()
        tor.close()


if __name__ == "__main__":
    main()
