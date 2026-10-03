import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import type { z } from "zod";
import { type BenchCase, benchCaseSchema, type RunResult, runResultSchema } from "./types";

export const BENCH_ROOT = join(import.meta.dir, "..");

/** Parse a JSONL file line by line against `schema`; throws on the first malformed line. */
export const jsonl = <S extends z.ZodType>(file: string, schema: S): z.output<S>[] =>
  readFileSync(file, "utf8").split("\n").filter(Boolean).map(l => schema.parse(JSON.parse(l)));

export function loadCases(experiment: string, dataset?: string): BenchCase[] {
  const dir = join(BENCH_ROOT, "datasets", experiment);
  return readdirSync(dir)
    .filter(f => f.endsWith(".jsonl") && (!dataset || f === `${dataset}.jsonl`))
    .flatMap(f => jsonl(join(dir, f), benchCaseSchema));
}

/** Latest successful result per (case id, rep), plus every failure for health reporting. */
export function loadRuns(experiment: string) {
  const dir = join(BENCH_ROOT, "runs", experiment);
  const ok = new Map<string, RunResult[]>();
  const failures: RunResult[] = [];
  if (!existsSync(dir)) return { ok, failures };
  for (const f of readdirSync(dir).filter(n => n.endsWith(".jsonl"))) {
    const latest = new Map<string, RunResult>();
    for (const r of jsonl(join(dir, f), runResultSchema)) {
      if (r.ok) latest.set(`${r.id}#${r.rep}`, r);
      else failures.push(r);
    }
    const backend = f.replace(/\.jsonl$/, "");
    ok.set(backend, [...latest.values()]);
  }
  return { ok, failures };
}
