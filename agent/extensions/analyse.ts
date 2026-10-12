// @orca-managed-pi-extension
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

const ANALYSE_SYSTEM_PROMPT = `<analyse-mode>
This is a read only mode that reports findings based on the user request but takes no actions other than to report.
You MAY inspect available evidence with read-only tools. You MUST NOT modify files, execute commands, delegate work, interact with external systems, or perform any other side effect. Return only the requested findings.
</analyse-mode>`;

const READ_ONLY_TOOLS = new Set(["read", "grep", "glob"]);

export function isAnalyseToolAllowed(toolName: string): boolean {
  return READ_ONLY_TOOLS.has(toolName);
}

export default function analyse(pi: ExtensionAPI): void {
  let pendingRequest: string | undefined;
  let active = false;

  pi.registerCommand("extension-health-analyse", {
    description: "Verify analyse extension registration",
    handler: async (_args, ctx) => {
      ctx.ui.notify("Extension registered: analyse", "info");
    },
  });

  pi.registerCommand("analyse", {
    description: "Report findings for a request without taking actions",
    handler: async (args, ctx) => {
      const request = args.trim();
      if (!request) {
        ctx.ui.notify("Usage: /analyse <request>", "warning");
        return;
      }

      await ctx.waitForIdle();
      pendingRequest = request;
      pi.sendUserMessage(request);
    },
  });

  pi.on("before_agent_start", async (event) => {
    if (event.prompt !== pendingRequest) return;

    pendingRequest = undefined;
    active = true;
    return {
      systemPrompt: [...event.systemPrompt, ANALYSE_SYSTEM_PROMPT],
    };
  });

  pi.on("tool_call", async (event) => {
    if (!active || isAnalyseToolAllowed(event.toolName)) return;

    return {
      block: true,
      reason: "Blocked by /analyse: this mode permits read-only inspection and reporting only",
    };
  });

  pi.on("agent_end", async () => {
    active = false;
  });
}
