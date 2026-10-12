import assert from "node:assert/strict";
import { describe, test } from "node:test";
import type { ExtensionAPI, ExtensionContext } from "@oh-my-pi/pi-coding-agent";
import {
  emailHash,
  parseThreshold,
  registerQuotaGate,
  selectSevenDayQuotaSample,
  type UsagePayload,
  type UsageReader,
} from "../agent/extensions/quota-gate";

const OWNER = "Owner@Example.test";
const OWNER_HASH = emailHash("owner@example.test");
const RESETS_AT = 1_800_000_000_000;

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

type Report = UsagePayload["reports"][number];

function sevenDay(id: string, usedFraction: number, tier?: string): Report["limits"][number] {
  return {
    id,
    scope: tier === undefined ? { windowId: "7d" } : { windowId: "7d", tier },
    amount: { usedFraction },
    window: { resetsAt: RESETS_AT },
  };
}

function codexReport(email: string | undefined, usedFraction: number): Report {
  return {
    provider: "openai-codex",
    metadata: email === undefined ? {} : { email },
    limits: [
      { id: "openai-codex:primary", scope: { windowId: "5h" }, amount: { usedFraction: 0.99 }, window: { resetsAt: RESETS_AT } },
      sevenDay("openai-codex:secondary", usedFraction),
      sevenDay("openai-codex:spark:secondary", 0.99, "spark"),
    ],
  };
}

function claudeReport(email: string, usedFraction: number): Report {
  return {
    provider: "anthropic",
    metadata: { email },
    limits: [sevenDay("anthropic:7d", usedFraction), sevenDay("anthropic:7d:fable", 0.99, "fable")],
  };
}

/** The owner's Codex and Claude accounts plus another Codex account that is nearly exhausted. */
function usagePayload(codex: number, claude = 0.1): UsagePayload {
  return {
    reports: [codexReport("team@example.test", 0.99), codexReport(OWNER, codex), claudeReport(OWNER, claude)],
  };
}

function createGateHarness(options: GateHarnessOptions) {
  const notifications: Notification[] = [];
  const sentUserMessages: string[] = [];
  const commands = new Map<string, Command>();
  let aborts = 0;
  let interval: (() => void | Promise<void>) | undefined;
  let timersStarted = 0;
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
      timersStarted += 1;
      return timersStarted;
    },
    clearTimer() {
      interval = undefined;
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
  registerQuotaGate(Object.assign({} as ExtensionAPI, candidate), options.usageReader, OWNER_HASH);
  assert.ok(sessionStart);
  assert.ok(toolCall);

  return {
    aborts: () => aborts,
    context,
    notifications,
    sentUserMessages,
    polling: () => interval !== undefined,
    timersStarted: () => timersStarted,
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

describe("Quota gate", () => {
  test("accepts fraction and percentage thresholds", () => {
    assert.equal(parseThreshold("0.85"), 0.85);
    assert.equal(parseThreshold("85"), 0.85);
    assert.equal(parseThreshold("1"), 1);
    assert.equal(parseThreshold("0"), undefined);
    assert.equal(parseThreshold("101"), undefined);
  });

  test("reads each provider's overall seven-day quota from the owner's account only", () => {
    const payload = usagePayload(0.24, 0.43);
    assert.deepEqual(selectSevenDayQuotaSample(payload, "openai-codex", OWNER_HASH), { usedFraction: 0.24, resetsAt: RESETS_AT });
    assert.deepEqual(selectSevenDayQuotaSample(payload, "anthropic", OWNER_HASH), { usedFraction: 0.43, resetsAt: RESETS_AT });

    const withoutOwner = { reports: [codexReport("team@example.test", 0.5), codexReport(undefined, 0.5)] };
    assert.equal(selectSevenDayQuotaSample(withoutOwner, "openai-codex", OWNER_HASH), undefined);

    const twoOwnerReports = { reports: [codexReport(OWNER, 0.2), codexReport(OWNER, 0.3)] };
    assert.equal(selectSevenDayQuotaSample(twoOwnerReports, "openai-codex", OWNER_HASH), undefined);
  });

  test("a limit without a window, such as Claude extra usage, does not hide the seven-day quota", async () => {
    const payload = usagePayload(0.1, 0.9);
    payload.reports[2]?.limits.push({ id: "anthropic:extra", scope: { windowId: "extra" }, amount: { usedFraction: 0.2 } });
    const harness = createGateHarness({ usageReader: async () => payload });

    await harness.start();
    await harness.runCommand("on");

    assert.equal(harness.toolResult()?.block, true);
    assert.ok(!harness.notifications.some(notification => notification.message.includes("unavailable or ambiguous")));
  });

  test("a new session leaves the gate off and never reads usage until requested", async () => {
    let reads = 0;
    const harness = createGateHarness({
      usageReader: async () => {
        reads += 1;
        return usagePayload(0.99);
      },
    });

    await harness.start();

    assert.equal(reads, 0);
    assert.equal(harness.polling(), false);
    assert.equal(harness.toolResult(), undefined);
    assert.deepEqual(harness.notifications, []);
  });

  test("latches at the default threshold, aborts the parent, and blocks subsequent tools", async () => {
    const harness = createGateHarness({ usageReader: async () => usagePayload(0.85) });

    await harness.start();
    await harness.runCommand("on");

    assert.equal(harness.aborts(), 1);
    assert.deepEqual(harness.toolResult(), {
      block: true,
      reason: "Quota gate is paused. Use /quota-gate continue to allow more work, or /quota-gate off to disable it.",
    });
    assert.ok(harness.notifications.some(notification => notification.message.startsWith("Codex seven-day quota reached 85%")));
  });

  test("the Claude quota trips the gate on its own", async () => {
    const harness = createGateHarness({ usageReader: async () => usagePayload(0.1, 0.9) });

    await harness.start();
    await harness.runCommand("on");

    assert.equal(harness.aborts(), 1);
    assert.equal(harness.toolResult()?.block, true);
    assert.ok(harness.notifications.some(notification => notification.message.startsWith("Claude seven-day quota reached 90%")));
  });

  test("confirmed continuation resumes an interrupted parent turn", async () => {
    const harness = createGateHarness({ usageReader: async () => usagePayload(0.85), confirm: true, idle: false });

    await harness.start();
    await harness.runCommand("on");

    assert.equal(harness.aborts(), 1);
    assert.equal(harness.toolResult(), undefined);
    assert.deepEqual(harness.sentUserMessages, [
      "[quota gate] The user approved continuing despite the Codex seven-day quota threshold. Resume the interrupted work.",
    ]);
  });

  test("on starts one poll timer, continue reopens, and off stops polling", async () => {
    const harness = createGateHarness({ usageReader: async () => usagePayload(0.8) });

    await harness.start();
    await harness.runCommand("on 80");
    assert.equal(harness.toolResult()?.block, true);
    assert.equal(harness.polling(), true);

    await harness.runCommand("continue");
    assert.equal(harness.toolResult(), undefined);

    await harness.runCommand("on 90");
    assert.equal(harness.timersStarted(), 1);

    await harness.runCommand("off");
    assert.equal(harness.polling(), false);
    assert.ok(harness.notifications.some(notification => notification.message === "Quota gate disabled."));
  });

  test("a new session resets a gate enabled in the previous one", async () => {
    const harness = createGateHarness({ usageReader: async () => usagePayload(0.1) });

    await harness.start();
    await harness.runCommand("on");
    await harness.start();
    await harness.runCommand("status");

    assert.equal(harness.notifications.at(-1)?.message, "Quota gate is disabled.");
  });

  test("a provider without the owner's account is reported once while the other still gates", async () => {
    let claude = 0.1;
    const harness = createGateHarness({
      usageReader: async () => ({ reports: [codexReport("team@example.test", 0.99), claudeReport(OWNER, claude)] }),
    });

    await harness.start();
    await harness.runCommand("on");
    await harness.tick();
    const missing = harness.notifications.filter(notification => notification.message.startsWith("Codex quota usage is unavailable"));
    assert.equal(missing.length, 1);
    assert.equal(harness.toolResult(), undefined);

    claude = 0.95;
    await harness.tick();
    assert.equal(harness.toolResult()?.block, true);
  });

  test("leaves work open when quota usage is unavailable", async () => {
    const harness = createGateHarness({
      usageReader: async () => {
        throw new Error("usage unavailable");
      },
    });

    await harness.start();
    await harness.runCommand("on");
    await harness.tick();

    assert.equal(harness.aborts(), 0);
    assert.equal(harness.toolResult(), undefined);
    assert.equal(
      harness.notifications.filter(notification => notification.message.includes("unavailable or ambiguous")).length,
      1,
    );
  });
});
