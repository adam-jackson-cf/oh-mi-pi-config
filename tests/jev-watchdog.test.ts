import assert from "node:assert/strict";
import { test } from "node:test";
import type { Context, Model } from "@oh-my-pi/pi-ai";
import { isUsageLimit } from "@oh-my-pi/pi-ai/error";
import { streamJev } from "../agent/extensions/jev-watchdog";

const model: Model = {
  id: "~typesafe/jev-latest", name: "Jev fixture", provider: "jev-watchdog", api: "jev-decisions",
  baseUrl: "https://example.invalid", reasoning: false, input: ["text"], contextWindow: 32000, maxTokens: 2000,
  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
};
const context: Context = {
  messages: [{ role: "user", content: "User: Fix only the typo. Agent: I will add an unrelated database.", timestamp: 0 }],
  tools: [{ name: "advise", description: "Advise primary", parameters: { type: "object" } }],
};
function decision(drift = 1) {
  const choice = (selected: string, probabilities: Record<string, number>) => ({ type: "choice", choice: selected, probabilities, confidence: 1 });
  return {
    id: "fixture", model: "typesafe/jev-fixture", provider: "fixture",
    answers: {
      drift: choice("yes", { yes: drift, no: 1 - drift, unknown: 0 }),
    }, usage: { input_tokens: 100, output_tokens: 20, cost: 0.001 },
  };
}

test("credit exhaustion is a native usage-limit error, not a continue decision", async () => {
  const original = globalThis.fetch;
  // SAFETY: The adapter only calls fetch; this stub returns a real Response and is restored below.
  globalThis.fetch = (async () => new Response("private upstream body must not be surfaced", { status: 402 })) as typeof fetch;
  try {
    const message = await streamJev(model, context, { apiKey: "test-placeholder" }).result();
    assert.equal(message.stopReason, "error");
    assert.equal(message.errorStatus, 402);
    assert.equal(isUsageLimit(new Error(message.errorMessage)), true);
    assert.match(message.errorMessage!, /OpenRouter/);
    assert.equal(message.content.length, 0);
    assert.equal(message.errorMessage!.includes("private upstream"), false);
  } finally { globalThis.fetch = original; }
});

test("overreach alone triggers a blocker without evidence selection", async () => {
  const original = globalThis.fetch;
  try {
    for (const [score, expected] of [
      [0.9, "blocker"],
      [0.89, "continue"],
    ] as const) {
      // SAFETY: The adapter only calls fetch; this stub returns a real Response and is restored below.
      globalThis.fetch = (async () => Response.json(decision(score))) as typeof fetch;
      const message = await streamJev(model, context, { apiKey: "test-placeholder" }).result();
      const report = message.content.find(part => part.type === "text");
      assert.equal(report!.text, expected);
      const advice = message.content.find(part => part.type === "toolCall");
      assert.equal(advice?.arguments.severity, expected === "blocker" ? "blocker" : undefined);
      assert.equal(message.stopReason, expected === "blocker" ? "toolUse" : "stop");
    }
  } finally { globalThis.fetch = original; }
});

test("split updates retain all current evidence and advise completion makes no request", async () => {
  const original = globalThis.fetch;
  let requests = 0;
  // SAFETY: The adapter only calls fetch; this stub returns a real Response and is restored below.
  globalThis.fetch = (async (_url, options) => {
    requests++;
    const state = JSON.parse(String(options!.body)).state;
    assert.match(state.current_update, /Fix only the typo/);
    assert.match(state.current_update, /unrelated database/);
    return Response.json(decision());
  }) as typeof fetch;
  try {
    const split: Context = { ...context, messages: [
      { role: "user", content: "User: Fix only the typo.", timestamp: 0 },
      { role: "user", content: "Agent: I will add an unrelated database.", timestamp: 1 },
    ] };
    const result = await streamJev(model, split, { apiKey: "test-placeholder" }).result();
    assert.equal(result.stopReason, "toolUse");
    const finished = await streamJev(model, { ...split, messages: [...split.messages, result,
      { role: "toolResult", toolCallId: "fixture", toolName: "advise", content: [{ type: "text", text: "Recorded." }], isError: false, timestamp: 2 },
    ] }, { apiKey: "test-placeholder" }).result();
    assert.equal(finished.stopReason, "stop");
    assert.equal(requests, 1);
    assert.equal(finished.usage.cost.total, 0);
  } finally { globalThis.fetch = original; }
});
