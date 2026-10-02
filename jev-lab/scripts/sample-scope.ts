#!/usr/bin/env bun
// Draw a stratified labelling sample from exported jev-scope cases (export-cases.ts output).
// Usage: bun jev-lab/scripts/sample-scope.ts --cases-in <cases.jsonl> [--n 150] [--version <v>]
//          [--seed 1] [--always-above 0.7] [--include-labelled] [--out <sample.jsonl>]
// Only sufficient-input cases are sampled. Every case at or above --always-above is included; the rest of
// the quota is spread evenly over P(yes) band x group (main/sub) cells, drawn with a seeded shuffle so a
// run is reproducible. Already labelled cases are skipped unless --include-labelled. Prints one JSON line
// per sampled case (id, band, group, score, verdict, label, evidence) and the per-cell counts to stderr.
import { readFile, writeFile } from "node:fs/promises";
import { z } from "zod";

const row = z.object({
  type: z.literal("case").optional(), id: z.string(), version: z.string().optional(), verdict: z.string(),
  score: z.number().optional(), sufficient: z.boolean().optional(), group: z.string().optional(),
  label: z.string().optional(), labelBy: z.string().optional(), evidence: z.string().optional(),
});
type Row = z.infer<typeof row>;

export const BANDS = [0, 0.1, 0.3, 0.5, 0.7] as const;

export function bandOf(score: number): string {
  let index = 0;
  BANDS.forEach((edge, i) => { if (score >= edge) index = i; });
  const lower = BANDS[index] ?? 0;
  const upper = BANDS[index + 1];
  return upper === undefined ? `>=${lower}` : `${lower}-${upper}`;
}

function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Sufficient scored cases: all at or above `above`, the rest of `n` round-robin over band x group cells. */
export function stratifiedSample(rows: Row[], n: number, above: number, seed: number): Row[] {
  const random = mulberry32(seed);
  const chosen = rows.filter(r => r.score! >= above);
  const cells = new Map<string, Row[]>();
  for (const r of rows) {
    if (r.score! >= above) continue;
    const key = `${bandOf(r.score!)}|${r.group ?? "main"}`;
    cells.set(key, [...(cells.get(key) ?? []), r]);
  }
  const pools = [...cells.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([, pool]) => {
    const shuffled = [...pool];
    for (let i = shuffled.length - 1; i > 0; i--) {
      const j = Math.floor(random() * (i + 1));
      [shuffled[i], shuffled[j]] = [shuffled[j]!, shuffled[i]!];
    }
    return shuffled;
  });
  let remaining = Math.max(0, n - chosen.length);
  while (remaining > 0 && pools.some(pool => pool.length)) {
    for (const pool of pools) {
      const next = pool.pop();
      if (next && remaining > 0) { chosen.push(next); remaining--; }
    }
  }
  return chosen;
}

if (import.meta.main) {
  const args = new Map<string, string>();
  for (let i = 2; i < process.argv.length; i++) {
    const key = process.argv[i]!.replace(/^--/, "");
    const next = process.argv[i + 1];
    if (next === undefined || next.startsWith("--")) args.set(key, "");
    else { args.set(key, next); i++; }
  }
  const casesIn = args.get("cases-in");
  if (!casesIn) {
    console.error("--cases-in <cases.jsonl> is required (export-cases.ts output)");
    process.exit(2);
  }
  const version = args.get("version");
  const all: Row[] = [];
  for (const line of (await readFile(casesIn, "utf8")).split("\n")) {
    if (!line.trim()) continue;
    let raw: unknown;
    try { raw = JSON.parse(line); } catch { continue; }
    const parsed = row.safeParse(raw);
    if (parsed.success && parsed.data.type === "case") all.push(parsed.data);
  }
  const pool = all.filter(r => r.sufficient === true && r.score !== undefined && (!version || r.version === version) &&
    (args.has("include-labelled") || r.label === undefined));
  const sample = stratifiedSample(pool, Number(args.get("n") || 150), Number(args.get("always-above") || 0.7),
    Number(args.get("seed") || 1));
  const tally: Record<string, number> = {};
  for (const r of sample) {
    const key = `${bandOf(r.score!)}|${r.group ?? "main"}`;
    tally[key] = (tally[key] ?? 0) + 1;
  }
  const text = sample.map(r => JSON.stringify({
    id: r.id, band: bandOf(r.score!), group: r.group ?? "main", score: r.score, verdict: r.verdict,
    label: r.label, evidence: r.evidence,
  })).join("\n") + (sample.length ? "\n" : "");
  const out = args.get("out");
  if (out) await writeFile(out, text, { mode: 0o600 });
  else process.stdout.write(text);
  console.error(`sampled ${sample.length} of ${pool.length} eligible; cells: ${JSON.stringify(tally)}`);
}
