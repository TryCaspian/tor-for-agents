"""toragents demo: two agents meet on the anonymous overlay.

  Bob   runs a "tool" service (reverse a string) at an onion address.
  Alice knows only that address. She dials it over Tor, calls the tool,
        and gets an answer. Neither the path nor the endpoints are
        observable, and Bob never learns who called.
  Alice then makes an anonymous outbound call to prove her origin is
        hidden: the exit IP is not her real one.

Bob and Alice each run their own Tor client, so they are genuinely
separate on the network, not two faces of one process. The two Tor
instances bootstrap concurrently to keep the wait to about a minute.

Run:  ../.venv/bin/python demo.py
"""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from toragents import Agent, TorNode  # noqa: E402


def banner(msg):
    print(f"\n\033[1;35m== {msg} ==\033[0m")


def start_tor(label, holder):
    node = TorNode(log=lambda m, l=label: None)
    node.start()
    holder[label] = node


def main():
    banner("Booting two independent Tor clients (concurrent)")
    holder = {}
    t0 = time.time()
    threads = [
        threading.Thread(target=start_tor, args=("bob", holder)),
        threading.Thread(target=start_tor, args=("alice", holder)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    bob_tor, alice_tor = holder["bob"], holder["alice"]
    print(f"both Tor clients up in {time.time() - t0:.0f}s")

    try:
        banner("Bob publishes a tool service on the overlay")
        bob = Agent(bob_tor, label="bob")

        def tool(req):
            if req.get("op") == "reverse":
                return {"ok": True, "result": req["s"][::-1]}
            if req.get("op") == "ping":
                return {"ok": True, "result": "pong"}
            return {"ok": False, "error": f"unknown op {req.get('op')!r}"}

        bob_addr = bob.serve(tool)
        print(f"Bob is reachable at:  {bob_addr.address}")
        print("(that address is Bob's whole identity. nobody can locate his host.)")

        banner("Alice dials Bob knowing only his onion address")
        alice = Agent(alice_tor, label="alice")
        with alice.dial(bob_addr) as sess:
            print("Alice -> {'op':'ping'}")
            print("Bob   ->", sess.request({"op": "ping"}))
            print("Alice -> {'op':'reverse','s':'anonymous corner of the internet'}")
            reply = sess.request(
                {"op": "reverse", "s": "anonymous corner of the internet"}
            )
            print("Bob   ->", reply)

        banner("Alice makes an anonymous outbound call")
        real_ip = _clearnet_ip()
        exit_ip = alice.fetch("https://api.ipify.org").strip()
        print(f"Alice's real IP:      {real_ip}")
        print(f"IP the world sees:    {exit_ip}")
        print("hidden" if exit_ip and exit_ip != real_ip else "NOT hidden")

        banner("Done")
        print("Two agents talked with no traceable origin and no observable path.")
    finally:
        bob.stop()
        bob_tor.close()
        alice_tor.close()


def _clearnet_ip():
    import httpx

    try:
        return httpx.get("https://api.ipify.org", timeout=15).text.strip()
    except Exception:
        return "unknown"


if __name__ == "__main__":
    main()
