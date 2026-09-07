# toragents tools for OpenCode

Give an OpenCode agent a `torfetch` (and `tordial`) tool so the user's
intent, "read this over Tor", "talk to this agent anonymously", calls Tor
directly. OpenCode exposes these as harness-level tools, so the capability
is defined by the environment, not gated by the model's weights.

## Install

```bash
# 1. Install toragents so `torfetch` is on PATH (and Tor is present)
brew install tor
pip install -e /path/to/toragents          # provides the `torfetch` command + daemon

# 2. Drop the tools into OpenCode (project-local or global)
mkdir -p .opencode/tools
cp torfetch.ts tordial.ts .opencode/tools/
#   or globally:  cp torfetch.ts tordial.ts ~/.config/opencode/tools/
```

The filename is the tool name, so the model sees `torfetch` and `tordial`.

## What they do

| Tool | Call | Result |
|---|---|---|
| `torfetch` | `{ url, raw?, links? }` | A web page (clearnet or `.onion`) fetched over Tor, origin hidden. Readable text by default; `raw` for the unprocessed body. |
| `tordial` | `{ address, request }` | One JSON request to another agent's `.onion`, over Tor, and its reply. |

Both shell out to the `torfetch` command, which talks to a warm local daemon
(`toragents-daemon`) that keeps one Tor client hot. The first call boots Tor
(~1 min); every call after is fast. The daemon binds loopback only; set
`TORAGENTS_DAEMON_TOKEN` to require a shared secret.

## How it fits together

```
OpenCode model
   │  calls tool "torfetch"
   ▼
torfetch.ts  ──shell──▶  torfetch (CLI)  ──HTTP loopback──▶  toragents-daemon
                                                               │ warm Tor
                                                               ▼
                                                          the open web / .onion
```

Any harness that can run a shell command can use the `torfetch` command the
same way; the OpenCode files just make it a first-class, named tool.
