import { tool } from "@opencode-ai/plugin"

/**
 * tordial — call another agent on the anet overlay by its .onion address.
 *
 * Drop into `.opencode/tools/`. Sends one JSON request to an agent's hidden
 * service over Tor and returns the reply. Pairs with torfetch: torfetch reads
 * the open web anonymously, tordial talks to other agents anonymously.
 */
export default tool({
  description:
    "Send one JSON request to another agent on the anet overlay at its " +
    ".onion address and return the reply, over Tor, with no traceable origin. " +
    "Use when the user wants to talk to a specific agent/service by its onion " +
    "address.",
  args: {
    address: tool.schema.string().describe("The agent's v3 .onion address"),
    request: tool.schema
      .string()
      .describe('The request as a JSON object string, e.g. {"op":"ping"}'),
  },
  async execute(args) {
    const res = await Bun.$`torfetch ${args.address} --dial ${args.request}`.quiet()
    if (res.exitCode !== 0) {
      return `tordial failed (exit ${res.exitCode}): ${res.stderr.toString().trim()}`
    }
    return res.stdout.toString().trim()
  },
})
