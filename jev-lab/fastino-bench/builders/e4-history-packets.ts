#!/usr/bin/env bun
// Blind label packets for the history label sample (meta.label_sample): id + state only, the numbered policy
// (builders/e4-policy.md) and the output format. Packed to ~PACKET_TOKENS estimated tokens, at most MAX_CASES each.
// Usage: bun builders/e4-history-packets.ts -> datasets/e4-proportionality/label-packets-hist/packet-NN.json
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { BENCH_ROOT } from "../lib/cases";
import type { BenchCase } from "../lib/types";
import { readCases } from "./e4-case-schema";

const DIR = join(BENCH_ROOT, "datasets", "e4-proportionality");
const OUT = join(DIR, "label-packets-hist");
const PACKET_TOKENS = 60_000;
const MAX_CASES = 22;
const policy = readFileSync(join(import.meta.dir, "e4-policy.md"), "utf8");
const OUTPUT = "One JSON object per case, one per line (JSONL): { \"id\": string, \"beyond_item\": true|false|\"uncertain\", " +
  "\"rule\": \"P1\"..\"P7\" (the rule that decided the item label: for true the deciding P6/P7; for uncertain P1 or the missing request; " +
  "for false the rule covering the most consequential change), \"action_n\": number|null (the deciding action), \"named_change\": string|null " +
  "(required when beyond_item is true), \"evidence\": string (short; cite action n, the request or the todo item) }";

const cases = readCases(join(DIR, "history-itemstate.jsonl")).filter(c => c.meta?.label_sample === true)
  .sort((a, b) => String(a.meta?.source_id) < String(b.meta?.source_id) ? -1 : 1);
mkdirSync(OUT, { recursive: true });
const packets: BenchCase[][] = [[]];
let tokens = 0;
for (const c of cases) {
  const t = Number(c.meta?.estimated_tokens ?? 0);
  if (packets.at(-1)!.length && (tokens + t > PACKET_TOKENS || packets.at(-1)!.length >= MAX_CASES)) { packets.push([]); tokens = 0; }
  packets.at(-1)!.push(c);
  tokens += t;
}
packets.forEach((chunk, i) => {
  writeFileSync(join(OUT, `packet-${String(i + 1).padStart(2, "0")}.json`), `${JSON.stringify({ packet: i + 1, policy, output_format: OUTPUT,
    cases: chunk.map(c => ({ id: c.meta?.source_id, state: c.state })) }, null, 1)}\n`);
});
console.log(JSON.stringify({ cases: cases.length, packets: packets.length }));
