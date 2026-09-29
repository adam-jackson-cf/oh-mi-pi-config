import { createHash } from "node:crypto";
import assert from "node:assert/strict";
import { chmod, mkdir, mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import type { Context, Model } from "@oh-my-pi/pi-ai";
import { isUsageLimit } from "@oh-my-pi/pi-ai/error";
import type { ExtensionAPI, ExtensionContext, ProviderConfig } from "@oh-my-pi/pi-coding-agent";
import jevWatchdog, { labelOutcome, streamJev } from "../agent/extensions/jev-watchdog";

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
    assert.deepEqual(rows[1].error, { stopReason: "error", reason: "invalid_response" });
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

test("non-filesystem writes and edits are not implementation steps", async () => {
  const original = globalThis.fetch;
  let requests = 0;
  // SAFETY: This test stub returns a Response and restores the original fetch afterward.
  globalThis.fetch = (async () => { requests++; return Response.json(decision(0)); }) as typeof fetch;
  try {
    const readOnly: Context = { ...context, messages: [context.messages[0]!,
      { role: "user", content: "**agent**:\n→ read(skill://coding-guidance) ⇒ ok\n```text\n→ edit(x) inside a result\n```\n→ write(agent://Peer) ⇒ ok\n→ write(proc://job/kill) ⇒ ok\n→ write(local://notes.md) ⇒ ok", timestamp: 1 },
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

test("approved plan comes from the session, is excluded from activity, and todo items carry", async () => {
  const root = await mkdtemp(join(tmpdir(), "jev-plan-"));
  const firstPlan = join(root, "first-plan.md");
  const secondPlan = join(root, "second-plan.md");
  await writeFile(firstPlan, "# Plan\nFix the typo only.");
  await writeFile(secondPlan, `# Plan\n${"step\n".repeat(2_000)}`);
  const original = globalThis.fetch;
  const plans: { path: string | null; source: string; excerpt: string; clipped: boolean; todo_items: string }[] = [];
  const excerpts: string[] = [];
  // SAFETY: This test stub returns a Response and restores the original fetch afterward.
  globalThis.fetch = (async (_url, options) => {
    const state = JSON.parse(String(options!.body)).state;
    plans.push(state.approved_plan);
    excerpts.push(state.agent_activity.excerpt);
    return Response.json(decision(0));
  }) as typeof fetch;
  const update = (agent: string): Context => ({ ...context, messages: [
    { role: "user", content: "**user**:\nFix only the typo.", timestamp: 0 },
    { role: "user", content: `**agent**:\n${agent}`, timestamp: 1 },
  ] });
  const run = (ctx: Context, planPath?: string) => streamJev(model, ctx, { apiKey: "test-placeholder", sessionId: "advisor-plan" },
    undefined, undefined, () => ({ cwd: root, plan: planPath ? { path: planPath, origin: "plan_mode" } : undefined })).result();
  try {
    await run(update(`→ write(${firstPlan}) ⇒ ok · 2 lines\n→ todo(init) ⇒ ok\nRemaining items (1):\n  - Fix typo [in_progress]\n→ edit(src/a.ts) ⇒ ok`), firstPlan);
    await run(update("→ edit(src/a.ts) ⇒ ok · 1 line"), firstPlan);
    await run(update("→ edit(src/b.ts) ⇒ ok"), secondPlan);
    await run(update("→ read(agent/agents/plan-judge.md) ⇒ ok\n→ edit(src/c.ts) ⇒ ok"));
    assert.deepEqual(plans.map(p => [p.path, p.source, p.clipped]), [
      [firstPlan, "current", false],
      [firstPlan, "current", false],
      [secondPlan, "current", true],
      [null, "carried_forward", false],
    ]);
    assert.doesNotMatch(excerpts[0]!, /first-plan/);
    assert.equal(plans[1]!.excerpt, "# Plan\nFix the typo only.");
    assert.match(plans[1]!.todo_items, /Fix typo \[in_progress\]/);
    assert.ok(plans[2]!.excerpt.length < 4_100 && plans[2]!.excerpt.includes("[middle of plan omitted]"));
  } finally {
    globalThis.fetch = original;
    await rm(root, { recursive: true, force: true });
  }
});

test("registered watchdog binds to the main session and reads the plan-mode path from its branch", async () => {
  const root = await mkdtemp(join(tmpdir(), "jev-bind-"));
  const previousKey = process.env.OPENROUTER_API_KEY;
  process.env.OPENROUTER_API_KEY = "test-placeholder";
  const original = globalThis.fetch;
  const states: { approved_plan: { path: string | null } }[] = [];
  // SAFETY: This test stub returns a Response and restores the original fetch afterward.
  globalThis.fetch = (async (_url, options) => {
    states.push(JSON.parse(String(options!.body)).state);
    return Response.json(decision(0));
  }) as typeof fetch;
  type StartHandler = (event: Record<string, never>, ctx: ExtensionContext) => void;
  let onStart: StartHandler | undefined;
  let stream: ProviderConfig["streamSimple"];
  const fakePi = {
    on: (name: string, handler: StartHandler) => { if (name === "session_start") onStart = handler; },
    registerProvider: (_name: string, config: ProviderConfig) => { stream = config.streamSimple; },
    registerCommand: () => {},
    sendMessage: () => {},
  };
  type BranchEntry = { type: string; mode?: string; data?: { planFilePath?: string }; customType?: string; content?: string };
  // SAFETY: Only the fields the watchdog reads are provided by this fixture.
  const fakeCtx = (kind: "main" | "sub", dir: string, branch: BranchEntry[]) => ({
    agent: { kind, id: kind },
    sessionManager: {
      getSessionFile: () => join(root, `${dir}.jsonl`), getSessionId: () => dir, getCwd: () => root,
      getArtifactsDir: () => join(root, dir), getBranch: () => branch,
    },
  }) as ExtensionContext;
  try {
    // SAFETY: The fixture implements the ExtensionAPI methods jevWatchdog calls.
    await jevWatchdog(fakePi as ExtensionAPI);
    const planned = [
      { type: "mode_change", mode: "plan", data: { planFilePath: "local://old.md" } },
      { type: "mode_change", mode: "plan", data: { planFilePath: "local://PLAN.md" } },
      { type: "mode_change", mode: "default" },
    ];
    onStart!({}, fakeCtx("main", "main", planned));
    onStart!({}, fakeCtx("sub", "sub", []));
    const ctx: Context = { ...context, messages: [
      { role: "user", content: "**user**:\nFix the typo.", timestamp: 0 },
      { role: "user", content: "**agent**:\n→ read(agent/agents/plan-judge.md) ⇒ ok\n→ edit(src/a.ts) ⇒ ok", timestamp: 1 },
    ] };
    // SAFETY: streamSimple is assigned by registerProvider above; the fixture model matches its provider.
    const result = await stream!(model, ctx, { apiKey: "test-placeholder" }).result();
    assert.equal(result.stopReason, "stop");
    assert.equal(states[0]!.approved_plan.path, "local://PLAN.md");
    const audit = join(root, "main", "jev-watchdog-requests.jsonl");
    assert.equal((await readFile(audit, "utf8")).trimEnd().split("\n").length, 2);
    await assert.rejects(stat(join(root, "sub", "jev-watchdog-requests.jsonl")));

    // Print-mode (plan-yolo) approval records no mode_change; its handoff message names the plan.
    onStart!({}, fakeCtx("main", "main2", [...planned,
      { type: "custom_message", customType: "plan-yolo-handoff", content: "Plan approved: **mul**.\n\nRead `local://mul-plan.md`; full tool access restored." },
    ]));
    await stream!(model, ctx, { apiKey: "test-placeholder" }).result();
    assert.equal(states[1]!.approved_plan.path, "local://mul-plan.md");
  } finally {
    globalThis.fetch = original;
    if (previousKey === undefined) delete process.env.OPENROUTER_API_KEY;
    else process.env.OPENROUTER_API_KEY = previousKey;
    await rm(root, { recursive: true, force: true });
  }
});

test("the request governing the earliest judged activity stays in the task context", async () => {
  const original = globalThis.fetch;
  const seen: { recent_user_requests: string[]; omitted_earlier_requests: boolean }[] = [];
  // SAFETY: This test stub returns a Response and restores the original fetch afterward.
  globalThis.fetch = (async (_url, options) => {
    const { recent_user_requests, omitted_earlier_requests } = JSON.parse(String(options!.body)).state.task_context;
    seen.push({ recent_user_requests, omitted_earlier_requests });
    return Response.json(decision(0));
  }) as typeof fetch;
  const user = (text: string, timestamp: number): Context["messages"][number] =>
    ({ role: "user", content: `**user**:\n${text}`, timestamp });
  const agent = (text: string, timestamp: number): Context["messages"][number] =>
    ({ role: "user", content: `**agent**:\n${text}`, timestamp });
  try {
    // A single update chunk run: agent activity follows A and B; earliest judged activity sits under A.
    const spanning: Context = { ...context, messages: [
      user("Request A", 0), agent("→ edit(src/a.ts) ⇒ ok", 1), user("Request B", 2), agent("→ edit(src/b.ts) ⇒ ok", 3),
    ] };
    await streamJev(model, spanning, { apiKey: "test-placeholder" }).result();
    const many: Context = { ...context, messages: [
      user("R0", 0), user("R1", 1), agent("→ edit(src/a.ts) ⇒ ok", 2),
      user("R2", 3), user("R3", 4), user("R4", 5), user("R5", 6), agent("→ edit(src/b.ts) ⇒ ok", 7),
    ] };
    await streamJev(model, many, { apiKey: "test-placeholder" }).result();
    assert.deepEqual(seen, [
      { recent_user_requests: ["Request A", "Request B"], omitted_earlier_requests: false },
      { recent_user_requests: ["R1", "R3", "R4", "R5"], omitted_earlier_requests: true },
    ]);
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

type SeenState = {
  task_context: { recent_user_requests: string[] };
  agent_activity: { excerpt: string };
  approved_plan: { path: string | null; origin: string | null; source: string; excerpt: string };
};
type ToolPart = { type: "toolCall"; name: string; arguments: { path: string } };
type Entry = { type: string; mode?: string; data?: { planFilePath?: string }; customType?: string;
  content?: string; message?: { role: string; content: string | ToolPart[] } };
const said = (text: string): Entry => ({ type: "message", message: { role: "user", content: text } });
const wrote = (path: string, name = "write"): Entry =>
  ({ type: "message", message: { role: "assistant", content: [{ type: "toolCall", name, arguments: { path } }] } });

/** Drives the registered watchdog with a fake main session whose branch is `branch`. */
async function reviewWith(root: string, branch: () => Entry[], messages: Context["messages"], sessionDir = "main", advisorId?: string) {
  const seen: SeenState[] = [];
  const original = globalThis.fetch;
  const previousKey = process.env.OPENROUTER_API_KEY;
  process.env.OPENROUTER_API_KEY = "test-placeholder";
  // SAFETY: This test stub returns a Response and restores the original fetch afterward.
  globalThis.fetch = (async (_url, options) => {
    seen.push(JSON.parse(String(options!.body)).state);
    return Response.json(decision(0));
  }) as typeof fetch;
  type StartHandler = (event: Record<string, never>, ctx: ExtensionContext) => void;
  let onStart: StartHandler | undefined;
  let stream: ProviderConfig["streamSimple"];
  const fakePi = {
    on: (name: string, handler: StartHandler) => { if (name === "session_start") onStart = handler; },
    registerProvider: (_name: string, config: ProviderConfig) => { stream = config.streamSimple; },
    registerCommand: () => {},
    sendMessage: () => {},
  };
  try {
    // SAFETY: The fixture implements the ExtensionAPI methods jevWatchdog calls.
    await jevWatchdog(fakePi as ExtensionAPI);
    // SAFETY: Only the fields the watchdog reads are provided by this fixture.
    onStart!({}, {
      agent: { kind: "main", id: "main" },
      sessionManager: {
        getSessionFile: () => join(root, `${sessionDir}.jsonl`), getSessionId: () => sessionDir, getCwd: () => root,
        getArtifactsDir: () => join(root, sessionDir), getBranch: branch,
      },
    } as ExtensionContext);
    // SAFETY: streamSimple is assigned by registerProvider above; the fixture model matches its provider.
    const result = await stream!(model, { ...context, messages }, { apiKey: "test-placeholder", sessionId: advisorId }).result();
    const auditFile = join(root, sessionDir, "jev-watchdog-requests.jsonl");
    const rows: { type: string; reason?: string; error?: { reason: string }; stage?: string }[] =
      (await readFile(auditFile, "utf8").catch(() => "")).split("\n").filter(Boolean).map(line => JSON.parse(line));
    return { seen, rows, result };
  } finally {
    globalThis.fetch = original;
    if (previousKey === undefined) delete process.env.OPENROUTER_API_KEY;
    else process.env.OPENROUTER_API_KEY = previousKey;
  }
}
const impl = (activity: string): Context["messages"] => [
  { role: "user", content: "**user**:\nDo the work.", timestamp: 0 },
  { role: "user", content: `**agent**:\n${activity}`, timestamp: 1 },
];

test("fresh-context plan approval names the inlined plan and inherits the request being planned", async () => {
  const root = await mkdtemp(join(tmpdir(), "jev-approval-"));
  try {
    // Planning session: the user's request is seen, but nothing is implemented yet.
    await reviewWith(root, () => [], [
      { role: "user", content: "### Session update\n\n**user**:\nPlan adding subtract to math.ts.", timestamp: 0 },
      { role: "user", content: "**agent**:\n→ read(math.ts) ⇒ ok", timestamp: 1 },
    ], "planning", "advisor-planning");
    // "Approve and execute" opens a new session holding only the synthetic approval message.
    const approved: Entry = { type: "message", message: { role: "developer", content:
      'Plan approved.\n\n<instruction>\nx\n</instruction>\n\n<plan path="local://add-subtract-plan.md">\n# Plan\n</plan>' } };
    const { seen } = await reviewWith(root, () => [approved], [
      { role: "user", content: "### Session update\n\n**developer**:\nPlan approved.\n\n<plan path=\"local://add-subtract-plan.md\">", timestamp: 0 },
      { role: "user", content: "**agent**:\n→ edit(src/a.ts) ⇒ ok", timestamp: 1 },
    ], "executing", "advisor-executing");
    assert.deepEqual([seen[0]!.approved_plan.path, seen[0]!.approved_plan.origin],
      ["local://add-subtract-plan.md", "plan_approval"]);
    assert.deepEqual(seen[0]!.task_context.recent_user_requests, ["Plan adding subtract to math.ts."]);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("plan inference: a document the request names or the agent creates for it, latest source wins", async () => {
  const root = await mkdtemp(join(tmpdir(), "jev-infer-"));
  try {
    await mkdir(join(root, "docs"));
    await mkdir(join(root, "notes"));
    await writeFile(join(root, "docs", "plan.md"), "# Named plan");
    await writeFile(join(root, "notes", "PLAN.md"), "# Created plan");
    const infer = async (branch: Entry[]) =>
      (await reviewWith(root, () => branch, impl("→ edit(src/a.ts) ⇒ ok"))).seen[0]!.approved_plan;

    const named = await infer([said("Please implement docs/plan.md carefully.")]);
    assert.deepEqual([named.path, named.origin, named.excerpt], ["docs/plan.md", "user_named", "# Named plan"]);

    const created = await infer([said("Write a plan to notes/PLAN.md, then implement it."), wrote("notes/PLAN.md")]);
    assert.deepEqual([created.path, created.origin, created.excerpt], ["notes/PLAN.md", "agent_created", "# Created plan"]);

    // No destination named: the plan-named markdown file the agent wrote for the request.
    const unnamed = await infer([said("Draft a plan for the change, then do it."), wrote("docs/change-plan.md")]);
    assert.deepEqual([unnamed.path, unnamed.origin], ["docs/change-plan.md", "agent_created"]);

    // Latest wins: an interactive plan-mode approval after the request supersedes the named document.
    const superseded = await infer([said("Implement docs/plan.md"),
      { type: "mode_change", mode: "plan", data: { planFilePath: "local://later-plan.md" } }]);
    assert.deepEqual([superseded.path, superseded.origin], ["local://later-plan.md", "plan_mode"]);
    const newer = await infer([{ type: "mode_change", mode: "plan", data: { planFilePath: "local://old-plan.md" } },
      said("Implement docs/plan.md")]);
    assert.deepEqual([newer.path, newer.origin], ["docs/plan.md", "user_named"]);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("plan inference never promotes reads, agent definitions, unrelated writes, outside paths or non-user text", async () => {
  const root = await mkdtemp(join(tmpdir(), "jev-noplan-"));
  try {
    const infer = async (branch: Entry[]) =>
      (await reviewWith(root, () => branch, impl("→ edit(src/a.ts) ⇒ ok"))).seen[0]!.approved_plan;
    const none = { path: null, origin: null };
    const pick = (plan: SeenState["approved_plan"]) => ({ path: plan.path, origin: plan.origin });
    // Read-only plan-judge.md, even while the user asked for a plan elsewhere.
    assert.deepEqual(pick(await infer([said("Review the agents, then fix it."), wrote("agent/agents/plan-judge.md", "read")])), none);
    // A plan was requested but the agent wrote research notes and a report instead.
    assert.deepEqual(pick(await infer([said("Write a plan to notes/PLAN.md then implement"), wrote("notes/research.md"), wrote("report.md")])), none);
    // Writes with no plan request in the turn do not create a plan, whatever they are named.
    assert.deepEqual(pick(await infer([said("Add the feature"), wrote("docs/plan.md")])), none);
    // A named path outside the workspace is not trusted; hidden system messages are not user requests.
    assert.deepEqual(pick(await infer([said("Implement ../../etc/plan.md")])), none);
    const synthetic: Entry = { type: "message", message: { role: "user", content: "Implement docs/plan.md" } };
    Object.assign(synthetic.message!, { attribution: "agent" });
    assert.deepEqual(pick(await infer([synthetic])), none);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("plan lookup faults never fail the review: content-less handoff entries and a throwing branch", async () => {
  const root = await mkdtemp(join(tmpdir(), "jev-lookup-"));
  try {
    const empty = await reviewWith(root, () => [{ type: "custom_message", customType: "plan-yolo-handoff" }], impl("→ edit(src/a.ts) ⇒ ok"));
    assert.equal(empty.result.stopReason, "stop");
    assert.equal(empty.seen[0]!.approved_plan.path, null);
    const broken = await reviewWith(root, () => { throw new Error("branch unavailable"); }, impl("→ edit(src/a.ts) ⇒ ok"), "broken");
    assert.equal(broken.result.stopReason, "stop");
    assert.equal(broken.seen.length, 1);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("failures before a request is logged still leave an audit record with a non-secret reason", async () => {
  const root = await mkdtemp(join(tmpdir(), "jev-unaudited-"));
  const sessionFile = join(root, "primary.jsonl");
  const original = globalThis.fetch;
  // SAFETY: This test stub returns a Response and restores the original fetch afterward.
  globalThis.fetch = (async () => Response.json(decision(0))) as typeof fetch;
  const audited = () => readFile(join(root, "primary", "jev-watchdog-requests.jsonl"), "utf8")
    .then(text => text.split("\n").filter(Boolean).map(line => JSON.parse(line)));
  const session = () => ({ sessionId: "primary", sessionFile });
  try {
    const noPolicy = await streamJev(model, { ...context, systemPrompt: ["routing only"].slice(0, 0) },
      { apiKey: "fixture-key", sessionId: "adv" }, session).result();
    assert.equal(noPolicy.stopReason, "error");
    const noKey = await streamJev(model, context, {}, session).result();
    assert.equal(noKey.stopReason, "error");
    const noUpdate = await streamJev(model, { ...context, messages: [{ role: "user", content: "**user**:\nx", timestamp: 0 },
      { role: "assistant", api: "jev-decisions", provider: "p", model: "m", content: [], stopReason: "stop", timestamp: 1,
        usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } } }] },
      { apiKey: "fixture-key" }, session).result();
    assert.equal(noUpdate.stopReason, "error");
    const throwing = await streamJev(model, context, { apiKey: "fixture-key" }, session, undefined,
      () => { throw new Error("secret-sk-abcdefghijkl"); }).result();
    assert.equal(throwing.stopReason, "error");
    const rows = await audited();
    assert.deepEqual(rows.map(row => [row.type, row.reason]), [
      ["failure", "policy_slot"], ["failure", "missing_key"], ["failure", "no_update"], ["failure", "state_build"],
    ]);
    assert.ok(rows.every(row => /^jev_[0-9a-f-]{36}$/.test(row.requestId) && row.stage));
    assert.doesNotMatch(JSON.stringify(rows), /secret-sk/);
  } finally {
    globalThis.fetch = original;
    await rm(root, { recursive: true, force: true });
  }
});

test("http and timeout failures after the request record a reason on the error outcome", async () => {
  const root = await mkdtemp(join(tmpdir(), "jev-reason-"));
  const original = globalThis.fetch;
  try {
    // SAFETY: This test stub returns a Response and restores the original fetch afterward.
    globalThis.fetch = (async () => new Response("provider body sk-leakleakleak", { status: 503 })) as typeof fetch;
    await streamJev(model, context, { apiKey: "fixture-key" }, () => ({ sessionId: "primary", sessionFile: join(root, "primary.jsonl") })).result();
    // SAFETY: This test stub throws the abort-timeout error fetch raises; restored below.
    globalThis.fetch = (async () => { throw new DOMException("timed out", "TimeoutError"); }) as typeof fetch;
    await streamJev(model, context, { apiKey: "fixture-key" }, () => ({ sessionId: "primary", sessionFile: join(root, "primary.jsonl") })).result();
    const text = await readFile(join(root, "primary", "jev-watchdog-requests.jsonl"), "utf8");
    const outcomes = text.split("\n").filter(Boolean).map(line => JSON.parse(line)).filter(row => row.type === "outcome");
    assert.deepEqual(outcomes.map(row => [row.error.reason, row.error.httpStatus]), [["http_error", 503], ["timeout", undefined]]);
    assert.doesNotMatch(text, /leakleak/);
  } finally {
    globalThis.fetch = original;
    await rm(root, { recursive: true, force: true });
  }
});

test("user-run commands and system notices are not user requests; file-changing commands are implementation", async () => {
  const root = await mkdtemp(join(tmpdir(), "jev-labels-"));
  try {
    const messages: Context["messages"] = [
      { role: "user", content: "### Session update\n\n**user**:\nRename the helper.", timestamp: 0 },
      { role: "user", content: "→ user-bash! ls -la ⇒ ok · 3 lines", timestamp: 1 },
      { role: "user", content: "[async-result] build finished", timestamp: 2 },
      { role: "user", content: "[irc] worker → me: also add caching", timestamp: 3 },
      { role: "user", content: "**agent**:\n→ bash(sed -i s/a/b/ src/a.ts) ⇒ ok · 0 lines\n→ bash(ls src 2>&1 > /dev/null) ⇒ ok · 0 lines\n" +
        "→ write(xd://ast_edit) ⇒ ok · 2 lines\n→ read(src/a.ts) ⇒ ok · 1 line\n→ write(agent://x) ⇒ ok\n→ bash(echo hi > out.txt) ⇒ ok · 0 lines", timestamp: 4 },
    ];
    const { seen } = await reviewWith(root, () => [], messages);
    assert.deepEqual(seen[0]!.task_context.recent_user_requests, ["Rename the helper."]);
    const excerpt = seen[0]!.agent_activity.excerpt;
    assert.match(excerpt, /sed -i/);
    assert.match(excerpt, /xd:\/\/ast_edit/);
    assert.match(excerpt, /echo hi > out\.txt/);
    assert.doesNotMatch(excerpt, /dev\/null|read\(|agent:\/\//);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("a subagent-instance provider still audits and alerts through the main session binding", async () => {
  const root = await mkdtemp(join(tmpdir(), "jev-shared-"));
  const alerts: string[] = [];
  const original = globalThis.fetch;
  const previousKey = process.env.OPENROUTER_API_KEY;
  process.env.OPENROUTER_API_KEY = "test-placeholder";
  // SAFETY: This test stub returns a Response and restores the original fetch afterward.
  globalThis.fetch = (async () => Response.json(decision(0))) as typeof fetch;
  type StartHandler = (event: Record<string, never>, ctx: ExtensionContext) => void;
  const instance = async (label: string) => {
    let onStart: StartHandler | undefined;
    let stream: ProviderConfig["streamSimple"];
    const fakePi = {
      on: (name: string, handler: StartHandler) => { if (name === "session_start") onStart = handler; },
      registerProvider: (_name: string, config: ProviderConfig) => { stream = config.streamSimple; },
      registerCommand: () => {},
      sendMessage: () => { alerts.push(label); },
    };
    // SAFETY: The fixture implements the ExtensionAPI methods jevWatchdog calls.
    await jevWatchdog(fakePi as ExtensionAPI);
    return { start: onStart!, stream: stream! };
  };
  try {
    const main = await instance("main");
    const sub = await instance("sub"); // Registered last, so its provider serves the advisor.
    // SAFETY: Only the fields the watchdog reads are provided by this fixture.
    main.start({}, { agent: { kind: "main", id: "main" }, sessionManager: {
      getSessionFile: () => join(root, "main.jsonl"), getSessionId: () => "main", getCwd: () => root,
      getArtifactsDir: () => join(root, "main"), getBranch: () => [] } } as ExtensionContext);
    // SAFETY: Only the fields the watchdog reads are provided by this fixture.
    sub.start({}, { agent: { kind: "sub", id: "sub" }, sessionManager: {} } as ExtensionContext);
    assert.equal((await sub.stream(model, context, { apiKey: "test-placeholder" }).result()).stopReason, "stop");
    assert.equal((await readFile(join(root, "main", "jev-watchdog-requests.jsonl"), "utf8")).trimEnd().split("\n").length, 2);
    await rm(join(root, "main"), { recursive: true });
    await writeFile(join(root, "main"), "not a directory"); // Force an audit write failure.
    await sub.stream(model, context, { apiKey: "test-placeholder" }).result();
    assert.deepEqual(alerts, ["main"]);
  } finally {
    globalThis.fetch = original;
    if (previousKey === undefined) delete process.env.OPENROUTER_API_KEY;
    else process.env.OPENROUTER_API_KEY = previousKey;
    await rm(root, { recursive: true, force: true });
  }
});
