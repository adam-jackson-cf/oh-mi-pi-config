#!/usr/bin/env bun
// E4 full-history replay: every todo item marked done in a main session that the shipped watchdog would review
// (agent/extensions/lib/proportionality.ts: a single item, a preceding user request, at least one state-changing
// action), as item-state cases. Split by session into dev and test (S1: tune on dev only; evaluate test once per
// decision point). Events already in real.jsonl keep their e4-real-NNN id so earlier labels still join.
// Ids end in `#${STATE_TAG}`: bump the tag whenever the state builder changes so Jev runs (keyed by id) re-run.
// `history-itemstate-v1.jsonl` keeps the first build (`#hist`, hashline edits without result diffs) that was labelled.
// Usage: bun builders/e4-history.ts   -> datasets/e4-proportionality/history-itemstate.jsonl
import { createHash } from "node:crypto";
import { readFileSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { Glob } from "bun";
import { z } from "zod";
import { buildItemState, ITEM_QUESTIONS, parseSession } from "../../../agent/extensions/lib/proportionality";
import { BENCH_ROOT } from "../lib/cases";
import type { BenchCase, JsonValue } from "../lib/types";
import { parseJsonValue, readCases } from "./e4-case-schema";

const SESSIONS = "/Users/adamjackson/.omp/agent/sessions";
const DIR = join(BENCH_ROOT, "datasets", "e4-proportionality");
// Sessions still being written (this one) and the watchdog smoke runs are not history.
const SETTLED_MS = 2 * 60 * 60 * 1000;
const EXCLUDE_SLUG = /jevwd-smoke|fastino-bench/;
// Test share by session hash. Fixed: changing it re-shuffles which cases have been seen.
const TEST_MOD = 3;
// Label sample: at most this many items per session (lowest id hash), so a few long sessions do not dominate.
const LABEL_CAP = 8;
// State tag: hist2 = edits shown as their result diffs; hist3 = plus code facts/flags and the worst_change Choice (.2);
// hist4 = git global options recognised in the commit/VCS rules (.3).
const STATE_TAG = "hist4";

const sessionHeader = z.object({ type: z.literal("session"), cwd: z.string() });
const known = new Map<string, string>();
for (const c of readCases(join(DIR, "real.jsonl"))) {
  known.set(`${c.meta?.cwd_slug}/${c.meta?.session}#${c.meta?.done_msg_idx}`, c.id);
}

const hash = (s: string) => createHash("sha256").update(s).digest("hex");
const stats = { sessions: 0, withEvents: 0, events: 0, noRequest: 0, noActions: 0, kept: 0, known: 0, dev: 0, test: 0, cut: 0 };
const out: BenchCase[] = [];
const now = Date.now();
for (const rel of [...new Glob("*/*.jsonl").scanSync(SESSIONS)].sort()) {
  const [slug = "", file = ""] = rel.split("/");
  if (EXCLUDE_SLUG.test(slug) || now - statSync(join(SESSIONS, rel)).mtimeMs < SETTLED_MS) continue;
  stats.sessions++;
  const session = file.slice(0, -".jsonl".length);
  const entries: JsonValue[] = readFileSync(join(SESSIONS, rel), "utf8").split("\n").filter(Boolean)
    .map(l => { try { return JSON.parse(l); } catch { return null; } });
  const header = entries.map(e => sessionHeader.safeParse(e)).find(r => r.success);
  const parsed = parseSession(entries, header?.success ? header.data.cwd : "");
  if (parsed.events.length) stats.withEvents++;
  const split = parseInt(hash(session).slice(0, 8), 16) % TEST_MOD === 0 ? "test" : "dev";
  for (const event of parsed.events) {
    stats.events++;
    const built = buildItemState(parsed, event, null);
    if (!built) { stats.noRequest++; continue; }
    if (!built.state.implementation.actions.length) { stats.noActions++; continue; }
    const key = `${slug}/${session}#${event.doneIdx}`;
    const prior = known.get(key);
    if (prior) stats.known++;
    if (!built.state.completeness.complete) stats.cut++;
    stats.kept++;
    stats[split]++;
    out.push({
      id: `${prior ?? `e4-hist-${hash(key).slice(0, 10)}`}#${STATE_TAG}`, experiment: "e4-proportionality", dataset: "history-itemstate",
      state: parseJsonValue(JSON.stringify(built.state)), questions: ITEM_QUESTIONS, truth: {}, primary: "worst_change",
      meta: { source_id: prior ?? `e4-hist-${hash(key).slice(0, 10)}`, cwd_slug: slug, session, done_msg_idx: event.doneIdx,
        split, variant: "item-state", estimated_tokens: built.estimatedTokens, change_share: Number(built.changeShare.toFixed(3)),
        actions: built.state.implementation.actions.length },
    });
  }
}
// Label-sample flag: per session, the LABEL_CAP cases with the lowest source-id hash (stable across state tags).
const bySession = new Map<string, BenchCase[]>();
for (const c of out) bySession.set(String(c.meta?.session), [...(bySession.get(String(c.meta?.session)) ?? []), c]);
let sampled = 0;
const sampleKey = (c: BenchCase) => hash(`${c.meta?.source_id}#hist`);
for (const cases of bySession.values()) {
  for (const c of [...cases].sort((a, b) => sampleKey(a) < sampleKey(b) ? -1 : 1).slice(0, LABEL_CAP)) { c.meta!.label_sample = true; sampled++; }
}
writeFileSync(join(DIR, "history-itemstate.jsonl"), out.map(c => JSON.stringify(c)).join("\n") + "\n");
console.log(JSON.stringify({ ...stats, sampled }));
