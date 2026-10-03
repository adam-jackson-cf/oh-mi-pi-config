/**
 * Jev proportionality watchdog (`jev-scope`): when the main session marks a todo item done, judge whether the
 * implementation made while that item was in progress went beyond the item. The state (user requests, the item,
 * the todo list, the approved plan, every state-changing action and code-computed facts, in full up to Jev's
 * context budget) is built by `lib/proportionality.ts`. Code decides first: an unrequested commit or push is
 * flagged without Jev, and items with no flagged fact are not sent; Jev judges the rest with one Choice.
 * Subagent sessions are not reviewed.
 *
 * Mode (`agent/jev-policies.json`, key `jev-scope`, read at session start): `off` does nothing, `shadow`
 * audits only, `enforce` also sends the agent a non-blocking advisory. Never blocks work.
 * Audit: `<session-trace-stem>/jev-watchdog-requests.jsonl` (0600); set JEV_WATCHDOG_AUDIT=0 to disable.
 */
import { createHash, randomUUID } from "node:crypto";
import { constants } from "node:fs";
import { mkdir, open, readdir, readFile } from "node:fs/promises";
import { basename, isAbsolute, join, relative, resolve } from "node:path";
import type { ExtensionAPI, ExtensionContext, ToolResultEvent } from "@oh-my-pi/pi-coding-agent";
import { z } from "zod";
import { decide, JEV_MODEL, policyMode, readPolicyModes, type JevSuccess, type PolicyMode } from "./lib/jev.ts";
import { loadJevApiKey } from "./lib/jev-auth.ts";
import { buildItemState, countMessages, ITEM_POLICY_VERSION, ITEM_QUESTIONS, parseSession, pBeyond, routed, textOf, type ItemState } from "./lib/proportionality.ts";

export const POLICY = "jev-scope";
export const POLICY_VERSION = ITEM_POLICY_VERSION;
/**
 * P(beyond the item) bands for `pBeyond` (P(oversized) + P(outside)), chosen on the E4 history dev split
 * (jev-lab/fastino-bench, 392 blind-labelled items, 14 overreach) and checked once on the test split (164, 7):
 * with the commit rule and router, 0.8 caught 13/14 dev and 6/7 test with 5/378 and 4/157 false notes.
 */
export const ADVISE_THRESHOLD = 0.7;
export const ASK_USER_THRESHOLD = 0.8;
const REVIEW_TIMEOUT_MS = 60_000;
const AUDIT_FILE = "jev-watchdog-requests.jsonl";

export type RuntimeAction = "continue" | "advise" | "ask_user";
export const runtimeAction = (pYes: number): RuntimeAction =>
  pYes >= ASK_USER_THRESHOLD ? "ask_user" : pYes >= ADVISE_THRESHOLD ? "advise" : "continue";
/** Who decided: the unrequested-commit rule, the router (nothing flagged, no Jev call), or Jev. */
type Stage = "rule" | "router" | "jev";

type AuditSession = { sessionId: string; sessionFile: string; sessionKind?: "main" | "sub"; agentId?: string };
type FailureReason = "missing_key" | "no_user_request" | "state_build" | "jev_error";
type AuditRecord =
  | { type: "request"; requestId: string; requestSha256: string; item: { text: string; phase: string };
      budget: { estimatedTokens: number; changeShare: number };
      request: { model: string; state: ItemState; questions: object } }
  | { type: "outcome"; requestId: string;
      decision?: { stage: Stage; choice: "yes" | "no"; probabilities: { yes: number; no: number }; confidence: number;
        threshold: number; reviewCandidate: boolean; runtimeAction: RuntimeAction; complete: boolean;
        components?: { worst_change: { choice: string; probabilities: Record<string, number>; confidence: number } } };
      usage?: { inputTokens: number; costUsd: number }; latencyMs: number;
      resolvedModel?: string; providerResponseId?: string;
      error?: { stopReason: "error"; reason: FailureReason; httpStatus?: number; message: string } }
  | { type: "failure"; requestId: string; stage: "state" | "key"; reason: FailureReason; item: { text: string; phase: string } }
  | { type: "reviewer_outcome"; requestId: string; label: "overreach" | "no_overreach" | "uncertain"; reviewer: "human" };
type OutcomeRecord = Extract<AuditRecord, { type: "outcome" }>;

async function appendAudit(session: AuditSession, record: AuditRecord): Promise<void> {
  const directory = session.sessionFile.slice(0, -".jsonl".length);
  await mkdir(directory, { recursive: true, mode: 0o700 });
  const handle = await open(join(directory, AUDIT_FILE), constants.O_WRONLY | constants.O_CREAT | constants.O_APPEND | constants.O_NOFOLLOW, 0o600);
  try {
    await handle.chmod(0o600);
    await handle.writeFile(JSON.stringify({
      timestamp: new Date().toISOString(),
      sessionId: session.sessionId, sessionFile: session.sessionFile, sessionKind: session.sessionKind,
      agentId: session.agentId, ...record,
    }) + "\n");
  } finally {
    await handle.close();
  }
}

const auditRow = z.object({
  type: z.string().optional(), requestId: z.string().optional(), sessionId: z.string().optional(),
  sessionFile: z.string().optional(), sessionKind: z.enum(["main", "sub"]).optional(), agentId: z.string().optional(),
});
type AuditRow = z.infer<typeof auditRow>;

/** Rows of one audit file that mention the request; an absent file has none. */
async function requestRows(file: string, requestId: string): Promise<AuditRow[]> {
  let content: string;
  try {
    const handle = await open(file, constants.O_RDONLY | constants.O_NOFOLLOW);
    try { content = await handle.readFile("utf8"); } finally { await handle.close(); }
  } catch (error) {
    if (error instanceof Error && "code" in error && error.code === "ENOENT") return [];
    throw error;
  }
  const rows: AuditRow[] = [];
  for (const line of content.split("\n")) {
    if (!line.includes(requestId)) continue;
    let parsed: AuditRow;
    try { parsed = auditRow.parse(JSON.parse(line)); }
    catch { throw new Error("Jev audit contains a malformed record."); }
    if (parsed.requestId === requestId) rows.push(parsed);
  }
  return rows;
}

/**
 * Label an outcome in this session's audit, or in a legacy subagent audit below it (the advisor-era watchdog
 * also reviewed subagents). The label is appended to the file that holds the request, under its identity.
 */
export async function labelOutcome(session: AuditSession, requestId: string, label: "overreach" | "no_overreach" | "uncertain"): Promise<void> {
  const directory = session.sessionFile.slice(0, -".jsonl".length);
  const nested = (await readdir(directory, { recursive: true }).catch(() => []))
    .filter(rel => basename(rel) === AUDIT_FILE && rel !== AUDIT_FILE).sort();
  for (const file of [AUDIT_FILE, ...nested]) {
    const rows = await requestRows(join(directory, file), requestId);
    if (!rows.length) continue;
    if (!rows.some(row => row.type === "outcome")) throw new Error("No Jev outcome with that request ID in this session audit.");
    if (rows.some(row => row.type === "reviewer_outcome")) throw new Error("This Jev outcome already has a human label.");
    const owner = rows[0]!;
    const target: AuditSession = file === AUDIT_FILE || !owner.sessionFile?.endsWith(".jsonl") ? session : {
      sessionId: owner.sessionId ?? session.sessionId, sessionFile: owner.sessionFile,
      sessionKind: owner.sessionKind, agentId: owner.agentId,
    };
    await appendAudit(target, { type: "reviewer_outcome", requestId, label, reviewer: "human" });
    return;
  }
  throw new Error("No Jev outcome with that request ID in this session audit.");
}

// ---- Approved plan --------------------------------------------------------------------------
type PlanOrigin = "plan_mode" | "plan_approval" | "plan_yolo_handoff" | "user_named" | "agent_created";
type PlanSource = { path: string; origin: PlanOrigin };
const SCHEME_TARGET = /^[a-z][a-z0-9+.-]*:\/\//i;
const planFilePath = z.string().min(1);
// Plan mode requires the canonical plan at local://<slug>-plan.md; print-mode approval names it in its handoff.
const HANDOFF_PLAN = /local:\/\/[\w-]+-plan\.md/;
// Interactive approval renders the plan into a developer message: `Plan approved.` ... `<plan path="local://x-plan.md">`.
const APPROVAL_PLAN = /^Plan approved\.[\s\S]*?<plan path="([^"\n]+)">/;
const PLAN_DOC = "[\\w./@-]+\\.(?:md|markdown|txt|rst)";
// A user request that names the document to implement: "implement docs/plan.md", "the plan in docs/x.md".
const NAMED_PLAN = new RegExp(
  `\\b(?:(?:implement|execute|follow|carry out|work from|work through|build from|according to|as per|based on)\\s+(?:the\\s+)?` +
  `(?:(?:plan|spec(?:ification)?|design)(?:\\s+file)?\\s+(?:in|at|from)\\s+)?|(?:plan|spec(?:ification)?)(?:\\s+file)?\\s+(?:in|at|from)\\s+)` +
  `\`?(${PLAN_DOC})\`?`, "i");
// A user request that asks the agent to author a plan document, optionally naming where.
const PLAN_AUTHORING = /\b(?:write|create|draft|make|produce|prepare|save|put)\b[^.\n]{0,60}\b(?:plan|spec(?:ification)?|design doc|roadmap)\b/i;
const AUTHORING_DEST = new RegExp(`\\b(?:to|in|at|into|as|called|named)\\s+\`?(${PLAN_DOC})\`?`, "i");
const PLAN_BASENAME = /(?:plan|spec|design|proposal|roadmap)[^/]*\.(?:md|markdown|txt|rst)$/i;

function withinCwd(path: string, cwd: string): boolean {
  const rel = relative(resolve(cwd), resolve(cwd, path));
  return !!rel && !rel.startsWith("..") && !isAbsolute(rel);
}

/**
 * The plan governing scope for this session, latest source wins: an interactive plan-mode `mode_change`, a
 * print-mode `plan-yolo-handoff` message, a user request naming the plan document to implement, or the first
 * file the agent writes in response to a request to author a plan. Inferred paths stay inside the workspace.
 */
function approvedPlan(branch: ReturnType<ExtensionContext["sessionManager"]["getBranch"]>, cwd: string): PlanSource | undefined {
  let latest: PlanSource | undefined;
  let authoring: { dest?: string } | undefined;
  for (const entry of branch) {
    if (entry.type === "mode_change" && entry.mode === "plan") {
      const parsed = planFilePath.safeParse(entry.data?.planFilePath);
      if (parsed.success) latest = { path: parsed.data, origin: "plan_mode" };
    } else if (entry.type === "custom_message" && entry.customType === "plan-yolo-handoff") {
      const named = textOf(entry.content).match(HANDOFF_PLAN)?.[0];
      if (named) latest = { path: named, origin: "plan_yolo_handoff" };
    } else if (entry.type === "message" && entry.message.role === "developer") {
      const approved = textOf(entry.message.content).match(APPROVAL_PLAN)?.[1];
      if (approved) latest = { path: approved, origin: "plan_approval" };
    } else if (entry.type === "message" && entry.message.role === "user" && entry.message.attribution !== "agent") {
      const text = textOf(entry.message.content);
      authoring = PLAN_AUTHORING.test(text) ? { dest: text.match(AUTHORING_DEST)?.[1] } : undefined;
      const named = text.match(NAMED_PLAN)?.[1];
      if (named && withinCwd(named, cwd)) latest = { path: named, origin: "user_named" };
    } else if (authoring && entry.type === "message" && entry.message.role === "assistant") {
      for (const part of entry.message.content) {
        if (part.type !== "toolCall" || part.name !== "write") continue;
        const path = z.string().safeParse(part.arguments.path);
        if (!path.success || SCHEME_TARGET.test(path.data) || !withinCwd(path.data, cwd)) continue;
        const wanted = authoring.dest ? resolve(cwd, path.data) === resolve(cwd, authoring.dest) : PLAN_BASENAME.test(path.data);
        if (!wanted) continue;
        latest = { path: path.data, origin: "agent_created" };
        authoring = undefined;
        break;
      }
    }
  }
  return latest;
}

/** The approved plan's current text; an unreadable plan is omitted rather than failing the review. */
async function readPlan(plan: PlanSource | undefined, cwd: string, localRoot: string | undefined): Promise<{ path: string; text: string } | null> {
  if (!plan) return null;
  const local = plan.path.match(/^local:\/\/(.+)$/);
  if (local && !localRoot) return null;
  const file = local ? resolve(localRoot!, local[1]!) : isAbsolute(plan.path) ? plan.path : resolve(cwd, plan.path);
  try { return { path: plan.path, text: (await readFile(file, "utf8")).trim() }; } catch { return null; }
}

// ---- Review -------------------------------------------------------------------------------
type ReviewInput = { pi: ExtensionAPI; ctx: ExtensionContext; event: ToolResultEvent; mode: PolicyMode; apiKey: string | undefined; audit: boolean };

/**
 * The branch with this todo call's result appended: `tool_result` fires before OMP records the result, and
 * the assistant message holding the call may not be in the branch yet either.
 */
function branchWithResult(ctx: ExtensionContext, event: ToolResultEvent): unknown[] {
  const branch: unknown[] = ctx.sessionManager.getBranch().filter(entry =>
    !(entry.type === "message" && entry.message.role === "toolResult" && entry.message.toolCallId === event.toolCallId));
  const hasCall = ctx.sessionManager.getBranch().some(entry => entry.type === "message" && entry.message.role === "assistant" &&
    entry.message.content.some(part => part.type === "toolCall" && part.id === event.toolCallId));
  if (!hasCall) {
    branch.push({ type: "message", message: { role: "assistant", content: [{ type: "toolCall", id: event.toolCallId, name: "todo", arguments: event.input }] } });
  }
  branch.push({ type: "message", message: { role: "toolResult", toolCallId: event.toolCallId, content: event.content, isError: false } });
  return branch;
}

async function review({ pi, ctx, event, mode, apiKey, audit }: ReviewInput): Promise<void> {
  const manager = ctx.sessionManager;
  const cwd = manager.getCwd();
  const source = approvedPlan(manager.getBranch(), cwd);
  const entries = branchWithResult(ctx, event);
  // "Approve and execute" starts a fresh session whose only instruction is the approval message: the
  // approved plan is then the request, stated as such rather than borrowed from another session.
  if (source?.origin === "plan_approval" && !parseSession(entries, cwd).users.length) {
    entries.unshift({ type: "message", message: { role: "user", content: `[plan approved by the user] Execute the approved plan ${source.path}.` } });
  }
  const parsed = parseSession(entries, cwd);
  // Only items completed by this call: their done index is the appended result's position.
  const doneIdx = countMessages(entries);
  const events = parsed.events.filter(e => e.doneIdx === doneIdx);
  if (!events.length) return;
  const sessionFile = manager.getSessionFile();
  const session: AuditSession | undefined = audit && sessionFile?.endsWith(".jsonl")
    ? { sessionId: manager.getSessionId(), sessionFile, sessionKind: ctx.agent.kind, agentId: ctx.agent.id } : undefined;
  const artifacts = manager.getArtifactsDir();
  const plan = await readPlan(source, cwd, artifacts ? join(artifacts, "local") : undefined);
  for (const done of events) {
    const requestId = `jev_${randomUUID()}`;
    const item = { text: done.item.text, phase: done.item.phase };
    const built = buildItemState(parsed, done, plan);
    if (!built) {
      if (session) await appendAudit(session, { type: "failure", requestId, stage: "state", reason: "no_user_request", item });
      continue;
    }
    // No state-changing action while the item was in progress: nothing visible can go beyond it.
    if (!built.state.implementation.actions.length) continue;
    // Code decides what it can (an unrequested commit; nothing a scope check could object to); Jev judges the rest.
    const stage: Stage = built.state.facts.unrequested_commit ? "rule" : routed(built.state.facts) ? "jev" : "router";
    if (stage === "jev" && !apiKey) {
      if (session) await appendAudit(session, { type: "failure", requestId, stage: "key", reason: "missing_key", item });
      continue;
    }
    // Scrub the key itself in case any transcript text echoed it.
    // SAFETY: Replacing the key inside the JSON text of an ItemState only rewrites string contents, so the parsed value keeps ItemState's shape.
    const state = apiKey ? JSON.parse(JSON.stringify(built.state).replaceAll(apiKey, "[REDACTED]")) as ItemState : built.state;
    const body = JSON.stringify({ model: JEV_MODEL, state, questions: ITEM_QUESTIONS });
    if (session) {
      await appendAudit(session, { type: "request", requestId, requestSha256: createHash("sha256").update(body).digest("hex"), item,
        budget: { estimatedTokens: built.estimatedTokens, changeShare: Number(built.changeShare.toFixed(3)) },
        request: { model: JEV_MODEL, state, questions: ITEM_QUESTIONS } });
    }
    let yes: number;
    let answer: { choice: string; probabilities: Record<string, number>; confidence: number } | undefined;
    let usage: { result: JevSuccess } | undefined;
    if (stage === "jev") {
      const result = await decide(apiKey!, state, ITEM_QUESTIONS, { timeoutMs: REVIEW_TIMEOUT_MS });
      if (!result.ok) {
        if (session) {
          await appendAudit(session, { type: "outcome", requestId, latencyMs: result.latencyMs, resolvedModel: result.resolvedModel,
            error: { stopReason: "error", reason: "jev_error", httpStatus: result.httpStatus, message: result.error } });
        }
        continue;
      }
      const worst = result.answers.worst_change;
      yes = pBeyond(worst) ?? 0;
      if (worst?.type === "choice") answer = { choice: worst.choice, probabilities: worst.probabilities, confidence: worst.confidence };
      usage = { result };
    } else {
      yes = stage === "rule" ? 1 : 0;
    }
    const action = runtimeAction(yes);
    if (session) {
      const decision: NonNullable<OutcomeRecord["decision"]> = { stage, choice: yes >= 0.5 ? "yes" : "no", probabilities: { yes, no: 1 - yes },
        confidence: Math.abs(2 * yes - 1), threshold: ADVISE_THRESHOLD,
        reviewCandidate: action !== "continue" && (stage === "rule" || state.completeness.complete),
        runtimeAction: action, complete: state.completeness.complete };
      if (answer) decision.components = { worst_change: answer };
      const outcome: OutcomeRecord = { type: "outcome", requestId, decision, latencyMs: usage?.result.latencyMs ?? 0 };
      if (usage) {
        outcome.usage = { inputTokens: usage.result.inputTokens, costUsd: usage.result.costUsd };
        outcome.resolvedModel = usage.result.resolvedModel;
        outcome.providerResponseId = usage.result.providerResponseId;
      }
      await appendAudit(session, outcome);
    }
    if (mode === "enforce" && action !== "continue") {
      const note = stage === "rule"
        ? `The work for "${item.text}" committed or pushed although nothing in the request, plan or todo list asks for a commit. Tell the user what was committed or pushed and ask whether to keep it.`
        : action === "ask_user"
          ? `Jev rates the work for "${item.text}" as likely beyond that plan item (P=${yes.toFixed(2)}, most out-of-scope class: ${answer?.choice ?? "?"}). Before continuing, tell the user which change went beyond the item and ask whether to keep it.`
          : `Jev rates the work for "${item.text}" as possibly beyond that plan item (P=${yes.toFixed(2)}). Check for any change the item did not need; keep going if none.`;
      pi.sendMessage({
        customType: "advisor",
        content: `<advisory advisor="${POLICY}" severity="${action === "ask_user" ? "concern" : "note"}" guidance="weigh, don't blindly obey">\n${note}\n</advisory>`,
        display: true, attribution: "agent",
        details: { notes: [{ advisor: POLICY, note, severity: action === "ask_user" ? "concern" : "note" }], requestId },
      }, { deliverAs: "steer" });
    }
  }
}

export type WatchdogOptions = { loadKey?: () => Promise<string | undefined> };

/** Factory for the extension; `idle()` resolves when every started review has finished (tests, harnesses). */
export function createJevWatchdog(options: WatchdogOptions = {}) {
  const pending = new Set<Promise<void>>();
  const audit = process.env.JEV_WATCHDOG_AUDIT !== "0";
  let mode: PolicyMode = "shadow";
  let apiKey: string | undefined;
  const extension = (pi: ExtensionAPI): void => {
    pi.on("session_start", async () => {
      try { mode = policyMode(await readPolicyModes(), POLICY); } catch { mode = "shadow"; }
      try { apiKey = await options.loadKey?.(); } catch { apiKey = undefined; }
    });
    pi.on("tool_result", (event, ctx) => {
      if (event.toolName !== "todo" || event.isError || ctx.agent.kind !== "main" || mode === "off") return;
      const work = review({ pi, ctx, event, mode, apiKey, audit }).catch(() => undefined);
      const tracked = work.finally(() => pending.delete(tracked));
      pending.add(tracked);
    });
    pi.registerCommand("jev-label", {
      description: "Label a Jev proportionality outcome in this session: /jev-label <requestId> overreach|no_overreach|uncertain",
      handler: async (args, ctx) => {
        const [requestId, label, extra] = args.trim().split(/\s+/);
        if (!audit || !/^jev_[0-9a-f-]{36}$/i.test(requestId ?? "") ||
            (label !== "overreach" && label !== "no_overreach" && label !== "uncertain") || extra) {
          ctx.ui.notify("Use /jev-label <requestId> overreach|no_overreach|uncertain with Jev audit enabled.", "warning");
          return;
        }
        const sessionFile = ctx.sessionManager.getSessionFile();
        if (!sessionFile?.endsWith(".jsonl")) {
          ctx.ui.notify("Jev labels require a persistent session trace.", "warning");
          return;
        }
        try {
          await labelOutcome({ sessionId: ctx.sessionManager.getSessionId(), sessionFile, sessionKind: ctx.agent.kind, agentId: ctx.agent.id }, requestId!, label);
          ctx.ui.notify("Jev outcome labeled for later evaluation.", "info");
        } catch (error) {
          ctx.ui.notify(error instanceof Error ? error.message : "Could not label Jev outcome.", "error");
        }
      },
    });
  };
  return { extension, idle: async () => { while (pending.size) await Promise.all(pending); } };
}

export default function jevWatchdog(pi: ExtensionAPI): void {
  createJevWatchdog({ loadKey: loadJevApiKey }).extension(pi);
}
