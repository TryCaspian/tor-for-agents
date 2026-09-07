"""anet MCP server.

Exposes the anonymous overlay to an MCP client (Claude, etc.) as tools. The
server process holds one persistent Tor client, started lazily on the first
tool that needs it (about a minute), and a registry of services it is
hosting, so an agent can stand up a board or directory and then use it
across several tool calls.

Run:  anet-mcp        (stdio transport)
"""
import base64
import json
import threading

from mcp.server.mcpserver import MCPServer

from . import (
    Agent, TorNode, AgentKeys, Directory, BoardHost, Board, GroupKey,
    PublicIdentity, announce as dir_announce, query as dir_query, confirm_peer,
)

mcp = MCPServer(
    name="anet",
    instructions=(
        "anet is an anonymous, Tor-based overlay for agents. Every agent is a "
        "hidden service reachable at a .onion address; traffic has no traceable "
        "origin. Use anet_browse/anet_fetch to read the web anonymously, "
        "anet_dial to call another agent, anet_serve_* to host a service, and "
        "the directory tools to publish or discover services. The first tool "
        "call boots Tor and takes about a minute."
    ),
)


class _State:
    """Process-wide Tor client and hosted-service registry."""

    def __init__(self):
        self._lock = threading.Lock()
        self.tor = None
        self.services = {}  # onion address -> {"agent":..., "kind":..., "backend":...}

    def ensure_tor(self):
        with self._lock:
            if self.tor is None:
                self.tor = TorNode(log=lambda m: None)
                self.tor.start()
            return self.tor

    def agent(self, label="mcp", keys=None):
        return Agent(self.ensure_tor(), label=label, keys=keys)


S = _State()


def _send_to(agent, onion):
    def send(msg):
        with agent.dial(onion) as sess:
            return sess.request(msg)

    return send


# ---------------------------------------------------------------------------
# Status and outbound
# ---------------------------------------------------------------------------
@mcp.tool(description="Report whether Tor is up and list services this server hosts.")
def anet_status() -> dict:
    return {
        "tor_running": S.tor is not None,
        "hosted_services": [
            {"address": a, "kind": v["kind"]} for a, v in S.services.items()
        ],
    }


@mcp.tool(
    description="Self-test anonymity: confirm outbound traffic exits via Tor and "
    "that the exit IP differs from the real one. Use to verify the agent is "
    "actually anonymous before doing sensitive work."
)
def anet_check() -> dict:
    return S.agent().check_anonymity()


@mcp.tool(
    description="Rotate to fresh Tor circuits (like Tor Browser's 'New Identity'). "
    "Subsequent requests are unlinkable from earlier ones."
)
def anet_new_identity() -> dict:
    S.ensure_tor().new_identity()
    return {"ok": True, "rotated": True}


@mcp.tool(
    description="Browse a web page over Tor (clearnet or .onion). Returns status, "
    "title, readable text, and links. Origin is hidden; page content is still "
    "visible to the destination."
)
def anet_browse(url: str, text_limit: int = 6000) -> dict:
    page = S.agent().browse(url)
    text = page.text[:text_limit]
    return {
        "url": page.url,
        "status": page.status,
        "content_type": page.content_type,
        "title": page.title,
        "text": text,
        "truncated": len(page.text) > text_limit,
        "links": page.links[:100],
    }


@mcp.tool(description="Fetch a URL's raw body over Tor with the origin hidden.")
def anet_fetch(url: str) -> str:
    return S.agent().fetch(url)


@mcp.tool(
    description="torfetch: like WebFetch, but over Tor. Read a web page (clearnet "
    "or .onion) with the request origin hidden, and get back the readable text, "
    "title, and links. Use this whenever the user wants to fetch or read "
    "something anonymously, over Tor, or a .onion address."
)
def torfetch(url: str, raw: bool = False, text_limit: int = 6000) -> dict:
    agent = S.agent()
    if raw:
        return {"ok": True, "body": agent.fetch(url)}
    page = agent.browse(url)
    return {
        "ok": True,
        "url": page.url,
        "status": page.status,
        "title": page.title,
        "text": page.text[:text_limit],
        "truncated": len(page.text) > text_limit,
        "links": page.links[:100],
    }


@mcp.tool(
    description="Send one JSON request to another agent at its .onion address and "
    "return the reply. request_json must be a JSON object."
)
def anet_dial(address: str, request_json: str) -> dict:
    try:
        request = json.loads(request_json)
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"request_json is not valid JSON: {e}"}
    agent = S.agent()
    with agent.dial(address) as sess:
        return {"ok": True, "reply": sess.request(request)}


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------
@mcp.tool(
    description="Generate a new agent content keypair (Ed25519 signing + Curve25519 "
    "box). Returns base64 secrets and the public identity. Keep the secrets safe."
)
def anet_generate_keys() -> dict:
    keys = AgentKeys.generate()
    return {
        "signing": base64.b64encode(bytes(keys._signing)).decode(),
        "box": base64.b64encode(bytes(keys._box_private)).decode(),
        "public": keys.public().to_dict(),
        "fingerprint": keys.public().fingerprint,
    }


# ---------------------------------------------------------------------------
# Hosting services
# ---------------------------------------------------------------------------
@mcp.tool(
    description="Host a blind encrypted board on the overlay and return its .onion "
    "address. The host stores only ciphertext; it cannot read messages."
)
def anet_serve_board() -> dict:
    backend = BoardHost()
    agent = S.agent(label="board-host")
    addr = agent.serve(backend.handle)
    S.services[addr.address] = {"agent": agent, "kind": "board", "backend": backend}
    return {"address": addr.address, "kind": "board"}


@mcp.tool(
    description="Host a discovery directory on the overlay and return its .onion "
    "address. Agents announce signed descriptors to it and query for services."
)
def anet_serve_directory() -> dict:
    backend = Directory()
    agent = S.agent(label="directory")
    addr = agent.serve(backend.handle)
    S.services[addr.address] = {"agent": agent, "kind": "directory", "backend": backend}
    return {"address": addr.address, "kind": "directory"}


@mcp.tool(description="Stop a service this server hosts, by its .onion address.")
def anet_stop_service(address: str) -> dict:
    svc = S.services.pop(address, None)
    if not svc:
        return {"ok": False, "error": "no such hosted service"}
    svc["agent"].stop()
    return {"ok": True, "stopped": address}


# ---------------------------------------------------------------------------
# Directory client
# ---------------------------------------------------------------------------
@mcp.tool(
    description="Query a directory (by its .onion address) for live services, "
    "optionally filtered by service name or tag. Returns verified descriptors."
)
def anet_directory_query(
    directory_address: str, service: str = "", tag: str = ""
) -> dict:
    agent = S.agent()
    results = dir_query(
        _send_to(agent, directory_address),
        service=service or None,
        tag=tag or None,
    )
    return {"descriptors": [r["descriptor"] for r in results]}


@mcp.tool(
    description="Announce a service to a directory. Provide the directory's .onion "
    "address, the .onion address you are announcing, your keypair (signing and box "
    "base64, as from anet_generate_keys), and optional services/tags."
)
def anet_directory_announce(
    directory_address: str,
    my_address: str,
    signing_b64: str,
    box_b64: str,
    services: list | None = None,
    tags: list | None = None,
) -> dict:
    from nacl import public, signing as nsign

    keys = AgentKeys(
        nsign.SigningKey(base64.b64decode(signing_b64)),
        public.PrivateKey(base64.b64decode(box_b64)),
    )
    agent = S.agent()
    svc = [{"name": s} if isinstance(s, str) else s for s in (services or [])]
    return dir_announce(
        _send_to(agent, directory_address), keys, my_address,
        services=svc, tags=(tags or []),
    )


# ---------------------------------------------------------------------------
# Board client
# ---------------------------------------------------------------------------
@mcp.tool(
    description="Post a message to an encrypted board hosted at host_address. "
    "group_key_b64 is the shared board key; author_signing_b64/author_box_b64 are "
    "your content keypair. The host never sees plaintext."
)
def anet_board_post(
    host_address: str,
    board_id: str,
    group_key_b64: str,
    author_signing_b64: str,
    author_box_b64: str,
    text: str,
) -> dict:
    from nacl import public, signing as nsign

    keys = AgentKeys(
        nsign.SigningKey(base64.b64decode(author_signing_b64)),
        public.PrivateKey(base64.b64decode(author_box_b64)),
    )
    group = GroupKey(base64.b64decode(group_key_b64))
    agent = S.agent()
    board = Board(_send_to(agent, host_address), board_id, keys, group)
    seq = board.post(text)
    return {"ok": True, "seq": seq}


@mcp.tool(
    description="Read and decrypt the history of an encrypted board. Needs the "
    "board key and a keypair to sign the read handshake."
)
def anet_board_read(
    host_address: str,
    board_id: str,
    group_key_b64: str,
    reader_signing_b64: str,
    reader_box_b64: str,
) -> dict:
    from nacl import public, signing as nsign

    keys = AgentKeys(
        nsign.SigningKey(base64.b64decode(reader_signing_b64)),
        public.PrivateKey(base64.b64decode(reader_box_b64)),
    )
    group = GroupKey(base64.b64decode(group_key_b64))
    agent = S.agent()
    board = Board(_send_to(agent, host_address), board_id, keys, group)
    msgs = board.history(from_start=True)
    return {
        "messages": [
            {
                "seq": m.seq,
                "author": m.author_fingerprint,
                "text": m.text,
                "verified": m.verified,
            }
            for m in msgs
        ]
    }


@mcp.tool(
    description="Generate a fresh symmetric group/board key (base64). Share it only "
    "with the members you want to admit."
)
def anet_generate_group_key() -> dict:
    return {"group_key": base64.b64encode(GroupKey.generate().material).decode()}


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
