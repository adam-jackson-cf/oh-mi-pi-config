import { readdir, readFile } from "node:fs/promises";
import { join } from "node:path";
import { z } from "zod";
import { appendLabel } from "../../agent/extensions/lib/jev";
import type { LabelReviewer } from "../../agent/extensions/lib/jev";
import {
  answerSchema, binaryUncertainty, choiceUncertainty, foldLabel, jsonValue, LabError, questionsSchema,
  type EffectiveLabel, type LabAnswer, type LabCase, type LabPaths,
} from "./types";

export const POLICY_PREFIX = "policy:";
const POLICY_NAME = /^[a-z0-9][a-z0-9_.-]{0,63}$/i;

const decisionLine = z.object({
  type: z.literal("decision"),
  timestamp: z.string(),
  requestId: z.string(),
  policy: z.string(),
  policyVersion: z.string().optional(),
  mode: z.string().optional(),
  sessionId: z.string().optional(),
  agentName: z.string().optional(),
  stage: z.string(),
  rule: z.string().optional(),
  subject: z.string(),
  state: jsonValue.optional(),
  questions: questionsSchema.optional(),
  answers: z.record(z.string(), answerSchema).optional(),
  resolvedModel: z.string().optional(),
  costUsd: z.number().optional(),
  latencyMs: z.number().optional(),
  error: z.string().optional(),
  verdict: z.string(),
  labels: z.array(z.string()),
});
const labelLine = z.object({
  type: z.literal("label"), requestId: z.string(), label: z.string(), note: z.string().optional(),
  reviewer: z.enum(["human", "agent"]).optional(),
});

export async function listPolicies(auditDir: string): Promise<string[]> {
  try {
    const entries = await readdir(auditDir, { withFileTypes: true });
    return entries.filter(e => e.isDirectory() && POLICY_NAME.test(e.name)).map(e => e.name).sort();
  } catch { return []; }
}

async function readJsonl<T>(file: string, schema: z.ZodType<T>): Promise<T[]> {
  let text: string;
  try { text = await readFile(file, "utf8"); } catch { return []; }
  const rows: T[] = [];
  for (const raw of text.split("\n")) {
    if (!raw) continue;
    try {
      const parsed = schema.safeParse(JSON.parse(raw));
      if (parsed.success) rows.push(parsed.data);
    } catch { /* malformed lines are skipped */ }
  }
  return rows;
}

type Headline = { score?: number; uncertainty: number };

function headline(answers: LabAnswer[]): Headline {
  const first = answers[0];
  if (!first) return { uncertainty: 1 };
  if (first.type === "noul" && first.noul !== undefined) return { score: first.noul, uncertainty: binaryUncertainty(first.noul) };
  const yes = first.probabilities?.yes;
  if (yes !== undefined) return { score: yes, uncertainty: binaryUncertainty(yes) };
  return { uncertainty: choiceUncertainty(first.probabilities) };
}

/** Load every decision of one policy with its (single) human label attached. */
export async function loadPolicyCases(paths: LabPaths, policy: string): Promise<LabCase[]> {
  if (!POLICY_NAME.test(policy)) throw new LabError("Invalid policy name.", 400);
  const dir = join(paths.auditDir, policy);
  const files = (await readdir(dir).catch(() => [])).filter(f => f.endsWith(".jsonl") && f !== "labels.jsonl").sort();
  const labels = new Map<string, EffectiveLabel>();
  for (const row of await readJsonl(join(dir, "labels.jsonl"), labelLine)) {
    labels.set(row.requestId, foldLabel(labels.get(row.requestId), { label: row.label, by: row.reviewer ?? "human", note: row.note }));
  }
  const cases: LabCase[] = [];
  for (const file of files) {
    for (const row of await readJsonl(join(dir, file), decisionLine)) {
      const answers: LabAnswer[] = Object.entries(row.answers ?? {}).map(([id, answer]) => ({ id, ...answer }));
      const applied = labels.get(row.requestId);
      cases.push({
        source: POLICY_PREFIX + policy,
        id: row.requestId,
        timestamp: row.timestamp,
        subject: row.subject,
        stage: row.stage,
        verdict: row.verdict,
        ...headline(answers),
        policyVersion: row.policyVersion,
        state: row.state ?? null,
        questions: row.questions,
        answers,
        resolvedModel: row.resolvedModel,
        costUsd: row.costUsd,
        latencyMs: row.latencyMs,
        error: row.error,
        labelOptions: row.labels,
        label: applied?.label ?? null,
        labelBy: applied?.by ?? null,
        labelNote: applied?.note,
        note: [row.rule ? `rule ${row.rule}` : "", row.mode ? `mode ${row.mode}` : ""].filter(Boolean).join(" · ") || undefined,
      });
    }
  }
  return cases;
}

/**
 * Append a policy label through the shared `appendLabel`. A human label is final; an agent label
 * is a first pass that a human may replace and that no later agent label may overwrite.
 */
export async function appendPolicyLabel(
  paths: LabPaths, policy: string, requestId: string, label: string, reviewer: LabelReviewer, note?: string,
): Promise<void> {
  const target = (await loadPolicyCases(paths, policy)).find(c => c.id === requestId);
  if (!target) throw new LabError("No decision with that request ID in this policy audit.", 404);
  if (target.labelBy === "human") throw new LabError("This decision already has a human label.", 409);
  if (reviewer === "agent" && target.label !== null) throw new LabError("This decision already has a label.", 409);
  if (!target.labelOptions.includes(label)) throw new LabError(`Label must be one of ${target.labelOptions.join(", ")}.`, 400);
  await appendLabel({ requestId, policy, label, reviewer, note });
}
