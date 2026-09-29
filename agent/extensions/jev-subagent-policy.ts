/**
 * Jev subagent policies. Principle: deterministic process > Jev classifier > LLM.
 * Every policy applies deterministic rules first; Jev only decides what the
 * rules cannot settle; an LLM (subagent, reviewer, human) is used only when the
 * policy escalates or Jev is unsure.
 *
 * Policies, both evaluated on `before_subagent_spawn`:
 *  - `subagent.review-triage` (agent `reviewer`): sizes review depth from the
 *    working-tree diff. Rules: no diff -> standard; sensitive path -> deep;
 *    docs-only -> light. Otherwise Jev scores security risk, complexity,
 *    behaviour change and missing tests; the weighted risk picks light,
 *    standard or deep. Enforce maps light/deep to a cheaper/stronger reviewer
 *    model, never in the author's model family.
 *  - `subagent.effort` (agent `task`, role `task`): rules keep an explicit short
 *    numbered plan; otherwise Jev rates how open-ended the assignment is and may
 *    raise the trailing thinking effort to `medium` (provider and model id
 *    are never changed).
 *
 * Modes come from `agent/jev-policies.json`. Shadow returns undefined at once,
 * adding no latency, and evaluates fire-and-forget into the audit log. Enforce
 * awaits the evaluation (8 s budget) and applies the verdict; a Jev failure or
 * timeout degrades to no change because these policies are advisory, not gates.
 *
 * The `task` tool_call is remembered (assignment and batch context, keyed by
 * task name or `${toolCallId}:${index}`) so the spawn hook can see the text.
 */
import type { ExtensionAPI, ExtensionContext } from "@oh-my-pi/pi-coding-agent";
import { z } from "zod";
import {
  appendDecision, clip, decide, JEV_STATE_CHAR_LIMIT, policyMode, readPolicyModes, redact,
  type DecisionStage, type JevAnswers, type JevQuestions, type JevResult, type JsonValue, type NewDecision, type PolicyMode,
} from "./lib/jev";
import { loadJevApiKey } from "./lib/jev-auth";

export const REVIEW_POLICY = "subagent.review-triage";
export const EFFORT_POLICY = "subagent.effort";
export const REVIEW_POLICY_VERSION = "review-triage-2026-09-29";
export const EFFORT_POLICY_VERSION = "effort-2026-09-29";

const REVIEW_AGENTS: readonly string[] = ["reviewer"];
const ENFORCE_TIMEOUT_MS = 8_000;
const CORRELATION_CAP = 64;

const REVIEW_LABELS = ["right_depth", "should_be_lighter", "should_be_deeper", "uncertain"];
const EFFORT_LABELS = ["right_effort", "needed_more", "needed_less", "uncertain"];

// Weighted risk over normalised (0..1) level scores; weights sum to 1.
const WEIGHT_SECURITY = 0.45;
const WEIGHT_COMPLEXITY = 0.3;
const WEIGHT_BEHAVIOUR = 0.25;
const LIGHT_RISK_BELOW = 0.2;
const DEEP_RISK_FROM = 0.6;
const MIN_SCORE_CONFIDENCE = 0.6;
const MISSING_TESTS_BELOW = 0.5;
// Review score questions have three levels, indices 0..2.
const MAX_LEVEL = 2;

export const LIGHT_REVIEW_MODEL = "openai-codex/gpt-6-luna:medium";
export const DEEP_REVIEW_MODEL = "openai-codex/gpt-6-sol:high";
const AUTHOR_ROLE = "@task";

const RAISE_OPENNESS = 1.5;
const RAISE_OPENNESS_WITHOUT_PLAN = 1.0;
const PLAN_MISSING_BELOW = 0.3;
const SHORT_PLAN_CHARS = 1_500;
const MIN_PLAN_LINES = 2;
const RAISED_EFFORT = "medium";
const RAISABLE_EFFORT = /:(off|minimal|low)$/;
const ANY_EFFORT = /:(off|minimal|low|medium|high|xhigh)$/;

const ASSIGNMENT_LIMIT_REVIEW = 2_000;
const DIFF_LIMIT = 40_000;
const ASSIGNMENT_LIMIT_EFFORT = 6_000;
const CONTEXT_LIMIT = 2_000;
const FILE_SUMMARY_LIMIT = 60;

const SENSITIVE_PATH = new RegExp(
  "auth|secur|crypto|permission|secret|\\.env|payment|billing|migration|infra|terraform|" +
  "\\.github/workflows|dockerfile|" +
  "(^|/)(package-lock\\.json|yarn\\.lock|pnpm-lock\\.yaml|bun\\.lockb?|cargo\\.lock|poetry\\.lock|uv\\.lock|" +
  "gemfile\\.lock|composer\\.lock|go\\.sum)$", "i");
const DOCS_ONLY_PATH = /\.(md|mdx|txt)$|^docs\//i;
const NUMBERED_LINE = /^\s*\d+[.)]/;

const REVIEW_QUESTIONS: JevQuestions = {
  security_risk: {
    type: "score",
    instructions: "How much does this diff touch security-sensitive behaviour?",
    criteria: [
      "The diff changes nothing that handles credentials, permissions, untrusted input, network access or data exposure.",
      "The diff changes code near credentials, permissions, untrusted input or data exposure without altering their rules.",
      "The diff changes how credentials, permissions, untrusted input, network access or data exposure are handled.",
    ],
  },
  complexity: {
    type: "score",
    instructions: "How hard is this diff to review correctly?",
    criteria: [
      "The diff is a small, local, mechanical change a reader can verify at a glance.",
      "The diff spans several functions or files but follows one clear pattern.",
      "The diff mixes several concerns, changes control flow or shared contracts, or needs cross-file reasoning.",
    ],
  },
  behaviour_change: {
    type: "score",
    instructions: "How much observable runtime behaviour does this diff change?",
    criteria: [
      "No observable behaviour changes: formatting, comments, docs, renames or pure refactors.",
      "A narrow behaviour change confined to one feature or code path.",
      "Behaviour changes across features, public interfaces, data formats or failure modes.",
    ],
  },
  missing_tests: {
    type: "noul",
    instructions: "Does the diff change behaviour without corresponding test changes?",
    criteria: {
      true: "The diff changes runtime behaviour and contains no added or updated tests for it.",
      false: "The diff changes no behaviour, or it includes tests that cover the behaviour it changes.",
    },
  },
};

const EFFORT_QUESTIONS: JevQuestions = {
  openness: {
    type: "score",
    instructions: "How open-ended is this assignment for the agent that must carry it out?",
    criteria: [
      "The exact edits are spelled out; the agent only applies them.",
      "A fixed plan whose steps must be resolved from codebase conventions across several files.",
      "Open-ended: design choices, debugging or unclear scope.",
    ],
  },
  has_plan: {
    type: "noul",
    instructions: "Does the assignment contain an explicit step-by-step plan?",
    criteria: {
      true: "The assignment lists concrete ordered steps the agent should follow.",
      false: "The assignment states a goal without ordered steps.",
    },
  },
};

const taskInput = z.object({
  context: z.string().optional(),
  tasks: z.array(z.object({ name: z.string().optional(), agent: z.string().optional(), task: z.string() })),
});

type Correlated = { assignment: string; context: string };
type SpawnResult = { model?: string | string[]; note?: string };
type Evaluation = Omit<NewDecision, "policy" | "policyVersion" | "mode" | "subject" | "labels" | "enforced"> & {
  /** Set when enforce mode should change the spawn. */
  apply?: SpawnResult;
};
type Changes = { paths: string[]; numstat: string[]; diff: string };
type SpawnEvent = {
  agent: string; modelRole?: string; patterns: string[]; spawnKey?: string;
};
type ModelQuery = ExtensionContext["models"];

/** Keep only the newest entries so a long session cannot grow the map without bound. */
function remember(store: Map<string, Correlated>, key: string, value: Correlated): void {
  store.delete(key);
  store.set(key, value);
  while (store.size > CORRELATION_CAP) {
    const oldest = store.keys().next();
    if (oldest.done) return;
    store.delete(oldest.value);
  }
}

/** Working-tree changes against HEAD; undefined when not a repo or nothing changed. */
async function readChanges(pi: ExtensionAPI, cwd: string, signal: AbortSignal | undefined): Promise<Changes | undefined> {
  const numstat = await pi.exec("git", ["diff", "HEAD", "--numstat"], { cwd, signal });
  const status = await pi.exec("git", ["status", "--porcelain", "--untracked-files=all"], { cwd, signal });
  if (numstat.code !== 0 || status.code !== 0) return undefined;
  const stats = numstat.stdout.split("\n").filter(Boolean);
  const paths = new Set(stats.map(line => line.split("\t").slice(2).join("\t")));
  const untracked: string[] = [];
  for (const line of status.stdout.split("\n")) {
    if (line.length <= 3) continue;
    const path = line.slice(3).replace(/^.* -> /, "").replace(/^"|"$/g, "");
    paths.add(path);
    if (line.startsWith("??") && !path.endsWith("/")) untracked.push(path);
  }
  if (paths.size === 0) return undefined;
  const tracked = await pi.exec("git", ["diff", "HEAD"], { cwd, signal });
  let diff = tracked.code === 0 ? tracked.stdout : "";
  // `git diff HEAD` omits untracked files; render each as a new-file diff within the state budget.
  for (const path of untracked) {
    if (diff.length >= DIFF_LIMIT) break;
    const added = await pi.exec("git", ["diff", "--no-index", "--", "/dev/null", path], { cwd, signal });
    if (added.code === 0 || added.code === 1) diff += `\n${added.stdout.slice(0, DIFF_LIMIT - diff.length)}`;
  }
  return { paths: [...paths], numstat: stats, diff };
}

function jevFields(result: JevResult): Partial<Evaluation> {
  if (result.ok) {
    return {
      answers: result.answers, resolvedModel: result.resolvedModel, providerResponseId: result.providerResponseId,
      costUsd: result.costUsd, latencyMs: result.latencyMs,
    };
  }
  return { error: result.error, httpStatus: result.httpStatus, resolvedModel: result.resolvedModel, latencyMs: result.latencyMs };
}

function scoreOf(answers: JevAnswers, id: string): { score: number; confidence: number } | undefined {
  const answer = answers[id];
  return answer?.type === "score" ? { score: answer.score, confidence: answer.confidence } : undefined;
}

function noulOf(answers: JevAnswers, id: string): number | undefined {
  const answer = answers[id];
  return answer?.type === "noul" ? answer.noul : undefined;
}

type Depth = "light" | "standard" | "deep";

function classifyRisk(answers: JevAnswers): { depth: Depth; risk: number } | undefined {
  const security = scoreOf(answers, "security_risk");
  const complexity = scoreOf(answers, "complexity");
  const behaviour = scoreOf(answers, "behaviour_change");
  const missingTests = noulOf(answers, "missing_tests");
  if (!security || !complexity || !behaviour || missingTests === undefined) return undefined;
  const levels = MAX_LEVEL;
  const risk = (WEIGHT_SECURITY * security.score + WEIGHT_COMPLEXITY * complexity.score + WEIGHT_BEHAVIOUR * behaviour.score) / levels;
  const confident = [security, complexity, behaviour].every(item => item.confidence >= MIN_SCORE_CONFIDENCE);
  if (risk >= DEEP_RISK_FROM) return { depth: "deep", risk };
  if (risk < LIGHT_RISK_BELOW && confident && missingTests < MISSING_TESTS_BELOW) return { depth: "light", risk };
  return { depth: "standard", risk };
}

function deterministicDepth(paths: string[]): { depth: Depth; rule: string } | undefined {
  if (paths.some(path => SENSITIVE_PATH.test(path))) return { depth: "deep", rule: "sensitive-path" };
  if (paths.every(path => DOCS_ONLY_PATH.test(path))) return { depth: "light", rule: "docs-only" };
  return undefined;
}

/** The reviewer must not share the author's model family; unresolvable models fail closed. */
function familyViolation(models: ModelQuery, target: string): boolean {
  const author = models.resolve(AUTHOR_ROLE);
  const reviewer = models.resolve(target.replace(/:[a-z]+$/, ""));
  if (!author || !reviewer) return true;
  return models.family(author) === models.family(reviewer);
}

async function evaluateReview(
  pi: ExtensionAPI, ctx: ExtensionContext, apiKey: string | undefined,
  assignment: string, signal: AbortSignal | undefined,
): Promise<Evaluation> {
  const changes = await readChanges(pi, ctx.cwd, signal);
  if (!changes) return { stage: "deterministic", rule: "no-working-tree-diff", verdict: "standard" };
  const settled = deterministicDepth(changes.paths);
  if (settled) {
    const base: Evaluation = { stage: "deterministic", rule: settled.rule, verdict: settled.depth };
    return applyDepth(ctx, base, settled.depth, settled.rule);
  }
  if (!apiKey) return { stage: "jev_error", verdict: "standard", error: "no OpenRouter credential" };
  const shown = changes.numstat.slice(0, FILE_SUMMARY_LIMIT).map(line => line.replace(/\t/g, " ")).join("\n");
  const omittedFiles = changes.numstat.length - FILE_SUMMARY_LIMIT;
  const state: JsonValue = {
    assignment: clip(redact(assignment, apiKey), ASSIGNMENT_LIMIT_REVIEW).text,
    changed_files: redact(omittedFiles > 0 ? `${shown}\n… ${omittedFiles} more files` : shown, apiKey),
    diff: clip(redact(changes.diff, apiKey), Math.min(DIFF_LIMIT, JEV_STATE_CHAR_LIMIT)).text,
  };
  const result = await decide(apiKey, state, REVIEW_QUESTIONS, { signal, timeoutMs: ENFORCE_TIMEOUT_MS });
  const base = { state, questions: REVIEW_QUESTIONS, ...jevFields(result) };
  if (!result.ok) return { stage: "jev_error", verdict: "standard", ...base };
  const classified = classifyRisk(result.answers);
  if (!classified) return { stage: "jev_error", verdict: "standard", ...base, error: "incomplete answers" };
  return applyDepth(ctx, { stage: "jev", verdict: classified.depth, ...base }, classified.depth, `risk ${classified.risk.toFixed(2)}`);
}

/** Maps a depth to a reviewer model unless the family guard refuses it. */
function applyDepth(ctx: ExtensionContext, outcome: Evaluation, depth: Depth, reason: string): Evaluation {
  if (depth === "standard") return outcome;
  const target = depth === "light" ? LIGHT_REVIEW_MODEL : DEEP_REVIEW_MODEL;
  if (familyViolation(ctx.models, target)) return { ...outcome, rule: "family-guard" };
  return { ...outcome, apply: { model: target, note: `review-triage: ${depth} (${reason})` } };
}

async function evaluateEffort(
  apiKey: string | undefined, event: SpawnEvent, found: Correlated, signal: AbortSignal | undefined,
): Promise<Evaluation> {
  const lines = found.assignment.split("\n").filter(line => NUMBERED_LINE.test(line));
  if (lines.length >= MIN_PLAN_LINES && found.assignment.length < SHORT_PLAN_CHARS) {
    return { stage: "deterministic", rule: "explicit-short-plan", verdict: "keep" };
  }
  if (!apiKey) return { stage: "jev_error", verdict: "keep", error: "no OpenRouter credential" };
  const state: JsonValue = {
    assignment: clip(redact(found.assignment, apiKey), ASSIGNMENT_LIMIT_EFFORT).text,
    context: clip(redact(found.context, apiKey), CONTEXT_LIMIT).text,
  };
  const result = await decide(apiKey, state, EFFORT_QUESTIONS, { signal, timeoutMs: ENFORCE_TIMEOUT_MS });
  const base = { state, questions: EFFORT_QUESTIONS, ...jevFields(result) };
  if (!result.ok) return { stage: "jev_error", verdict: "keep", ...base };
  const openness = scoreOf(result.answers, "openness");
  const hasPlan = noulOf(result.answers, "has_plan");
  if (!openness || hasPlan === undefined) return { stage: "jev_error", verdict: "keep", ...base, error: "incomplete answers" };
  const raise = openness.score >= RAISE_OPENNESS ||
    (openness.score >= RAISE_OPENNESS_WITHOUT_PLAN && hasPlan < PLAN_MISSING_BELOW);
  if (!raise) return { stage: "jev", verdict: "keep", ...base };
  const reason = `openness ${openness.score.toFixed(1)}`;
  if (!event.patterns.some(pattern => ANY_EFFORT.test(pattern))) {
    return { stage: "jev", verdict: "raise", rule: "no-patterns", ...base };
  }
  if (!event.patterns.some(pattern => RAISABLE_EFFORT.test(pattern))) {
    return { stage: "jev", verdict: "raise", rule: "effort-not-lower", ...base };
  }
  const model = event.patterns.map(pattern => pattern.replace(RAISABLE_EFFORT, `:${RAISED_EFFORT}`));
  return { stage: "jev", verdict: "raise", ...base, apply: { model, note: `effort: ${RAISED_EFFORT} (${reason})` } };
}

export type SpawnDecision = SpawnResult | undefined;

/** Factory with an injectable credential so tests never touch real auth. */
export async function createJevSubagentPolicy(pi: ExtensionAPI, apiKey: string | undefined): Promise<void> {
  const modes = await readPolicyModes();
  const correlated = new Map<string, Correlated>();

  pi.on("tool_call", (event) => {
    if (event.toolName !== "task") return undefined;
    const parsed = taskInput.safeParse(event.input);
    if (!parsed.success) return undefined;
    parsed.data.tasks.forEach((item, index) => {
      remember(correlated, item.name ?? `${event.toolCallId}:${index}`, {
        assignment: item.task, context: parsed.data.context ?? "",
      });
    });
    return undefined;
  });

  async function run(
    policy: string, version: string, labels: string[], mode: PolicyMode, ctx: ExtensionContext,
    event: SpawnEvent, evaluate: (signal: AbortSignal | undefined) => Promise<Evaluation>,
  ): Promise<SpawnDecision> {
    const signal = mode === "enforce" ? AbortSignal.timeout(ENFORCE_TIMEOUT_MS) : undefined;
    let evaluation: Evaluation;
    try {
      evaluation = await evaluate(signal);
    } catch (error) {
      const stage: DecisionStage = "jev_error";
      evaluation = { stage, verdict: policy === REVIEW_POLICY ? "standard" : "keep", error: error instanceof Error ? error.message : "evaluation failed" };
    }
    const { apply, ...record } = evaluation;
    const enforced = mode === "enforce" && apply !== undefined;
    try {
      await appendDecision({
        ...record, policy, policyVersion: version, mode, labels, enforced,
        subject: redact(`${event.agent}:${event.spawnKey ?? ""}`, apiKey),
        sessionId: ctx.sessionManager.getSessionId(), agentKind: ctx.agent.kind,
      });
    } catch {
      // Audit failure must never affect the spawn.
    }
    return enforced ? apply : undefined;
  }

  pi.on("before_subagent_spawn", async (event, ctx): Promise<SpawnDecision> => {
    try {
      const isReview = REVIEW_AGENTS.includes(event.agent);
      const isEffort = event.agent === "task" && event.modelRole === "task";
      if (!isReview && !isEffort) return undefined;
      const policy = isReview ? REVIEW_POLICY : EFFORT_POLICY;
      const mode = policyMode(modes, policy);
      if (mode === "off") return undefined;
      const found = event.spawnKey ? correlated.get(event.spawnKey) : undefined;
      if (isEffort && !found) return undefined;
      const version = isReview ? REVIEW_POLICY_VERSION : EFFORT_POLICY_VERSION;
      const labels = isReview ? REVIEW_LABELS : EFFORT_LABELS;
      const evaluate = isReview
        ? (signal: AbortSignal | undefined) => evaluateReview(pi, ctx, apiKey, found?.assignment ?? "", signal)
        : (signal: AbortSignal | undefined) => evaluateEffort(apiKey, event, found ?? { assignment: "", context: "" }, signal);
      const pending = run(policy, version, labels, mode, ctx, event, evaluate);
      if (mode === "enforce") return await pending;
      pending.catch(() => undefined);
      return undefined;
    } catch {
      return undefined;
    }
  });
}

export default async function jevSubagentPolicy(pi: ExtensionAPI): Promise<void> {
  await createJevSubagentPolicy(pi, await loadJevApiKey().catch(() => undefined));
}
