#!/usr/bin/env bun
// Fold blind labels into the E4 datasets. Truth is set only where both labeller families agree on a
// definite value, or where the orchestrator adjudicated a definite disagreement (labels/adjudicated.jsonl);
// `uncertain` leaves the case unscored. Rewrites the variant files in place; safe to re-run.
// labels/{sol,son}-item.jsonl relabel cases from the unclipped item state; they supersede the base labels
// for real-itemstate.jsonl only (other variants showed the clipped state the base labels describe).
// Item-label disagreements are not adjudicated: they stay unscored.
// Usage: bun builders/e4-apply-labels.ts   (reads datasets/e4-proportionality/labels/*.jsonl)
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { z } from "zod";
import { BENCH_ROOT } from "../lib/cases";
import type { Truth } from "../lib/types";
import { readCases } from "./e4-case-schema";

const triState = z.union([z.boolean(), z.literal("uncertain")]);
const label = z.object({ id: z.string(), beyond_item: triState, runtime_action: z.string().optional(), item_satisfied: triState.optional() });
const adjudicatedLabel = z.object({ id: z.string(), beyond_item: triState });
type Label = z.infer<typeof label>;
type Agreed = { beyond_item?: boolean; runtime_action?: string; item_satisfied?: boolean };
const dir = join(BENCH_ROOT, "datasets", "e4-proportionality");
const read = <S extends z.ZodType>(schema: S, f: string): z.infer<S>[] =>
  readFileSync(f, "utf8").split("\n").filter(Boolean).map(l => schema.parse(JSON.parse(l)));
const labels = (name: string) => new Map(read(label, join(dir, "labels", `${name}.jsonl`)).map(l => [l.id, l]));

function agree(sol: Map<string, Label>, son: Map<string, Label>, adjudicated: Map<string, boolean | "uncertain">) {
  const agreed = new Map<string, Agreed>();
  const stats = { both: 0, beyondAgree: 0, beyondTrue: 0, beyondDisagree: 0, adjudicatedTrue: 0, adjudicatedFalse: 0, beyondUncertain: 0, actionAgree: 0 };
  for (const [id, a] of sol) {
    const b = son.get(id);
    if (!b) continue;
    stats.both++;
    const t: Agreed = {};
    if (a.beyond_item === "uncertain" || b.beyond_item === "uncertain") stats.beyondUncertain++;
    else if (a.beyond_item === b.beyond_item) { t.beyond_item = a.beyond_item; stats.beyondAgree++; if (a.beyond_item) stats.beyondTrue++; }
    else {
      stats.beyondDisagree++;
      const verdict = adjudicated.get(id);
      if (verdict !== undefined && verdict !== "uncertain") { t.beyond_item = verdict; stats[verdict ? "adjudicatedTrue" : "adjudicatedFalse"]++; }
    }
    if (a.runtime_action === b.runtime_action) { t.runtime_action = a.runtime_action; stats.actionAgree++; }
    if (a.item_satisfied !== undefined && a.item_satisfied !== "uncertain" && a.item_satisfied === b.item_satisfied) t.item_satisfied = a.item_satisfied;
    agreed.set(id, t);
  }
  return { agreed, stats };
}

const adjudicated = new Map(read(adjudicatedLabel, join(dir, "labels", "adjudicated.jsonl")).map(l => [l.id, l.beyond_item]));
const base = agree(labels("sol"), labels("son"), adjudicated);
const item = agree(labels("sol-item"), labels("son-item"), new Map());
// History label sample (builders/e4-history.ts): round 1 labelled the first build; cases either family left
// uncertain or that the families disagreed on were relabelled blind (round 2, diff-visible state, policy v2), and
// round 2 replaces round 1 for them. Disagreements stay unscored.
const hist1 = agree(labels("hist/sol"), labels("hist/son"), new Map());
const round2 = labels("hist/r2-sol");
const hist2 = agree(round2, labels("hist/r2-son"), new Map());
const historyLabel = (id: string) => round2.has(id) ? hist2.agreed.get(id) : hist1.agreed.get(id);
const agreed = base.agreed;
const stats = { base: base.stats, item: item.stats, history: { round1: hist1.stats, round2: hist2.stats } };

const WATCHDOG_RISK = ["outside_scope:yes", "required:no", "repaired:no"];
// The shipped Choice: P(beyond the item) = P(oversized) + P(outside) (lib/proportionality.ts pBeyond).
const CHOICE_RISK = ["worst_change:oversized", "worst_change:outside"];
const FILES = ["real.jsonl", "real-watchdogstate.jsonl", "real-todostate-wdquestion.jsonl", "perturbed.jsonl",
  "real-itemstate.jsonl", "perturbed-itemstate.jsonl", "history-itemstate.jsonl"];
for (const file of FILES) {
  const cases = readCases(join(dir, file)).map(c => {
    const id = String(c.meta?.source_id ?? c.id);
    const label = file === "history-itemstate.jsonl" ? historyLabel(id)
      : (file === "real-itemstate.jsonl" ? item.agreed.get(id) : undefined) ?? agreed.get(id);
    const watchdogQuestions = "outside_scope" in c.questions;
    if (!watchdogQuestions) {
      // Injected cases carry constructed truth only (the injection changes what labels described);
      // unmodified controls and real cases take the labels.
      const perturbation = z.string().safeParse(c.meta?.perturbation);
      const injected = perturbation.success && perturbation.data !== "unmodified";
      const truth: Truth = {};
      if (injected) {
        const constructed = z.boolean().safeParse(c.truth.beyond_item);
        if (constructed.success) truth.beyond_item = constructed.data;
      } else {
        for (const [key, value] of Object.entries(label ?? {})) if (value !== undefined) truth[key] = value;
      }
      c.truth = truth;
    }
    const truthBeyond = z.boolean().safeParse(c.truth.beyond_item);
    const beyond = watchdogQuestions ? label?.beyond_item : (truthBeyond.success ? truthBeyond.data : undefined);
    if (beyond !== undefined) {
      c.act = watchdogQuestions ? { truth: beyond, risk: WATCHDOG_RISK, mode: "product" }
        : "worst_change" in c.questions ? { truth: beyond, risk: CHOICE_RISK, mode: "sum" }
        : { truth: beyond, risk: ["beyond_item"] };
    } else delete c.act;
    return c;
  });
  writeFileSync(join(dir, file), cases.map(c => JSON.stringify(c)).join("\n") + "\n");
}
console.log(JSON.stringify(stats));
