#!/usr/bin/env bun
// Evidence snapshot for omp-config-optimise: configured roles joined with catalog
// prices, current subscription headroom, and per-model spend from `omp stats`.
// Read-only; account identifiers are redacted. Prints Markdown.
// Usage: bun snapshot.ts [--config <path>] [--no-stats]
import { $ } from "bun";
import { homedir } from "node:os";
import { z } from "zod";

const Cost = z.object({ input: z.number(), output: z.number(), cacheRead: z.number() });
const Catalog = z.object({
  models: z.array(
    z.object({
      provider: z.string(),
      id: z.string(),
      cost: Cost.optional(),
      thinking: z.array(z.string()).nullish(),
    }),
  ),
});
const Config = z.object({
  modelRoles: z.record(z.string(), z.string()).default({}),
  defaultThinkingLevel: z.string().optional(),
  task: z.object({ agentServiceTierOverrides: z.record(z.string(), z.string()).optional() }).optional(),
});
const Usage = z.object({
  reports: z.array(
    z.object({
      provider: z.string(),
      metadata: z.object({ email: z.string().optional(), planType: z.string().optional() }),
      limits: z.array(
        z.object({
          label: z.string(),
          amount: z.object({ usedFraction: z.number() }),
          window: z.object({ resetsAt: z.number().optional() }),
          scope: z.object({ tier: z.string().optional() }),
        }),
      ),
    }),
  ),
});

const args = process.argv.slice(2);
const configIndex = args.indexOf("--config");
const configPath = configIndex >= 0 ? args[configIndex + 1] : `${homedir()}/.omp/agent/config.yml`;
const withStats = !args.includes("--no-stats");

const config = Config.parse(Bun.YAML.parse(await Bun.file(configPath).text()));
const catalog = Catalog.parse(JSON.parse(await $`omp models --json --no-extensions`.quiet().text())).models;
const bySelector = new Map(catalog.map(m => [`${m.provider}/${m.id}`, m]));

console.log(`# OMP routing snapshot — ${new Date().toISOString()}\n`);
console.log(`defaultThinkingLevel: \`${config.defaultThinkingLevel ?? "(unset)"}\`\n`);
console.log("## Roles\n\n| Role | Model | Effort | In $/M | Out $/M | Cache $/M | Supported efforts |\n|---|---|---|---|---|---|---|");
const unresolved: string[] = [];
for (const [role, selector] of Object.entries(config.modelRoles).sort(([a], [b]) => a.localeCompare(b))) {
  const match = /^(.*?)(?::(minimal|low|medium|high|xhigh|max|off))?$/.exec(selector);
  const model = match?.[1] ?? selector;
  const effort = match?.[2] ?? "–";
  const entry = bySelector.get(model);
  if (!entry && !model.startsWith("@") && !model.startsWith("web/")) unresolved.push(`${role} → ${model}`);
  const c = entry?.cost;
  console.log(`| ${role} | \`${model}\` | ${effort} | ${c?.input ?? "?"} | ${c?.output ?? "?"} | ${c?.cacheRead ?? "?"} | ${entry?.thinking?.join(", ") ?? "–"} |`);
}
if (unresolved.length) console.log(`\n**Not in catalog (role cannot resolve):** ${unresolved.join("; ")}`);
const tiers = config.task?.agentServiceTierOverrides ?? {};
if (Object.keys(tiers).length) console.log(`\nService-tier overrides: ${Object.entries(tiers).map(([a, t]) => `${a}=${t}`).join(", ")}`);

console.log("\n## Catalog prices for subscription families ($/M, API list)\n\n| Model | In | Out | Cache read |\n|---|---|---|---|");
const priced = catalog.flatMap(m => (m.cost && ["anthropic", "openai-codex"].includes(m.provider) ? [{ ...m, cost: m.cost }] : []));
for (const m of priced.sort((a, b) => b.cost.output - a.cost.output)) {
  console.log(`| \`${m.provider}/${m.id}\` | ${m.cost.input} | ${m.cost.output} | ${m.cost.cacheRead} |`);
}

const usage = Usage.parse(JSON.parse(await $`omp usage --json --redact`.quiet().text()));
console.log("\n## Subscription headroom\n\n| Provider | Account | Meter | Used | Resets (UTC) |\n|---|---|---|---|---|");
for (const report of usage.reports) {
  for (const limit of report.limits) {
    const resets = limit.window.resetsAt ? new Date(limit.window.resetsAt).toISOString().slice(0, 16) : "–";
    const meter = limit.scope.tier ? `${limit.label} (sub-cap of shared limit)` : limit.label;
    const account = `${report.metadata.email ?? "?"} ${report.metadata.planType ?? ""}`.trim();
    console.log(`| ${report.provider} | ${account} | ${meter} | ${(limit.amount.usedFraction * 100).toFixed(0)}% | ${resets} |`);
  }
}

if (withStats) {
  const stats: string[] = (await $`omp stats --summary`.quiet().nothrow().text()).split("\n");
  const start = stats.findIndex(line => line.startsWith("By Model:"));
  const end = stats.findIndex((line, i) => i > start && line.startsWith("By Folder:"));
  if (start >= 0) console.log(`\n## Spend by model (omp stats)\n\n\`\`\`\n${stats.slice(start + 1, end < 0 ? undefined : end).join("\n").trim()}\n\`\`\``);
}
