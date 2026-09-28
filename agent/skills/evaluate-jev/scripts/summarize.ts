#!/usr/bin/env bun
// Summarize Jev watchdog audit logs for rubric-based evaluation.
// Usage: bun summarize.ts [--root <sessionsDir>] [--policy <policy_version>] [--since <ISO>] [--cases <out.jsonl>]
// Reads only request/outcome/reviewer_outcome records; never prints provider bodies or credentials.
import { Glob } from "bun";
import { homedir } from "node:os";
import { join } from "node:path";
import { z } from "zod";

const args = new Map<string, string>();
for (let i = 2; i < process.argv.length; i += 2) args.set(process.argv[i]!.replace(/^--/, ""), process.argv[i + 1] ?? "");
const root = args.get("root") ?? join(homedir(), ".omp/agent/sessions");
const policy = args.get("policy");
const since = args.get("since");

// Every field is optional: session header lines and older records omit most of them.
const AuditLine = z.object({
  type: z.string().optional(),
  requestId: z.string().optional(),
  timestamp: z.string().optional(),
  label: z.string().optional(),
  request: z.object({
    state: z.object({
      policy_version: z.string().optional(),
      task_context: z.object({
        source: z.string().optional(),
        recent_user_requests: z.array(z.string()).optional(),
        omitted_earlier_requests: z.boolean().optional(),
        clipped_requests: z.boolean().optional(),
      }).optional(),
      constraints: z.object({
        recent_instructions: z.array(z.string()).optional(),
        omitted_earlier_instructions: z.boolean().optional(),
        clipped_instructions: z.boolean().optional(),
        source: z.string().optional(),
      }).optional(),
      approved_plan: z.object({
        path: z.string().nullable().optional(),
        excerpt: z.string().optional(),
        clipped: z.boolean().optional(),
        todo_items: z.string().optional(),
        source: z.string().optional(),
      }).optional(),
      agent_activity: z.object({ excerpt: z.string().optional() }).optional(),
    }).optional(),
  }).optional(),
  decision: z.object({
    choice: z.string().optional(),
    probabilities: z.record(z.string(), z.number()).optional(),
    reviewCandidate: z.boolean().optional(),
  }).optional(),
  error: z.object({ stopReason: z.string().optional() }).optional(),
  resolvedModel: z.string().optional(),
});
type Row = z.infer<typeof AuditLine> & { file: string };
const rows: Row[] = [];
let malformed = 0;
for (const rel of new Glob("**/jev-watchdog-requests.jsonl").scanSync({ cwd: root, dot: true })) {
  for (const line of (await Bun.file(join(root, rel)).text()).split("\n")) {
    if (!line) continue;
    try {
      const parsed = AuditLine.safeParse(JSON.parse(line));
      if (parsed.success) rows.push({ file: rel, ...parsed.data });
      else malformed++;
    } catch { malformed++; }
  }
}

const outcomes = new Map(rows.filter(r => r.type === "outcome").map(r => [r.requestId, r]));
const labels = new Map(rows.filter(r => r.type === "reviewer_outcome").map(r => [r.requestId, r.label]));
const cases = rows
  .filter(r => r.type === "request")
  .filter(r => !policy || r.request?.state?.policy_version === policy)
  .filter(r => !since || (r.timestamp ?? "") >= since)
  .map(r => {
    const o = outcomes.get(r.requestId);
    const tc = r.request?.state?.task_context ?? {};
    return {
      requestId: r.requestId,
      timestamp: r.timestamp,
      session: r.file.replace(/\/jev-watchdog-requests\.jsonl$/, ""),
      policy: r.request?.state?.policy_version ?? "unversioned",
      taskSource: tc.source ?? "absent",
      userRequests: (tc.recent_user_requests ?? []).map((s: string) => s.slice(0, 300)),
      omittedEarlier: Boolean(tc.omitted_earlier_requests),
      clipped: Boolean(tc.clipped_requests),
      constraints: r.request?.state?.constraints ?? [],
      approvedPlan: r.request?.state?.approved_plan ?? null,
      activity: String(r.request?.state?.agent_activity?.excerpt ?? "").slice(0, 1200),
      choice: o?.decision?.choice ?? (o?.error ? `error:${o.error.stopReason}` : "no-outcome"),
      probabilities: o?.decision?.probabilities,
      reviewCandidate: Boolean(o?.decision?.reviewCandidate),
      resolvedModel: o?.resolvedModel ?? "absent",
      humanLabel: labels.get(r.requestId) ?? null,
    };
  });

const tally = (f: (c: (typeof cases)[number]) => string) =>
  cases.reduce<Record<string, number>>((m, c) => ((m[f(c)] = (m[f(c)] ?? 0) + 1), m), {});
const yesP = cases.map(c => c.probabilities?.yes).filter((p): p is number => Number.isFinite(p)).sort((a, b) => a - b);

console.log(JSON.stringify({
  root, filters: { policy: policy ?? null, since: since ?? null }, malformedLines: malformed,
  requests: cases.length,
  byPolicy: tally(c => c.policy),
  byChoice: tally(c => c.choice),
  byTaskSource: tally(c => c.taskSource),
  choiceByTaskSource: tally(c => `${c.taskSource}:${c.choice}`),
  byResolvedModel: tally(c => c.resolvedModel),
  reviewCandidates: cases.filter(c => c.reviewCandidate).length,
  humanLabelled: cases.filter(c => c.humanLabel).length,
  pYes: yesP.length ? { min: yesP[0], median: yesP[yesP.length >> 1], max: yesP.at(-1), atLeast0_5: yesP.filter(p => p >= 0.5).length } : null,
}, null, 2));

const out = args.get("cases");
if (out) {
  await Bun.write(out, cases.map(c => JSON.stringify(c)).join("\n") + "\n");
  console.log(`cases written: ${out}`);
}
