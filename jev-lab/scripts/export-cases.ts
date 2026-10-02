#!/usr/bin/env bun
// Export a Jev lab source as portable evaluate-jev case lines
// (agent/skills/evaluate-jev/references/case-format.md), with effective labels applied.
// Usage: bun jev-lab/scripts/export-cases.ts --source <jev-scope|policy:<name>|caseset:<name>>
//          [--out <cases.jsonl>] [--sessions <dir>] [--audit <dir>]
//        bun jev-lab/scripts/export-cases.ts --list
// Writes all versions; summarize.ts selects the version and window. Without --out, prints to stdout.
import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { SCOPE_SOURCE, scopeAuditFiles } from "../lib/scope";
import { listSources, loadCases } from "../lib/sources";
import { defaultPaths } from "../server";
import { z } from "zod";

const failureLine = z.object({ type: z.literal("failure"), timestamp: z.string().optional(), reason: z.string().optional() });

const args = new Map<string, string>();
for (let i = 2; i < process.argv.length; i++) {
  const key = process.argv[i]!.replace(/^--/, "");
  const next = process.argv[i + 1];
  if (next === undefined || next.startsWith("--")) args.set(key, "");
  else { args.set(key, next); i++; }
}
const paths = defaultPaths();
if (args.get("sessions")) paths.sessionsDir = args.get("sessions")!;
if (args.get("audit")) paths.auditDir = args.get("audit")!;

if (args.has("list")) {
  console.log(JSON.stringify(await listSources(paths), null, 2));
  process.exit(0);
}
const source = args.get("source");
if (!source) {
  console.error("--source is required (see --list)");
  process.exit(2);
}

const lines: string[] = [];
for (const c of await loadCases(paths, source)) {
  lines.push(JSON.stringify({
    type: "case", id: c.id, timestamp: c.timestamp, version: c.version, stage: c.stage, subject: c.subject,
    state: c.state, questions: c.questions, answers: c.answers, verdict: c.verdict, score: c.score,
    label: c.label ?? undefined, labelBy: c.labelBy ?? undefined, labelNote: c.labelNote,
    labelOptions: c.labelOptions, positiveLabel: c.positiveLabel, sufficient: c.sufficient,
    group: c.sessionKind, evidence: c.transcriptPath, resolvedModel: c.resolvedModel, error: c.error,
    costUsd: c.costUsd, latencyMs: c.latencyMs,
    // Kept for the jev-scope profile; ignored by the summarizer.
    taskSource: c.taskSource, agentId: c.agentId, note: c.note, expected: c.expected,
  }));
}
// The watchdog also logs reviews that failed before a request existed.
if (source === SCOPE_SOURCE) {
  for (const rel of await scopeAuditFiles(paths.sessionsDir)) {
    for (const line of (await readFile(join(paths.sessionsDir, rel), "utf8")).split("\n")) {
      if (!line.includes("\"failure\"")) continue;
      let raw: unknown;
      try { raw = JSON.parse(line); } catch { continue; /* malformed lines are counted by the loader */ }
      const row = failureLine.safeParse(raw);
      if (row.success) lines.push(JSON.stringify({ type: "failure", timestamp: row.data.timestamp, reason: row.data.reason ?? "unknown" }));
    }
  }
}

const text = lines.length ? lines.join("\n") + "\n" : "";
const out = args.get("out");
if (out) {
  await writeFile(out, text, { mode: 0o600 });
  console.error(`exported ${lines.length} lines from ${source} to ${out}`);
} else {
  process.stdout.write(text);
}
