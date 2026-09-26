import { createHash } from "node:crypto";
import assert from "node:assert/strict";
import { chmod, mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
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
  const failures: string[] = [];
  // SAFETY: The adapter only calls fetch; this stub returns a real Response and is restored below.
  globalThis.fetch = (async () => new Response("private upstream body must not be surfaced", { status: 402 })) as typeof fetch;
  try {
    const message = await streamJev(model, context, { apiKey: "test-placeholder" },
      undefined, (failure, succeeded) => failures.push(`${failure}:${succeeded}`)).result();
    assert.equal(message.stopReason, "error");
    assert.equal(message.errorStatus, 402);
    assert.equal(isUsageLimit(new Error(message.errorMessage)), true);
    assert.match(message.errorMessage!, /OpenRouter/);
    assert.equal(message.content.length, 0);
    assert.equal(message.errorMessage!.includes("private upstream"), false);
    assert.deepEqual(failures, ["review:false"]);
  } finally { globalThis.fetch = original; }
});

test("malformed decisions record a safe failed attempt without relaying provider content", async () => {
  const root = await mkdtemp(join(tmpdir(), "jev-audit-"));
  const auditFile = join(root, "primary", "jev-watchdog-requests.jsonl");
  const original = globalThis.fetch;
  const failures: string[] = [];
  // SAFETY: The adapter only calls fetch; this stub returns a real Response and is restored below.
  globalThis.fetch = (async () => Response.json({ private: "untrusted provider data" })) as typeof fetch;
  try {
    const message = await streamJev(model, context, { apiKey: "test-placeholder" },
      () => ({ sessionId: "primary", sessionFile: join(root, "primary.jsonl") }),
      (failure, succeeded) => failures.push(`${failure}:${succeeded}`)).result();
    assert.equal(message.stopReason, "error");
    assert.deepEqual(failures, ["audit:true", "audit:true", "review:false"]);
    assert.match(message.errorMessage!, /invalid decision response/);
    assert.equal(message.errorMessage!.includes("untrusted provider data"), false);
    const rows = (await readFile(auditFile, "utf8")).trimEnd().split("\n").map(line => JSON.parse(line));
    assert.equal(rows.length, 2);
    assert.equal(rows[1].requestId, rows[0].requestId);
    assert.equal(rows[1].traceResponseId, message.responseId);
    assert.deepEqual(rows[1].error, { stopReason: "error" });
    assert.equal((await readFile(auditFile, "utf8")).includes("untrusted provider data"), false);
  } finally {
    globalThis.fetch = original;
    await rm(root, { recursive: true, force: true });
  }
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

test("audit joins transmitted request and decision to the native advisor trace", async () => {
  const root = await mkdtemp(join(tmpdir(), "jev-audit-"));
  const sessionFile = join(root, "primary.jsonl");
  const auditFile = join(root, "primary", "jev-watchdog-requests.jsonl");
  const original = globalThis.fetch;
  const transmitted: string[] = [];
  // SAFETY: The adapter only calls fetch; this stub returns a real Response and is restored below.
  globalThis.fetch = (async (_url, options) => {
    transmitted.push(String(options!.body));
    return Response.json(decision(transmitted.length === 1 ? 0.89 : 0.9));
  }) as typeof fetch;
  try {
    const withPrompt: Context = { ...context,
      systemPrompt: ["Review only. password=fixture-value"],
      messages: [{ role: "user", content: "Check this Bearer fixture-token123456. OPENROUTER_API_KEY=fixture-second", timestamp: 0 }],
    };
    const provenance = () => ({ sessionId: "primary-session", sessionFile });
    const responses = [];
    for (let i = 0; i < 2; i++) {
      responses.push(await streamJev(model, withPrompt, { apiKey: "fixture-key", sessionId: "advisor-provider-id" }, provenance).result());
      if (i === 0) await chmod(auditFile, 0o644);
    }
    const rows = (await readFile(auditFile, "utf8")).trimEnd().split("\n").map(line => JSON.parse(line));
    assert.equal(rows.length, 4);
    for (let i = 0; i < 2; i++) {
      const request = rows[2 * i];
      const outcome = rows[2 * i + 1];
      assert.equal(request.type, "request");
      assert.equal(outcome.type, "outcome");
      assert.equal(request.sessionId, "primary-session");
      assert.equal(request.sessionFile, sessionFile);
      assert.equal(request.advisorSessionId, "advisor-provider-id");
      assert.equal(request.requestSha256, createHash("sha256").update(transmitted[i]).digest("hex"));
      assert.equal(request.request.model, "~typesafe/jev-latest");
      assert.deepEqual(request.request.questions, JSON.parse(transmitted[i]).questions);
      assert.equal(request.request.state.project_context[0], "Review only. password=[REDACTED]");
      assert.equal(request.request.state.current_update, "Check this Bearer [REDACTED]. OPENROUTER_API_KEY=[REDACTED]");
      assert.equal(outcome.requestId, request.requestId);
      assert.equal(outcome.traceResponseId, responses[i].responseId);
      assert.equal(outcome.traceResponseId, request.requestId);
      assert.equal(outcome.providerResponseId, "fixture");
      assert.deepEqual(outcome.decision, {
        choice: "yes", probabilities: { yes: i ? 0.9 : 0.89, no: 1 - (i ? 0.9 : 0.89), unknown: 0 },
        confidence: 1, threshold: 0.9, verdict: i ? "blocker" : "continue",
      });
      assert.equal(responses[i].stopReason, i ? "toolUse" : "stop");
    }
    assert.notEqual(rows[0].requestId, rows[2].requestId);
    assert.equal((await stat(auditFile)).mode & 0o777, 0o600);
    assert.equal((await readFile(auditFile, "utf8")).includes("fixture-value"), false);
    assert.equal((await readFile(auditFile, "utf8")).includes("fixture-second"), false);
  } finally {
    globalThis.fetch = original;
    await rm(root, { recursive: true, force: true });
  }
});

test("audit write failure alerts the agent without suppressing Jev review", async () => {
  const root = await mkdtemp(join(tmpdir(), "jev-audit-"));
  const original = globalThis.fetch;
  let requests = 0;
  const failures: string[] = [];
  // SAFETY: The adapter only calls fetch; this stub returns a real Response and is restored below.
  globalThis.fetch = (async () => { requests++; return Response.json(decision()); }) as typeof fetch;
  try {
    const file = join(root, "blocked");
    await writeFile(file, "");
    const result = await streamJev(model, context, { apiKey: "fixture-key" },
      () => ({ sessionId: "primary-session", sessionFile: `${file}.jsonl` }),
      (failure, succeeded) => failures.push(`${failure}:${succeeded}`)).result();
    assert.equal(result.stopReason, "toolUse");
    assert.equal(requests, 1);
    assert.deepEqual(failures, ["audit:false", "review:true"]);
  } finally {
    globalThis.fetch = original;
    await rm(root, { recursive: true, force: true });
  }
});
