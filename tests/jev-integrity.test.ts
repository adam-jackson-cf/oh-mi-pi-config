import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import { after, before, test } from "node:test";
import type { ExtensionAPI, ExtensionContext } from "@oh-my-pi/pi-coding-agent";
import { commitPlans, createJevGuard, INTEGRITY_POLICY, INTEGRITY_SUSPECT_POLICY } from "../agent/extensions/jev-guard.ts";
import { fixtureMatches, parseFixtures, parseRules, parseUnifiedDiff, routeOf } from "../agent/extensions/lib/integrity.ts";
import { JEV_PINNED_MODEL, type DecisionRecord, type JevQuestions, type PolicyMode } from "../agent/extensions/lib/jev.ts";

const RULES_DIR = join(import.meta.dir, "..", "agent", "integrity");
let root = "";
let repo = "";
let auditDir = "";
let policiesFile = "";
const saved = { audit: process.env.JEV_AUDIT_DIR, policies: process.env.JEV_POLICIES_FILE, dir: process.env.JEV_INTEGRITY_DIR,
  maintainer: process.env.JEV_INTEGRITY_MAINTAINER };

before(() => {
  root = mkdtempSync(join(tmpdir(), "jev-integrity-"));
  auditDir = join(root, "audit");
  policiesFile = join(root, "policies.json");
  process.env.JEV_AUDIT_DIR = auditDir;
  process.env.JEV_POLICIES_FILE = policiesFile;
  process.env.JEV_INTEGRITY_DIR = RULES_DIR;
  delete process.env.JEV_INTEGRITY_MAINTAINER;
});
after(() => {
  const restore = (name: string, value: string | undefined) => {
    if (value === undefined) delete process.env[name];
    else process.env[name] = value;
  };
  restore("JEV_AUDIT_DIR", saved.audit);
  restore("JEV_POLICIES_FILE", saved.policies);
  restore("JEV_INTEGRITY_DIR", saved.dir);
  restore("JEV_INTEGRITY_MAINTAINER", saved.maintainer);
  rmSync(root, { recursive: true, force: true });
});

function git(...args: string[]): string {
  return execFileSync("git", ["-C", repo, ...args], { encoding: "utf8" });
}

function freshRepo(): void {
  repo = mkdtempSync(join(root, "repo-"));
  execFileSync("git", ["init", "-q", repo]);
  git("config", "user.email", "t@example.com");
  git("config", "user.name", "t");
  writeFileSync(join(repo, "app.py"), "def total(items):\n    return sum(items)\n");
  git("add", "-A");
  git("commit", "-q", "-m", "init");
}

type ToolInput = { command?: string; path?: string; content?: string };
type Outcome = { block?: boolean; reason?: string } | undefined;
type FakeEvent = { type: string; toolName: string; input: ToolInput };
type Handler = (event: FakeEvent, ctx: ExtensionContext) => Promise<Outcome>;
type JevRequestBody = { questions: JevQuestions };
type Harness = { call: (toolName: string, input: ToolInput, ctx?: ExtensionContext) => Promise<Outcome>; jevCalls: () => number;
  idle: () => Promise<void> };

function makeCtx(kind: "main" | "sub", hasUI: boolean, confirms: string[] = [], approve = false): ExtensionContext {
  // SAFETY: tests provide only the context members the extension reads.
  return Object.assign({} as ExtensionContext, {
    cwd: repo, hasUI,
    agent: { kind, id: "0-Test", name: kind === "main" ? "main" : "task", depth: 0 },
    sessionManager: { getSessionId: () => "session-1" },
    ui: { confirm: async (title: string) => { confirms.push(title); return approve; } },
  });
}

async function withGuard(mode: PolicyMode, noul: number, run: (h: Harness) => Promise<void>, suspectMode: PolicyMode = mode): Promise<void> {
  rmSync(auditDir, { recursive: true, force: true });
  writeFileSync(policiesFile, JSON.stringify({ [INTEGRITY_POLICY]: mode, [INTEGRITY_SUSPECT_POLICY]: suspectMode,
    "guard.bash": "off", "guard.write": "off", "guard.result": "off" }));
  const realFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = Object.assign(async (_url: URL | RequestInfo, init?: RequestInit) => {
    calls++;
    // SAFETY: the Jev client always sends a JSON body with a `questions` block.
    const { questions } = JSON.parse(String(init?.body)) as JevRequestBody;
    const answers = Object.fromEntries(Object.keys(questions).map((id) => [id, { type: "noul", noul }]));
    return new Response(JSON.stringify({ id: "resp_1", model: JEV_PINNED_MODEL, usage: { input_tokens: 10, output_tokens: 1, cost: 0.00001 }, answers }),
      { status: 200 });
  }, { preconnect: realFetch.preconnect });
  try {
    const guard = createJevGuard({ loadKey: async () => "or-test-key-0123456789abcdef" });
    const handlers = new Map<string, Handler>();
    // SAFETY: only `on` is exercised by the extension; the rest of ExtensionAPI is unused here.
    const pi = Object.assign({} as ExtensionAPI, { on: (name: string, handler: Handler) => { handlers.set(name, handler); } });
    await guard.extension(pi);
    await handlers.get("session_start")?.({ type: "session_start", toolName: "", input: {} }, makeCtx("sub", false));
    const toolCall = handlers.get("tool_call");
    assert.ok(toolCall);
    await run({
      call: (toolName, input, ctx = makeCtx("sub", false)) => toolCall({ type: "tool_call", toolName, input }, ctx),
      jevCalls: () => calls, idle: () => guard.idle(),
    });
  } finally {
    globalThis.fetch = realFetch;
  }
}

function records(): DecisionRecord[] {
  const dir = join(auditDir, INTEGRITY_POLICY);
  try {
    return readdirSync(dir).filter((f) => f.endsWith(".jsonl")).flatMap((f) =>
      // SAFETY: the audit writer emits DecisionRecord JSON lines.
      readFileSync(join(dir, f), "utf8").trim().split("\n").map((line) => JSON.parse(line) as DecisionRecord));
  } catch {
    return [];
  }
}

test("shipped rules: no legitimate fixture escalates without Jev, every non-miss hack is routed", () => {
  const rules = parseRules(readFileSync(join(RULES_DIR, "rules.json"), "utf8"));
  const fixtures = parseFixtures(readFileSync(join(RULES_DIR, "fixtures.json"), "utf8"));
  for (const fixture of fixtures) {
    const route = routeOf(fixtureMatches(fixture, rules));
    if (fixture.label === "legit") assert.notEqual(route, "certain", fixture.id);
    else if (!fixture.miss) assert.ok(route === "certain" || route === "suspect", `${fixture.id} routed ${route}`);
  }
});

test("rules: invalid regexes and duplicate ids are rejected", () => {
  const rule = { id: "a-rule", category: "suppression", verdict: "suspect", scope: "added", pattern: "x", rationale: "r" };
  assert.throws(() => parseRules(JSON.stringify({ rules: [rule, rule] })), /Duplicate/);
  assert.throws(() => parseRules(JSON.stringify({ rules: [{ ...rule, pattern: "(" }] })));
  assert.throws(() => parseRules(JSON.stringify({ rules: [{ ...rule, category: "made_up" }] })));
});

test("diff parser: statuses, renames and hunk lines that look like headers", () => {
  const diff = [
    "diff --git a/tests/test_a.py b/tests/test_a.py", "deleted file mode 100644", "--- a/tests/test_a.py", "+++ /dev/null",
    "@@ -1,2 +0,0 @@", "-def test_a():", "-    assert f() == 1",
    "diff --git a/old.ts b/new.ts", "similarity index 90%", "rename from old.ts", "rename to new.ts", "--- a/old.ts", "+++ b/new.ts",
    "@@ -1 +1 @@", "---- a removed line starting with dashes", "++++ an added line starting with pluses",
    "diff --git a/n.md b/n.md", "new file mode 100644", "--- /dev/null", "+++ b/n.md", "@@ -0,0 +1 @@", "+hello",
  ].join("\n");
  const changes = parseUnifiedDiff(diff);
  assert.deepEqual(changes.map((c) => [c.path, c.status]), [["tests/test_a.py", "deleted"], ["new.ts", "renamed"], ["n.md", "added"]]);
  assert.deepEqual(changes[0]?.removed, ["def test_a():", "    assert f() == 1"]);
  assert.deepEqual(changes[1]?.removed, ["--- a removed line starting with dashes"]);
  assert.deepEqual(changes[1]?.added, ["+++ an added line starting with pluses"]);
});

test("commit plans: cd, git -C, same-command staging and -a", () => {
  assert.deepEqual(commitPlans("cd sub && git add -A && git commit -m 'x -a'", "/r"), [{ repoDir: "/r/sub", stagesInCommand: true, allTracked: false }]);
  assert.deepEqual(commitPlans("git -C /other commit -am fix", "/r"), [{ repoDir: "/other", stagesInCommand: false, allTracked: true }]);
  assert.deepEqual(commitPlans("git commit -C HEAD --amend", "/r"), [{ repoDir: "/r", stagesInCommand: false, allTracked: false }]);
  assert.deepEqual(commitPlans("git status && git log", "/r"), []);
});

test("enforce: a certain hook bypass blocks a subagent and tells it to escalate, without asking Jev", async () => {
  freshRepo();
  await withGuard("enforce", 0, async (h) => {
    const outcome = await h.call("bash", { command: "git commit --no-verify -m wip" });
    assert.equal(outcome?.block, true);
    assert.match(outcome?.reason ?? "", /git-no-verify[\s\S]*orchestrator, which asks the user/);
    assert.equal(h.jevCalls(), 0);
  });
  const checked = records().find((r) => r.rule === "commit-checked");
  assert.equal(checked?.verdict, "block");
});

test("enforce: a staged suspect suppression escalates only when Jev rates it a violation", async () => {
  freshRepo();
  writeFileSync(join(repo, "app.py"), "def total(items):  # noqa\n    return sum(items)\n");
  git("add", "app.py");
  await withGuard("enforce", 0.9, async (h) => {
    const outcome = await h.call("bash", { command: "git commit -m 'tidy'" });
    assert.equal(outcome?.block, true);
    assert.match(outcome?.reason ?? "", /py-suppress \(suppression\)[\s\S]*Jev suppression 0\.90/);
  });
  await withGuard("enforce", 0.1, async (h) => {
    assert.equal(await h.call("bash", { command: "git commit -m 'tidy'" }), undefined);
    assert.equal(h.jevCalls(), 1);
  });
});

test("enforce: files staged by the same command are screened, including untracked ones", async () => {
  freshRepo();
  writeFileSync(join(repo, "test_cart.py"), "import pytest\n\n@pytest.mark.skip\ndef test_cart():\n    assert False\n");
  await withGuard("enforce", 0.8, async (h) => {
    const outcome = await h.call("bash", { command: "git add -A && git commit -m cart" });
    assert.equal(outcome?.block, true);
    assert.match(outcome?.reason ?? "", /test_cart\.py: test-skip-marker/);
  });
});

test("enforce: the main agent asks the user; a declined change is re-blocked without asking again", async () => {
  freshRepo();
  const confirms: string[] = [];
  await withGuard("enforce", 0, async (h) => {
    const ctx = makeCtx("main", true, confirms, false);
    const first = await h.call("bash", { command: "HUSKY=0 git commit -m x" }, ctx);
    assert.match(first?.reason ?? "", /The user declined/);
    const second = await h.call("bash", { command: "HUSKY=0 git commit -m x" }, ctx);
    assert.match(second?.reason ?? "", /already declined/);
    assert.equal(confirms.length, 1);
    const approved = await h.call("bash", { command: "SKIP=mypy git commit -m y" }, makeCtx("main", true, confirms, true));
    assert.equal(approved, undefined);
  });
  assert.ok(records().some((r) => r.rule === "hook-skip-env" && r.verdict === "confirm"));
});

test("enforce: once the user approves editing a guard file, later edits to that file in the session do not ask again", async () => {
  freshRepo();
  const policies = join(homedir(), ".omp", "agent", "jev-policies.json");
  const guardFile = join(homedir(), ".omp", "agent", "extensions", "jev-guard.ts");
  const confirms: string[] = [];
  await withGuard("enforce", 0, async (h) => {
    assert.equal(await h.call("write", { path: policies, content: "{}" }, makeCtx("main", true, confirms, true)), undefined);
    assert.equal(await h.call("write", { path: policies, content: "{ }" }, makeCtx("main", true, confirms, false)), undefined);
    assert.equal(confirms.length, 1);
    const other = await h.call("write", { path: guardFile, content: "x" }, makeCtx("main", true, confirms, false));
    assert.match(other?.reason ?? "", /The user declined/);
    assert.equal(confirms.length, 2);
  });
});

test("enforce: editing the guard escalates; the maintainer may edit only the rules data", async () => {
  freshRepo();
  const policies = join(homedir(), ".omp", "agent", "jev-policies.json");
  const rulesPath = join(homedir(), ".omp", "agent", "integrity", "rules.json");
  await withGuard("enforce", 0, async (h) => {
    assert.equal((await h.call("write", { path: policies, content: "{}" }))?.block, true);
    process.env.JEV_INTEGRITY_MAINTAINER = "1";
    try {
      assert.equal(await h.call("write", { path: rulesPath, content: "{}" }), undefined);
      assert.equal((await h.call("write", { path: policies, content: "{}" }))?.block, true);
    } finally {
      delete process.env.JEV_INTEGRITY_MAINTAINER;
    }
    assert.equal((await h.call("write", { path: rulesPath, content: "{}" }))?.block, true);
  });
});

test("shadow: findings are recorded but nothing is blocked", async () => {
  freshRepo();
  await withGuard("shadow", 0.9, async (h) => {
    assert.equal(await h.call("bash", { command: "git commit -n -m wip" }), undefined);
    assert.equal(await h.call("bash", { command: "bun test 2>&1 | tail -5" }), undefined);
    await h.idle();
  });
  const all = records();
  assert.ok(all.some((r) => r.rule === "git-commit-n" && r.verdict === "flag" && !r.enforced));
  assert.ok(all.some((r) => r.rule === "gate-masked-pipe" && r.verdict === "flag"));
  assert.ok(all.some((r) => r.rule === "commit-checked"));
});

test("enforce with the Jev tier in shadow: certain matches still escalate, Jev-judged ones are only recorded", async () => {
  freshRepo();
  writeFileSync(join(repo, "app.py"), "def total(items):  # noqa\n    return sum(items)\n");
  git("add", "app.py");
  await withGuard("enforce", 0.9, async (h) => {
    assert.equal(await h.call("bash", { command: "git commit -m 'tidy'" }), undefined);
    assert.equal((await h.call("bash", { command: "git commit --no-verify -m tidy" }))?.block, true);
    await h.idle();
  }, "shadow");
  assert.ok(records().some((r) => r.rule === "py-suppress" && r.verdict === "flag" && !r.enforced));
});
