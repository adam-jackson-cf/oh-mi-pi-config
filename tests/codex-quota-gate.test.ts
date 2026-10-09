import assert from "node:assert/strict";
import { describe, test } from "node:test";
import type { ExtensionAPI, ExtensionContext } from "@oh-my-pi/pi-coding-agent";
import {
  parseThreshold,
  registerCodexQuotaGate,
  selectSevenDayQuotaSample,
  type UsageReader,
} from "../agent/extensions/codex-quota-gate";

type Notification = {
  message: string;
  level: "info" | "warning" | "error";
};

type Command = {
  handler(args: string, ctx: ExtensionContext): Promise<void>;
};

type SessionStartHandler = (event: { type: "session_start" }, ctx: ExtensionContext) => void | Promise<void>;
type ToolCallHandler = () => { block: true; reason: string } | undefined;

type GateHarnessOptions = {
  usageReader: UsageReader;
  confirm?: boolean;
  idle?: boolean;
};

function usagePayload(usedFraction: number) {
  return {
    reports: [
      {
        provider: "openai-codex",
        limits: [
          {
            id: "openai-codex:primary",
            scope: { windowId: "5h" },
            amount: { usedFraction: 0.99 },
            window: { resetsAt: 1_800_000_000_000 },
          },
          {
            id: "openai-codex:secondary",
            scope: { windowId: "7d" },
            amount: { usedFraction },
            window: { resetsAt: 1_800_000_000_000 },
          },
          {
            id: "openai-codex:spark:secondary",
            scope: { windowId: "7d", tier: "spark" },
            amount: { usedFraction: 0.99 },
            window: { resetsAt: 1_800_000_000_000 },
          },
        ],
      },
    ],
  };
}

function createGateHarness(options: GateHarnessOptions) {
  const notifications: Notification[] = [];
  const sentUserMessages: string[] = [];
  const commands = new Map<string, Command>();
  let aborts = 0;
  let interval: (() => void | Promise<void>) | undefined;
  let sessionStart: SessionStartHandler | undefined;
  let toolCall: ToolCallHandler | undefined;
  // SAFETY: tests provide only the context members the extension reads.
  const context = Object.assign({} as ExtensionContext, {
    cwd: "/project",
    hasUI: true,
    ui: {
      notify(message: string, level: Notification["level"]) {
        notifications.push({ message, level });
      },
      async confirm() {
        return options.confirm ?? false;
      },
    },
    isIdle() {
      return options.idle ?? true;
    },
    abort() {
      aborts += 1;
    },
    setInterval(callback: () => void | Promise<void>) {
      interval = callback;
    },
  });
  const candidate = {
    on(event: string, handler: SessionStartHandler | ToolCallHandler) {
      if (event === "session_start") {
        // SAFETY: the event discriminator pairs session_start with SessionStartHandler.
        sessionStart = handler as SessionStartHandler;
      }
      if (event === "tool_call") {
        // SAFETY: the event discriminator pairs tool_call with ToolCallHandler.
        toolCall = handler as ToolCallHandler;
      }
    },
    registerCommand(name: string, command: Command) {
      commands.set(name, command);
    },
    sendUserMessage(message: string) {
      sentUserMessages.push(message);
    },
  };

  // SAFETY: only `on`, `registerCommand` and `sendUserMessage` are exercised by the extension.
  registerCodexQuotaGate(Object.assign({} as ExtensionAPI, candidate), options.usageReader);
  assert.ok(sessionStart);
  assert.ok(toolCall);

  return {
    aborts: () => aborts,
    context,
    notifications,
    sentUserMessages,
    async start() {
      assert.ok(sessionStart);
      await sessionStart({ type: "session_start" }, context);
    },
    async tick() {
      assert.ok(interval);
      await interval();
    },
    async runCommand(args: string) {
      const command = commands.get("quota-gate");
      assert.ok(command);
      await command.handler(args, context);
    },
    toolResult() {
      assert.ok(toolCall);
      return toolCall();
    },
  };
}

describe("Codex quota gate", () => {
  test("accepts fraction and percentage thresholds", () => {
    assert.equal(parseThreshold("0.85"), 0.85);
    assert.equal(parseThreshold("85"), 0.85);
    assert.equal(parseThreshold("1"), 1);
    assert.equal(parseThreshold("0"), undefined);
    assert.equal(parseThreshold("101"), undefined);
  });

  test("requires exactly one normal seven-day Codex usage report", () => {
    const payload = usagePayload(0.85);
    payload.reports.push({
      provider: "openai-codex",
      limits: [
        {
          id: "openai-codex:primary",
          scope: { windowId: "7d" },
          amount: { usedFraction: 0.7 },
          window: { resetsAt: 1_800_000_001_000 },
        },
      ],
    });

    assert.equal(selectSevenDayQuotaSample(payload), undefined);
  });

  test("latches at the default threshold, aborts the parent, and blocks subsequent tools", async () => {
    const harness = createGateHarness({
      usageReader: async () => usagePayload(0.85),
    });

    await harness.start();

    assert.equal(harness.aborts(), 1);
    assert.deepEqual(harness.toolResult(), {
      block: true,
      reason:
        "Codex quota gate is paused. Use /quota-gate continue to allow more work, or /quota-gate off to disable it.",
    });
    assert.ok(harness.notifications.some(notification => notification.message.includes("Work is paused.")));
  });

  test("confirmed continuation resumes an interrupted parent turn", async () => {
    const harness = createGateHarness({
      usageReader: async () => usagePayload(0.85),
      confirm: true,
      idle: false,
    });

    await harness.start();

    assert.equal(harness.aborts(), 1);
    assert.equal(harness.toolResult(), undefined);
    assert.deepEqual(harness.sentUserMessages, [
      "[quota gate] The user approved continuing despite the Codex seven-day quota threshold. Resume the interrupted work.",
    ]);
  });

  test("manual commands enable, continue, and disable the current session gate", async () => {
    const harness = createGateHarness({ usageReader: async () => usagePayload(0.8) });

    await harness.start();
    assert.equal(harness.aborts(), 0);

    await harness.runCommand("on 80");
    assert.equal(harness.toolResult()?.block, true);

    await harness.runCommand("continue");
    assert.equal(harness.toolResult(), undefined);

    await harness.runCommand("off");
    assert.ok(harness.notifications.some(notification => notification.message === "Codex quota gate disabled."));
  });

  test("leaves work open when quota usage is unavailable", async () => {
    const harness = createGateHarness({
      usageReader: async () => {
        throw new Error("usage unavailable");
      },
    });

    await harness.start();
    await harness.tick();

    assert.equal(harness.aborts(), 0);
    assert.equal(harness.toolResult(), undefined);
    assert.equal(
      harness.notifications.filter(notification => notification.message.includes("unavailable or ambiguous")).length,
      1,
    );
  });
});
