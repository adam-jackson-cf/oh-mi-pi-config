#!/usr/bin/env bun
// Replays the CURRENT guard.integrity rules and Jev composition over a units file, so an evidence
// phase can measure the shipped logic. Per unit it writes the route, matched rules, Jev answers and
// the composed verdict.
// Usage: bun agent/integrity/replay.ts --units <units.json> --out <rows.json> [--fixtures] [--no-jev] [--concurrency N]
//   units.json: { "units": [{ id, kind, set?, label?, state }] }  (kind: bash | edit | commit | read)
//   --fixtures  also replays agent/integrity/fixtures.json (their pinned answers are used with --no-jev)
//   --no-jev    never calls Jev: suspect units use pinned `answers` when present, else verdict "needs_jev"
// The unit state carries no user request: the hack policy does not depend on who asked.
import { readFileSync, writeFileSync } from "node:fs";
import { z } from "zod";
import {
  changeFromHunk, directiveLines, escalatingFamilies, fixtureMatches, loadRules, matchBash, matchChange, missingAnswerIds, noulsOf,
  parseFixtures, questionsFor, readTargets, routeOf, suspectState, withScratchExemption,
  type CompiledRule, type FileChange, type NoulAnswers, type RuleMatch, type SuspectSubject,
} from "../extensions/lib/integrity.ts";
import { decide } from "../extensions/lib/jev.ts";
import { loadJevApiKey } from "../extensions/lib/jev-auth.ts";

const statusSchema = z.enum(["added", "deleted", "modified", "renamed"]);
const unitSchema = z.object({
  id: z.string(),
  kind: z.enum(["bash", "edit", "commit", "read"]),
  set: z.string().optional(),
  label: z.string().optional(),
  subject: z.string().optional(),
  state: z.object({
    command: z.string().optional(), cwd: z.string().optional(), path: z.string().optional(), tool: z.string().optional(),
    file_status: statusSchema.optional(), added_lines: z.string().optional(), removed_lines: z.string().optional(),
    other_changed_files: z.array(z.object({ path: z.string(), status: statusSchema.optional() })).optional(),
  }),
  answers: z.record(z.string(), z.number()).optional(),
});
type Unit = z.infer<typeof unitSchema>;
type Verdict = "escalate" | "allow" | "error" | "needs_jev";
type Row = {
  id: string; set: string; label: string | null; route: string; rules: string[]; families: string[]; answers: NoulAnswers;
  missing: string[]; verdict: Verdict;
};
type Prepared = { unit: Unit; matches: RuleMatch[]; subject: SuspectSubject };
type Args = { units?: string; out?: string; fixtures: boolean; noJev: boolean; concurrency: number };

function parseArgs(argv: string[]): Args {
  const args: Args = { fixtures: false, noJev: false, concurrency: 8 };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--units") args.units = argv[++i];
    else if (arg === "--out") args.out = argv[++i];
    else if (arg === "--fixtures") args.fixtures = true;
    else if (arg === "--no-jev") args.noJev = true;
    else if (arg === "--concurrency") args.concurrency = Number(argv[++i]);
    else throw new Error(`Unknown argument: ${arg}`);
  }
  return args;
}

function prepare(unit: Unit, rules: CompiledRule[]): Prepared[] {
  const state = unit.state;
  const cwd = state.cwd ?? "";
  if (unit.kind === "bash") {
    const command = state.command ?? "";
    const subject: SuspectSubject = { command, directives: [], otherFiles: [], cwd };
    return [{ unit, matches: withScratchExemption(subject, matchBash(command, rules)), subject }];
  }
  if (unit.kind === "read") {
    return readTargets(state.path ?? "", state.tool ?? "read", cwd || "/").map((path) => {
      const change: FileChange = { path, status: "modified", added: [], removed: [] };
      return { unit, matches: matchChange(change, rules, "read"), subject: { change, directives: [], otherFiles: [], cwd } };
    });
  }
  const path = state.path ?? unit.subject ?? "";
  const change: FileChange = {
    path, status: state.file_status ?? "modified", added: (state.added_lines ?? "").split("\n"), removed: (state.removed_lines ?? "").split("\n"),
  };
  const matches = matchChange(change, rules, unit.kind === "commit" ? "commit" : "edit")
    .filter((match) => unit.kind === "commit" || match.category === "guard_tamper");
  const subject: SuspectSubject = {
    change, directives: directiveLines(change, matches, rules), cwd,
    otherFiles: (state.other_changed_files ?? []).map((other) => ({ path: other.path, status: other.status ?? "modified" })),
  };
  return [{ unit, matches: withScratchExemption(subject, matches), subject }];
}

function fixtureUnits(rules: CompiledRule[]): Prepared[] {
  const fixtures = parseFixtures(readFileSync(new URL("./fixtures.json", import.meta.url), "utf8"));
  return fixtures.map((fixture) => {
    const matches = fixtureMatches(fixture, rules);
    const cwd = fixture.cwd ?? "";
    const change = fixture.kind === "change" ? changeFromHunk(fixture.path ?? "", fixture.status ?? "modified", fixture.text) : undefined;
    const subject: SuspectSubject = change
      ? { change, directives: directiveLines(change, matches, rules), otherFiles: fixture.others ?? [], cwd }
      : { command: fixture.kind === "bash" ? fixture.text : undefined, directives: [], otherFiles: [], cwd };
    const unit: Unit = { id: fixture.id, kind: "bash", set: "fixture", label: fixture.label, state: {}, answers: fixture.answers };
    return { unit, matches, subject };
  });
}

async function main(): Promise<void> {
  const args = parseArgs(process.argv.slice(2));
  if (!args.out || (!args.units && !args.fixtures)) throw new Error("usage: bun agent/integrity/replay.ts --units <units.json> --out <rows.json> [--fixtures] [--no-jev]");
  const rules = await loadRules(import.meta.dir);
  const key = args.noJev ? undefined : await loadJevApiKey();
  if (!args.noJev && !key) throw new Error("no Jev key; pass --no-jev for a deterministic-only replay");
  const prepared: Prepared[] = [];
  if (args.units) {
    const { units } = z.object({ units: z.array(unitSchema) }).parse(JSON.parse(readFileSync(args.units, "utf8")));
    prepared.push(...units.flatMap((unit) => prepare(unit, rules)));
  }
  if (args.fixtures) prepared.push(...fixtureUnits(rules));

  const rows: Row[] = [];
  let next = 0;
  const worker = async (): Promise<void> => {
    while (next < prepared.length) {
      const item = prepared[next++];
      if (!item) return;
      const { unit, matches, subject } = item;
      const route = routeOf(matches);
      const base = {
        id: unit.id, set: unit.set ?? "units", label: unit.label ?? null, route, rules: matches.map((match) => match.ruleId),
      };
      if (route !== "suspect") {
        rows.push({ ...base, families: [], answers: {}, missing: [], verdict: route === "certain" ? "escalate" : "allow" });
        continue;
      }
      const questions = questionsFor(matches);
      let answers: NoulAnswers | undefined = unit.answers;
      if (!answers && key) {
        const judged = await decide(key, suspectState(subject, matches), questions, { timeoutMs: 30_000 });
        answers = judged.ok ? noulsOf(judged.answers) : undefined;
        if (!judged.ok) {
          rows.push({ ...base, families: [], answers: {}, missing: [], verdict: "error" });
          continue;
        }
      }
      if (!answers) {
        rows.push({ ...base, families: [], answers: {}, missing: Object.keys(questions), verdict: "needs_jev" });
        continue;
      }
      const missing = missingAnswerIds(matches, answers);
      if (missing.length > 0) {
        rows.push({ ...base, families: [], answers, missing, verdict: "error" });
        continue;
      }
      const families = escalatingFamilies(subject, matches, answers);
      rows.push({ ...base, families, answers, missing: [], verdict: families.length > 0 ? "escalate" : "allow" });
    }
  };
  await Promise.all(Array.from({ length: args.concurrency }, worker));
  rows.sort((a, b) => a.id.localeCompare(b.id));
  writeFileSync(args.out, JSON.stringify(rows, null, 1));
  const counts = new Map<string, number>();
  for (const row of rows) counts.set(`${row.set} ${row.route} ${row.verdict}`, (counts.get(`${row.set} ${row.route} ${row.verdict}`) ?? 0) + 1);
  for (const [name, count] of [...counts].sort()) console.log(`${name}: ${count}`);
}

await main();
