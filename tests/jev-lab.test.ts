import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { appendFile, mkdir, mkdtemp, readdir, readFile, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { promisify } from "node:util";
import { appendDecision, type JsonValue, type NewDecision } from "../agent/extensions/lib/jev";
import { scoreExpected } from "../jev-lab/lib/caseset";
import { computeMetrics, thresholdSweep } from "../agent/skills/evaluate-jev/scripts/cases.ts";
import { appendPolicyLabel, loadPolicyCases } from "../jev-lab/lib/policy";
import type { LabCase, LabPaths } from "../jev-lab/lib/types";
import { createHandler } from "../jev-lab/server";

const run = promisify(execFile);
const KEY = "sk-or-test-KEYVALUE1234567";
const TOKEN = "lab-token-fixture";

type Fixture = { root: string; paths: LabPaths };

/** A temp lab with one policy `fixture` holding a single unlabelled decision `d1` that stored its Jev request. */
async function fixture(): Promise<Fixture> {
  const root = await mkdtemp(join(tmpdir(), "jev-lab-"));
  const paths: LabPaths = { auditDir: join(root, "audit"), casesetDir: join(root, "casesets"), runsDir: join(root, "runs") };
  await mkdir(join(paths.auditDir, "fixture"), { recursive: true });
  const decision = {
    schema: 1, type: "decision", timestamp: "2026-10-01T00:00:00Z", requestId: "d1", policy: "fixture", policyVersion: "v1",
    mode: "shadow", stage: "jev", subject: "rm -rf x", verdict: "block", enforced: false, labels: ["block", "allow"],
    state: { command: "rm -rf x" }, questions: { on_topic: { type: "noul", instructions: "Dangerous?" } },
    answers: { on_topic: { type: "noul", noul: 0.9 } },
  };
  await writeFile(join(paths.auditDir, "fixture", "2026-10-01.jsonl"), JSON.stringify(decision) + "\n", { mode: 0o600 });
  return { root, paths };
}

function labCase(score: number | undefined, label: string | null, extra: Partial<LabCase> = {}): LabCase {
  return {
    source: "policy:demo", id: `c${Math.random()}`, subject: "s", verdict: "yes", uncertainty: 0, state: null, answers: [],
    labelOptions: ["block", "allow", "uncertain"], positiveLabel: "block", score, label, labelBy: label === null ? null : "human", ...extra,
  };
}

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
      "--audit", f.paths.auditDir, "--source", "policy:guard.demo", "--out", cases]);
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

test("threshold sweep and confusion counts on a fixture", () => {
  const cases = [
    labCase(0.95, "block"), labCase(0.92, "allow"), labCase(0.7, "block"),
    labCase(0.6, "allow"), labCase(0.2, "allow"), labCase(0.99, "uncertain"), labCase(0.95, null),
  ];
  const sweep = thresholdSweep(cases, "block");
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
  assert.deepEqual(metrics.verdictByLabel.yes, { block: 2, allow: 3, uncertain: 1, unlabelled: 1 });
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
  const previousAudit = process.env.JEV_AUDIT_DIR;
  process.env.JEV_AUDIT_DIR = f.paths.auditDir;
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
      ["/api/label", { source: "policy:fixture", id: "d1", label: "block" }],
      ["/api/replay", { source: "policy:fixture", id: "d1" }],
      ["/api/playground/run", playground],
      ["/api/caseset/save", { name: "scratch", case: { id: "x", ...playground } }],
      ["/api/caseset/run", { name: "scratch" }],
    ];
    for (const [path, body] of endpoints) {
      assert.equal((await send(post(path, body))).status, 403, `${path} without token`);
      assert.equal((await send(post(path, body, "wrong"))).status, 403, `${path} wrong token`);
    }
    assert.equal((await send(post("/api/label", { source: "policy:fixture", id: "d1", label: "block" }, TOKEN))).status, 200);
    assert.equal((await send(post("/api/label", { source: "policy:fixture", id: "d1", label: "block" }, TOKEN))).status, 409);
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
    assert.equal((await send(post("/api/replay", { source: "policy:fixture", id: "d1" }, TOKEN))).status, 200);

    for (const path of ["/", "/app.js", "/api/sources", "/api/cases?source=policy:fixture", "/api/case?source=policy:fixture&id=d1", "/api/metrics?source=policy:fixture"]) {
      const res = await send(new Request(`http://127.0.0.1:4398${path}`));
      assert.equal(res.status, 200, path);
    }
    const listed = JSON.parse((await send(new Request("http://127.0.0.1:4398/api/sources"))).text);
    assert.deepEqual(listed.sources.map((s: { kind: string }) => s.kind), ["policy", "caseset"]);
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
    if (previousAudit === undefined) delete process.env.JEV_AUDIT_DIR; else process.env.JEV_AUDIT_DIR = previousAudit;
    await rm(f.root, { recursive: true, force: true });
  }
});

test("policy cases and metrics default to the newest version and can select a legacy one", async () => {
  const f = await fixture();
  try {
    const legacy = { schema: 1, type: "decision", timestamp: "2026-10-02T00:00:00Z", requestId: "d2", policy: "fixture",
      policyVersion: "v2", mode: "shadow", stage: "jev", subject: "ls", verdict: "allow", enforced: false, labels: ["block", "allow"] };
    await appendFile(join(f.paths.auditDir, "fixture", "2026-10-01.jsonl"), JSON.stringify(legacy) + "\n");
    const handler = createHandler({ paths: f.paths, token: TOKEN });
    const get = async (path: string) => JSON.parse(await (await handler(new Request(`http://127.0.0.1:4398${path}`))).text());
    const current = await get("/api/cases?source=policy:fixture");
    assert.deepEqual(current.versions, { current: "v2", versions: ["v2", "v1"] });
    assert.deepEqual(current.cases.map((c: LabCase) => c.id), ["d2"]);
    assert.deepEqual((await get("/api/cases?source=policy:fixture&version=v1")).cases.map((c: LabCase) => c.id), ["d1"]);
    assert.equal((await get("/api/metrics?source=policy:fixture")).metrics.total, 1);
    assert.equal((await get("/api/metrics?source=policy:fixture&version=v1")).metrics.total, 1);
  } finally { await rm(f.root, { recursive: true, force: true }); }
});

test("live endpoints report disabled when no key is available", async () => {
  const f = await fixture();
  try {
    const handler = createHandler({ paths: f.paths, token: TOKEN });
    const res = await handler(post("/api/playground/run", { state: {}, questions: { q: { type: "noul", instructions: "x" } } }, TOKEN));
    assert.equal(res.status, 503);
  } finally { await rm(f.root, { recursive: true, force: true }); }
});
