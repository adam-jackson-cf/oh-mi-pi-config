import assert from "node:assert/strict";
import { appendFileSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, test } from "node:test";
import GuardrailsReporter from "../agent/extensions/dynaflows/engine/guardrails/commands/adapters/vitest/reporter.ts";
import type { GuardSettings } from "../agent/extensions/dynaflows/engine/guardrails/commands/adapters/vitest/runtime.ts";

const scratches: string[] = [];
const exitCode = process.exitCode ?? 0;

afterEach(() => {
  process.exitCode = exitCode;
  for (const scratch of scratches.splice(0)) rmSync(scratch, { recursive: true, force: true });
});

function reportPath(): string {
  const scratch = mkdtempSync(join(tmpdir(), "vitest-reporter-"));
  scratches.push(scratch);
  return join(scratch, "t1.xml");
}

function project() {
  const provided = new Map<string, GuardSettings>();
  return {
    provided,
    config: {
      setupFiles: ["./setup.ts"],
      sequence: { setupFiles: "parallel" },
      env: Object.fromEntries([["KEEP", "1"]]),
      cache: true,
      fsModuleCache: true,
    },
    provide(key: string, value: GuardSettings) {
      provided.set(key, value);
    },
  };
}

type Outcome = { state: string; errors?: { message?: string }[]; note?: string };

function testCase(name: string, result: Outcome, fails = false) {
  return {
    fullName: `suite > ${name}`,
    name,
    options: { mode: "run", fails },
    result: () => result,
    diagnostic: () => ({ retryCount: 1, duration: 250 }),
  };
}

function testModule(tests: ReturnType<typeof testCase>[], errors: { message?: string }[] = []) {
  return {
    type: "module",
    moduleId: join(process.cwd(), "src", "cart.test.ts"),
    errors: () => errors,
    children: {
      allTests: () => tests,
      allSuites: () => [{ type: "suite", errors: () => [{ message: "beforeAll failed" }] }],
    },
  };
}

describe("Vitest guardrails reporter", () => {
  test("refuses a run without a tier or with a report inside the repository", () => {
    assert.throws(() => new GuardrailsReporter({ tier: "t9", report: reportPath() }).onInit({ projects: [] }), /needs a tier/);
    assert.throws(() => new GuardrailsReporter({ tier: "t1", report: "relative.xml" }).onInit({ projects: [] }), /needs a tier/);
    assert.throws(
      () => new GuardrailsReporter({ tier: "t1", report: join(process.cwd(), "report.xml") }).onInit({ projects: [] }),
      /outside the target repository/,
    );
  });

  test("puts its guards first and gives every project a scratch home once", () => {
    const report = reportPath();
    const reporter = new GuardrailsReporter({ tier: "t1", report });
    const first = project();
    reporter.onInit({ projects: [first] });
    reporter.onTestRunStart([{ project: first }]);
    const [guards, setup] = first.config.setupFiles;
    assert.match(guards ?? "", /vitest\/guards\.ts$/);
    assert.equal(setup, "./setup.ts");
    assert.equal(first.config.setupFiles.length, 2);
    assert.equal(first.config.sequence.setupFiles, "list");
    assert.equal(first.config.cache, false);
    assert.equal(first.config.fsModuleCache, false);
    assert.equal(first.config.env["KEEP"], "1");
    const settings = first.provided.get("omp.guardrails");
    assert.ok(settings);
    assert.equal(settings.tier, "t1");
    assert.ok(first.config.env["HOME"]?.startsWith(settings.allowed[0] ?? "-"));
    const second = project();
    reporter.onTestRunStart([{ project: second }]);
    assert.ok(second.provided.has("omp.guardrails"));
  });

  test("records every outcome, suite error and guard violation in the JUnit report", () => {
    const report = reportPath();
    const reporter = new GuardrailsReporter({ tier: "t1", report });
    reporter.onInit({ projects: [] });
    appendFileSync(reporter.events, [
      JSON.stringify({ kind: "armed", guards: ["fast-tier", "user-data"] }),
      JSON.stringify({ kind: "armed", guards: ["fast-tier"] }),
      JSON.stringify({ kind: "violation", reason: "spawned <git>" }),
    ].join("\n") + "\n");
    reporter.onTestRunEnd(
      [testModule([
        testCase("passes", { state: "passed" }),
        testCase("fails", { state: "failed", errors: [{ message: "expected 1 & got 2" }] }),
        testCase("skips", { state: "skipped", note: "later" }),
        testCase("hangs", { state: "pending" }),
        testCase("known bug", { state: "passed" }, true),
      ], [{ message: "import failed" }])],
      [{ message: "unhandled rejection" }],
    );
    const xml = readFileSync(report, "utf8");
    assert.match(xml, /<testsuite tests="9" failures="2" errors="4"/);
    assert.match(xml, /name="omp.guards" value="fast-tier"/);
    assert.match(xml, /name="omp.exit_code" value="1"/);
    assert.match(xml, /file="src\/cart.test.ts" classname="src\/cart.test.ts" name="suite &gt; passes" time="0.25"><properties><property name="retries" value="1"\/><\/properties><\/testcase>/);
    assert.match(xml, /<failure message="expected 1 &amp; got 2"\/>/);
    assert.match(xml, /<skipped message="later"\/>/);
    assert.match(xml, /<error message="test did not finish"\/>/);
    assert.match(xml, /<skipped message="known bug" type="xfail"\/>/);
    assert.match(xml, /name="guardrails::module".*message="import failed"/);
    assert.match(xml, /name="guardrails::suite".*message="beforeAll failed"/);
    assert.match(xml, /name="guardrails::unhandled".*message="unhandled rejection"/);
    assert.match(xml, /name="guardrails::violation".*message="spawned &lt;git&gt;"/);
    assert.equal(process.exitCode, 1);
  });

  test("an empty run fails and a clean run passes", () => {
    const empty = new GuardrailsReporter({ tier: "t2", report: reportPath() });
    empty.onInit({ projects: [] });
    empty.onTestRunEnd([]);
    assert.match(readFileSync(empty.options.report, "utf8"), /name="omp.exit_code" value="1"/);
    process.exitCode = exitCode;
    const clean = new GuardrailsReporter({ tier: "t2", report: reportPath() });
    clean.onInit({ projects: [] });
    clean.onTestRunEnd([testModule([testCase("passes", { state: "passed" })])].map(module => ({
      ...module,
      children: { ...module.children, allSuites: () => [] },
    })));
    assert.match(readFileSync(clean.options.report, "utf8"), /name="omp.exit_code" value="0"/);
    assert.equal(process.exitCode ?? 0, exitCode);
  });
});
