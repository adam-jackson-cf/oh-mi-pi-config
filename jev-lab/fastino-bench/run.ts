#!/usr/bin/env bun
// Replay benchmark cases against decision backends; resumable, append-only results.
// Usage (from jev-lab/fastino-bench, Fastino key injected by Infisical):
//   infisical run --projectId <id> --env dev -- bun run.ts --experiment e1-baseline \
//     [--dataset guard-bash] [--backends jev,glide,decide,decide-keys] [--concurrency 4] [--reps 1] [--limit N]
// Keys: FASTINO_API_KEY; OPENROUTER_API_KEY or `omp token openrouter` for Jev. Keys are never logged.
import { appendFileSync, existsSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { parseArgs } from "node:util";
import { BENCH_ROOT, jsonl, loadCases } from "./lib/cases";
import { type Backend, decideBackend, glideBackend, jevBackend } from "./lib/backends";
import { type RunResult, runResultSchema } from "./lib/types";

const { values: args } = parseArgs({
  options: {
    experiment: { type: "string" }, dataset: { type: "string" }, backends: { type: "string", default: "jev,glide,decide,decide-keys" },
    concurrency: { type: "string", default: "4" }, reps: { type: "string", default: "1" }, limit: { type: "string" },
  },
});
if (!args.experiment) throw new Error("--experiment is required");
let cases = loadCases(args.experiment, args.dataset);
if (args.limit) cases = cases.slice(0, Number(args.limit));
const reps = Number(args.reps);

async function openRouterKey(): Promise<string> {
  if (process.env.OPENROUTER_API_KEY) return process.env.OPENROUTER_API_KEY;
  const proc = Bun.spawn(["omp", "token", "openrouter"], { stdout: "pipe", stderr: "ignore" });
  const key = (await new Response(proc.stdout).text()).trim();
  if (!key) throw new Error("No OpenRouter key: set OPENROUTER_API_KEY or run /login openrouter");
  return key;
}

const backends: Backend[] = [];
for (const id of args.backends.split(",")) {
  if (id === "jev") backends.push(jevBackend(await openRouterKey()));
  else {
    const key = process.env.FASTINO_API_KEY;
    if (!key) throw new Error("FASTINO_API_KEY missing: run under `infisical run`");
    if (id === "glide") backends.push(glideBackend(key));
    else if (id === "decide") backends.push(decideBackend(key, "described"));
    else if (id === "decide-keys") backends.push(decideBackend(key, "keys"));
    else throw new Error(`unknown backend ${id}`);
  }
}
const outDir = join(BENCH_ROOT, "runs", args.experiment);
mkdirSync(outDir, { recursive: true, mode: 0o700 });

async function runBackend(backend: Backend): Promise<void> {
  const file = join(outDir, `${backend.id}.jsonl`);
  const done = new Set<string>();
  if (existsSync(file)) {
    for (const r of jsonl(file, runResultSchema)) {
      if (r.ok) done.add(`${r.id}#${r.rep}`);
    }
  }
  const queue = cases.flatMap(c => Array.from({ length: reps }, (_, rep) => ({ c, rep }))).filter(({ c, rep }) => !done.has(`${c.id}#${rep}`));
  let next = 0, ok = 0, failed = 0;
  const worker = async (): Promise<void> => {
    while (next < queue.length) {
      const { c, rep } = queue[next++]!;
      const result = await backend.call(c.state, c.questions);
      const row: RunResult = { id: c.id, backend: backend.id, rep, timestamp: new Date().toISOString(), ...result };
      appendFileSync(file, JSON.stringify(row) + "\n", { mode: 0o600 });
      if (result.ok) ok++; else { failed++; console.error(`${backend.id} ${c.id}: ${result.error}`); }
    }
  };
  await Promise.all(Array.from({ length: Number(args.concurrency) }, worker));
  console.log(`${backend.id}: ${ok} ok, ${failed} failed, ${done.size} already done`);
}

await Promise.all(backends.map(runBackend));
