import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";
import { test } from "node:test";
import type { ExtensionAPI, ExtensionContext, ToolResultEvent } from "@oh-my-pi/pi-coding-agent";
import { createJevWatchdog, labelOutcome, POLICY_VERSION } from "../agent/extensions/jev-watchdog";
import type { JsonValue } from "../agent/extensions/lib/jev";
import type { ItemState } from "../agent/extensions/lib/proportionality";

// ---- Fixture: session entries as OMP's sessionManager.getBranch() returns them ----------------
type Args = Record<string, JsonValue>;
type Entry = { type: "message"; message: Record<string, JsonValue> };
type FixtureEvent = { type: string };
const user = (text: string): Entry => ({ type: "message", message: { role: "user", content: text } });
const call = (id: string, name: string, args: Args): Entry =>
  ({ type: "message", message: { role: "assistant", content: [{ type: "toolCall", id, name, arguments: args }] } });
const result = (id: string, text: string): Entry =>
  ({ type: "message", message: { role: "toolResult", toolCallId: id, content: [{ type: "text", text }], isError: false } });
const todoText = (items: [string, "in_progress" | "pending"][]) => items.map(([t, s]) => `  - ${t} [${s}] (Tasks)`).join("\n");
const write = (id: string, path: string, content: string): Entry[] => [call(id, "write", { path, content }), result(id, "ok")];

type AuditRow = { type: string; requestId: string; sessionKind?: string; agentId?: string; label?: string; reason?: string;
  item?: { text: string }; budget?: { estimatedTokens: number; changeShare: number };
  request?: { state: { policy_version: string; plan_item: { item: string }; approved_plan: { path: string; text: string } | null;
    implementation: { actions: { path: string }[] }; completeness: { complete: boolean } } };
  decision?: { stage: string; probabilities: { yes: number }; runtimeAction: string; reviewCandidate: boolean };
  error?: { reason: string; httpStatus?: number } };

type Session = {
  root: string;
  /** Emit tool_result for a todo call whose rendered result is `text`; `branch` holds everything before it. */
  todoDone: (branch: Entry[], args: Args, text: string, inBranch?: boolean) => Promise<void>;
  rows: () => Promise<AuditRow[]>;
  steers: string[];
  label: (args: string) => Promise<string[]>;
};

async function session(root: string, opts: { kind?: "main" | "sub"; key?: string | undefined; mode?: "off" | "shadow" | "enforce" } = {}): Promise<Session> {
  const policies = join(root, "policies.json");
  await writeFile(policies, JSON.stringify({ "jev-scope": opts.mode ?? "shadow" }));
  const previous = process.env.JEV_POLICIES_FILE;
  process.env.JEV_POLICIES_FILE = policies;
  const handlers = new Map<string, ((event: FixtureEvent, ctx: ExtensionContext) => void | Promise<void>)[]>();
  const commands = new Map<string, (args: string, ctx: ExtensionContext) => Promise<void>>();
  const steers: string[] = [];
  const notices: string[] = [];
  const fakePi = {
    on: (name: string, handler: (event: FixtureEvent, ctx: ExtensionContext) => void | Promise<void>) => { handlers.set(name, [...(handlers.get(name) ?? []), handler]); },
    registerCommand: (name: string, command: { handler: (args: string, ctx: ExtensionContext) => Promise<void> }) => { commands.set(name, command.handler); },
    sendMessage: (message: { content: string }, options: { deliverAs?: string }) => { if (options.deliverAs === "steer") steers.push(message.content); },
  };
  const watchdog = createJevWatchdog({ loadKey: async () => "key" in opts ? opts.key : "test-placeholder" });
  // SAFETY: The fixture implements the ExtensionAPI methods the watchdog calls.
  watchdog.extension(fakePi as ExtensionAPI);
  let branch: Entry[] = [];
  const sessionFile = join(root, "main.jsonl");
  // SAFETY: Only the fields the watchdog reads are provided by this fixture.
  const ctx = {
    agent: { kind: opts.kind ?? "main", id: opts.kind === "sub" ? "SubA" : "Main" },
    sessionManager: {
      getSessionFile: () => sessionFile, getSessionId: () => "main", getCwd: () => root,
      getArtifactsDir: () => join(root, "main"), getBranch: () => branch,
    },
    ui: { notify: (message: string) => { notices.push(message); } },
  } as ExtensionContext;
  try {
    for (const handler of handlers.get("session_start") ?? []) await handler({ type: "session_start" }, ctx);
  } finally {
    if (previous === undefined) delete process.env.JEV_POLICIES_FILE; else process.env.JEV_POLICIES_FILE = previous;
  }
  let n = 0;
  return {
    root, steers,
    todoDone: async (before, args, text, inBranch = false) => {
      const id = `todo-${++n}`;
      // OMP fires tool_result before recording the result; the assistant call is normally recorded already.
      branch = inBranch ? [...before, call(id, "todo", args), result(id, text)] : [...before, call(id, "todo", args)];
      // SAFETY: The fixture carries the ToolResultEvent fields the watchdog reads.
      const event = { type: "tool_result", toolName: "todo", toolCallId: id, input: args,
        content: [{ type: "text", text }], isError: false } as ToolResultEvent;
      for (const handler of handlers.get("tool_result") ?? []) await handler(event, ctx);
      await watchdog.idle();
    },
    rows: async () => (await readFile(join(root, "main", "jev-watchdog-requests.jsonl"), "utf8").catch(() => ""))
      .split("\n").filter(Boolean).map(line => JSON.parse(line)),
    label: async args => {
      notices.length = 0;
      await commands.get("jev-label")!(args, ctx);
      return [...notices];
    },
  };
}

/** Stub OpenRouter's decisions endpoint with P(beyond_item) = `p`, or an HTTP status. */
async function withJev<T>(answer: { p: number } | { status: number }, run: (bodies: { state: ItemState }[]) => Promise<T>): Promise<T> {
  const bodies: { state: ItemState }[] = [];
  const original = globalThis.fetch;
  // SAFETY: This test stub returns a Response and restores the original fetch afterward.
  globalThis.fetch = (async (_url: Parameters<typeof fetch>[0], options?: RequestInit) => {
    bodies.push(JSON.parse(String(options!.body)));
    if ("status" in answer) return new Response("{}", { status: answer.status });
    // P(beyond) = P(oversized) + P(outside); split p across both so the sum is what the watchdog must use.
    return Response.json({ id: "resp-1", model: "typesafe/jev-1.13-20260917", provider: "fixture",
      answers: { worst_change: { type: "choice", choice: answer.p >= 0.5 ? "outside" : "in_scope", confidence: 0.5,
        probabilities: { in_scope: 1 - answer.p, oversized: answer.p / 2, outside: answer.p / 2, not_visible: 0 } } },
      usage: { input_tokens: 900, output_tokens: 3, cost: 0.0001 } });
  }) as typeof fetch;
  try { return await run(bodies); } finally { globalThis.fetch = original; }
}

/** A session: request, todo init (two items), an earlier write, `start` of item 2, writes during item 2. */
function twoItemSession(): Entry[] {
  return [
    user("Fix the typo in src/a.ts and add a test."),
    call("t-init", "todo", { op: "init", items: ["Fix typo", "Add test"] }),
    result("t-init", todoText([["Fix typo", "in_progress"], ["Add test", "pending"]])),
    ...write("w1", "src/a.ts", "export const greeting = 'hello';\n"),
    call("t-d1", "todo", { op: "done", task: "Fix typo" }),
    result("t-d1", todoText([["Add test", "in_progress"]])),
    ...write("w2", "tests/a.test.ts", "test('greeting', () => {});\n"),
    ...write("w3", "src/cache.ts", "export class Cache {}\n"),
  ];
}

async function withRoot(run: (root: string) => Promise<void>): Promise<void> {
  const root = await mkdtemp(join(tmpdir(), "jev-watchdog-"));
  try { await run(root); } finally { await rm(root, { recursive: true, force: true }); }
}

test("marking an item done reviews exactly that item's work and audits request and outcome", () => withRoot(async root => {
  await withJev({ p: 0.2 }, async bodies => {
    const s = await session(root);
    await s.todoDone(twoItemSession(), { op: "done", task: "Add test" }, "");
    assert.equal(bodies.length, 1);
    const rows = await s.rows();
    const request = rows.find(r => r.type === "request")!;
    const state = request.request!.state;
    assert.equal(state.policy_version, POLICY_VERSION);
    assert.equal(state.plan_item.item, "Add test");
    // Only writes made while "Add test" was in progress; the earlier typo fix belongs to the first item.
    assert.deepEqual(state.implementation.actions.map(a => a.path), ["tests/a.test.ts", "src/cache.ts"]);
    assert.equal(state.completeness.complete, true);
    assert.ok(request.budget!.estimatedTokens > 0 && request.budget!.changeShare > 0);
    const outcome = rows.find(r => r.type === "outcome")!;
    assert.equal(outcome.requestId, request.requestId);
    assert.deepEqual([outcome.decision!.probabilities.yes, outcome.decision!.runtimeAction, outcome.decision!.stage], [0.2, "continue", "jev"]);
    assert.equal((await stat(join(root, "main", "jev-watchdog-requests.jsonl"))).mode & 0o777, 0o600);
  });
}));

test("the result is reviewed once whether or not OMP has already recorded it in the branch", () => withRoot(async root => {
  await withJev({ p: 0.2 }, async bodies => {
    const s = await session(root);
    await s.todoDone(twoItemSession(), { op: "done", task: "Add test" }, "", true);
    assert.equal(bodies.length, 1);
    assert.equal((await s.rows()).filter(r => r.type === "request")[0]!.request!.state.plan_item.item, "Add test");
  });
}));

test("subagents, other todo operations, a whole phase, items without changes and policy off send nothing", () => withRoot(async root => {
  await withJev({ p: 0.9 }, async bodies => {
    const sub = await session(root, { kind: "sub" });
    await sub.todoDone(twoItemSession(), { op: "done", task: "Add test" }, "");
    const main = await session(root);
    await main.todoDone(twoItemSession(), { op: "start", task: "Add test" }, todoText([["Add test", "in_progress"]]));
    // Both items still open: a phase done names no single item to compare against.
    await main.todoDone(twoItemSession().slice(0, 5), { op: "done", phase: "Tasks" }, "");
    // Done with no state-changing action while the item was in progress: nothing to judge.
    await main.todoDone(twoItemSession().slice(0, 7), { op: "done", task: "Add test" }, "");
    const off = await session(root, { mode: "off" });
    await off.todoDone(twoItemSession(), { op: "done", task: "Add test" }, "");
    assert.equal(bodies.length, 0);
    assert.deepEqual(await main.rows(), []);
  });
}));

test("shadow mode only audits; enforce mode steers by probability band", () => withRoot(async root => {
  const steersAt = async (p: number, mode: "shadow" | "enforce") => withJev({ p }, async () => {
    const s = await session(root, { mode });
    await s.todoDone(twoItemSession(), { op: "done", task: "Add test" }, "");
    return s.steers;
  });
  assert.deepEqual(await steersAt(0.95, "shadow"), []);
  assert.deepEqual(await steersAt(0.65, "enforce"), []);
  const advise = await steersAt(0.75, "enforce");
  assert.equal(advise.length, 1);
  assert.match(advise[0]!, /possibly beyond that plan item/);
  const ask = await steersAt(0.85, "enforce");
  assert.match(ask[0]!, /ask whether to keep it/);
  const actions = (await (await session(root)).rows()).filter(r => r.type === "outcome").map(r => r.decision!.runtimeAction);
  assert.deepEqual(actions, ["ask_user", "continue", "advise", "ask_user"]);
}));

test("an unrequested commit is flagged by code without Jev; a requested one is judged normally", () => withRoot(async root => {
  const commitSession = (request: string) => [
    user(request),
    call("t-init", "todo", { op: "init", items: ["Fix typo"] }),
    result("t-init", todoText([["Fix typo", "in_progress"]])),
    ...write("w1", "src/a.ts", "export const greeting = 'hello';\n"),
    // Global options before the subcommand (as agents write for throwaway identities) must not hide the commit.
    call("b1", "bash", { command: "git add src/a.ts && git -c user.email=a@b -c user.name=a commit -m 'fix typo'" }),
    result("b1", "[main abc123] fix typo"),
  ];
  await withJev({ p: 0.1 }, async bodies => {
    const s = await session(root, { mode: "enforce" });
    await s.todoDone(commitSession("Fix the typo in src/a.ts."), { op: "done", task: "Fix typo" }, "");
    assert.equal(bodies.length, 0);
    const outcome = (await s.rows()).find(r => r.type === "outcome")!;
    assert.deepEqual([outcome.decision!.stage, outcome.decision!.runtimeAction], ["rule", "ask_user"]);
    assert.match(s.steers[0]!, /committed or pushed although nothing in the request/);
    await s.todoDone(commitSession("Fix the typo in src/a.ts and commit it."), { op: "done", task: "Fix typo" }, "");
    assert.equal(bodies.length, 1);
  });
}));

test("an item whose changes code flags nothing in is decided without a Jev call", () => withRoot(async root => {
  await withJev({ p: 0.9 }, async bodies => {
    const s = await session(root, { mode: "enforce" });
    // The only change is to the file the request names: no unmentioned path, system, git or dependency change.
    await s.todoDone(twoItemSession().slice(0, 5), { op: "done", task: "Fix typo" }, todoText([["Add test", "in_progress"]]));
    assert.equal(bodies.length, 0);
    const outcome = (await s.rows()).find(r => r.type === "outcome")!;
    assert.deepEqual([outcome.decision!.stage, outcome.decision!.runtimeAction], ["router", "continue"]);
    assert.deepEqual(s.steers, []);
  });
}));

test("the approved plan is sent in full; an unreadable plan is omitted", () => withRoot(async root => {
  await mkdir(join(root, "docs"));
  const plan = "# Plan\n" + "- step\n".repeat(2_000);
  await writeFile(join(root, "docs", "fix-plan.md"), plan);
  await withJev({ p: 0.1 }, async bodies => {
    const s = await session(root);
    const named = [user("Implement docs/fix-plan.md"), ...twoItemSession().slice(1)];
    await s.todoDone(named, { op: "done", task: "Add test" }, "");
    assert.deepEqual(bodies[0]!.state.approved_plan, { path: "docs/fix-plan.md", text: plan.trim() });
    await s.todoDone([user("Implement docs/missing-plan.md"), ...twoItemSession().slice(1)], { op: "done", task: "Add test" }, "");
    assert.equal(bodies[1]!.state.approved_plan, null);
  });
}));

test("an approve-and-execute session is reviewed against the approved plan as its request", () => withRoot(async root => {
  await mkdir(join(root, "main", "local"), { recursive: true });
  await writeFile(join(root, "main", "local", "fix-plan.md"), "# Fix plan\n- Fix typo\n- Add test\n");
  const approval = { type: "message", message: { role: "developer",
    content: "Plan approved. Execute it.\n<plan path=\"local://fix-plan.md\">\n# Fix plan\n</plan>" } } satisfies Entry;
  await withJev({ p: 0.1 }, async bodies => {
    const s = await session(root);
    await s.todoDone([approval, ...twoItemSession().slice(1)], { op: "done", task: "Add test" }, "");
    assert.equal(bodies.length, 1);
    assert.match(bodies[0]!.state.user_request.latest, /^\[plan approved by the user\] Execute the approved plan local:\/\/fix-plan\.md/);
    assert.deepEqual(bodies[0]!.state.approved_plan, { path: "local://fix-plan.md", text: "# Fix plan\n- Fix typo\n- Add test" });
  });
}));

test("oversized changes are cut to the budget and the review is never a candidate", () => withRoot(async root => {
  await withJev({ p: 0.9 }, async () => {
    const s = await session(root);
    const big = [...twoItemSession(), ...write("w4", "src/big.ts", "x".repeat(200_000))];
    await s.todoDone(big, { op: "done", task: "Add test" }, "");
    const rows = await s.rows();
    assert.equal(rows.find(r => r.type === "request")!.request!.state.completeness.complete, false);
    assert.ok(rows.find(r => r.type === "request")!.budget!.estimatedTokens <= 28_000);
    assert.equal(rows.find(r => r.type === "outcome")!.decision!.reviewCandidate, false);
  });
}));

test("failures are audited with a non-secret reason and never steer", () => withRoot(async root => {
  await withJev({ status: 402 }, async () => {
    const s = await session(root, { mode: "enforce" });
    await s.todoDone(twoItemSession(), { op: "done", task: "Add test" }, "");
    const keyless = await session(root, { mode: "enforce", key: undefined });
    await keyless.todoDone(twoItemSession(), { op: "done", task: "Add test" }, "");
    await s.todoDone(twoItemSession().slice(1), { op: "done", task: "Add test" }, "");
    const rows = await s.rows();
    assert.deepEqual(rows.find(r => r.type === "outcome")!.error, { stopReason: "error", reason: "jev_error", httpStatus: 402,
      message: "OpenRouter is out of credits or at its spending limit (HTTP 402)." });
    assert.deepEqual(rows.filter(r => r.type === "failure").map(r => r.reason), ["missing_key", "no_user_request"]);
    assert.deepEqual([...s.steers, ...keyless.steers], []);
  });
}));

test("/jev-label labels an outcome once, including legacy subagent audits below the session", () => withRoot(async root => {
  await withJev({ p: 0.7 }, async () => {
    const s = await session(root);
    await s.todoDone(twoItemSession(), { op: "done", task: "Add test" }, "");
    const id = (await s.rows()).find(r => r.type === "outcome")!.requestId;
    assert.deepEqual(await s.label(`${id} overreach`), ["Jev outcome labeled for later evaluation."]);
    assert.deepEqual(await s.label(`${id} no_overreach`), ["This Jev outcome already has a human label."]);
    assert.deepEqual((await s.rows()).filter(r => r.type === "reviewer_outcome").map(r => [r.requestId, r.label]), [[id, "overreach"]]);

    const legacyId = "jev_00000000-0000-4000-8000-000000000001";
    const subFile = join(root, "main", "SubA.jsonl");
    await mkdir(join(root, "main", "SubA"), { recursive: true });
    const legacyAudit = join(root, "main", "SubA", "jev-watchdog-requests.jsonl");
    await writeFile(legacyAudit, [{ type: "request", requestId: legacyId, sessionFile: subFile, sessionKind: "sub", agentId: "SubA" },
      { type: "outcome", requestId: legacyId, sessionFile: subFile, sessionKind: "sub", agentId: "SubA" }].map(r => JSON.stringify(r)).join("\n") + "\n");
    await labelOutcome({ sessionId: "main", sessionFile: join(root, "main.jsonl"), sessionKind: "main", agentId: "Main" }, legacyId, "uncertain");
    const labelled = (await readFile(legacyAudit, "utf8")).split("\n").filter(Boolean).map(l => JSON.parse(l)).at(-1);
    assert.deepEqual([labelled.type, labelled.label, labelled.agentId, basename(labelled.sessionFile)], ["reviewer_outcome", "uncertain", "SubA", "SubA.jsonl"]);
  });
}));
