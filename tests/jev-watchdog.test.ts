import { createHash } from "node:crypto";
import assert from "node:assert/strict";
import { chmod, mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import type { Context, Model } from "@oh-my-pi/pi-ai";
import { isUsageLimit } from "@oh-my-pi/pi-ai/error";
import { labelOutcome, streamJev } from "../agent/extensions/jev-watchdog";

const model: Model = {
  id: "~typesafe/jev-latest", name: "Jev fixture", provider: "jev-watchdog", api: "jev-decisions",
  baseUrl: "https://example.invalid", reasoning: false, input: ["text"], contextWindow: 32000, maxTokens: 2000,
  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
};
const context: Context = {
  systemPrompt: ["Native advisor routing instructions", "Review only concrete scope drift."],
  messages: [
    { role: "user", content: "### Session update\n\n**user**:\nFix only the typo.", timestamp: 0 },
    { role: "user", content: "**agent**:\n→ write(src/db.ts) ⇒ ok · 1 line\nAdded an unrelated database.", timestamp: 1 },
  ],
  tools: [{ name: "advise", description: "Advise primary", parameters: { type: "object" } }],
};
function decision(drift = 1) {
  const choice = (selected: string, probabilities: Record<string, number>) => ({ type: "choice", choice: selected, probabilities, confidence: 1 });
  return {
    id: "fixture", model: "typesafe/jev-1.13-20260917", provider: "fixture",
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

test("high Jev probability stays nonblocking during shadow evaluation", async () => {
  const original = globalThis.fetch;
  try {
    // SAFETY: This test stub returns a Response and restores the original fetch afterward.
    globalThis.fetch = (async () => Response.json(decision(0.9))) as typeof fetch;
    const message = await streamJev(model, context, { apiKey: "test-placeholder" }).result();
    assert.equal(message.content[0].type === "text" && message.content[0].text, "continue");
    assert.equal(message.content.some(part => part.type === "toolCall"), false);
    assert.equal(message.stopReason, "stop");
  } finally { globalThis.fetch = original; }
});

test("split rendered updates select the task and current agent evidence only", async () => {
  const original = globalThis.fetch;
  let requests = 0;
  // SAFETY: This test stub returns a Response and restores the original fetch afterward.
  globalThis.fetch = (async (_url, options) => {
    requests++;
    const state = JSON.parse(String(options!.body)).state;
    assert.equal(state.review_policy, "Review only concrete scope drift.");
    assert.deepEqual(state.task_context.recent_user_requests, ["Fix only the typo."]);
    assert.match(state.agent_activity.excerpt, /unrelated database/);
    assert.doesNotMatch(state.agent_activity.excerpt, /grep|skill:/);
    assert.match(state.agent_activity.excerpt, /→ edit\(src\/db.ts\)/);
    assert.equal(JSON.stringify(state).includes("Native advisor routing instructions"), false);
    return Response.json(decision());
  }) as typeof fetch;
  try {
    const split: Context = { ...context, messages: [
      ...context.messages,
      { role: "user", content: "// Checking guidance\n→ read(skill://coding-guidance) ⇒ ok\n→ grep(db) ⇒ ok\n→ edit(src/db.ts) ⇒ ok · 3 lines", timestamp: 2 },
    ] };
    const result = await streamJev(model, split, { apiKey: "test-placeholder" }).result();
    assert.equal(result.stopReason, "stop");
    assert.equal(requests, 1);
  } finally { globalThis.fetch = original; }
});

test("updates without implementation steps are not sent to Jev", async () => {
  const original = globalThis.fetch;
  let requests = 0;
  // SAFETY: This test stub returns a Response and restores the original fetch afterward.
  globalThis.fetch = (async () => { requests++; return Response.json(decision(0)); }) as typeof fetch;
  try {
    const readOnly: Context = { ...context, messages: [context.messages[0]!,
      { role: "user", content: "**agent**:\n→ read(skill://coding-guidance) ⇒ ok\n```text\n→ edit(x) inside a result\n```\n→ write(fix-plan.md) ⇒ ok", timestamp: 1 },
    ] };
    const result = await streamJev(model, readOnly, { apiKey: "test-placeholder" }).result();
    assert.equal(result.stopReason, "stop");
    assert.equal(requests, 0);
  } finally { globalThis.fetch = original; }
});

test("watched role embedded after native prefix still supplies the user objective", async () => {
  const original = globalThis.fetch;
  // SAFETY: This test stub returns a Response and restores the original fetch afterward.
  globalThis.fetch = (async (_url, options) => {
    const state = JSON.parse(String(options!.body)).state;
    assert.deepEqual(state.task_context.recent_user_requests, ["Fix only the typo."]);
    assert.equal(state.task_context.source, "current");
    return Response.json(decision());
  }) as typeof fetch;
  try {
    const prefixed: Context = { ...context, messages: [
      { role: "user", content: "### Session update\n\n[advisor metadata]\n**user**:\nFix only the typo.", timestamp: 0 },
      context.messages[1],
    ] };
    const result = await streamJev(model, prefixed, { apiKey: "test-placeholder" }).result();
    assert.equal(result.stopReason, "stop");
  } finally { globalThis.fetch = original; }
});

test("agent-only follow-up update reuses the latest objective until a new request replaces it", async () => {
  const original = globalThis.fetch;
  const seen: { source: string; requests: string[] }[] = [];
  // SAFETY: This test stub returns a Response and restores the original fetch afterward.
  globalThis.fetch = (async (_url, options) => {
    const { task_context } = JSON.parse(String(options!.body)).state;
    seen.push({ source: task_context.source, requests: task_context.recent_user_requests });
    return Response.json(decision(0));
  }) as typeof fetch;
  const agentOnly: Context = { ...context, messages: [
    // SAFETY: An assistant turn with text content is a valid native Context message; the adapter only skips it.
    { role: "assistant", content: [{ type: "text", text: "ack" }], timestamp: 2 } as Context["messages"][number],
    { role: "user", content: "**agent**:\n→ edit(demo.txt) ⇒ ok · 1 line", timestamp: 3 },
  ] };
  const run = (ctx: Context, sessionId: string) =>
    streamJev(model, ctx, { apiKey: "test-placeholder", sessionId }).result();
  try {
    await run(context, "advisor-a");
    await run(agentOnly, "advisor-a");
    await run(agentOnly, "advisor-b");
    await run({ ...context, messages: [
      { role: "user", content: "**user**:\nNow update the changelog.", timestamp: 4 }, agentOnly.messages[1],
    ] }, "advisor-a");
    await run(agentOnly, "advisor-a");
    assert.deepEqual(seen, [
      { source: "current", requests: ["Fix only the typo."] },
      { source: "carried_forward", requests: ["Fix only the typo."] },
      { source: "missing", requests: [] },
      { source: "current", requests: ["Now update the changelog."] },
      { source: "carried_forward", requests: ["Now update the changelog."] },
    ]);
  } finally { globalThis.fetch = original; }
});

test("approved plan file and todo items are sent, carried, replaced, and bounded", async () => {
  const root = await mkdtemp(join(tmpdir(), "jev-plan-"));
  await writeFile(join(root, "first-plan.md"), "# Plan\nFix the typo only.");
  await writeFile(join(root, "second-plan.md"), `# Plan\n${"step\n".repeat(2_000)}`);
  const original = globalThis.fetch;
  const plans: { path: string | null; source: string; excerpt: string; clipped: boolean; todo_items: string }[] = [];
  // SAFETY: This test stub returns a Response and restores the original fetch afterward.
  globalThis.fetch = (async (_url, options) => {
    plans.push(JSON.parse(String(options!.body)).state.approved_plan);
    return Response.json(decision(0));
  }) as typeof fetch;
  const update = (agent: string): Context => ({ ...context, messages: [
    { role: "user", content: "**user**:\nFix only the typo.", timestamp: 0 },
    { role: "user", content: `**agent**:\n${agent}`, timestamp: 1 },
  ] });
  const run = (ctx: Context) => streamJev(model, ctx, { apiKey: "test-placeholder", sessionId: "advisor-plan" },
    undefined, undefined, () => ({ cwd: root })).result();
  try {
    await run(update("→ write(first-plan.md) ⇒ ok · 2 lines\n→ todo(init) ⇒ ok\nRemaining items (1):\n  - Fix typo [in_progress]\n→ edit(src/a.ts) ⇒ ok"));
    await run(update("→ edit(src/a.ts) ⇒ ok · 1 line"));
    await run(update("→ edit(*** Begin Patch [second-plan.md#AB12] PUT 1.=1: +# Plan *** End Patch) ⇒ ok\n→ edit(src/b.ts) ⇒ ok"));
    assert.deepEqual(plans.map(p => [p.path, p.source, p.clipped]), [
      ["first-plan.md", "current", false],
      ["first-plan.md", "carried_forward", false],
      ["second-plan.md", "current", true],
    ]);
    assert.equal(plans[1]!.excerpt, "# Plan\nFix the typo only.");
    assert.match(plans[2]!.todo_items, /Fix typo \[in_progress\]/);
    assert.ok(plans[2]!.excerpt.length < 4_100 && plans[2]!.excerpt.includes("[middle of plan omitted]"));
  } finally {
    globalThis.fetch = original;
    await rm(root, { recursive: true, force: true });
  }
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
      systemPrompt: ["Native instructions should not be sent", "Review only. password=fixture-value"],
      messages: [
        { role: "user", content: "### Session update\n\n**user**:\nFix this typo. OPENROUTER_API_KEY=fixture-second", timestamp: 0 },
        { role: "user", content: "**developer**:\nPreserve the existing pattern. token=fixture-third", timestamp: 1 },
        { role: "user", content: "**agent**:\n→ write(src/db.ts) ⇒ ok\nCheck this Bearer fixture-token123456. Add a database.", timestamp: 1 },
      ],
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
      assert.equal(request.request.state.review_policy, "Review only. password=[REDACTED]");
      assert.deepEqual(request.request.state.task_context.recent_user_requests, ["Fix this typo. OPENROUTER_API_KEY=[REDACTED]"]);
      assert.deepEqual(request.request.state.constraints.recent_instructions, ["Preserve the existing pattern. token=[REDACTED]"]);
      assert.equal(request.request.state.agent_activity.excerpt, "→ write(src/db.ts) ⇒ ok\nCheck this Bearer [REDACTED]. Add a database.");
      assert.equal(outcome.requestId, request.requestId);
      assert.equal(outcome.traceResponseId, responses[i].responseId);
      assert.equal(outcome.traceResponseId, request.requestId);
      assert.equal(outcome.providerResponseId, "fixture");
      assert.equal(outcome.resolvedModel, "typesafe/jev-1.13-20260917");
      assert.deepEqual(outcome.decision, {
        choice: "yes", probabilities: { yes: i ? 0.9 : 0.89, no: 1 - (i ? 0.9 : 0.89), unknown: 0 },
        confidence: 1, threshold: 0.9, reviewCandidate: i === 1,
      });
      assert.equal(responses[i].stopReason, "stop");
    }
    assert.notEqual(rows[0].requestId, rows[2].requestId);
    assert.equal((await stat(auditFile)).mode & 0o777, 0o600);
    assert.equal((await readFile(auditFile, "utf8")).includes("fixture-value"), false);
    assert.equal((await readFile(auditFile, "utf8")).includes("fixture-second"), false);
    assert.equal((await readFile(auditFile, "utf8")).includes("fixture-third"), false);
    await labelOutcome({ sessionId: "primary-session", sessionFile }, rows[2].requestId, "no_overreach");
    const labeled = (await readFile(auditFile, "utf8")).trimEnd().split("\n").map(line => JSON.parse(line));
    assert.deepEqual(
      { type: labeled[4].type, requestId: labeled[4].requestId, label: labeled[4].label, reviewer: labeled[4].reviewer },
      { type: "reviewer_outcome", requestId: rows[2].requestId, label: "no_overreach", reviewer: "human" },
    );
    await assert.rejects(labelOutcome({ sessionId: "primary-session", sessionFile }, rows[2].requestId, "overreach"),
      /already has a human label/);
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
    assert.equal(result.stopReason, "stop");
    assert.equal(requests, 1);
    assert.deepEqual(failures, ["audit:false", "review:true"]);
  } finally {
    globalThis.fetch = original;
    await rm(root, { recursive: true, force: true });
  }
});

test("an alias move leaves the review unresolved instead of accepting another model", async () => {
  const root = await mkdtemp(join(tmpdir(), "jev-version-"));
  const original = globalThis.fetch;
  const failures: string[] = [];
  // SAFETY: This test stub returns a Response and restores the original fetch afterward.
  globalThis.fetch = (async () => Response.json({ ...decision(), model: "typesafe/jev-next" })) as typeof fetch;
  try {
    const sessionFile = join(root, "primary.jsonl");
    const result = await streamJev(model, context, { apiKey: "fixture-key" },
      () => ({ sessionId: "primary", sessionFile }),
      (kind, succeeded) => failures.push(`${kind}:${succeeded}`)).result();
    const rows = (await readFile(join(root, "primary", "jev-watchdog-requests.jsonl"), "utf8"))
      .trimEnd().split("\n").map(line => JSON.parse(line));
    assert.equal(result.stopReason, "error");
    assert.match(result.errorMessage!, /different model version/);
    assert.equal(rows[1].resolvedModel, "typesafe/jev-next");
    assert.equal(rows[1].decision, undefined);
    assert.deepEqual(failures, ["audit:true", "audit:true", "review:false"]);
  } finally {
    globalThis.fetch = original;
    await rm(root, { recursive: true, force: true });
  }
});

test("oversized task and activity mark missing evidence and never promote a candidate", async () => {
  const root = await mkdtemp(join(tmpdir(), "jev-bounds-"));
  const original = globalThis.fetch;
  // SAFETY: This test stub returns a Response and restores the original fetch afterward.
  globalThis.fetch = (async (_url, options) => {
    const state = JSON.parse(String(options!.body)).state;
    assert.equal(state.task_context.clipped_requests, true);
    assert.ok(state.task_context.recent_user_requests[0].length <= 3000);
    assert.ok(state.agent_activity.excerpt.length < 6100);
    assert.ok(state.agent_activity.omitted_characters > 0);
    return Response.json(decision());
  }) as typeof fetch;
  try {
    const sessionFile = join(root, "primary.jsonl");
    const longContext: Context = { ...context, messages: [
      { role: "user", content: `### Session update\n\n**user**:\n${"A".repeat(4000)}`, timestamp: 0 },
      { role: "user", content: `**agent**:\n→ write(src/big.ts) ⇒ ok\n${"B".repeat(9000)}`, timestamp: 1 },
    ] };
    const result = await streamJev(model, longContext, { apiKey: "fixture-key" },
      () => ({ sessionId: "primary", sessionFile })).result();
    const rows = (await readFile(join(root, "primary", "jev-watchdog-requests.jsonl"), "utf8"))
      .trimEnd().split("\n").map(line => JSON.parse(line));
    assert.equal(result.stopReason, "stop");
    assert.equal(rows[1].decision.reviewCandidate, false);
  } finally {
    globalThis.fetch = original;
    await rm(root, { recursive: true, force: true });
  }
});
