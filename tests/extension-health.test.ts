import assert from "node:assert/strict";
import { describe, test } from "node:test";
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import extensionHealth, {
  type ExtensionHealthAPI,
  type ExtensionHealthCommand,
  type ExtensionHealthContext,
} from "../agent/extensions/extension-health";

type Notification = { message: string; level: "info" | "warning" | "error" };
type Handler = (args: string, ctx: ExtensionHealthContext) => Promise<void>;

export function hostApiFitsExtensionHealth(pi: ExtensionAPI): ExtensionHealthAPI {
  return pi;
}

async function report(commands: ExtensionHealthCommand[]): Promise<Notification[]> {
  let handler: Handler | undefined;
  const pi: ExtensionHealthAPI = {
    registerCommand(name, options) {
      if (name === "extension-health") handler = options.handler;
    },
    getCommands: () => commands,
  };
  extensionHealth(pi);
  assert.ok(handler);
  const notifications: Notification[] = [];
  await handler("", { ui: { notify: (message, level) => notifications.push({ message, level }) } });
  return notifications;
}

const extension = (name: string): ExtensionHealthCommand => ({ name, source: "extension" });

const healthy = [
  extension("extension-health"),
  extension("extension-health-analyse"),
  extension("extension-health-agent-rails"),
  extension("agent:reviewer"),
];

describe("extension-health command", () => {
  test("all four defaults registered reports healthy at info level", async () => {
    assert.deepEqual(await report(healthy), [
      {
        message:
          "Extension health: 4/4 default extensions registered; conditional registrations: <none>; registered: agent-autocomplete, agent-rails, analyse, extension-health",
        level: "info",
      },
    ]);
  });

  test("conditional extensions are listed when their markers are present", async () => {
    const [notification] = await report([...healthy, extension("extension-health-orca-prefill")]);
    assert.equal(notification?.level, "info");
    assert.match(notification?.message ?? "", /conditional registrations: orca-prefill;/);
  });

  test("a missing default and an unknown marker report a mismatch at error level", async () => {
    const commands = [
      extension("extension-health"),
      extension("extension-health-analyse"),
      extension("extension-health-mystery"),
      extension("extension-health-mystery"),
    ];
    assert.deepEqual(await report(commands), [
      {
        message:
          "Extension health mismatch: 2/4 default extensions registered; registered: analyse, extension-health; missing defaults: agent-autocomplete, agent-rails; conditional registrations: <none>; unexpected: mystery",
        level: "error",
      },
    ]);
  });

  test("an unknown marker alone is a mismatch even when every default is registered", async () => {
    const [notification] = await report([...healthy, extension("extension-health-stray")]);
    assert.equal(notification?.level, "error");
    assert.match(notification?.message ?? "", /4\/4 default extensions registered; .*missing defaults: <none>; .*unexpected: stray$/);
  });

  test("commands from other sources do not count as extension registrations", async () => {
    const commands = healthy.map(command => ({ ...command, source: "prompt" }));
    const [notification] = await report(commands);
    assert.equal(notification?.level, "error");
    assert.match(notification?.message ?? "", /0\/4 default extensions registered; registered: <none>; missing defaults: analyse, agent-autocomplete, extension-health, agent-rails;/);
  });
});
