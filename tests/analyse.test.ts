import assert from "node:assert/strict";
import { describe, test } from "node:test";
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import analyse, {
  type AnalyseAPI,
  type AnalyseCommandContext,
  type AnalyseStartEvent,
  type AnalyseToolCallEvent,
} from "../agent/extensions/analyse";

type Notification = { message: string; level: "info" | "warning" | "error" };
type CommandHandler = (args: string, ctx: AnalyseCommandContext) => Promise<void>;
type StartHandler = (event: AnalyseStartEvent) => Promise<{ systemPrompt: string[] } | undefined>;
type ToolCallHandler = (event: AnalyseToolCallEvent) => Promise<{ block: true; reason: string } | undefined>;
type EndHandler = () => Promise<void>;

export function hostApiFitsAnalyse(pi: ExtensionAPI): AnalyseAPI {
  return pi;
}

function analyseSession() {
  const commands = new Map<string, CommandHandler>();
  const notifications: Notification[] = [];
  const sent: string[] = [];
  const order: string[] = [];
  const handlers = new Map<string, StartHandler | ToolCallHandler | EndHandler>();
  const pi: AnalyseAPI = {
    registerCommand(name, options) {
      commands.set(name, options.handler);
    },
    on(event: string, handler: StartHandler | ToolCallHandler | EndHandler) {
      handlers.set(event, handler);
    },
    sendUserMessage(content) {
      order.push("send");
      sent.push(content);
    },
  };
  analyse(pi);
  const ctx: AnalyseCommandContext = {
    ui: { notify: (message, level) => notifications.push({ message, level }) },
    async waitForIdle() {
      order.push("idle");
    },
  };
  const fire = async (event: string, payload: AnalyseStartEvent & AnalyseToolCallEvent) => {
    const handler = handlers.get(event);
    assert.ok(handler, `${event} has a handler`);
    return handler(payload);
  };
  return {
    notifications,
    sent,
    order,
    async command(name: string, args: string) {
      const handler = commands.get(name);
      assert.ok(handler, `/${name} is registered`);
      await handler(args, ctx);
    },
    async start(prompt: string) {
      const result = await fire("before_agent_start", { prompt, systemPrompt: ["base prompt"], toolName: "" });
      return result && "systemPrompt" in result ? result.systemPrompt : undefined;
    },
    async toolCall(toolName: string) {
      const result = await fire("tool_call", { prompt: "", systemPrompt: [], toolName });
      return result && "block" in result ? result : undefined;
    },
    async end() {
      await fire("agent_end", { prompt: "", systemPrompt: [], toolName: "" });
    },
  };
}

describe("analyse extension", () => {
  test("the registration marker reports that analyse is loaded", async () => {
    const session = analyseSession();
    await session.command("extension-health-analyse", "");
    assert.deepEqual(session.notifications, [{ message: "Extension registered: analyse", level: "info" }]);
  });

  test("an empty request shows usage and sends nothing", async () => {
    const session = analyseSession();
    await session.command("analyse", "   ");
    assert.deepEqual(session.notifications, [{ message: "Usage: /analyse <request>", level: "warning" }]);
    assert.deepEqual(session.sent, []);
  });

  test("a request waits for the agent to go idle, then sends the trimmed request", async () => {
    const session = analyseSession();
    await session.command("analyse", "  why does the build fail?  ");
    assert.deepEqual(session.order, ["idle", "send"]);
    assert.deepEqual(session.sent, ["why does the build fail?"]);
  });

  test("the analysed turn gets the read-only system prompt and only read tools", async () => {
    const session = analyseSession();
    await session.command("analyse", "audit the gate");
    const systemPrompt = await session.start("audit the gate");
    assert.ok(systemPrompt);
    assert.equal(systemPrompt[0], "base prompt");
    assert.match(systemPrompt[1] ?? "", /<analyse-mode>[\s\S]*MUST NOT modify files/);
    assert.equal(await session.toolCall("read"), undefined);
    assert.equal(await session.toolCall("grep"), undefined);
    assert.equal(await session.toolCall("glob"), undefined);
    const blocked = await session.toolCall("bash");
    assert.equal(blocked?.block, true);
    assert.match(blocked?.reason ?? "", /Blocked by \/analyse/);
    assert.equal((await session.toolCall("edit"))?.block, true);
  });

  test("the block ends with the analysed turn", async () => {
    const session = analyseSession();
    await session.command("analyse", "audit the gate");
    await session.start("audit the gate");
    await session.end();
    assert.equal(await session.toolCall("bash"), undefined);
  });

  test("an ordinary prompt keeps its system prompt and every tool", async () => {
    const session = analyseSession();
    await session.command("analyse", "audit the gate");
    assert.equal(await session.start("something else"), undefined);
    assert.equal(await session.toolCall("bash"), undefined);
    const fresh = analyseSession();
    assert.equal(await fresh.start("audit the gate"), undefined);
    assert.equal(await fresh.toolCall("write"), undefined);
  });

  test("a request is honoured once: a repeat of the same prompt is an ordinary turn", async () => {
    const session = analyseSession();
    await session.command("analyse", "audit the gate");
    await session.start("audit the gate");
    await session.end();
    assert.equal(await session.start("audit the gate"), undefined);
  });
});
