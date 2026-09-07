import { tool } from "@opencode-ai/plugin"

/**
 * torfetch — WebFetch, but over Tor.
 *
 * Drop this file into `.opencode/tools/` (project) or
 * `~/.config/opencode/tools/` (global). The filename is the tool name, so the
 * model can call `torfetch` directly. It shells out to the `torfetch` command
 * from the toragents package, which talks to a warm local daemon and boots Tor on
 * first use. Because OpenCode exposes this as a harness tool, the user's
 * intent ("read this over Tor") calls Tor directly — the weights do not gate
 * it.
 *
 * Requires: `pip install -e /path/to/toragents` so `torfetch` is on PATH, and
 * `brew install tor`.
 */
export default tool({
  description:
    "Fetch a web page over the Tor network with the request origin hidden. " +
    "Works for clearnet URLs and .onion hidden services. Returns readable " +
    "text by default; set raw=true for the unprocessed body. Use this when " +
    "the user wants to read or fetch something anonymously / over Tor / on a " +
    ".onion address.",
  args: {
    url: tool.schema
      .string()
      .describe("The URL to fetch (https://… or a v3 .onion address)"),
    raw: tool.schema
      .boolean()
      .optional()
      .describe("Return the raw body instead of extracted readable text"),
    links: tool.schema
      .boolean()
      .optional()
      .describe("Also include the page's outbound links"),
  },
  async execute(args) {
    const flags: string[] = ["--json"]
    if (args.raw) flags.push("--raw")
    if (args.links) flags.push("--links")
    const res = await Bun.$`torfetch ${args.url} ${flags}`.quiet()
    if (res.exitCode !== 0) {
      return `torfetch failed (exit ${res.exitCode}): ${res.stderr.toString().trim()}`
    }
    const data = JSON.parse(res.stdout.toString())
    if (args.raw) return data.body
    const head = `${data.status} ${data.content_type ?? ""} :: ${data.title ?? ""}`.trim()
    let out = `${head}\n\n${data.text ?? ""}`
    if (args.links && data.links?.length) {
      out += `\n\n--- links ---\n${data.links.join("\n")}`
    }
    return out
  },
})
