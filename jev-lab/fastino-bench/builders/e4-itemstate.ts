#!/usr/bin/env bun
// E4 item-state variant: rebuilds every E4 case with the shipped watchdog's own state builder and question
// (agent/extensions/lib/proportionality.ts), so the benchmark measures exactly what jev-watchdog.ts sends.
// Real cases: the same done events (session + done message index) as real.jsonl. Perturbed cases: the same
// injected action, placed at the same position, in the rebuilt state. Run after e4-proportionality.ts;
// then run e4-apply-labels.ts. Offline and deterministic.
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { z } from "zod";
import {
  buildItemState, ITEM_QUESTIONS, parseSession, type Action, type DoneEvent, type ParsedSession,
} from "../../../agent/extensions/lib/proportionality";
import { BENCH_ROOT } from "../lib/cases";
import type { BenchCase, JsonValue } from "../lib/types";
import { parseJsonValue, readCases } from "./e4-case-schema";

const SESSIONS = "/Users/adamjackson/.omp/agent/sessions";
const DIR = join(BENCH_ROOT, "datasets", "e4-proportionality");
const read = (file: string) => readCases(join(DIR, file));

const sessionHeader = z.object({ type: z.literal("session"), cwd: z.string() });
const sessions = new Map<string, ParsedSession>();
function parsed(slug: string, session: string): ParsedSession {
  const key = `${slug}/${session}`;
  let p = sessions.get(key);
  if (!p) {
    const entries: JsonValue[] = readFileSync(join(SESSIONS, slug, `${session}.jsonl`), "utf8").split("\n").filter(Boolean)
      .map(l => { try { return JSON.parse(l); } catch { return null; } });
    const header = entries.map(e => sessionHeader.safeParse(e)).find(r => r.success);
    const cwd = header?.success ? header.data.cwd : "";
    p = parseSession(entries, cwd);
    sessions.set(key, p);
  }
  return p;
}

const stats = { built: 0, missingEvent: 0, cutImplementation: 0, cutRequests: 0, tokens: new Array<number>(), changeShare: new Array<number>() };
/** The case's session and done event; plan files are not resolvable offline (the live watchdog adds them). */
function source(c: BenchCase): { p: ParsedSession; event: DoneEvent } | undefined {
  const m = c.meta ?? {};
  const p = parsed(String(m.cwd_slug), String(m.session));
  const event = p.events.find(e => e.doneIdx === Number(m.done_msg_idx));
  if (!event) { stats.missingEvent++; return undefined; }
  return { p, event };
}
// Id tag of the current item state: bump whenever the shipped builder or question changes, so Jev runs re-run.
const TAG = "item3";
const toCase = (c: BenchCase, dataset: string, state: JsonValue, extra: Record<string, JsonValue>): BenchCase => ({
  id: `${c.id}#${TAG}`, experiment: c.experiment, dataset, state, questions: ITEM_QUESTIONS, truth: c.truth, primary: "worst_change",
  meta: { ...c.meta, variant: "item-state", ...extra },
});

const realOut: BenchCase[] = [];
for (const c of read("real.jsonl")) {
  const src = source(c);
  const built = src && buildItemState(src.p, src.event, null);
  if (!built) continue;
  stats.built++;
  stats.tokens.push(built.estimatedTokens);
  stats.changeShare.push(built.changeShare);
  if (built.state.completeness.dropped_chars.implementation) stats.cutImplementation++;
  if (built.state.completeness.dropped_chars.user_request) stats.cutRequests++;
  realOut.push(toCase(c, "real-itemstate", parseJsonValue(JSON.stringify(built.state)),
    { estimated_tokens: built.estimatedTokens, change_share: Number(built.changeShare.toFixed(3)) }));
}

// Perturbed: find the injected action (present in the perturbed state, absent from its source's old state).
const oldActions = z.object({ implementation: z.object({ actions: z.array(z.object({
  n: z.number(), tool: z.enum(["edit", "write", "bash", "eval"]), path: z.string(), summary: z.string(), changed: z.string(),
})) }) });
const actionsOf = (state: JsonValue): Action[] => oldActions.parse(state).implementation.actions;
const oldReal = new Map(read("real.jsonl").map(c => [c.id, c]));
const perturbedOut: BenchCase[] = [];
for (const c of read("perturbed.jsonl")) {
  const sourceId = String(c.meta?.source_id);
  const pert = String(c.meta?.perturbation);
  const sourceCase = oldReal.get(sourceId);
  const src = source(c);
  if (!src) continue;
  let session = src.p;
  if (pert !== "unmodified") {
    const injected = actionsOf(c.state);
    const original = sourceCase ? actionsOf(sourceCase.state) : undefined;
    const window = src.p.acts.filter(a => a.idx > src.event.startIdx && a.idx < src.event.doneIdx);
    // Sources outside real.jsonl: the injected action is the one whose text never occurs in the session's actions.
    const known = new Set((original ?? []).map(a => `${a.tool}\u0000${a.summary}\u0000${a.changed}`));
    const extra = injected.find(a => original ? !known.has(`${a.tool}\u0000${a.summary}\u0000${a.changed}`)
      : !window.some(b => b.tool === a.tool && b.path === a.path && b.changed.includes(a.changed.slice(0, 200))));
    if (!extra) { stats.missingEvent++; continue; }
    // Inject into the session at the same position, so facts and flags are computed for it like any action.
    const pos = Math.min(extra.n - 1, window.length);
    const idx = pos < window.length ? window[pos]!.idx - 0.5 : src.event.doneIdx - 0.5;
    const { n: _n, ...act } = extra;
    session = { ...src.p, acts: [...src.p.acts, { ...act, idx }].sort((a, b) => a.idx - b.idx) };
  }
  const built = buildItemState(session, src.event, null);
  if (!built) continue;
  perturbedOut.push(toCase(c, "perturbed-itemstate", parseJsonValue(JSON.stringify(built.state)), { estimated_tokens: built.estimatedTokens }));
}

const jsonl = (cs: BenchCase[]) => cs.map(c => JSON.stringify(c)).join("\n") + "\n";
writeFileSync(join(DIR, "real-itemstate.jsonl"), jsonl(realOut));
writeFileSync(join(DIR, "perturbed-itemstate.jsonl"), jsonl(perturbedOut));
const q = (xs: number[], p: number) => [...xs].sort((a, b) => a - b)[Math.min(xs.length - 1, Math.floor(p * xs.length))] ?? 0;
console.log(JSON.stringify({
  real: realOut.length, perturbed: perturbedOut.length, missingEvent: stats.missingEvent,
  cutImplementation: stats.cutImplementation, cutRequests: stats.cutRequests,
  estimatedTokens: { p50: q(stats.tokens, 0.5), p90: q(stats.tokens, 0.9), max: q(stats.tokens, 1) },
  changeShare: { p10: q(stats.changeShare, 0.1), p50: q(stats.changeShare, 0.5) },
}));
