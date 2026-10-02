import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { appendFile, mkdir, mkdtemp, readdir, readFile, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { promisify } from "node:util";
import { appendDecision, type JsonValue, type NewDecision } from "../agent/extensions/lib/jev";
import { scoreExpected } from "../jev-lab/lib/caseset";
import { casesForVersion, computeMetrics, thresholdSweep, versionsOf } from "../agent/skills/evaluate-jev/scripts/cases.ts";
import { buildProposalQueue, buildQueue, scopeProgress } from "../jev-lab/lib/metrics";
import { loadProposals } from "../jev-lab/lib/proposals";
import { loadCases } from "../jev-lab/lib/sources";
import { appendPolicyLabel, loadPolicyCases } from "../jev-lab/lib/policy";
import { appendScopeLabel, loadScopeCases } from "../jev-lab/lib/scope";
import type { LabCase, LabPaths } from "../jev-lab/lib/types";
import { createHandler } from "../jev-lab/server";

const run = promisify(execFile);
const KEY = "sk-or-test-KEYVALUE1234567";
const TOKEN = "lab-token-fixture";

type Fixture = { root: string; paths: LabPaths; auditFile: string };

async function fixture(): Promise<Fixture> {
  const root = await mkdtemp(join(tmpdir(), "jev-lab-"));
  const paths: LabPaths = {
    sessionsDir: join(root, "sessions"), auditDir: join(root, "audit"),
    casesetDir: join(root, "casesets"), runsDir: join(root, "runs"),
  };
  const dir = join(paths.sessionsDir, "proj", "s1");
  await mkdir(dir, { recursive: true });
  const auditFile = join(dir, "jev-watchdog-requests.jsonl");
  const sessionFile = join(paths.sessionsDir, "proj", "s1.jsonl");
  const base = { sessionId: "s1", sessionFile };
  const request = (id: string, source: string | undefined, extra: { clipped?: boolean; legacy?: boolean } = {}) => ({
    ...base, timestamp: `2026-09-2${id.length}T00:00:00Z`, type: "request", requestId: id,
    request: { model: "m", questions: { drift: { type: "choice", instructions: "q", criteria: { yes: "a", no: "b", unknown: "c" } } },
      state: extra.legacy ? { review_policy: "old" } : {
        policy_version: "p1",
        task_context: { recent_user_requests: source === "missing" ? [] : ["fix the typo"], source, clipped_requests: extra.clipped ?? false },
        agent_activity: { excerpt: "write" },
      } },
  });
  const outcome = (id: string, yes: number) => ({
    ...base, type: "outcome", requestId: id, traceResponseId: "t", resolvedModel: "typesafe/jev-1.13-20260917",
    decision: { choice: yes >= 0.5 ? "yes" : "no", probabilities: { yes, no: 1 - yes, unknown: 0 }, confidence: 1, threshold: 0.9, reviewCandidate: yes >= 0.9 },
  });
  const lines = [
    { type: "session", id: "header" },
    request("r1", "current"), outcome("r1", 0.95),
    request("r22", "missing"), outcome("r22", 0.2), { ...base, type: "reviewer_outcome", requestId: "r22", label: "no_overreach", reviewer: "human" },
    request("r333", undefined, { legacy: true }),
    request("r4444", "carried_forward", { clipped: true }), outcome("r4444", 0.6),
    request("r55555", "carried_forward"), outcome("r55555", 0.92), { ...base, type: "reviewer_outcome", requestId: "r55555", label: "overreach", reviewer: "human" },
    request("r666666", "current"), { ...base, type: "outcome", requestId: "r666666", traceResponseId: "t", error: { stopReason: "error", httpStatus: 500 } },
  ];
  await writeFile(auditFile, lines.map(l => JSON.stringify(l)).join("\n") + "\nnot json\n", { mode: 0o600 });
  return { root, paths, auditFile };
}

function labCase(score: number | undefined, label: string | null, extra: Partial<LabCase> = {}): LabCase {
  return {
    source: "jev-scope", id: `c${Math.random()}`, subject: "s", verdict: "yes", uncertainty: 0, state: null, answers: [],
    labelOptions: ["overreach", "no_overreach", "uncertain"], positiveLabel: "overreach", score, label, labelBy: label === null ? null : "human", sufficient: true, ...extra,
  };
}

test("jev-scope join keeps legacy records and marks missing outcomes and errors", async () => {
  const f = await fixture();
  try {
    const { cases, malformed } = await loadScopeCases(f.paths);
    assert.equal(malformed, 1);
    const byId = new Map(cases.map(c => [c.id, c]));
    assert.equal(cases.length, 6);
    assert.equal(byId.get("r1")?.score, 0.95);
    assert.equal(byId.get("r1")?.label, null);
    assert.equal(byId.get("r22")?.label, "no_overreach");
    assert.equal(byId.get("r55555")?.label, "overreach");
    assert.equal(byId.get("r333")?.version, "unversioned");
    assert.equal(byId.get("r333")?.verdict, "no-outcome");
    assert.equal(byId.get("r333")?.sufficient, false);
    assert.equal(byId.get("r666666")?.verdict, "error:error");
    assert.equal(byId.get("r666666")?.score, undefined);
  } finally { await rm(f.root, { recursive: true, force: true }); }
});

test("exported lab cases summarize in the portable format, defaulting to the current version", async () => {
  const f = await fixture();
  const decision = (requestId: string, policyVersion: string, timestamp: string, verdict: string) => JSON.stringify({
    schema: 1, type: "decision", timestamp, requestId, policy: "guard.demo", policyVersion, mode: "shadow", stage: "jev",
    subject: "cmd", verdict, enforced: false, labels: ["correct", "false_positive", "false_negative", "uncertain"],
  });
  const previous = process.env.JEV_AUDIT_DIR;
  process.env.JEV_AUDIT_DIR = f.paths.auditDir;
  try {
    await mkdir(join(f.paths.auditDir, "guard.demo"), { recursive: true });
    await writeFile(join(f.paths.auditDir, "guard.demo", "2026-10-01.jsonl"), [
      decision("old", "v1", "2026-10-01T00:00:00Z", "block"),
      decision("allow", "v2", "2026-10-01T01:00:00Z", "allow"),
      decision("block", "v2", "2026-10-01T02:00:00Z", "block"),
    ].join("\n") + "\n", { mode: 0o600 });
    await appendPolicyLabel(f.paths, "guard.demo", "block", "false_positive", "human");
    await appendPolicyLabel(f.paths, "guard.demo", "allow", "correct", "agent");
    const cases = join(f.root, "cases.jsonl");
    await run("bun", [join(import.meta.dirname, "..", "jev-lab/scripts/export-cases.ts"),
      "--sessions", f.paths.sessionsDir, "--audit", f.paths.auditDir, "--source", "policy:guard.demo", "--out", cases]);
    const summarize = async (...extra: string[]) => JSON.parse((await run("bun", [
      join(import.meta.dirname, "..", "agent/skills/evaluate-jev/scripts/summarize.ts"), "--cases-in", cases, ...extra,
    ])).stdout);
    const current = await summarize();
    assert.equal(current.filters.resolvedVersion, "v2");
    assert.equal(current.total, 2);
    assert.deepEqual(current.verdictByLabel, { allow: { correct: 1 }, block: { false_positive: 1 } });
    assert.deepEqual(current.labels.byReviewer, { agent: 1, human: 1 });
    const all = await summarize("--version", "all");
    assert.equal(all.total, 3);
    assert.equal(all.byVerdict.block, 2);
    assert.equal(all.labels.byReviewer.unlabelled, 1);
  } finally {
    if (previous === undefined) delete process.env.JEV_AUDIT_DIR; else process.env.JEV_AUDIT_DIR = previous;
    await rm(f.root, { recursive: true, force: true });
  }
});

test("current policy version comes from the newest case; queue, progress and metrics exclude older versions unless asked", async () => {
  const f = await fixture();
  try {
    const extra = (id: string, version: string, timestamp: string, yes: number) => [
      { sessionId: "s1", timestamp, type: "request", requestId: id, request: { model: "m", state: {
        policy_version: version, task_context: { recent_user_requests: ["fix"], source: "current", clipped_requests: false } } } },
      { sessionId: "s1", type: "outcome", requestId: id, decision: { choice: "yes", probabilities: { yes, no: 1 - yes, unknown: 0 }, confidence: 1 } },
    ];
    const lines = [...extra("old1", "p0", "2026-01-01T00:00:00Z", 0.95), ...extra("new1", "p2", "2026-12-01T00:00:00Z", 0.95)];
    await appendFile(f.auditFile, lines.map(l => JSON.stringify(l)).join("\n") + "\n");
    const { cases } = await loadScopeCases(f.paths);
    assert.deepEqual(versionsOf(cases).current, "p2");
    assert.deepEqual(casesForVersion(cases, "current").map(c => c.id), ["new1"]);
    // r333 is an unversioned legacy record, so it belongs to no policy version.
    assert.deepEqual(casesForVersion(cases, "p1").map(c => c.id).sort(), ["r1", "r22", "r4444", "r55555", "r666666"]);

    const handler = createHandler({ paths: f.paths, token: TOKEN });
    const get = async (path: string) => JSON.parse(await (await handler(new Request(`http://127.0.0.1:4398${path}`))).text());
    const queue = await get("/api/queue");
    assert.deepEqual(queue.queue.map((c: LabCase) => c.id), ["new1"]);
    assert.equal(queue.progress.labelledSufficient, 0);
    assert.equal((await get("/api/metrics?source=jev-scope")).metrics.total, 1);
    const legacy = await get("/api/metrics?source=jev-scope&version=p1");
    assert.equal(legacy.version, "p1");
    assert.equal(legacy.metrics.total, 3);
    assert.deepEqual((await get("/api/cases?source=jev-scope&version=p0")).cases.map((c: LabCase) => c.id), ["old1"]);
  } finally { await rm(f.root, { recursive: true, force: true }); }
});

test("scope label append uses the labelOutcome record shape, mode 0600, and rejects duplicates and missing outcomes", async () => {
  const f = await fixture();
  try {
    await appendScopeLabel(f.paths, "r1", "overreach", "human");
    const last = JSON.parse((await readFile(f.auditFile, "utf8")).trim().split("\n").at(-1) ?? "");
    assert.deepEqual(Object.keys(last).sort(), ["label", "requestId", "reviewer", "sessionFile", "sessionId", "timestamp", "type"]);
    assert.equal(last.type, "reviewer_outcome");
    assert.equal(last.reviewer, "human");
    assert.equal(last.sessionId, "s1");
    assert.equal((await stat(f.auditFile)).mode & 0o777, 0o600);
    await assert.rejects(appendScopeLabel(f.paths, "r1", "no_overreach", "human"), /already has a human label/);
    await assert.rejects(appendScopeLabel(f.paths, "r333", "overreach", "human"), /No Jev outcome/);
    await assert.rejects(appendScopeLabel(f.paths, "nope", "overreach", "human"), /No Jev request/);
    await assert.rejects(appendScopeLabel(f.paths, "r4444", "maybe", "human"), /Label must be one of/);
    const { cases } = await loadScopeCases(f.paths);
    assert.equal(cases.find(c => c.id === "r1")?.label, "overreach");
  } finally { await rm(f.root, { recursive: true, force: true }); }
});

test("policy audit decisions load with labels and labels are appended once via appendLabel", async () => {
  const f = await fixture();
  const previous = process.env.JEV_AUDIT_DIR;
  process.env.JEV_AUDIT_DIR = f.paths.auditDir;
  try {
    const decision: NewDecision = {
      policy: "demo", policyVersion: "v1", mode: "shadow", stage: "jev", subject: "rm -rf x", verdict: "block", enforced: false,
      labels: ["block", "allow"], resolvedModel: "typesafe/jev-1.13-20260917", costUsd: 0.00001, latencyMs: 300,
      state: { command: "rm -rf x" }, questions: { danger: { type: "noul", instructions: "Is it dangerous?" } },
      answers: { danger: { type: "noul", noul: 0.97 } },
    };
    const written = await appendDecision(decision);
    assert.ok(written);
    let cases = await loadPolicyCases(f.paths, "demo");
    assert.equal(cases.length, 1);
    assert.equal(cases[0]?.score, 0.97);
    assert.equal(cases[0]?.label, null);
    await appendPolicyLabel(f.paths, "demo", written.requestId, "block", "human", "clearly destructive");
    cases = await loadPolicyCases(f.paths, "demo");
    assert.equal(cases[0]?.label, "block");
    assert.equal(cases[0]?.labelNote, "clearly destructive");
    assert.equal((await stat(join(f.paths.auditDir, "demo", "labels.jsonl"))).mode & 0o777, 0o600);
    await assert.rejects(appendPolicyLabel(f.paths, "demo", written.requestId, "allow", "human"), /already has a human label/);
    await assert.rejects(appendPolicyLabel(f.paths, "demo", "missing", "allow", "human"), /No decision/);
  } finally {
    if (previous === undefined) delete process.env.JEV_AUDIT_DIR; else process.env.JEV_AUDIT_DIR = previous;
    await rm(f.root, { recursive: true, force: true });
  }
});

test("scope labels: human beats agent, agent never overwrites, legacy reviewer-less rows are human", async () => {
  const f = await fixture();
  try {
    await appendScopeLabel(f.paths, "r1", "overreach", "agent");
    let r1 = (await loadScopeCases(f.paths)).cases.find(c => c.id === "r1");
    assert.deepEqual([r1?.label, r1?.labelBy], ["overreach", "agent"]);
    await assert.rejects(appendScopeLabel(f.paths, "r1", "uncertain", "agent"), /already has a label/);
    await appendScopeLabel(f.paths, "r1", "no_overreach", "human");
    r1 = (await loadScopeCases(f.paths)).cases.find(c => c.id === "r1");
    assert.deepEqual([r1?.label, r1?.labelBy], ["no_overreach", "human"]);
    // A stray later agent row (e.g. from a concurrent writer) cannot displace the human label.
    const base = { sessionId: "s1", type: "reviewer_outcome", requestId: "r1" };
    await appendFile(f.auditFile, JSON.stringify({ ...base, label: "overreach", reviewer: "agent" }) + "\n");
    r1 = (await loadScopeCases(f.paths)).cases.find(c => c.id === "r1");
    assert.deepEqual([r1?.label, r1?.labelBy], ["no_overreach", "human"]);
    await assert.rejects(appendScopeLabel(f.paths, "r1", "uncertain", "agent"), /already has a human label/);

    // Legacy rows have no reviewer field and count as human.
    await appendFile(f.auditFile, JSON.stringify({ sessionId: "s1", type: "request", requestId: "leg", request: { state: {} } }) + "\n");
    await appendFile(f.auditFile, JSON.stringify({ sessionId: "s1", type: "outcome", requestId: "leg", decision: { choice: "yes", probabilities: { yes: 0.9, no: 0.1 } } }) + "\n");
    await appendFile(f.auditFile, JSON.stringify({ sessionId: "s1", type: "reviewer_outcome", requestId: "leg", label: "overreach" }) + "\n");
    const leg = (await loadScopeCases(f.paths)).cases.find(c => c.id === "leg");
    assert.deepEqual([leg?.label, leg?.labelBy], ["overreach", "human"]);
    await assert.rejects(appendScopeLabel(f.paths, "leg", "uncertain", "agent"), /already has a human label/);
    await assert.rejects(appendScopeLabel(f.paths, "leg", "uncertain", "human"), /already has a human label/);
  } finally { await rm(f.root, { recursive: true, force: true }); }
});

test("policy labels: human beats agent, agent never overwrites, legacy reviewer-less rows are human", async () => {
  const f = await fixture();
  const previous = process.env.JEV_AUDIT_DIR;
  process.env.JEV_AUDIT_DIR = f.paths.auditDir;
  try {
    const decision: NewDecision = {
      policy: "demo", policyVersion: "v1", mode: "shadow", stage: "jev", subject: "rm -rf x", verdict: "block", enforced: false,
      labels: ["block", "allow"], state: { command: "rm -rf x" },
      questions: { danger: { type: "noul", instructions: "Is it dangerous?" } }, answers: { danger: { type: "noul", noul: 0.97 } },
    };
    const first = await appendDecision(decision);
    const second = await appendDecision(decision);
    assert.ok(first && second);
    const find = async (id: string) => (await loadPolicyCases(f.paths, "demo")).find(c => c.id === id);

    await appendPolicyLabel(f.paths, "demo", first.requestId, "allow", "agent", "first pass");
    assert.deepEqual([(await find(first.requestId))?.label, (await find(first.requestId))?.labelBy], ["allow", "agent"]);
    await assert.rejects(appendPolicyLabel(f.paths, "demo", first.requestId, "block", "agent"), /already has a label/);
    await appendPolicyLabel(f.paths, "demo", first.requestId, "block", "human");
    assert.deepEqual([(await find(first.requestId))?.label, (await find(first.requestId))?.labelBy], ["block", "human"]);
    await appendFile(join(f.paths.auditDir, "demo", "labels.jsonl"),
      JSON.stringify({ type: "label", requestId: first.requestId, label: "allow", reviewer: "agent" }) + "\n");
    assert.deepEqual([(await find(first.requestId))?.label, (await find(first.requestId))?.labelBy], ["block", "human"]);
    await assert.rejects(appendPolicyLabel(f.paths, "demo", first.requestId, "allow", "agent"), /already has a human label/);

    await appendFile(join(f.paths.auditDir, "demo", "labels.jsonl"),
      JSON.stringify({ type: "label", requestId: second.requestId, label: "allow" }) + "\n");
    assert.deepEqual([(await find(second.requestId))?.label, (await find(second.requestId))?.labelBy], ["allow", "human"]);
    await assert.rejects(appendPolicyLabel(f.paths, "demo", second.requestId, "block", "agent"), /already has a human label/);
  } finally {
    if (previous === undefined) delete process.env.JEV_AUDIT_DIR; else process.env.JEV_AUDIT_DIR = previous;
    await rm(f.root, { recursive: true, force: true });
  }
});

test("threshold sweep, confusion counts and progress on a fixture", () => {
  const cases = [
    labCase(0.95, "overreach"), labCase(0.92, "no_overreach"), labCase(0.7, "overreach"),
    labCase(0.6, "no_overreach"), labCase(0.2, "no_overreach"), labCase(0.99, "uncertain"), labCase(0.95, null),
  ];
  const sweep = thresholdSweep(cases, "overreach");
  assert.deepEqual(sweep.map(r => r.threshold), [0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95]);
  const at = (t: number) => sweep.find(r => r.threshold === t);
  assert.deepEqual([at(0.9)?.tp, at(0.9)?.fp, at(0.9)?.fn, at(0.9)?.tn], [1, 1, 1, 2]);
  assert.equal(at(0.9)?.precision, 0.5);
  assert.equal(at(0.9)?.recall, 0.5);
  assert.deepEqual([at(0.5)?.tp, at(0.5)?.fp, at(0.5)?.fn, at(0.5)?.tn], [2, 2, 0, 1]);
  assert.equal(at(0.5)?.recall, 1);
  assert.equal(at(0.95)?.tp, 1);
  const metrics = computeMetrics(cases, 0.9);
  assert.equal(metrics.excludedUncertain, 1);
  assert.equal(metrics.scored, 5);
  assert.equal(metrics.unlabelled, 1);
  assert.deepEqual(metrics.verdictByLabel.yes, { overreach: 2, no_overreach: 3, uncertain: 1, unlabelled: 1 });
  assert.deepEqual(scopeProgress(cases, 30, 5), {
    proposalsReviewed: 0, proposalsTotal: 0, labelledSufficient: 6, positives: 2, minSufficient: 30, minPositive: 5, done: false,
    agentLabelled: 0, humanConfirmed: 6,
  });
  const proposal = { label: "overreach", agreement: "agreed" as const, rationale: "r", labellers: {} };
  const withAgent = scopeProgress([
    labCase(0.9, "overreach", { labelBy: "agent", proposal }), labCase(0.9, "overreach", { proposal }),
  ], 30, 5);
  assert.deepEqual([withAgent.agentLabelled, withAgent.humanConfirmed, withAgent.proposalsReviewed, withAgent.proposalsTotal], [1, 1, 1, 2]);
});

test("queue prefers unlabelled sufficient cases and spans P bands", () => {
  const cases = [
    ...[0.95, 0.96, 0.97].map(p => labCase(p, null, { id: `hi${p}` })),
    ...[0.1, 0.15].map(p => labCase(p, null, { id: `lo${p}` })),
    labCase(0.85, null, { id: "mid" }),
    labCase(0.93, "overreach", { id: "labelled" }),
    labCase(0.99, null, { id: "insufficient", sufficient: false }),
    labCase(undefined, null, { id: "no-score" }),
  ];
  const ids = buildQueue(cases, 4).map(c => c.id);
  assert.equal(ids.length, 4);
  assert.ok(ids[0]?.startsWith("hi"));
  assert.equal(ids[1], "mid");
  assert.ok(ids[2]?.startsWith("lo"));
  assert.ok(!ids.includes("labelled") && !ids.includes("insufficient") && !ids.includes("no-score"));
});

test("expected values score choice, noul and score answers", () => {
  const checks = scoreExpected({ a: "yes", b: true, c: 2 }, {
    a: { type: "choice", choice: "yes", probabilities: { yes: 1 }, confidence: 1 },
    b: { type: "noul", noul: 0.2 },
    c: { type: "score", score: 2.3, probabilities: {}, confidence: 1 },
  });
  assert.deepEqual(checks.map(c => c.pass), [true, false, true]);
});

function post(path: string, body: JsonValue, token?: string): Request {
  return new Request(`http://127.0.0.1:4398${path}`, {
    method: "POST", body: JSON.stringify(body),
    headers: token ? { "x-jev-lab-token": token, "content-type": "application/json" } : { "content-type": "application/json" },
  });
}

test("mutating and live endpoints require the token; the API key never appears in responses or run files", async () => {
  const f = await fixture();
  const original = globalThis.fetch;
  const bodies: string[] = [];
  // SAFETY: the workbench only calls fetch through decide(); this stub returns a real Response and is restored below.
  globalThis.fetch = (async () => new Response(JSON.stringify({
    id: `resp-${KEY}`, model: "typesafe/jev-1.13-20260917",
    answers: { on_topic: { type: "noul", noul: 0.8 } }, usage: { input_tokens: 10, output_tokens: 2, cost: 0.00001 },
  }), { status: 200, headers: { "content-type": "application/json" } })) as typeof fetch;
  try {
    const handler = createHandler({ paths: f.paths, token: TOKEN, apiKey: KEY });
    const send = async (req: Request) => {
      const res = await handler(req);
      const text = await res.text();
      bodies.push(text);
      return { status: res.status, text };
    };
    const playground = { state: { text: "hello" }, questions: { on_topic: { type: "noul", instructions: "On topic?" } } };
    const endpoints: Array<[string, JsonValue]> = [
      ["/api/label", { source: "jev-scope", id: "r1", label: "overreach" }],
      ["/api/replay", { source: "jev-scope", id: "r1" }],
      ["/api/playground/run", playground],
      ["/api/caseset/save", { name: "scratch", case: { id: "x", ...playground } }],
      ["/api/caseset/run", { name: "scratch" }],
    ];
    for (const [path, body] of endpoints) {
      assert.equal((await send(post(path, body))).status, 403, `${path} without token`);
      assert.equal((await send(post(path, body, "wrong"))).status, 403, `${path} wrong token`);
    }
    assert.equal((await send(post("/api/label", { source: "jev-scope", id: "r1", label: "overreach" }, TOKEN))).status, 200);
    assert.equal((await send(post("/api/label", { source: "jev-scope", id: "r1", label: "overreach" }, TOKEN))).status, 409);
    const forged = new Request("http://127.0.0.1:4398/api/label", {
      method: "POST", body: "{}", headers: { "x-jev-lab-token": TOKEN, origin: "https://evil.example" },
    });
    assert.equal((await send(forged)).status, 403);

    const live = await send(post("/api/playground/run", playground, TOKEN));
    assert.equal(live.status, 200);
    assert.equal(JSON.parse(live.text).outcome.answers[0].noul, 0.8);
    assert.equal((await send(post("/api/caseset/save", { name: "scratch", case: { id: "x", expected: { on_topic: true }, ...playground } }, TOKEN))).status, 200);
    const set = await send(post("/api/caseset/run", { name: "scratch" }, TOKEN));
    assert.equal(JSON.parse(set.text).passed, 1);
    assert.equal((await send(post("/api/replay", { source: "jev-scope", id: "r1" }, TOKEN))).status, 200);

    for (const path of ["/", "/app.js", "/api/sources", "/api/cases?source=jev-scope", "/api/case?source=jev-scope&id=r1", "/api/metrics?source=jev-scope", "/api/queue"]) {
      const res = await send(new Request(`http://127.0.0.1:4398${path}`));
      assert.equal(res.status, 200, path);
    }
    const page = bodies.find(b => b.includes("<html"));
    assert.ok(page?.includes(TOKEN), "page carries the per-process token");
    assert.equal((await send(new Request("http://evil.example/api/sources"))).status, 403);

    for (const body of bodies) assert.ok(!body.includes(KEY), "key leaked in a response");
    const runs = await readdir(f.paths.runsDir);
    assert.ok(runs.length >= 3);
    for (const name of runs) {
      assert.ok(!(await readFile(join(f.paths.runsDir, name), "utf8")).includes(KEY), "key leaked in a run file");
      assert.equal((await stat(join(f.paths.runsDir, name))).mode & 0o777, 0o600);
    }
  } finally {
    globalThis.fetch = original;
    await rm(f.root, { recursive: true, force: true });
  }
});

test("live endpoints report disabled when no key is available", async () => {
  const f = await fixture();
  try {
    const handler = createHandler({ paths: f.paths, token: TOKEN });
    const res = await handler(post("/api/playground/run", { state: {}, questions: { q: { type: "noul", instructions: "x" } } }, TOKEN));
    assert.equal(res.status, 503);
  } finally { await rm(f.root, { recursive: true, force: true }); }
});

async function writeProposals(f: Fixture, rows: string[]): Promise<void> {
  await mkdir(f.paths.runsDir, { recursive: true });
  await writeFile(join(f.paths.runsDir, "scope-proposals.jsonl"), rows.join("\n") + "\n");
}
const proposal = (id: string, label: string, agreement: string) => JSON.stringify({
  id, label, agreement, rationale: "why", namedChange: "", evidence: "q", labellers: { "gpt-6-sol": label, "sonnet-5.5": label },
});

test("proposals attach to matching cases, malformed rows are skipped, other cases are unaffected", async () => {
  const f = await fixture();
  try {
    await writeProposals(f, [proposal("r1", "overreach", "disputed"), "not json", JSON.stringify({ id: "r55555", label: "bogus" }), proposal("ghost", "uncertain", "agreed")]);
    const index = await loadProposals(f.paths);
    assert.equal(index.malformed, 2);
    const cases = await loadCases(f.paths, "jev-scope");
    assert.equal(cases.find(c => c.id === "r1")?.proposal?.agreement, "disputed");
    assert.equal(cases.find(c => c.id === "r1")?.proposal?.labellers["sonnet-5.5"], "overreach");
    assert.equal(cases.find(c => c.id === "r55555")?.proposal, undefined);
    assert.equal(cases.find(c => c.id === "r4444")?.proposal, undefined);
    assert.equal(cases.length, 6);
  } finally { await rm(f.root, { recursive: true, force: true }); }
});

test("without a proposals file cases load normally; review queue puts disputed then overreach first", async () => {
  const f = await fixture();
  try {
    assert.ok((await loadCases(f.paths, "jev-scope")).every(c => c.proposal === undefined));
    const cases = [
      labCase(0.3, null, { id: "plain", proposal: undefined }),
      labCase(0.4, null, { id: "over", proposal: { label: "overreach", agreement: "agreed", rationale: "", labellers: {} } }),
      labCase(0.1, null, { id: "disp", proposal: { label: "no_overreach", agreement: "disputed", rationale: "", labellers: {} } }),
    ];
    assert.deepEqual(buildProposalQueue(cases, 10).map(c => c.id), ["disp", "over", "plain"]);
  } finally { await rm(f.root, { recursive: true, force: true }); }
});

test("accepting a proposal goes through the human label path with the single-label guard", async () => {
  const f = await fixture();
  try {
    await writeProposals(f, [proposal("r1", "overreach", "agreed")]);
    const handler = createHandler({ paths: f.paths, token: TOKEN });
    const accept = () => handler(post("/api/label", { source: "jev-scope", id: "r1", label: "overreach", note: "accepted first-pass proposal" }, TOKEN));
    assert.equal((await accept()).status, 200);
    const last = JSON.parse((await readFile(f.auditFile, "utf8")).trim().split("\n").at(-1) ?? "");
    assert.equal(last.type, "reviewer_outcome");
    assert.equal(last.reviewer, "human");
    assert.equal(last.label, "overreach");
    assert.equal((await accept()).status, 409);
    const cases = await loadCases(f.paths, "jev-scope");
    const done = cases.find(c => c.id === "r1");
    assert.equal(done?.label, "overreach");
    assert.equal(done?.proposal?.label, "overreach");
    assert.equal(done?.labelNote, "accepted first-pass proposal");
  } finally { await rm(f.root, { recursive: true, force: true }); }
});


test("nested subagent audits are discovered by the lab, keep their identity, and label in place", async () => {
  const root = await mkdtemp(join(tmpdir(), "jev-lab-sub-"));
  try {
    const paths: LabPaths = {
      sessionsDir: join(root, "sessions"), auditDir: join(root, "audit"),
      casesetDir: join(root, "casesets"), runsDir: join(root, "runs"),
    };
    const mainDir = join(paths.sessionsDir, "proj", "main");
    const subDir = join(mainDir, "EditA");
    await mkdir(subDir, { recursive: true });
    const row = (identity: { sessionFile: string; sessionKind?: string; agentId?: string }, type: string, requestId: string, extra: Record<string, JsonValue> = {}) =>
      JSON.stringify({ sessionId: "sid", timestamp: "2026-09-29T00:00:00Z", ...identity, type, requestId, ...extra });
    const state = (request: string) => ({ policy_version: "p1", task_context: { recent_user_requests: [request], source: "current", clipped_requests: false },
      agent_activity: { excerpt: "write" } });
    const decision = { decision: { choice: "no", probabilities: { yes: 0.1, no: 0.9, unknown: 0 }, confidence: 1, reviewCandidate: false } };
    const main = { sessionFile: join(paths.sessionsDir, "proj", "main.jsonl"), sessionKind: "main", agentId: "Main" };
    const sub = { sessionFile: join(mainDir, "EditA.jsonl"), sessionKind: "sub", agentId: "EditA" };
    await writeFile(join(mainDir, "jev-watchdog-requests.jsonl"), [
      row(main, "request", "m1", { request: { model: "m", state: state("main request") } }), row(main, "outcome", "m1", decision),
    ].join("\n") + "\n", { mode: 0o600 });
    await writeFile(join(subDir, "jev-watchdog-requests.jsonl"), [
      row(sub, "request", "s1", { request: { model: "m", state: state("sub assignment") } }), row(sub, "outcome", "s1", decision),
    ].join("\n") + "\n", { mode: 0o600 });

    const { cases } = await loadScopeCases(paths);
    const byId = new Map(cases.map(c => [c.id, c]));
    assert.deepEqual([...byId.keys()].sort(), ["m1", "s1"]);
    assert.deepEqual([byId.get("s1")?.sessionKind, byId.get("s1")?.agentId, byId.get("s1")?.subject], ["sub", "EditA", "sub assignment"]);
    assert.equal(byId.get("s1")?.transcriptPath, sub.sessionFile);
    assert.deepEqual([byId.get("m1")?.sessionKind, byId.get("m1")?.agentId], ["main", "Main"]);

    // A label lands in the file holding the request, under the subagent's identity.
    await appendScopeLabel(paths, "s1", "overreach", "human");
    const labelled = (await readFile(join(subDir, "jev-watchdog-requests.jsonl"), "utf8")).trim().split("\n").map(line => JSON.parse(line)).at(-1);
    assert.deepEqual([labelled.type, labelled.sessionKind, labelled.agentId, labelled.sessionFile], ["reviewer_outcome", "sub", "EditA", sub.sessionFile]);
    assert.equal((await readFile(join(mainDir, "jev-watchdog-requests.jsonl"), "utf8")).includes("reviewer_outcome"), false);
    assert.equal((await loadScopeCases(paths)).cases.find(c => c.id === "s1")?.label, "overreach");
  } finally { await rm(root, { recursive: true, force: true }); }
});
