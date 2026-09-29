import assert from "node:assert/strict";
import { mkdtemp, readdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { setImmediate as nextTick } from "node:timers/promises";
import { test } from "node:test";
import {
  createJevSubagentPolicy, DEEP_REVIEW_MODEL, EFFORT_POLICY, LIGHT_REVIEW_MODEL, REVIEW_POLICY,
} from "../agent/extensions/jev-subagent-policy";

type SpawnOut = { model?: string | string[]; note?: string };
type TaskItem = { name?: string; agent?: string; task: string };
type SpawnFixture = { agent: string; modelRole?: string; patterns: string[]; spawnKey?: string };
type ToolCallFixture = {
  type: "tool_call"; toolName: "task"; toolCallId: string; input: { context?: string; tasks: TaskItem[] };
};
type AnswerFixture =
  | { type: "score"; score: number; probabilities: Record<string, number>; confidence: number }
  | { type: "noul"; noul: number };
type ModelRef = { id: string };
type FakeCtx = {
  cwd: string; sessionManager: { getSessionId: () => string }; agent: { kind: "sub" };
  models: { resolve: (spec: string) => ModelRef | undefined; family: (model: ModelRef) => string };
};
type Handler = (event: SpawnFixture | ToolCallFixture, ctx: FakeCtx) => Promise<SpawnOut | undefined> | SpawnOut | undefined;
type Mode = "shadow" | "enforce";
type Repo = { numstat: string; status: string; diff: string; code?: number; untrackedDiff?: string };
type Fixture = { handlers: Map<string, Handler>; ctx: FakeCtx; audit: string; cleanup: () => Promise<void> };

const KEY = "sk-or-test-placeholder-key";
const NO_REPO: Repo = { numstat: "", status: "", diff: "", code: 128 };
const CLAUDE: ModelRef = { id: "claude-sonnet-5-5" };

function fakeModels(author: ModelRef): FakeCtx["models"] {
  const known = new Map<string, ModelRef>([
    ["@task", author], ["openai-codex/gpt-6-luna", { id: "gpt-6-luna" }], ["openai-codex/gpt-6-sol", { id: "gpt-6-sol" }],
  ]);
  return {
    resolve: (spec) => known.get(spec),
    family: (model) => (model.id.startsWith("claude") ? "claude" : "gpt"),
  };
}

async function setup(mode: Mode, repo: Repo, author: { id: string } = CLAUDE): Promise<Fixture> {
  const dir = await mkdtemp(join(tmpdir(), "jev-subagent-"));
  const policies = join(dir, "policies.json");
  await writeFile(policies, JSON.stringify({ [REVIEW_POLICY]: mode, [EFFORT_POLICY]: mode }));
  process.env.JEV_AUDIT_DIR = join(dir, "audit");
  process.env.JEV_POLICIES_FILE = policies;
  const handlers = new Map<string, Handler>();
  const pi = {
    on: (name: string, handler: Handler) => { handlers.set(name, handler); },
    exec: async (_command: string, args: string[]) => {
      const joined = args.join(" ");
      if (joined.startsWith("diff --no-index")) return { stdout: repo.untrackedDiff ?? "", stderr: "", code: 1 };
      const out = joined === "diff HEAD --numstat" ? repo.numstat
        : joined === "status --porcelain --untracked-files=all" ? repo.status : repo.diff;
      return { stdout: out, stderr: "", code: repo.code ?? 0 };
    },
  };
  // SAFETY: the fake implements only the ExtensionAPI members the extension calls.
  await createJevSubagentPolicy(pi as never, KEY);
  const ctx = {
    cwd: dir, sessionManager: { getSessionId: () => "session-1" }, agent: { kind: "sub" }, models: fakeModels(author),
  };
  return {
    handlers, ctx, audit: join(dir, "audit"),
    cleanup: async () => { delete process.env.JEV_AUDIT_DIR; delete process.env.JEV_POLICIES_FILE; await rm(dir, { recursive: true, force: true }); },
  };
}

type AuditRow = { rule?: string; verdict?: string; stage?: string; subject?: string; enforced?: boolean; mode?: string };

async function records(fixture: Fixture, policy: string): Promise<AuditRow[]> {
  for (let attempt = 0; attempt < 200_000; attempt++) {
    const files = await readdir(join(fixture.audit, policy)).catch(() => []);
    const name = files.find(file => file !== "labels.jsonl");
    if (name) {
      const text = await readFile(join(fixture.audit, policy, name), "utf8");
      const lines = text.split("\n").filter(Boolean);
      if (lines.length > 0) return lines.map(line => JSON.parse(line));
    }
    await nextTick();
  }
  return [];
}

function jevResponse(answers: Record<string, AnswerFixture>) {
  return new Response(JSON.stringify({
    id: "resp-1", model: "typesafe/jev-1.13-20260917", answers, usage: { input_tokens: 10, output_tokens: 2, cost: 0.00001 },
  }));
}
const score = (value: number, confidence = 0.9): AnswerFixture => ({ type: "score", score: value, probabilities: {}, confidence });
const noul = (value: number): AnswerFixture => ({ type: "noul", noul: value });
const reviewAnswers = (sec: number, cx: number, bh: number, missing: number, confidence = 0.9) => ({
  security_risk: score(sec, confidence), complexity: score(cx, confidence), behaviour_change: score(bh, confidence),
  missing_tests: noul(missing),
});

async function withFetch(body: () => Response | Promise<Response>, run: (calls: string[]) => Promise<void>): Promise<void> {
  const original = globalThis.fetch;
  const calls: string[] = [];
  const stub = async (_url: string | URL | Request, init?: RequestInit) => { calls.push(String(init?.body)); return body(); };
  // SAFETY: the extension only calls fetch(url, init); the stub returns a real Response and is restored below.
  globalThis.fetch = stub as typeof fetch;
  try { await run(calls); } finally { globalThis.fetch = original; }
}

const CODE_REPO: Repo = {
  numstat: "3\t1\tsrc/app.ts", status: " M src/app.ts", diff: `diff --git a/src/app.ts\n+ const token = "${KEY}";\n`,
};

function spawn(fixture: Fixture, event: SpawnFixture) {
  const handler = fixture.handlers.get("before_subagent_spawn");
  assert.ok(handler);
  return handler(event, fixture.ctx);
}
function taskCall(fixture: Fixture, toolCallId: string, tasks: TaskItem[], context?: string) {
  return fixture.handlers.get("tool_call")?.({ type: "tool_call", toolName: "task", toolCallId, input: { context, tasks } }, fixture.ctx);
}
const reviewEvent = (spawnKey = "rev") => ({ agent: "reviewer", modelRole: "reviewer", patterns: ["anthropic/x:high"], spawnKey });
const taskEvent = (spawnKey: string, patterns = ["anthropic/claude-sonnet-5-5:low"]) =>
  ({ agent: "task", modelRole: "task", patterns, spawnKey });

test("review: deterministic rules settle without calling Jev", async () => {
  const cases: [Repo, string, string, string | undefined][] = [
    [NO_REPO, "standard", "no-working-tree-diff", undefined],
    [{ numstat: "", status: "", diff: "" }, "standard", "no-working-tree-diff", undefined],
    [{ numstat: "1\t1\tsrc/auth/login.ts", status: " M src/auth/login.ts", diff: "" }, "deep", "sensitive-path", DEEP_REVIEW_MODEL],
    [{ numstat: "", status: "?? .github/workflows/ci.yml", diff: "" }, "deep", "sensitive-path", DEEP_REVIEW_MODEL],
    [{ numstat: "1\t0\tREADME.md", status: " M README.md", diff: "" }, "light", "docs-only", LIGHT_REVIEW_MODEL],
  ];
  for (const [repo, verdict, rule, model] of cases) {
    const fixture = await setup("enforce", repo);
    try {
      await withFetch(() => { throw new Error("Jev must not be called"); }, async (calls) => {
        const result = await spawn(fixture, reviewEvent());
        assert.equal(result?.model, model);
        assert.equal(calls.length, 0);
      });
      const [record] = await records(fixture, REVIEW_POLICY);
      assert.equal(record?.rule, rule);
      assert.equal(record?.verdict, verdict);
      assert.equal(record?.stage, "deterministic");
    } finally { await fixture.cleanup(); }
  }
});

test("review: Jev risk maps to light, standard and deep with exact model and note", async () => {
  const cases: [Record<string, AnswerFixture>, string, SpawnOut | undefined][] = [
    [reviewAnswers(0, 0, 0, 0.1), "light", { model: LIGHT_REVIEW_MODEL, note: "review-triage: light (risk 0.00)" }],
    [reviewAnswers(1, 1, 0, 0.1), "standard", undefined],
    [reviewAnswers(0, 0, 0, 0.1, 0.4), "standard", undefined],
    [reviewAnswers(0, 0, 0, 0.9), "standard", undefined],
    [reviewAnswers(2, 2, 0, 0.1), "deep", { model: DEEP_REVIEW_MODEL, note: "review-triage: deep (risk 0.75)" }],
  ];
  for (const [answers, verdict, expected] of cases) {
    const fixture = await setup("enforce", CODE_REPO);
    try {
      await withFetch(() => jevResponse(answers), async (calls) => {
        await taskCall(fixture, "t1", [{ name: "rev", agent: "reviewer", task: "Review the auth flow" }]);
        assert.deepEqual(await spawn(fixture, reviewEvent()), expected);
        assert.equal(calls.length, 1);
        assert.ok(!calls[0]?.includes(KEY), "credential must be redacted before Jev");
        assert.ok(calls[0]?.includes("Review the auth flow"));
      });
      const [record] = await records(fixture, REVIEW_POLICY);
      assert.equal(record?.verdict, verdict);
      assert.equal(record?.stage, "jev");
      assert.equal(record?.subject, "reviewer:rev");
      assert.equal(record?.enforced, expected !== undefined);
    } finally { await fixture.cleanup(); }
  }
});

test("review: family guard refuses same-family routing and records it", async () => {
  const fixture = await setup("enforce", { numstat: "1\t1\tsrc/auth.ts", status: " M src/auth.ts", diff: "" }, { id: "gpt-6-terra" });
  try {
    assert.equal(await spawn(fixture, reviewEvent()), undefined);
    const [record] = await records(fixture, REVIEW_POLICY);
    assert.equal(record?.rule, "family-guard");
    assert.equal(record?.enforced, false);
  } finally { await fixture.cleanup(); }
});

test("effort: correlates by name and by toolCallId:index", async () => {
  const fixture = await setup("enforce", NO_REPO);
  try {
    await withFetch(() => jevResponse({ openness: score(2), has_plan: noul(0.1) }), async (calls) => {
      await taskCall(fixture, "call-9", [{ task: "first unnamed" }, { name: "named", task: "Design the cache layer" }], "batch context");
      assert.ok(await spawn(fixture, taskEvent("call-9:0")));
      assert.ok(await spawn(fixture, taskEvent("named")));
      assert.equal(await spawn(fixture, taskEvent("unknown")), undefined);
      assert.equal(calls.length, 2);
      assert.ok(calls[0]?.includes("first unnamed"));
      assert.ok(calls[1]?.includes("Design the cache layer") && calls[1]?.includes("batch context"));
    });
  } finally { await fixture.cleanup(); }
});

test("effort: short explicit numbered plan keeps without calling Jev", async () => {
  const fixture = await setup("enforce", NO_REPO);
  try {
    await withFetch(() => { throw new Error("Jev must not be called"); }, async () => {
      await taskCall(fixture, "c", [{ name: "planned", task: "1. edit a.ts\n2) edit b.ts" }]);
      assert.equal(await spawn(fixture, taskEvent("planned")), undefined);
    });
    const [record] = await records(fixture, EFFORT_POLICY);
    assert.equal(record?.rule, "explicit-short-plan");
    assert.equal(record?.verdict, "keep");
  } finally { await fixture.cleanup(); }
});

test("effort: raise swaps only the effort suffix; thresholds decide", async () => {
  const cases: [number, number, string][] = [[1.5, 0.9, "raise"], [1.0, 0.1, "raise"], [1.0, 0.5, "keep"], [0.4, 0.0, "keep"]];
  for (const [openness, hasPlan, verdict] of cases) {
    const fixture = await setup("enforce", NO_REPO);
    try {
      await withFetch(() => jevResponse({ openness: score(openness), has_plan: noul(hasPlan) }), async () => {
        await taskCall(fixture, "c", [{ name: "job", task: "Fix the flaky sync" }]);
        const result = await spawn(fixture, taskEvent("job", ["anthropic/claude-sonnet-5-5:low", "openai/gpt-x:minimal", "plain/model"]));
        const expected = verdict === "raise"
          ? { model: ["anthropic/claude-sonnet-5-5:medium", "openai/gpt-x:medium", "plain/model"], note: `effort: medium (openness ${openness.toFixed(1)})` }
          : undefined;
        assert.deepEqual(result, expected);
      });
      const [record] = await records(fixture, EFFORT_POLICY);
      assert.equal(record?.verdict, verdict);
    } finally { await fixture.cleanup(); }
  }
});

test("effort: patterns without an effort suffix are left alone", async () => {
  const fixture = await setup("enforce", NO_REPO);
  try {
    await withFetch(() => jevResponse({ openness: score(2), has_plan: noul(0) }), async () => {
      await taskCall(fixture, "c", [{ name: "job", task: "Investigate" }]);
      assert.equal(await spawn(fixture, taskEvent("job", [])), undefined);
    });
    const [record] = await records(fixture, EFFORT_POLICY);
    assert.equal(record?.rule, "no-patterns");
    assert.equal(record?.enforced, false);
  } finally { await fixture.cleanup(); }
});

test("shadow: returns undefined immediately and still records", async () => {
  const fixture = await setup("shadow", NO_REPO);
  try {
    await withFetch(() => jevResponse({ openness: score(2), has_plan: noul(0) }), async () => {
      await taskCall(fixture, "c", [{ name: "job", task: "Investigate the outage" }]);
      assert.equal(await spawn(fixture, taskEvent("job")), undefined);
    });
    const [record] = await records(fixture, EFFORT_POLICY);
    assert.equal(record?.verdict, "raise");
    assert.equal(record?.mode, "shadow");
    assert.equal(record?.enforced, false);
  } finally { await fixture.cleanup(); }
});

test("Jev failure degrades to no change and records jev_error", async () => {
  const fixture = await setup("enforce", CODE_REPO);
  try {
    await withFetch(() => new Response("nope", { status: 500 }), async () => {
      await taskCall(fixture, "c", [{ name: "job", task: "Investigate" }]);
      assert.equal(await spawn(fixture, reviewEvent()), undefined);
      assert.equal(await spawn(fixture, taskEvent("job")), undefined);
    });
    assert.equal((await records(fixture, REVIEW_POLICY))[0]?.stage, "jev_error");
    assert.equal((await records(fixture, EFFORT_POLICY))[0]?.stage, "jev_error");
  } finally { await fixture.cleanup(); }
});

test("review: untracked file content reaches the Jev state", async () => {
  const repo: Repo = {
    numstat: "", status: "?? src/config.ts\n?? src/newdir/", diff: "",
    untrackedDiff: 'diff --git a/src/config.ts b/src/config.ts\n+const marker = 1;\n+password = "x"\n',
  };
  const fixture = await setup("enforce", repo);
  try {
    await withFetch(() => jevResponse(reviewAnswers(1, 1, 0, 0.1)), async (calls) => {
      await spawn(fixture, reviewEvent());
      assert.equal(calls.length, 1);
      // The secret value is redacted before Jev; the file content itself must still arrive.
      assert.ok(calls[0]?.includes("const marker = 1;") && calls[0]?.includes('password = \\"[REDACTED]'));
    });
  } finally { await fixture.cleanup(); }
});
