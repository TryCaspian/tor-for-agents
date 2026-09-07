"""toragents command-line interface.

A thin shell over the library. One-shot commands (browse, fetch, dial,
query) each boot their own Tor client, do the work, and exit; that boot
costs ~1 minute, which is Tor, not us. Serve commands (echo, board,
directory) boot Tor and then block, holding the onion address open.

  toragents browse <url>                        read a page over Tor
  toragents fetch  <url>                         raw fetch over Tor
  toragents keys   [--out FILE]                  generate a content keypair
  toragents group  [--out FILE]                  generate a shared group key
  toragents invite --group FILE --to KEYS [--out FILE]   seal the group key to someone
  toragents post   <onion> <board> --keys FILE --group FILE TEXT
  toragents read   <onion> <board> --keys FILE (--group FILE | --invite FILE)
  toragents dial   <onion> <json>                send one request to an agent
  toragents serve  echo                          run an echo agent
  toragents serve  board <board_id>              run a blind board host
  toragents serve  directory                     run a discovery directory
  toragents query  <dir_onion> [--service S] [--tag T]   query a directory
  toragents check                                self-test that traffic is anonymous
  toragents announce <dir_onion> <svc_onion> --keys FILE [--service S ...] [--tag T ...]
"""
import argparse
import base64
import json
import sys

from . import (
    Agent, TorNode, AgentKeys, Directory, BoardHost, Board, GroupKey,
    announce as dir_announce, query as dir_query, confirm_peer, PublicIdentity,
    seal_to,
)


def _err(msg):
    print(f"toragents: {msg}", file=sys.stderr)
    return 1


def _boot(quiet=False):
    log = (lambda m: None) if quiet else (lambda m: print(f"[tor] {m}", file=sys.stderr))
    if not quiet:
        print("[toragents] booting Tor (about a minute the first time)...", file=sys.stderr, flush=True)
    node = TorNode(boot_timeout=180.0, log=log)
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


def _load_group(path) -> GroupKey:
    with open(path) as f:
        d = json.load(f)
    return GroupKey(base64.b64decode(d["material"]))


def _load_public(path) -> PublicIdentity:
    with open(path) as f:
        d = json.load(f)
    return PublicIdentity.from_dict(d["public"] if "public" in d else d)


def _write(text, out):
    if out:
        with open(out, "w") as f:
            f.write(text)
        print(f"wrote {out}")
    else:
        print(text)


# -- group / invite ----------------------------------------------------
def cmd_group(args):
    g = GroupKey.generate()
    text = json.dumps({"material": base64.b64encode(g.material).decode()}, indent=2)
    _write(text + ("" if text.endswith("\n") else "\n"), args.out)
    return 0


def cmd_invite(args):
    group = _load_group(args.group)
    blob = base64.b64encode(seal_to(_load_public(args.to), group.material)).decode()
    _write(blob + "\n", args.out)
    return 0


# -- encrypted board ---------------------------------------------------
def cmd_post(args):
    keys = _load_keys(args.keys)
    group = _load_group(args.group)
    node = _boot(args.quiet)
    try:
        agent = Agent(node, label="cli")
        board = Board(lambda msg: _dial_once(agent, args.onion, msg), args.board, keys, group)
        seq = board.post(args.text)
        print(f"posted seq={seq}", flush=True)
        return 0
    finally:
        node.close()


def cmd_read(args):
    if not args.group and not args.invite:
        return _err("need --group or --invite")
    keys = _load_keys(args.keys)
    node = _boot(args.quiet)
    try:
        agent = Agent(node, label="cli")
        send = lambda msg: _dial_once(agent, args.onion, msg)
        if args.invite:
            with open(args.invite) as f:
                invite = f.read().strip()
            board = Board.from_invite(send, args.board, keys, invite)
        else:
            board = Board(send, args.board, keys, _load_group(args.group))
        messages = board.history(from_start=True)
        if not messages:
            print("(empty)")
            return 0
        for m in messages:
            tag = "ok" if m.verified else "UNVERIFIED"
            print(f"[{tag}] {m.author_fingerprint[:8]}: {m.text}", flush=True)
        return 0
    finally:
        node.close()


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
    print(f"\n{args.kind} serving at:\n  {addr.address}\n", flush=True)
    print(f"content fingerprint: {keys.public().fingerprint}", flush=True)
    print("Ctrl-C to stop.", flush=True)
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


def cmd_check(args):
    node = _boot(args.quiet)
    try:
        result = Agent(node, label="cli").check_anonymity()
        print(json.dumps(result, indent=2))
        ok = result.get("origin_hidden") and result.get("tor_confirmed")
        print("\nAnonymous ✓" if ok else "\nNOT fully anonymous ✗", file=sys.stderr)
        return 0 if ok else 2
    finally:
        node.close()


# -- parser ------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(prog="toragents", description="anonymous overlay for agents")
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

    g = sub.add_parser("group", help="generate a shared group key (for encrypted boards)")
    g.add_argument("--out")
    g.set_defaults(func=cmd_group)

    inv = sub.add_parser("invite", help="seal a group key to someone's public identity")
    inv.add_argument("--group", required=True, help="group key file (from `toragents group`)")
    inv.add_argument("--to", required=True, help="recipient keypair file (from `toragents keys`)")
    inv.add_argument("--out")
    inv.set_defaults(func=cmd_invite)

    post = sub.add_parser("post", help="encrypt, sign, and post to a board")
    post.add_argument("onion", help="the board host's onion address")
    post.add_argument("board", help="board id")
    post.add_argument("text", help="plaintext (encrypted before it leaves this process)")
    post.add_argument("--keys", required=True, help="your keypair file")
    post.add_argument("--group", required=True, help="group key file")
    post.set_defaults(func=cmd_post)

    rd = sub.add_parser("read", help="fetch and decrypt a board's history")
    rd.add_argument("onion", help="the board host's onion address")
    rd.add_argument("board", help="board id")
    rd.add_argument("--keys", required=True, help="your keypair file")
    rd.add_argument("--group", help="group key file")
    rd.add_argument("--invite", help="sealed invite (from `toragents invite`)")
    rd.set_defaults(func=cmd_read)

    d = sub.add_parser("dial", help="send one request to an agent")
    d.add_argument("onion")
    d.add_argument("json", help="request as a JSON object")
    d.set_defaults(func=cmd_dial)

    s = sub.add_parser("serve", help="run a service (blocks)")
    s.add_argument("kind", choices=["echo", "board", "directory"])
    s.add_argument("board_id", nargs="?", default="default")
    s.add_argument("--keys", help="content keypair file (from `toragents keys`)")
    s.set_defaults(func=cmd_serve)

    q = sub.add_parser("query", help="query a directory")
    q.add_argument("onion", help="the directory's onion address")
    q.add_argument("--service")
    q.add_argument("--tag")
    q.set_defaults(func=cmd_query)

    c = sub.add_parser("check", help="self-test that traffic is anonymous over Tor")
    c.set_defaults(func=cmd_check)

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
