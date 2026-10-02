#!/usr/bin/env bun
// Deterministic merge gate for the guard.integrity rule and fixture files.
// Usage: bun integrity-regress.ts [--base <ref>] [--scope-check] [--json <out>]
// Exit 0 = every check passed, 1 = at least one failure (listed in the table output).
import { spawnSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import {
  fixtureMatches, parseFixtures, parseRules, routeOf,
  type CompiledRule, type IntegrityFixture, type Route,
} from "../../../extensions/lib/integrity.ts";

const RULES_PATH = "agent/integrity/rules.json";
const FIXTURES_PATH = "agent/integrity/fixtures.json";
const ALLOWED = new Set([RULES_PATH, FIXTURES_PATH]);
const ROUTES: Route[] = ["certain", "suspect", "record", "none"];
const LABELS = ["hack", "legit"] as const;

type Side = { rules: CompiledRule[]; fixtures: IntegrityFixture[] };
type RouteCounts = Record<Route, number>;
type Counts = { hack: RouteCounts; legit: RouteCounts };
type GitResult = { ok: boolean; out: string };
type CliArgs = { base: string; scopeCheck: boolean; json?: string };
type Routing = { baseRoutes: Map<string, Route>; headRoutes: Map<string, Route> };
type Report = {
  base: string; failures: string[]; baseCounts: Counts; headCounts: Counts;
  baseFixtures: number; headFixtures: number; baseRules: number; headRules: number;
};

function git(args: string[], cwd?: string): GitResult {
  const result = spawnSync("git", args, { cwd, encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
  return { ok: result.status === 0, out: result.stdout ?? "" };
}

function parseArgs(argv: string[]): CliArgs {
  let base = "origin/main";
  let scopeCheck = false;
  let json: string | undefined;
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--base") base = argv[++i] ?? base;
    else if (arg === "--json") json = argv[++i];
    else if (arg === "--scope-check") scopeCheck = true;
    else throw new Error(`Unknown argument: ${arg}`);
  }
  return { base, scopeCheck, json };
}

function emptyCounts(): Counts {
  return {
    hack: { certain: 0, suspect: 0, record: 0, none: 0 },
    legit: { certain: 0, suspect: 0, record: 0, none: 0 },
  };
}

function routes(side: Side): Map<string, Route> {
  const map = new Map<string, Route>();
  for (const fixture of side.fixtures) map.set(fixture.id, routeOf(fixtureMatches(fixture, side.rules)));
  return map;
}

function tally(side: Side, routed: Map<string, Route>): Counts {
  const counts = emptyCounts();
  for (const fixture of side.fixtures) counts[fixture.label][routed.get(fixture.id) ?? "none"]++;
  return counts;
}

function loadHead(top: string, failures: string[]): Side {
  const side: Side = { rules: [], fixtures: [] };
  try {
    side.rules = parseRules(readFileSync(join(top, RULES_PATH), "utf8"));
  } catch (error) {
    failures.push(`head ${RULES_PATH} invalid: ${String(error).slice(0, 300)}`);
  }
  try {
    side.fixtures = parseFixtures(readFileSync(join(top, FIXTURES_PATH), "utf8"));
  } catch (error) {
    failures.push(`head ${FIXTURES_PATH} invalid: ${String(error).slice(0, 300)}`);
  }
  return side;
}

/** A file absent at the base ref (first run) is an empty base; present but unparseable fails. */
function loadBase(top: string, ref: string, failures: string[]): Side {
  const side: Side = { rules: [], fixtures: [] };
  const rules = git(["show", `${ref}:${RULES_PATH}`], top);
  if (rules.ok) {
    try {
      side.rules = parseRules(rules.out);
    } catch (error) {
      failures.push(`base ${RULES_PATH} invalid: ${String(error).slice(0, 300)}`);
    }
  }
  const fixtures = git(["show", `${ref}:${FIXTURES_PATH}`], top);
  if (fixtures.ok) {
    try {
      side.fixtures = parseFixtures(fixtures.out);
    } catch (error) {
      failures.push(`base ${FIXTURES_PATH} invalid: ${String(error).slice(0, 300)}`);
    }
  }
  return side;
}

const isRouted = (route: Route | undefined): boolean => route === "certain" || route === "suspect";
const contentKey = (f: IntegrityFixture): string =>
  JSON.stringify([f.kind, f.text, f.path ?? null, f.status ?? null, f.label]);

function compare(base: Side, head: Side, failures: string[]): Routing {
  const baseRoutes = routes(base);
  const headRoutes = routes(head);
  const headById = new Map(head.fixtures.map((f) => [f.id, f]));
  const baseIds = new Set(base.fixtures.map((f) => f.id));
  for (const fixture of base.fixtures) {
    const next = headById.get(fixture.id);
    if (!next) failures.push(`fixture removed: ${fixture.id}`);
    else if (contentKey(next) !== contentKey(fixture)) failures.push(`fixture content or label changed: ${fixture.id}`);
  }
  for (const fixture of head.fixtures) {
    const route = headRoutes.get(fixture.id);
    if (fixture.label === "legit" && route === "certain") failures.push(`legit fixture routes certain: ${fixture.id}`);
    if (fixture.label === "hack" && baseIds.has(fixture.id) && isRouted(baseRoutes.get(fixture.id)) && !isRouted(route)) {
      failures.push(`hack no longer routed (was ${baseRoutes.get(fixture.id)}): ${fixture.id}`);
    }
    if (fixture.label === "hack" && !baseIds.has(fixture.id) && !fixture.miss && !isRouted(route)) {
      failures.push(`new hack fixture not routed and not marked miss: ${fixture.id}`);
    }
  }
  return { baseRoutes, headRoutes };
}

function scopeCheck(top: string, ref: string, failures: string[]): void {
  const committed = git(["diff", "--name-only", `${ref}...HEAD`], top);
  if (!committed.ok) failures.push(`scope-check: git diff ${ref}...HEAD failed`);
  const working = git(["diff", "--name-only", "HEAD"], top).out;
  const untracked = git(["ls-files", "--others", "--exclude-standard"], top).out;
  const touched = new Set(`${committed.out}\n${working}\n${untracked}`.split("\n").filter(Boolean));
  for (const path of touched) if (!ALLOWED.has(path)) failures.push(`scope-check: touched file outside the data files: ${path}`);
}

function countsTable(report: Report): string {
  const rows = ["| label | side | " + ROUTES.join(" | ") + " |", "|---|---|" + ROUTES.map(() => "---:").join("|") + "|"];
  for (const label of LABELS) {
    for (const [name, counts] of [["base", report.baseCounts], ["head", report.headCounts]] as const) {
      rows.push(`| ${label} | ${name} | ${ROUTES.map((r) => counts[label][r]).join(" | ")} |`);
    }
  }
  return rows.join("\n");
}

function render(report: Report): string {
  const lines = [
    `### Integrity regression (base \`${report.base}\`)`,
    "",
    `Rules: base ${report.baseRules}, head ${report.headRules}. Fixtures: base ${report.baseFixtures}, head ${report.headFixtures}.`,
    "",
    countsTable(report),
    "",
    report.failures.length === 0 ? "Result: **pass**" : `Result: **FAIL** (${report.failures.length})`,
    ...report.failures.map((failure) => `- ${failure}`),
  ];
  return lines.join("\n");
}

function main(): number {
  const { base: ref, scopeCheck: checkScope, json } = parseArgs(process.argv.slice(2));
  const top = git(["rev-parse", "--show-toplevel"]).out.trim();
  if (!top) throw new Error("Not inside a git repository.");
  const failures: string[] = [];
  const head = loadHead(top, failures);
  const base = loadBase(top, ref, failures);
  const { baseRoutes, headRoutes } = compare(base, head, failures);
  if (checkScope) scopeCheck(top, ref, failures);
  const report: Report = {
    base: ref, failures,
    baseCounts: tally(base, baseRoutes), headCounts: tally(head, headRoutes),
    baseFixtures: base.fixtures.length, headFixtures: head.fixtures.length,
    baseRules: base.rules.length, headRules: head.rules.length,
  };
  console.log(render(report));
  if (json) writeFileSync(json, JSON.stringify(report, null, 2));
  return failures.length === 0 ? 0 : 1;
}

process.exit(main());
