"""torfetch: WebFetch, but over Tor.

A tiny client that talks to the warm anet daemon (starting it if it is not
already running) and prints a page fetched anonymously through Tor. Any
harness that can run a shell command can call it, so a user's intent, "read
this over Tor", becomes a direct capability without the model having to
decide anything.

    torfetch https://example.com                 # readable text (browse)
    torfetch https://example.com --raw            # raw body (fetch)
    torfetch https://example.com --links          # text + links
    torfetch https://example.com --json           # full JSON (status/title/text/links)
    torfetch <onion> --dial '{"op":"ping"}'       # call an agent, print reply

Exit codes: 0 ok, 1 usage/arg error, 2 fetch/network error, 3 daemon boot timeout.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

HOST = os.environ.get("ANET_DAEMON_HOST", "127.0.0.1")
PORT = int(os.environ.get("ANET_DAEMON_PORT", "8787"))
TOKEN = os.environ.get("ANET_DAEMON_TOKEN")
BASE = f"http://{HOST}:{PORT}"


def _call(method, path, body=None, timeout=120):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if TOKEN:
        req.add_header("X-Anet-Token", TOKEN)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def _daemon_status():
    try:
        _, obj = _call("GET", "/status", timeout=3)
        return obj
    except (urllib.error.URLError, ConnectionError, OSError):
        return None


def ensure_daemon(boot_timeout=150) -> bool:
    """Make sure the daemon is up and Tor is ready. Spawn it if needed."""
    status = _daemon_status()
    if status is None:
        # Not running: spawn it detached, logging to ~/.anet/daemon.log.
        log_dir = os.path.expanduser("~/.anet")
        os.makedirs(log_dir, exist_ok=True)
        logf = open(os.path.join(log_dir, "daemon.log"), "ab")
        print("torfetch: starting anet daemon (first run boots Tor, ~1 min)...",
              file=sys.stderr)
        subprocess.Popen(
            [sys.executable, "-m", "anet.daemon"],
            stdout=logf, stderr=logf, stdin=subprocess.DEVNULL,
            start_new_session=True,
        )

    deadline = time.time() + boot_timeout
    while time.time() < deadline:
        status = _daemon_status()
        if status and status.get("tor_ready"):
            return True
        time.sleep(2)
    return False


def main(argv=None):
    p = argparse.ArgumentParser(prog="torfetch", description="WebFetch over Tor")
    p.add_argument("url", help="a URL (clearnet or .onion), or an onion for --dial")
    p.add_argument("--raw", action="store_true", help="raw body instead of readable text")
    p.add_argument("--links", action="store_true", help="also print extracted links")
    p.add_argument("--json", action="store_true", help="print the full JSON response")
    p.add_argument("--dial", metavar="JSON", help="call an agent: JSON request object")
    p.add_argument("--limit", type=int, default=6000, help="max chars of text to print")
    args = p.parse_args(argv)

    if not ensure_daemon():
        print("torfetch: daemon did not become ready in time (see ~/.anet/daemon.log)",
              file=sys.stderr)
        return 3

    try:
        if args.dial is not None:
            try:
                request = json.loads(args.dial)
            except json.JSONDecodeError as e:
                print(f"torfetch: --dial is not valid JSON: {e}", file=sys.stderr)
                return 1
            _, obj = _call("POST", "/dial", {"address": args.url, "request": request})
        elif args.raw:
            _, obj = _call("POST", "/fetch", {"url": args.url})
        else:
            _, obj = _call("POST", "/browse", {"url": args.url})
    except urllib.error.URLError as e:
        print(f"torfetch: request failed: {e}", file=sys.stderr)
        return 2

    if not obj.get("ok"):
        print(f"torfetch: {obj.get('error', 'unknown error')}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(obj, indent=2))
        return 0
    if args.dial is not None:
        print(json.dumps(obj["reply"], indent=2))
        return 0
    if args.raw:
        print(obj["body"])
        return 0

    # readable browse output
    print(f"{obj['status']} {obj.get('content_type','')} :: {obj.get('title','')}".strip())
    print()
    print(obj["text"][: args.limit])
    if args.links:
        print("\n--- links ---")
        for l in obj.get("links", []):
            print(l)
    return 0


if __name__ == "__main__":
    sys.exit(main())
