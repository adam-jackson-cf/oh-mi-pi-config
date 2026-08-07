type InputEvent = {
  source: "interactive" | "rpc" | "extension"
  text: string
  images?: unknown[]
}

type InputContext = {
  hasUI: boolean
  ui?: { notify(message: string, level: "warning"): void }
}

type InputResult =
  | { action: "continue" }
  | { action: "transform"; text: string; images?: unknown[] }

type ExtensionAPI = {
  on(
    event: "input",
    handler: (event: InputEvent, ctx: InputContext) => Promise<InputResult> | InputResult,
  ): void
}

const AGENT_TOKEN = /^\s*\$([a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?)(?:\s+([\s\S]*))?$/
const AGENT_ALIASES: Readonly<Record<string, string>> = {
  orchestrate: "orchestrator",
}

function parseAgentInvocation(text: string): { agent: string; task: string } | undefined {
  if (text.startsWith("\\$")) return undefined

  const match = text.match(AGENT_TOKEN)
  if (!match) return undefined

  const requestedAgent = match[1]
  const task = match[2]?.trim()
  if (!task) return { agent: requestedAgent, task: "" }

  return {
    agent: AGENT_ALIASES[requestedAgent] ?? requestedAgent,
    task,
  }
}

function buildDelegationPrompt(agent: string, task: string): string {
  return [
    `The user explicitly selected the task agent \`${agent}\` through the dollar-agent extension.`,
    `Delegate the request below with the task tool using agent \`${agent}\`. Do not perform the delegated work yourself.`,
    "",
    "<delegated-request>",
    task,
    "</delegated-request>",
  ].join("\n")
}

export default function dollarAgentExtension(pi: ExtensionAPI): void {
  pi.on("input", async (event, ctx) => {
    if (event.source === "extension") return { action: "continue" }

    const invocation = parseAgentInvocation(event.text)
    if (!invocation) return { action: "continue" }

    if (!invocation.task) {
      if (ctx.hasUI) {
        ctx.ui?.notify(`Add a request after $${invocation.agent}.`, "warning")
      }
      return { action: "continue" }
    }

    return {
      action: "transform",
      text: buildDelegationPrompt(invocation.agent, invocation.task),
      images: event.images,
    }
  })
}
