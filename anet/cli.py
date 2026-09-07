"""anet command-line interface.

A thin shell over the library. One-shot commands (browse, fetch, dial,
query) each boot their own Tor client, do the work, and exit; that boot
costs ~1 minute, which is Tor, not us. Serve commands (echo, board,
directory) boot Tor and then block, holding the onion address open.

  anet browse <url>                        read a page over Tor
  anet fetch  <url>                         raw fetch over Tor
  anet keys   [--out FILE]                  generate a content keypair
  anet dial   <onion> <json>                send one request to an agent
  anet serve  echo                          run an echo agent
  anet serve  board <board_id>              run a blind board host
  anet serve  directory                     run a discovery directory
  anet query  <dir_onion> [--service S] [--tag T]   query a directory
  anet announce <dir_onion> <svc_onion> --keys FILE [--service S ...] [--tag T ...]
"""
import argparse
import base64
import json
import sys

from . import (
    Agent, TorNode, AgentKeys, Directory, BoardHost,
    announce as dir_announce, query as dir_query, confirm_peer, PublicIdentity,
)


def _err(msg):
    print(f"anet: {msg}", file=sys.stderr)
    return 1


def _boot(quiet=False):
    log = (lambda m: None) if quiet else (lambda m: print(f"[tor] {m}", file=sys.stderr))
    if not quiet:
        print("[anet] booting Tor (about a minute the first time)...", file=sys.stderr)
    node = TorNode(log=log)
    node.start()
    return node


# -- keys --------------------------------------------------------------
def cmd_keys(args):
    keys = AgentKeys.generate()
    pub = keys.public()
    material = {
        "signing": base64.b64encode(bytes(keys._signing)).decode(),
        "box": base64.b64encode(bytes(keys._box_private)).decode(),
        "public": pub.to_dict(),
        "fingerprint": pub.fingerprint,
    }
    text = json.dumps(material, indent=2)
    if args.out:
        with open(args.out, "w") as f:
            f.write(text)
        print(f"wrote keypair to {args.out} (fingerprint {pub.fingerprint})")
    else:
        print(text)
    return 0


def _load_keys(path) -> AgentKeys:
    from nacl import public, signing

    with open(path) as f:
        d = json.load(f)
    return AgentKeys(
        signing.SigningKey(base64.b64decode(d["signing"])),
        public.PrivateKey(base64.b64decode(d["box"])),
    )


# -- browse / fetch ----------------------------------------------------
def cmd_browse(args):
    node = _boot(args.quiet)
    try:
        page = Agent(node, label="cli").browse(args.url)
        print(f"{page.status} {page.content_type} :: {page.title}")
        print()
        print(page.text[: args.limit])
        if args.links:
            print("\n--- links ---")
            for l in page.links:
                print(l)
        return 0
    finally:
        node.close()


def cmd_fetch(args):
    node = _boot(args.quiet)
    try:
        print(Agent(node, label="cli").fetch(args.url))
        return 0
    finally:
        node.close()


# -- dial --------------------------------------------------------------
def cmd_dial(args):
    try:
        request = json.loads(args.json)
    except json.JSONDecodeError as e:
        return _err(f"request is not valid JSON: {e}")
    node = _boot(args.quiet)
    try:
        with Agent(node, label="cli").dial(args.onion) as sess:
            print(json.dumps(sess.request(request), indent=2))
        return 0
    finally:
        node.close()


# -- serve -------------------------------------------------------------
def cmd_serve(args):
    node = _boot(args.quiet)
    keys = _load_keys(args.keys) if args.keys else AgentKeys.generate()
    agent = Agent(node, label=args.kind, keys=keys)

    if args.kind == "echo":
        handler = lambda req: {"ok": True, "echo": req}
    elif args.kind == "board":
        handler = BoardHost().handle
    elif args.kind == "directory":
        handler = Directory().handle
    else:
        node.close()
        return _err(f"unknown serve kind {args.kind!r}")

    addr = agent.serve(handler)
    print(f"\n{args.kind} serving at:\n  {addr.address}\n")
    print(f"content fingerprint: {keys.public().fingerprint}")
    print("Ctrl-C to stop.")
    try:
        import time

        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\nstopping.")
    finally:
        agent.stop()
        node.close()
    return 0


# -- directory ---------------------------------------------------------
def cmd_query(args):
    node = _boot(args.quiet)
    try:
        agent = Agent(node, label="cli")
        send = lambda msg: _dial_once(agent, args.onion, msg)
        results = dir_query(send, service=args.service, tag=args.tag)
        print(json.dumps([r["descriptor"] for r in results], indent=2))
        return 0
    finally:
        node.close()


def cmd_announce(args):
    keys = _load_keys(args.keys)
    services = [{"name": s} for s in (args.service or [])]
    node = _boot(args.quiet)
    try:
        agent = Agent(node, label="cli")
        send = lambda msg: _dial_once(agent, args.onion, msg)
        reply = dir_announce(
            send, keys, args.address, services=services, tags=(args.tag or [])
        )
        print(json.dumps(reply, indent=2))
        return 0 if reply.get("ok") else 1
    finally:
        node.close()


def _dial_once(agent, onion, msg):
    with agent.dial(onion) as sess:
        return sess.request(msg)


# -- parser ------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(prog="anet", description="anonymous overlay for agents")
    p.add_argument("-q", "--quiet", action="store_true", help="suppress Tor boot logs")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("browse", help="read a web page over Tor")
    b.add_argument("url")
    b.add_argument("--limit", type=int, default=4000)
    b.add_argument("--links", action="store_true")
    b.set_defaults(func=cmd_browse)

    f = sub.add_parser("fetch", help="raw fetch over Tor")
    f.add_argument("url")
    f.set_defaults(func=cmd_fetch)

    k = sub.add_parser("keys", help="generate a content keypair")
    k.add_argument("--out")
    k.set_defaults(func=cmd_keys)

    d = sub.add_parser("dial", help="send one request to an agent")
    d.add_argument("onion")
    d.add_argument("json", help="request as a JSON object")
    d.set_defaults(func=cmd_dial)

    s = sub.add_parser("serve", help="run a service (blocks)")
    s.add_argument("kind", choices=["echo", "board", "directory"])
    s.add_argument("board_id", nargs="?", default="default")
    s.add_argument("--keys", help="content keypair file (from `anet keys`)")
    s.set_defaults(func=cmd_serve)

    q = sub.add_parser("query", help="query a directory")
    q.add_argument("onion", help="the directory's onion address")
    q.add_argument("--service")
    q.add_argument("--tag")
    q.set_defaults(func=cmd_query)

    a = sub.add_parser("announce", help="announce a service to a directory")
    a.add_argument("onion", help="the directory's onion address")
    a.add_argument("address", help="the onion address you are announcing")
    a.add_argument("--keys", required=True, help="content keypair file")
    a.add_argument("--service", action="append")
    a.add_argument("--tag", action="append")
    a.set_defaults(func=cmd_announce)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
