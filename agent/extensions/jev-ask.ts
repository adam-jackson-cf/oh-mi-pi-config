/**
 * jev_ask: cheap judgments over files or inline state without loading the content into
 * the model context (Jev levels 8-10), plus an evidence-ladder nudge in the system prompt.
 *
 * Principle: deterministic process > Jev classifier > LLM. Rules (grep, lsp, codegraph,
 * git, tests) settle what they can; Jev, a ~300 ms typed decision model costing ~$0.00001
 * per call, answers what rules cannot; the main LLM reads only what it must edit or quote.
 *
 * jev_ask beats `read` when the question is "do these files do X?", "which of these
 * candidates matter?" or "does this captured output show Y?": the answer is a few
 * probabilities, not thousands of tokens of source in context. It loses to `read` the
 * moment you need the code itself, and to grep/lsp/codegraph for exact symbols or text.
 */
import { createHash } from "node:crypto";
import { mkdir, readdir, realpath, stat, writeFile } from "node:fs/promises";
import { isAbsolute, join, relative, resolve } from "node:path";
import type { AgentToolResult } from "@oh-my-pi/pi-agent-core";
import type { ExtensionAPI, ExtensionContext } from "@oh-my-pi/pi-coding-agent";
import { z } from "zod";
import {
  appendDecision, auditRoot, clip, decide, JEV_STATE_CHAR_LIMIT, redact, validateQuestions,
  type JevAnswer, type JevAnswers, type JevQuestion, type JevQuestions, type JevResult, type JsonValue, type NewDecision,
} from "./lib/jev.ts";
import { loadJevApiKey } from "./lib/jev-auth.ts";
import { loadRules, matchChange, type CompiledRule } from "./lib/integrity.ts";

export const ASK_POLICY = "ask";
export const ASK_POLICY_VERSION = "ask-2026-10-02.1";
export const MAX_FILES = 64;
export const MAX_QUESTIONS = 8;
export const MAX_INLINE_CHARS = 8_000;
export const CONCURRENCY = 8;
const INLINE_AUDIT_LIMIT = 2_000;
const BINARY_SNIFF_BYTES = 8_192;
const MAX_SCAN = 5_000;
const IGNORED_DIRS = new Set([".git", "node_modules", "dist", "build", "coverage", ".venv"]);
const LOCKFILE = /(?:^|\/)(?:package-lock\.json|npm-shrinkwrap\.json|yarn\.lock|pnpm-lock\.yaml|bun\.lockb?|go\.sum|[^/]+\.lock)$/;
const GLOB_CHARS = /[*?[\]{}]/;
export const NUDGE_MARKER = "<!-- jev-evidence-ladder -->";
export const NUDGE_TEXT = `${NUDGE_MARKER}
## Evidence ladder
Prefer the cheapest evidence that settles the question:
1. Deterministic: codegraph (structure, callers, impact, affected tests), lsp, grep/glob, git, tests.
2. Jev (fast, ~$0.00001): \`find\` locates behaviour by description when names are unknown; \`jev_ask\` judges files or captured output you have not read; eval \`judge()\`/\`judge_batch\` and the \`jevify\` keyword classify in bulk.
3. LLM context: \`read\` only ranges you must edit, quote or reason over in depth.

\`jev_ask\` is an xd:// device: read \`xd://jev_ask\` once for the schema, then write JSON to it, e.g. \`{"questions":{"retries":{"type":"noul","instructions":"Does \`content\` implement retry with backoff?"}},"paths":["src/**/*.ts"]}\`.`;

const noulQuestion = z.object({
  type: z.literal("noul"), instructions: z.string(),
  criteria: z.object({ true: z.string().optional(), false: z.string().optional() }).optional(),
});
const choiceQuestion = z.object({
  type: z.literal("choice"), instructions: z.string(), criteria: z.record(z.string(), z.string().nullable()),
});
const scoreQuestion = z.object({ type: z.literal("score"), instructions: z.string(), criteria: z.array(z.string()) });
const question = z.discriminatedUnion("type", [noulQuestion, choiceQuestion, scoreQuestion]);
const QUESTION_ID = /^[a-z][a-z0-9_]{0,63}$/;

export const askParameters = z.object({
  questions: z.record(z.string().regex(QUESTION_ID, "question ids are lower_snake_case, max 64 chars"), question)
    .refine((all) => Object.keys(all).length >= 1 && Object.keys(all).length <= MAX_QUESTIONS,
      `1-${MAX_QUESTIONS} questions`),
  paths: z.array(z.string().min(1)).max(MAX_FILES).optional(),
  state: z.string().max(MAX_INLINE_CHARS).optional(),
  mode: z.enum(["per_file", "combined"]).optional(),
});
export type AskParameters = z.infer<typeof askParameters>;

export type Skip = { path: string; reason: string };
export type Candidate = { path: string; abs: string };
export type LoadedFile = { path: string; content: string };
export type AuditFields = { [key: string]: JsonValue };
export type Unit = { key: string; state: JsonValue; audit: AuditFields; subject: string };
export type UnitOutcome = { key: string; answers?: JevAnswers; error?: string };
export type AskDetails = {
  mode: "per_file" | "combined"; questions: string[];
  results: UnitOutcome[]; skipped: Skip[]; costUsd: number; calls: number;
  /** Audit writes (records or content snapshots) that failed during this call. */
  auditFailures: number;
};
export type LoadKey = () => Promise<string | undefined>;

function failure(text: string): AgentToolResult<AskDetails> {
  return { content: [{ type: "text", text }], isError: true };
}

const FALLBACK = "Fall back to `read` (with ranges) or `grep`.";

function inWorkspace(cwd: string, abs: string): boolean {
  const rel = relative(cwd, abs);
  return rel !== ".." && !rel.startsWith("../") && !isAbsolute(rel);
}

/** True when the symlink-resolved path stays inside the real workspace root. */
async function realInside(realRoot: string, abs: string): Promise<boolean> {
  const real = await realpath(abs).catch(() => undefined);
  return real !== undefined && inWorkspace(realRoot, real);
}

function ignoredSegment(rel: string): string | undefined {
  const parts = rel.split("/");
  const at = parts.findIndex((part, index) => index < parts.length - 1 && IGNORED_DIRS.has(part));
  return at === -1 ? undefined : parts.slice(0, at + 1).join("/");
}

async function walk(cwd: string, dir: string, out: Candidate[], skipped: Skip[]): Promise<void> {
  const entries = await readdir(dir, { withFileTypes: true });
  entries.sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
  for (const entry of entries) {
    const abs = join(dir, entry.name);
    const rel = relative(cwd, abs);
    if (entry.isDirectory()) {
      if (IGNORED_DIRS.has(entry.name)) skipped.push({ path: `${rel}/`, reason: "ignored directory" });
      else await walk(cwd, abs, out, skipped);
    } else if (entry.isFile()) out.push({ path: rel, abs });
  }
}

async function expandGlob(cwd: string, pattern: string, out: Candidate[], skipped: Skip[]): Promise<void> {
  const reported = new Set<string>();
  let seen = 0;
  for await (const rel of new Bun.Glob(pattern).scan({ cwd, onlyFiles: true, dot: true })) {
    if (++seen > MAX_SCAN) {
      skipped.push({ path: pattern, reason: `glob matched more than ${MAX_SCAN} files; scan truncated` });
      break;
    }
    const ignored = ignoredSegment(rel);
    if (ignored) {
      if (!reported.has(ignored)) skipped.push({ path: `${ignored}/`, reason: "ignored directory" });
      reported.add(ignored);
    } else out.push({ path: rel, abs: join(cwd, rel) });
  }
}

/** Resolve paths, directories and globs under `cwd` to candidate files (deduplicated, input order). */
export async function expandPaths(cwd: string, paths: string[]): Promise<{ files: Candidate[]; skipped: Skip[] }> {
  const found: Candidate[] = [];
  const skipped: Skip[] = [];
  const realRoot = await realpath(cwd).catch(() => cwd);
  for (const input of paths) {
    const abs = resolve(cwd, input);
    const globbed = GLOB_CHARS.test(input);
    if (input.split("/").includes("..") || (globbed ? isAbsolute(input) : !inWorkspace(cwd, abs))) {
      skipped.push({ path: input, reason: "outside the workspace" });
    } else if (globbed) {
      await expandGlob(cwd, input, found, skipped);
    } else {
      const info = await stat(abs).catch(() => undefined);
      if (!info) skipped.push({ path: input, reason: "not found" });
      else if (!(await realInside(realRoot, abs))) skipped.push({ path: input, reason: "outside the workspace" });
      else if (info.isDirectory()) await walk(cwd, abs, found, skipped);
      else found.push({ path: relative(cwd, abs), abs });
    }
  }
  const contained: Candidate[] = [];
  for (const file of found) {
    if (await realInside(realRoot, file.abs)) contained.push(file);
    else skipped.push({ path: file.path, reason: "outside the workspace" });
  }
  const seen = new Set<string>();
  const files = contained.filter((file) => !seen.has(file.path) && seen.add(file.path));
  if (files.length > MAX_FILES) {
    for (const extra of files.slice(MAX_FILES)) skipped.push({ path: extra.path, reason: `over the ${MAX_FILES}-file cap` });
    files.length = MAX_FILES;
  }
  return { files, skipped };
}

/**
 * Deterministic sensitive-path check. Guarded paths come from the integrity rules' read-context
 * matcher (one list); secret file names are matched here because those rules cover guard files only.
 */
const SECRET_FILE = /(?:^|\/)(?:\.env(?:\.(?!(?:example|sample|template|dist)$)[^/]+)?|\.netrc|\.npmrc|\.pgpass|\.git-credentials|id_(?:rsa|dsa|ecdsa|ed25519)|credentials(?:\.(?:json|ya?ml|toml|ini|xml))?|[^/]+\.(?:pem|key|p12|pfx|keystore|jks|kdbx|ppk))$|(?:^|\/)\.(?:ssh|aws|gnupg)\//i;

function sensitivePath(file: Candidate, rules: CompiledRule[]): boolean {
  if (SECRET_FILE.test(file.path) || SECRET_FILE.test(file.abs)) return true;
  return matchChange({ path: file.abs, status: "modified", added: [], removed: [] }, rules, "read").length > 0;
}

/** Deterministic pre-filter: returns the file content or the reason it cannot be judged. */
async function loadFile(file: Candidate, rules: CompiledRule[]): Promise<LoadedFile | Skip> {
  const ignored = ignoredSegment(file.path);
  if (ignored) return { path: file.path, reason: "ignored directory" };
  if (sensitivePath(file, rules)) return { path: file.path, reason: "sensitive path" };
  if (LOCKFILE.test(file.path)) return { path: file.path, reason: "lockfile" };
  const handle = Bun.file(file.abs);
  const bytes = handle.size;
  if (bytes === 0) return { path: file.path, reason: "empty" };
  const tooLarge = { path: file.path, reason: "too large: use grep/read with ranges" };
  if (bytes > JEV_STATE_CHAR_LIMIT * 4) return tooLarge;
  const data = new Uint8Array(await handle.arrayBuffer());
  if (data.subarray(0, BINARY_SNIFF_BYTES).includes(0)) return { path: file.path, reason: "binary" };
  const content = new TextDecoder().decode(data);
  if (content.trim() === "") return { path: file.path, reason: "empty" };
  if (content.length > JEV_STATE_CHAR_LIMIT) return tooLarge;
  return { path: file.path, content };
}

function sha256(text: string): string {
  return createHash("sha256").update(text).digest("hex");
}

function fileAudit(file: LoadedFile) {
  return { path: file.path, chars: file.content.length, sha256: sha256(file.content) };
}

type BuiltUnits = { calls: Unit[]; error?: string };
type CombinedState = { files: LoadedFile[]; inline?: string };
type CombinedAudit = { files: AuditFields[]; inline?: string };

/** Build the Jev calls: one per file, one combined call, and/or one inline-state call. */
function buildUnits(files: LoadedFile[], inline: string | undefined, mode: "per_file" | "combined",
  apiKey: string): BuiltUnits {
  const calls: Unit[] = [];
  const redactedInline = inline === undefined ? undefined : redact(inline, apiKey);
  const inlineAudit = redactedInline === undefined ? "" : clip(redactedInline, INLINE_AUDIT_LIMIT).text;
  if (mode === "combined" && files.length > 0) {
    const state: CombinedState = {
      files: files.map((file) => ({ path: file.path, content: redact(file.content, apiKey) })) };
    if (redactedInline !== undefined) state.inline = redactedInline;
    if (JSON.stringify(state).length > JEV_STATE_CHAR_LIMIT) {
      return { calls, error: `Combined content exceeds ${JEV_STATE_CHAR_LIMIT} characters; use mode per_file or fewer paths.` };
    }
    const audit: CombinedAudit = { files: files.map(fileAudit) };
    if (redactedInline !== undefined) audit.inline = inlineAudit;
    calls.push({ key: "(combined)", state, audit, subject: `combined ${files.length} files` });
    return { calls };
  }
  for (const file of files) {
    calls.push({ key: file.path, state: { path: file.path, content: redact(file.content, apiKey) },
      audit: fileAudit(file), subject: redact(file.path, apiKey) });
  }
  if (redactedInline !== undefined) {
    calls.push({ key: "(inline)", state: redactedInline,
      audit: { inline: inlineAudit, chars: redactedInline.length }, subject: "inline state" });
  }
  return { calls };
}

async function runPool<T, R>(items: T[], limit: number, work: (item: T) => Promise<R>): Promise<R[]> {
  const results: R[] = [];
  let next = 0;
  const lane = async () => {
    while (next < items.length) {
      const index = next++;
      results[index] = await work(items[index]);
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, lane));
  return results;
}

function cell(q: JevQuestion, given: JevAnswer | undefined): string {
  if (!given) return "-";
  if (given.type === "noul") return `${given.noul >= 0.5 ? "yes" : "no"} ${given.noul.toFixed(2)}`;
  if (given.type === "choice") return `${given.choice} ${(given.probabilities[given.choice] ?? given.confidence).toFixed(2)}`;
  const max = q.type === "score" ? q.criteria.length - 1 : 0;
  return `${given.score.toFixed(1)}/${max}`;
}

function renderTable(ids: string[], questions: JevQuestions, results: UnitOutcome[]): string {
  const lines = [`| target | ${ids.join(" | ")} |`, `|${" --- |".repeat(ids.length + 1)}`];
  for (const result of results) {
    const cells = ids.map((id) => (result.answers ? cell(questions[id], result.answers[id]) : "failed"));
    lines.push(`| ${result.key} | ${cells.join(" | ")} |`);
  }
  return lines.join("\n");
}

/** Content-addressed snapshot of exactly what Jev was sent (redacted, bounded by the state limit). */
async function writeBlob(text: string): Promise<string> {
  const hash = sha256(text);
  if (process.env.JEV_AUDIT === "0") return hash;
  const dir = join(auditRoot(), ASK_POLICY, "blobs");
  await mkdir(dir, { recursive: true, mode: 0o700 });
  try {
    await writeFile(join(dir, hash), text, { flag: "wx", mode: 0o600 });
  } catch (cause) {
    if (!(cause instanceof Error && "code" in cause && cause.code === "EEXIST")) throw cause;
  }
  return hash;
}

/** Audit is best effort and never fails the tool call; failures are counted and surfaced in the result. */
type AuditSink = { failures: number };

async function writeRecord(record: NewDecision, sink: AuditSink): Promise<void> {
  try {
    await appendDecision(record);
  } catch {
    sink.failures++;
  }
}

async function auditCall(unit: Unit, questions: JevQuestions, outcome: JevResult, toolCallId: string | undefined,
  ctx: ExtensionContext, sink: AuditSink): Promise<void> {
  const base = { ...unit.audit, toolCallId: toolCallId ?? null };
  let blob: string | undefined;
  try {
    blob = await writeBlob(JSON.stringify(unit.state));
  } catch {
    sink.failures++;
  }
  const state = blob === undefined ? base : { ...base, blob };
  await writeRecord({
    policy: ASK_POLICY, policyVersion: ASK_POLICY_VERSION, mode: "enforce",
    sessionId: ctx.sessionManager?.getSessionId?.(), agentKind: ctx.agent?.kind, agentName: ctx.agent?.name,
    stage: outcome.ok ? "jev" : "jev_error", subject: unit.subject, state, questions,
    answers: outcome.ok ? outcome.answers : undefined,
    resolvedModel: outcome.resolvedModel,
    providerResponseId: outcome.ok ? outcome.providerResponseId : undefined,
    costUsd: outcome.ok ? outcome.costUsd : undefined, latencyMs: outcome.latencyMs,
    error: outcome.ok ? undefined : outcome.error, httpStatus: outcome.ok ? undefined : outcome.httpStatus,
    verdict: outcome.ok ? "answered" : "error", enforced: true, labels: ["correct", "incorrect", "uncertain"],
  }, sink);
}

type PrecheckContext = { ctx: ExtensionContext; toolCallId: string | undefined; sink: AuditSink; questions: JevQuestions };

/** Records a refused call (no Jev request was made) so unused, unavailable and failing stay distinguishable. */
async function precheck(pre: PrecheckContext, reason: string, message: string, validQuestions: boolean,
  skipped: Skip[] = []): Promise<AgentToolResult<AskDetails>> {
  const ids = Object.keys(pre.questions);
  await writeRecord({
    policy: ASK_POLICY, policyVersion: ASK_POLICY_VERSION, mode: "enforce",
    sessionId: pre.ctx.sessionManager?.getSessionId?.(), agentKind: pre.ctx.agent?.kind, agentName: pre.ctx.agent?.name,
    stage: "skipped", rule: `precheck:${reason}`, subject: `precheck ${reason}`,
    state: { toolCallId: pre.toolCallId ?? null, questionIds: ids, skipped: skipped.map((s) => ({ ...s })) },
    questions: validQuestions ? pre.questions : undefined,
    error: message, verdict: "error", enforced: true, labels: ["uncertain"],
  }, pre.sink);
  const details: AskDetails = { mode: "per_file", questions: ids, results: [], skipped, costUsd: 0, calls: 0,
    auditFailures: pre.sink.failures };
  const note = pre.sink.failures > 0 ? `\n(audit write failed ${pre.sink.failures}x)` : "";
  return { ...failure(`${message}${note}`), details };
}

const NO_MATCH_OPTION = /^(?:none|other|no_match|neither|unknown|n_a|na|not_applicable)\b/i;

/** Non-blocking question-quality diagnostics (jev-design: criteria define every answer; choices need a no-match). */
function questionNotes(questions: JevQuestions): string[] {
  const notes: string[] = [];
  for (const [id, q] of Object.entries(questions)) {
    if (q.type === "noul" && !q.criteria?.true && !q.criteria?.false) {
      notes.push(`${id}: noul has no criteria; state what counts as true and as false.`);
    }
    if (q.type === "choice") {
      const options = Object.keys(q.criteria);
      if (!options.some((option) => NO_MATCH_OPTION.test(option))) {
        notes.push(`${id}: choice has no no-match option (e.g. "none"); a forced pick will be wrong when nothing fits.`);
      }
      if (options.some((option) => !q.criteria[option])) notes.push(`${id}: choice options without a description are ambiguous.`);
    }
  }
  return notes;
}

/** Execute one jev_ask call. Exported for tests; the registered tool wraps it. */
export async function runAsk(params: AskParameters, signal: AbortSignal | undefined, ctx: ExtensionContext,
  loadKey: LoadKey, toolCallId?: string): Promise<AgentToolResult<AskDetails>> {
  const questions: JevQuestions = params.questions;
  const sink: AuditSink = { failures: 0 };
  const pre: PrecheckContext = { ctx, toolCallId, sink, questions };
  try {
    validateQuestions(questions);
  } catch (cause) {
    return precheck(pre, "invalid_questions", cause instanceof Error ? cause.message : "Invalid Jev questions.", false);
  }
  if (!params.paths?.length && params.state === undefined) {
    return precheck(pre, "no_input", "Provide `paths` and/or `state` to judge.", true);
  }
  let apiKey: string | undefined;
  try {
    apiKey = await loadKey();
  } catch {
    apiKey = undefined;
  }
  if (!apiKey) {
    return precheck(pre, "no_credential", `Jev is unavailable: no OpenRouter credential (run /login openrouter). ${FALLBACK}`, true);
  }
  let rules: CompiledRule[];
  try {
    rules = await loadRules();
  } catch {
    return precheck(pre, "rules_unavailable", `Sensitive-path rules are unavailable, so no file can be judged. ${FALLBACK}`, true);
  }

  const expanded = params.paths?.length ? await expandPaths(ctx.cwd, params.paths) : { files: [], skipped: [] };
  const skipped = [...expanded.skipped];
  const files: LoadedFile[] = [];
  for (const outcome of await runPool(expanded.files, CONCURRENCY, (file) => loadFile(file, rules))) {
    if ("content" in outcome) files.push(outcome);
    else skipped.push(outcome);
  }
  const mode = params.mode ?? "per_file";
  const built = buildUnits(files, params.state, mode, apiKey);
  if (built.error !== undefined) return precheck(pre, "combined_too_large", `${built.error} ${FALLBACK}`, true, skipped);
  const units = built.calls;
  if (units.length === 0) {
    return precheck(pre, "no_judgeable_input", `No judgeable input.\n${skippedLines(skipped)}\n${FALLBACK}`, true, skipped);
  }

  const ids = Object.keys(questions);
  const outcomes = await runPool(units, CONCURRENCY, async (unit) => {
    const result = await decide(apiKey, unit.state, questions, { signal });
    await auditCall(unit, questions, result, toolCallId, ctx, sink);
    return result;
  });
  const results: UnitOutcome[] = units.map((unit, index) => {
    const outcome = outcomes[index];
    return outcome.ok ? { key: unit.key, answers: outcome.answers } : { key: unit.key, error: outcome.error };
  });
  const costUsd = outcomes.reduce((sum, outcome) => sum + (outcome.ok ? outcome.costUsd : 0), 0);
  const details: AskDetails = { mode, questions: ids, results, skipped, costUsd, calls: units.length,
    auditFailures: sink.failures };
  const failed = results.filter((result) => result.error);
  const auditNote = sink.failures > 0 ? ` (audit write failed ${sink.failures}x)` : "";
  if (failed.length === results.length) {
    return { ...failure(`Jev failed: ${failed[0].error} ${FALLBACK}${auditNote}`), details };
  }
  const text = [renderTable(ids, questions, results)];
  for (const item of failed) text.push(`failed ${item.key}: ${item.error}`);
  if (skipped.length > 0) text.push(`Skipped:\n${skippedLines(skipped)}`);
  const notes = questionNotes(questions);
  if (notes.length > 0) text.push(`Question notes:\n${notes.map((note) => `- ${note}`).join("\n")}`);
  text.push(`cost $${costUsd.toFixed(6)}, ${units.length} call${units.length === 1 ? "" : "s"}${auditNote}`);
  return { content: [{ type: "text", text: text.join("\n") }], details };
}

function skippedLines(skipped: Skip[]): string {
  return skipped.map((item) => `- ${item.path}: ${item.reason}`).join("\n");
}

const DESCRIPTION = `Ask Jev questions about files you have not read: write JSON to xd://jev_ask (read it for the schema). Answers are probabilities (noul yes/no, choice, score), not file content.
Jev is a fast, ~$0.00001 typed decision model. Use it to: learn whether a file or many files do X; rank or filter candidates; check an assumption about command output you already captured (pass it as \`state\`).
Do NOT use it for exact symbol/text lookup (use grep, lsp, codegraph) or when you need the code to edit or quote (use read with ranges). A noul between 0.4 and 0.6, or a weak choice, means unknown: read the file.
\`paths\` accepts files, directories and globs under the workspace (max ${MAX_FILES} files; binaries, lockfiles, node_modules/.git/dist/build/coverage/.venv, secret and guarded files (dotenv, keys, credentials, integrity-maintainer) and files over ${JEV_STATE_CHAR_LIMIT} chars are skipped and reported). Max ${MAX_QUESTIONS} questions; ids are lower_snake_case.
\`mode\` and the state the questions see (cite fields by backticked name in instructions):
- per_file (default): one row per file; state \`{path, content}\`.
- combined: one judgment and one answer for the whole set; state \`{files: [{path, content}], inline?}\`. Use per_file when you need an answer per file.
- \`state\` text alone: the state is that bare string.
Writing questions: ask one narrow judgment per question; the id is not sent, so the instructions carry the whole meaning and name the field they inspect. Give criteria that define every answer, write boundary cases literally, and use noul for a yes/no condition, choice for a defined set (always include a "none" option for no match), score only for described ordered levels.
Example: \`{"questions":{"retries":{"type":"noul","instructions":"Does \`content\` retry a failed network request in code that runs in this file?","criteria":{"true":"A loop or helper re-sends the request after a failure","false":"No re-send, or retry appears only in comments, strings or tests"}},"kind":{"type":"choice","instructions":"Which best describes \`content\`?","criteria":{"source":"Executable code","docs":"Prose documentation","none":"Neither"}}},"paths":["src/**/*.ts"]}\``;

/** Host-side (omptype) schema for the tool contract; `askParameters` re-validates the limits at execute time. */
function hostParameters(z: ExtensionAPI["zod"]) {
  const noul = z.object({
    type: z.literal("noul"), instructions: z.string(),
    criteria: z.object({ true: z.string().optional(), false: z.string().optional() }).optional(),
  });
  const choice = z.object({
    type: z.literal("choice"), instructions: z.string(),
    criteria: z.record(z.string(), z.union([z.string(), z.literal(null)])),
  });
  const score = z.object({ type: z.literal("score"), instructions: z.string(), criteria: z.array(z.string()) });
  return z.object({
    questions: z.record(z.string(), z.union([noul, choice, score])),
    paths: z.array(z.string()).optional(),
    state: z.string().optional(),
    mode: z.enum(["per_file", "combined"]).optional(),
  });
}

/** Register the tool and the evidence-ladder nudge; `loadKey` is injectable for tests. */
export function registerJevAsk(pi: ExtensionAPI, loadKey: LoadKey): void {
  pi.registerTool({
    name: "jev_ask",
    label: "Jev ask",
    description: DESCRIPTION,
    parameters: hostParameters(pi.zod),
    approval: "read",
    execute: async (toolCallId, params, signal, _onUpdate, ctx) => {
      const parsed = askParameters.safeParse(params);
      if (!parsed.success) return failure(`Invalid jev_ask arguments: ${parsed.error.issues[0]?.message ?? "unknown problem"}.`);
      return runAsk(parsed.data, signal, ctx, loadKey, toolCallId);
    },
  });
  pi.on("before_agent_start", (event) => {
    if (event.systemPrompt.some((part) => part.includes(NUDGE_MARKER))) return undefined;
    return { systemPrompt: [...event.systemPrompt, NUDGE_TEXT] };
  });
}

export default function jevAsk(pi: ExtensionAPI): void {
  registerJevAsk(pi, loadJevApiKey);
}
