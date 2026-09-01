// @orca-managed-pi-extension
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent"

const defaultExtensions = [
  "analyse",
  "agent-autocomplete",
  "extension-health",
  "lsp-evidence-policy",
  "skill-auto-whitelist",
] as const
const conditionalExtensions = [
  "orca-agent-status",
  "orca-prefill",
  "orca-titlebar-spinner",
] as const
const knownExtensions = {
  "analyse": true,
  "agent-autocomplete": true,
  "extension-health": true,
  "skill-auto-whitelist": true,
  "lsp-evidence-policy": true,
  "orca-agent-status": true,
  "orca-prefill": true,
  "orca-titlebar-spinner": true,
} as const

export default function extensionHealth(pi: ExtensionAPI): void {
  pi.registerCommand("extension-health", {
    description: "Report local extension registration health",
    handler: async (_args, ctx) => {
      const commandNames = new Set(
        pi.getCommands()
          .filter((command) => command.source === "extension")
          .map((command) => command.name),
      )
      const markerPrefix = "extension-health-"
      const markerNames = [...commandNames]
        .filter((name) => name.startsWith(markerPrefix))
        .map((name) => name.slice(markerPrefix.length))
      const registered = new Set(
        markerNames.filter((name) => Object.hasOwn(knownExtensions, name)),
      )
      const unexpected = [
        ...new Set(markerNames.filter((name) => !Object.hasOwn(knownExtensions, name))),
      ].sort()

      if (commandNames.has("extension-health")) {
        registered.add("extension-health")
      }
      for (const name of commandNames) {
        if (name.startsWith("agent:")) {
          registered.add("agent-autocomplete")
          break
        }
      }


      const registeredNames = [...registered].sort()
      const missing = defaultExtensions.filter((name) => !registered.has(name))
      const conditional = conditionalExtensions.filter((name) => registered.has(name))
      const count = `${defaultExtensions.length - missing.length}/${defaultExtensions.length}`
      const registeredList = registeredNames.join(", ") || "<none>"
      const conditionalList = conditional.join(", ") || "<none>"

      if (missing.length === 0 && unexpected.length === 0) {
        ctx.ui.notify(
          `Extension health: ${count} default extensions registered; conditional registrations: ${conditionalList}; registered: ${registeredList}`,
          "info",
        )
        return
      }

      ctx.ui.notify(
        `Extension health mismatch: ${count} default extensions registered; registered: ${registeredList}; missing defaults: ${missing.join(", ") || "<none>"}; conditional registrations: ${conditionalList}; unexpected: ${unexpected.join(", ") || "<none>"}`,
        "error",
      )
    },
  })
}
