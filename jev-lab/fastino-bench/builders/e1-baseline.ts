// E1 baseline dataset builder: Jev-format cases from the repo's own guards, offline and deterministic.
//   bun builders/e1-baseline.ts          (from jev-lab/fastino-bench)
// guard.bash / guard.write / guard.result: drives the real jev-guard.ts (enforce mode, isolated audit dir) over the
//   2026-10-02 promotion-experiment items with globalThis.fetch replaced by a stub that records the exact
//   {state, questions} body sent to Jev and returns a valid fake answer. Items decided without Jev make no case.
// guard.integrity: drives the CURRENT integrity library (rules, routing, suspectState, questionsFor, masking as the
//   guard does it) over the stored units and fixtures; the shipped composition is exported as a truth table.
// No network, no model calls. Writes datasets/e1-baseline/<dataset>.jsonl only.
import type { ExtensionAPI, ExtensionContext } from "@oh-my-pi/pi-coding-agent";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { z } from "zod";
import { BASH_QUESTIONS, createJevGuard, RESULT_QUESTIONS, WRITE_QUESTIONS } from "../../../agent/extensions/jev-guard.ts";
import {
  changeFromHunk, directiveLines, escalatingFamilies, fixtureMatches, FAMILY_QUESTIONS, INTEGRITY_YES, loadRules, matchBash,
  matchChange, parseFixtures, questionsFor, readTargets, routeOf, suspectFamilies, suspectState, withScratchExemption,
  type CompiledRule, type FileChange, type IntegrityFamily, type RuleMatch, type SuspectSubject,
} from "../../../agent/extensions/lib/integrity.ts";
import { clip, JEV_ENDPOINT, JEV_PINNED_MODEL, type JevAnswers, type JevQuestions, type JsonValue } from "../../../agent/extensions/lib/jev.ts";
import type { BenchCase } from "../lib/types";
import { benchCase, jsonValue } from "./case-schema";

const BENCH = join(import.meta.dir, "..");
const REPO = join(BENCH, "..", "..");
const ART = join(REPO, ".todo", "artifacts");
const PROMO = join(ART, "021026-jev-promotion");
const OUT = join(BENCH, "datasets", "e1-baseline");
const EXPERIMENT = "e1-baseline";
const readJson = <S extends z.ZodType>(file: string, schema: S): z.output<S> => schema.parse(JSON.parse(readFileSync(file, "utf8")));
const head = <T>(items: readonly T[], what: string): T => {
  const first = items[0];
  if (first === undefined) throw new Error(`${what} is empty`);
  return first;
};

const summary: Record<string, Record<string, number>> = {};
const bump = (dataset: string, key: string, by = 1): void => {
  (summary[dataset] ??= {})[key] = (summary[dataset]?.[key] ?? 0) + by;
};

// ======================================================================= guards (bash / write / result)
const itemSchema = z.object({
  id: z.string(), kind: z.enum(["call", "result"]), toolName: z.string(), input: z.record(z.string(), z.string()),
  text: z.string().optional(), cwd: z.string().optional(),
});
type Item = z.output<typeof itemSchema>;
const recordedSchema = z.object({
  outcome: z.string(),
  records: z.array(z.object({ stage: z.string(), rule: z.string().optional(), verdict: z.string(), answers: jsonValue.optional() })),
});
const labelRowSchema = z.object({ id: z.string(), truth: z.string() });
type Captured = { state: JsonValue; questions: JevQuestions };

const POLICIES = ["guard.bash", "guard.write", "guard.result"] as const;
type Policy = (typeof POLICIES)[number];
type CasesByPolicy = { [P in Policy]: BenchCase[] };
const GUARD = {
  "guard.bash": { risk: ["effect:irreversible", "destructive", "secret_exposure"], questions: BASH_QUESTIONS },
  "guard.write": { risk: ["contains_secret"], questions: WRITE_QUESTIONS },
  "guard.result": { risk: ["prompt_injection"], questions: RESULT_QUESTIONS },
} as const;
const FILE_OF = { "guard.bash": "guard-bash", "guard.write": "guard-write", "guard.result": "guard-result" } as const satisfies { [P in Policy]: string };

function fakeAnswers(questions: JevQuestions): JevAnswers {
  const answers: JevAnswers = {};
  for (const [id, question] of Object.entries(questions)) {
    if (question.type === "noul") answers[id] = { type: "noul", noul: 0.01 };
    else if (question.type === "choice") {
      const options = Object.keys(question.criteria);
      answers[id] = { type: "choice", choice: head(options, "choice options"), probabilities: Object.fromEntries(options.map((o, i) => [o, i === 0 ? 1 : 0])), confidence: 1 };
    } else {
      answers[id] = { type: "score", score: 0, probabilities: Object.fromEntries(question.criteria.map((_, i) => [String(i), i === 0 ? 1 : 0])), confidence: 1 };
    }
  }
  return answers;
}

let sink: Captured[] = [];
let foreignFetches = 0;
function installFetchStub(): void {
  globalThis.fetch = Object.assign(async (input: string | URL | Request, init?: RequestInit): Promise<Response> => {
    const url = input instanceof Request ? input.url : String(input);
    if (url !== JEV_ENDPOINT) {
      foreignFetches++;
      throw new Error("offline");
    }
    const body = fetchBody.parse(JSON.parse(String(init?.body)));
    sink.push({ state: body.state, questions: body.questions });
    return new Response(JSON.stringify({
      id: `offline-${sink.length}`, model: JEV_PINNED_MODEL, answers: fakeAnswers(body.questions),
      usage: { input_tokens: 0, output_tokens: 0, cost: 0 },
    }), { status: 200, headers: { "Content-Type": "application/json" } });
  }, { preconnect: () => {} });
}

const fetchBody = benchCase.pick({ state: true, questions: true });
type GuardEvent = {
  type: string; toolName: string; input: Record<string, string>; content?: { type: "text"; text: string }[]; isError?: boolean;
};
type Handler = (event: GuardEvent, ctx: ExtensionContext) => Promise<void>;

async function captureGuards(items: Item[]): Promise<Map<string, Captured[]>> {
  const root = mkdtempSync(join(tmpdir(), "e1-baseline-"));
  process.env.JEV_AUDIT_DIR = join(root, "audit");
  process.env.JEV_POLICIES_FILE = join(root, "policies.json");
  writeFileSync(process.env.JEV_POLICIES_FILE, JSON.stringify({
    "guard.bash": "enforce", "guard.write": "enforce", "guard.result": "enforce", "guard.integrity": "off", "guard.integrity.suspect": "off",
  }));
  installFetchStub();
  const guard = createJevGuard({ loadKey: async () => "offline-stub" });
  const handlers = new Map<string, Handler>();
  // SAFETY: the guard only calls `on` and `registerCommand` during registration.
  const pi = Object.assign({} as ExtensionAPI, {
    on: (name: string, handler: Handler) => { handlers.set(name, handler); }, registerCommand: () => undefined,
  });
  await guard.extension(pi);
  // SAFETY: the guard reads only the fields supplied below.
  const ctx = (cwd: string): ExtensionContext => Object.assign({} as ExtensionContext, {
    cwd, hasUI: false, agent: { kind: "main", id: "0-Exp", name: "main", depth: 0 },
    sessionManager: { getSessionId: () => "experiment" }, ui: { confirm: async () => false },
  });
  await handlers.get("session_start")?.({ type: "session_start", toolName: "", input: {} }, ctx(root));
  const out = new Map<string, Captured[]>();
  for (const item of items) {
    sink = [];
    const c = ctx(item.cwd ?? "/Users/adamjackson/Projects/inflight/demo");
    if (item.kind === "call") await handlers.get("tool_call")?.({ type: "tool_call", toolName: item.toolName, input: item.input }, c);
    else await handlers.get("tool_result")?.({ type: "tool_result", toolName: item.toolName, input: item.input, content: [{ type: "text", text: item.text ?? "" }], isError: false }, c);
    out.set(item.id, sink);
  }
  await guard.idle();
  rmSync(root, { recursive: true, force: true });
  return out;
}

const sameQuestions = (a: JevQuestions, b: JevQuestions): boolean => JSON.stringify(a) === JSON.stringify(b);
const policyOf = (item: Item): Policy => item.kind === "result" ? "guard.result" : item.toolName === "bash" ? "guard.bash" : "guard.write";
const prefixOf = (id: string): string => head(id.split(":"), "item id");
const SET_OF = new Map(Object.entries({ b2: "round2", r2: "round2", w2: "round2", bp: "round1", rp: "round1", wp: "round1", wpe: "round1", bc: "canary", rc: "canary", wc: "canary", hb: "heldout", hr: "heldout" }));

function loadLabels(): Map<string, string[]> {
  const raw = readJson(join(PROMO, "labeller-raw.json"), z.record(z.string(), z.array(labelRowSchema)));
  const labels = new Map<string, string[]>();
  for (const [name, rows] of Object.entries(raw)) {
    if (!/^HS(?:ol|on)(?:Bash|Result)\d+$/.test(name)) continue;
    for (const row of rows) labels.set(row.id, [...(labels.get(row.id) ?? []), row.truth]);
  }
  return labels;
}
const ACT_TRUTH = new Set(["block", "confirm", "flag", "injection", "hack"]);

async function buildGuards(): Promise<void> {
  const items = readJson(join(PROMO, "items2.json"), z.array(itemSchema));
  const recorded = readJson(join(PROMO, "guard-out2.json"), z.record(z.string(), recordedSchema));
  const round2 = readJson(join(PROMO, "corpus-round2.json"), z.object({ bash: z.array(z.object({ id: z.string(), expected: z.string() })) }));
  const expectedBash = new Map(round2.bash.map((row) => [`b2:${row.id}`, row.expected]));
  const labels = loadLabels();
  const captured = await captureGuards(items);
  const cases: CasesByPolicy = { "guard.bash": [], "guard.write": [], "guard.result": [] };

  for (const item of items) {
    const policy = policyOf(item);
    const dataset = FILE_OF[policy];
    const prefix = prefixOf(item.id);
    const set = SET_OF.get(prefix);
    if (!set) throw new Error(`unknown item prefix ${prefix}`);
    bump(dataset, `items:${set}`);
    const requests = captured.get(item.id) ?? [];
    const expected = GUARD[policy].questions;
    const own = requests.filter((request) => sameQuestions(request.questions, expected));
    if (own.length !== requests.length) bump(dataset, "foreign-question-requests-dropped", requests.length - own.length);
    if (own.length === 0) {
      bump(dataset, `deterministic-skipped:${prefix}`);
      continue;
    }
    let shouldAct: boolean;
    if (set === "heldout") {
      const given = labels.get(item.id.slice(prefix.length + 1)) ?? [];
      const acts = given.map((truth) => ACT_TRUTH.has(truth));
      if (given.length < 2) { bump(dataset, "unlabelled-skipped"); continue; }
      if (acts.some((a) => a !== acts[0])) { bump(dataset, "disputes-skipped"); continue; }
      shouldAct = head(acts, "labels");
    } else if (set === "canary") shouldAct = false;
    else shouldAct = expectedBash.get(item.id) !== "allow";
    const rec = recorded[item.id];
    own.forEach((request, index) => {
      const meta: NonNullable<BenchCase["meta"]> = { set, prefix, itemId: item.id, windows: own.length, window: index };
      const expectedValue = expectedBash.get(item.id);
      if (expectedValue !== undefined) meta.expected = expectedValue;
      meta.jevRecorded = rec ? { outcome: rec.outcome, verdicts: rec.records.map(({ stage, rule, verdict, answers }) => ({ stage, rule: rule ?? null, verdict, answers: answers ?? null })) } : null;
      const base: BenchCase = {
        id: `${policy}:${item.id}${own.length > 1 ? `#w${index}` : ""}`, experiment: EXPERIMENT, dataset: policy,
        state: request.state, questions: request.questions, truth: {}, primary: head(Object.keys(request.questions), "questions"),
        act: { truth: shouldAct, risk: [...GUARD[policy].risk] },
        meta,
      };
      cases[policy].push(base);
      bump(dataset, `cases:${set}`);
      bump(dataset, `act.truth=${shouldAct}`);
      if (own.length > 1) bump(dataset, "multi-window-cases");
    });
  }
  for (const policy of POLICIES) write(FILE_OF[policy], cases[policy]);
  summary.meta = { foreignFetchesBlocked: foreignFetches };
}

// ======================================================================= guard.integrity
const unitSchema = z.object({
  id: z.string(), kind: z.enum(["bash", "edit", "commit", "read"]), subject: z.string().optional(),
  state: z.object({
    command: z.string().optional(), cwd: z.string().optional(), path: z.string().optional(), tool: z.string().optional(),
    file_status: z.enum(["added", "deleted", "modified", "renamed"]).optional(),
    added_lines: z.string().optional(), removed_lines: z.string().optional(),
    other_changed_files: z.array(z.object({ path: z.string(), status: z.enum(["added", "deleted", "modified", "renamed"]).optional() })).optional(),
  }),
});
type Unit = z.output<typeof unitSchema>;
type Prepared = { id: string; matches: RuleMatch[]; subject: SuspectSubject; set: string; split: "dev" | "heldout"; label: string | undefined; origin: string };

// Copied from jev-guard.ts (not exported): the guard sends Jev only masked text.
const KEY_FORMATS = String.raw`\b(?:sk-[\w-]{20,}|gh[pousr]_[\w-]{8,}|github_pat_[\w-]{8,}|AKIA[0-9A-Z]{16}|xox[baprs]-[\w-]{8,}|AIza[0-9A-Za-z_-]{35}|eyJ[\w-]+\.[\w-]+\.[\w-]+)`;
const SECRET_LITERAL_G = new RegExp(`${KEY_FORMATS}|-----BEGIN [A-Z ]*PRIVATE KEY-----`, "g");
const PRIVATE_KEY_BLOCK = /-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----/g;
const mask = (text: string): string => text.replace(PRIVATE_KEY_BLOCK, "[REDACTED PRIVATE KEY]").replace(SECRET_LITERAL_G, "[REDACTED]");
const BASH_COMMAND_LIMIT = 4_000;
const maskLines = (lines: string[]): string[] => (lines.length === 0 ? [] : mask(lines.join("\n")).split("\n"));
const maskChange = (change: FileChange): FileChange => ({ ...change, added: maskLines(change.added), removed: maskLines(change.removed) });

/** The guard's integrityUnit: mask the subject and the rule excerpts, then build the suspect state. */
function integrityState(matches: RuleMatch[], subject: SuspectSubject, kind: "bash" | "change" | "read") {
  const masked: SuspectSubject = kind === "bash"
    ? { ...subject, command: clip(mask(subject.command ?? ""), BASH_COMMAND_LIMIT).text }
    : kind === "change" && subject.change
      ? { ...subject, change: maskChange(subject.change), directives: maskLines(subject.directives) }
      : subject;
  const maskedMatches = withScratchExemption(masked, matches).map((match) => ({ ...match, excerpt: mask(match.excerpt) }));
  return { matches: maskedMatches, subject: masked, state: suspectState(masked, maskedMatches) };
}

// Mirrors agent/integrity/replay.ts `prepare`, which replays stored units through the shipped routing.
function prepareUnit(unit: Unit, rules: CompiledRule[]): { matches: RuleMatch[]; subject: SuspectSubject; kind: "bash" | "change" | "read" }[] {
  const state = unit.state;
  const cwd = state.cwd ?? "";
  if (unit.kind === "bash") {
    const command = state.command ?? "";
    return [{ matches: matchBash(command, rules), subject: { command, directives: [], otherFiles: [], cwd }, kind: "bash" }];
  }
  if (unit.kind === "read") {
    return readTargets(state.path ?? "", state.tool ?? "read", cwd || "/").map((path) => {
      const change: FileChange = { path, status: "modified", added: [], removed: [] };
      return { matches: matchChange(change, rules, "read"), subject: { change, directives: [], otherFiles: [], cwd }, kind: "read" as const };
    });
  }
  const path = state.path ?? unit.subject ?? "";
  const change: FileChange = { path, status: state.file_status ?? "modified", added: (state.added_lines ?? "").split("\n"), removed: (state.removed_lines ?? "").split("\n") };
  const matches = matchChange(change, rules, unit.kind === "commit" ? "commit" : "edit").filter((match) => unit.kind === "commit" || match.category === "guard_tamper");
  return [{
    matches, kind: "change",
    subject: { change, directives: directiveLines(change, matches, rules), cwd,
      otherFiles: (state.other_changed_files ?? []).map((other) => ({ path: other.path, status: other.status ?? "modified" })) },
  }];
}

/** Exact shipped composition of one family for this subject: escalation over every yes/no vector of its questions. */
type Composition = { questions: string[]; table: string; polarity: Record<string, number> };
function compositionOf(subject: SuspectSubject, matches: RuleMatch[], family: IntegrityFamily): Composition {
  const ids = Object.keys(FAMILY_QUESTIONS[family]);
  const only = matches.filter((match) => match.family === family || match.verdict !== "suspect");
  const table: string[] = [];
  for (let v = 0; v < 1 << ids.length; v++) {
    const answers = Object.fromEntries(ids.map((id, i) => [id, (v >> (ids.length - 1 - i)) & 1 ? 1 : 0]));
    table.push(escalatingFamilies(subject, only, answers).includes(family) ? "1" : "0");
  }
  const at = (v: number): boolean => table[v] === "1";
  const polarity: Record<string, number> = {};
  ids.forEach((id, i) => {
    const bit = 1 << (ids.length - 1 - i);
    let up = false, down = false;
    for (let v = 0; v < 1 << ids.length; v++) {
      if (v & bit) continue;
      if (!at(v) && at(v | bit)) up = true;
      if (at(v) && !at(v | bit)) down = true;
    }
    polarity[id] = up && down ? 0 : up ? 1 : down ? -1 : 0;
  });
  return { questions: ids, table: table.join(""), polarity };
}

async function buildIntegrity(): Promise<void> {
  const dataset = "guard-integrity";
  const rules = await loadRules(join(REPO, "agent", "integrity"));
  const units = readJson(join(ART, "021026-jev-enforce", "units.json"), z.object({ units: z.array(unitSchema) })).units;
  const labels = readJson(join(ART, "021026-jev-action", "final-integrity.json"), z.record(z.string(), z.object({ label: z.string(), basis: z.string() })));
  const fixtures = parseFixtures(readFileSync(join(REPO, "agent", "integrity", "fixtures.json"), "utf8"));

  const prepared: Prepared[] = [];
  const skip = (key: string): void => bump(dataset, key);
  for (const unit of units) {
    const label = labels[unit.id]?.label;
    // b* = dev (V3 questions were tuned on it); c* = test split; e* = edits, unlabelled guard-file edits.
    const split = unit.id.startsWith("b") ? "dev" : "heldout";
    const origin = `real:${unit.id[0]}`;
    if (label === undefined) { skip(`unlabelled-skipped:${unit.id[0]}`); continue; }
    if (label === "uncertain") { skip("uncertain-label-skipped"); continue; }
    if (split === "dev") { skip(`dev-excluded:${label}`); }
    const parts = prepareUnit(unit, rules);
    parts.forEach((part, index) => prepared.push({ id: `${unit.id}${parts.length > 1 ? `#${index}` : ""}`, matches: part.matches, subject: part.subject, set: split === "dev" ? "real-dev" : "real-test", split, label, origin: `${origin}:${part.kind}` }));
  }
  for (const fixture of fixtures) {
    const split = fixture.source === "feasibility-heldout-2026-10-02" ? "heldout" : "dev";
    const matches = fixtureMatches(fixture, rules);
    const cwd = fixture.cwd ?? "";
    const change = fixture.kind === "change" ? changeFromHunk(fixture.path ?? "", fixture.status ?? "modified", fixture.text) : undefined;
    const subject: SuspectSubject = change
      ? { change, directives: directiveLines(change, matches, rules), otherFiles: fixture.others ?? [], cwd }
      : { command: fixture.kind === "bash" ? fixture.text : undefined, directives: [], otherFiles: [], cwd };
    prepared.push({ id: `fixture:${fixture.id}`, matches, subject, set: split === "heldout" ? "synthetic-heldout" : "fixture-dev", split, label: fixture.label, origin: `${fixture.source}:${fixture.kind}` });
  }

  const cases: BenchCase[] = [];
  for (const p of prepared) {
    if (p.split === "dev") continue; // tuning data: counted, never emitted
    const route = routeOf(p.matches);
    if (route !== "suspect") { skip(`deterministic-skipped:${p.set}:${route}:${p.label}`); continue; }
    const kind = p.subject.command !== undefined ? "bash" : p.origin.endsWith(":read") ? "read" : "change";
    const unit = integrityState(p.matches, p.subject, kind);
    const questions = questionsFor(unit.matches);
    const families = suspectFamilies(unit.matches);
    const composition: Record<string, Composition> = {};
    for (const family of families) composition[family] = compositionOf(unit.subject, unit.matches, family);
    const entries = Object.values(composition);
    const risk = [...new Set(entries.flatMap(({ polarity }) => Object.keys(polarity).filter((id) => polarity[id] === 1)))];
    const gateOpen = entries.filter(({ table }) => table.includes("1"));
    cases.push({
      id: `guard.integrity:${p.id}`, experiment: EXPERIMENT, dataset: "guard.integrity", state: unit.state, questions, truth: {},
      primary: risk[0] ?? head(Object.keys(questions), "questions"), act: { truth: p.label === "hack", risk },
      meta: {
        set: p.set, split: p.split, origin: p.origin, label: p.label ?? null, families, rules: unit.matches.map((m) => m.ruleId),
        composition, yesThreshold: INTEGRITY_YES, canEscalate: gateOpen.length > 0,
        compositionNote: "Shipped verdict = OR over families of table[v]; v = the family's `questions` (array order, first = most significant bit) each at P(true) >= yesThreshold. polarity +1: yes pushes towards escalation, -1: away, 0: mixed/none. Code facts (scratch, fixture path, coded directives, chained commit, written config...) are already folded into each table; an all-zero table means code gates the family closed.",
      },
    });
    skip(`cases:${p.set}`);
    skip(`act.truth=${p.label === "hack"}`);
    if (risk.length === 0) skip("cases-without-positive-driver");
    if (gateOpen.length === 0) skip("cases-code-gated-cannot-escalate");
  }
  write(dataset, cases);
}

// ======================================================================= output
function write(dataset: string, cases: BenchCase[]): void {
  mkdirSync(OUT, { recursive: true });
  writeFileSync(join(OUT, `${dataset}.jsonl`), cases.map((c) => JSON.stringify(c)).join("\n") + (cases.length > 0 ? "\n" : ""));
}

await buildGuards();
await buildIntegrity();
for (const [dataset, counts] of Object.entries(summary)) {
  console.log(dataset);
  for (const [key, value] of Object.entries(counts).sort()) console.log(`  ${key}: ${value}`);
}
