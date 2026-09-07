"""anet browse demo: an agent browses the web over Tor, like WebFetch.

Fetches a clearnet page and a .onion service, both through the Tor exit,
and prints the readable text and links. Origin hidden throughout.

Run:  ../.venv/bin/python browse_demo.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anet import Agent, TorNode  # noqa: E402


def banner(msg):
    print(f"\n\033[1;35m== {msg} ==\033[0m")


# The Tor Project's own onion service, a stable v3 address for the demo.
TOR_ONION = "http://2gzyxa5ihm7nsggfxnu52rck2vv4rvmdlkiu3zzui5du4xyclen53wid.onion/"


def main():
    banner("Booting Tor")
    tor = TorNode(log=lambda m: None)
    tor.start()
    agent = Agent(tor, label="browser")
    print("tor up")

    try:
        banner("Browse a clearnet page over Tor")
        page = agent.browse("https://check.torproject.org/")
        print(f"{page.status} {page.content_type} :: {page.title}")
        print(page.text[:400])
        print(f"... ({len(page.links)} links found)")

        banner("Browse a .onion site over Tor (no exit node, end to end)")
        try:
            onion = agent.browse(TOR_ONION)
            print(f"{onion.status} {onion.content_type} :: {onion.title}")
            print(onion.text[:400])
        except Exception as exc:
            print(f"(onion fetch failed this run: {exc})")

        banner("Done")
        print("An agent browsed the clearnet and the dark web, origin hidden.")
    finally:
        tor.close()


if __name__ == "__main__":
    main()
