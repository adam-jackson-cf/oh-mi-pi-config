#!/usr/bin/env bun
// Jev lab workbench: labelling and evaluation for every Jev use. Loopback only.
// Start: bun ~/.omp/jev-lab/server.ts
import { createHash, randomBytes, timingSafeEqual } from "node:crypto";
import { constants } from "node:fs";
import { mkdir, open, readFile } from "node:fs/promises";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { z } from "zod";
import { auditRoot, decide, type JevAnswers, type JevQuestions, type JevResult, type JsonValue } from "../agent/extensions/lib/jev";
import { scoreExpected, readCaseRows, saveCase, caseRow, toLabAnswers } from "./lib/caseset";
import { casesForVersion, computeMetrics, CURRENT_VERSION, versionsOf } from "../agent/skills/evaluate-jev/scripts/cases.ts";
import { buildProposalQueue, buildQueue, scopeProgress } from "./lib/metrics";
import { MIN_POSITIVE, MIN_SUFFICIENT, SCOPE_SOURCE, SCOPE_THRESHOLD } from "./lib/scope";
import { applyLabel, listSources, loadCases } from "./lib/sources";
import { jsonValue, LabError, questionsSchema, type LabAnswer, type LabPaths } from "./lib/types";

const LAB_DIR = dirname(new URL(import.meta.url).pathname);
const PORT = 4398;
const TOKEN_HEADER = "x-jev-lab-token";
const MAX_BODY = 2_000_000;
const MAX_CASESET_RUN = 200;
const HOST = /^(127\.0\.0\.1|localhost)(:\d+)?$/;
const STATIC = new Map([["/app.js", "text/javascript"], ["/style.css", "text/css"]]);

export type LabDeps = { paths: LabPaths; token: string; apiKey?: string };

export function defaultPaths(): LabPaths {
  return {
    sessionsDir: join(homedir(), ".omp", "agent", "sessions"),
    auditDir: auditRoot(),
    casesetDir: join(LAB_DIR, "casesets"),
    runsDir: join(LAB_DIR, "runs"),
  };
}

const labelBody = z.object({ source: z.string(), id: z.string(), label: z.string().min(1), note: z.string().max(2000).optional() });
const replayBody = z.object({ source: z.string(), id: z.string() });
const playgroundBody = z.object({ state: jsonValue, questions: questionsSchema });
const saveBody = z.object({ name: z.string(), case: caseRow });
const runSetBody = z.object({ name: z.string() });

function tokenMatches(given: string | null, expected: string): boolean {
  if (!given) return false;
  const a = createHash("sha256").update(given).digest();
  const b = createHash("sha256").update(expected).digest();
  return timingSafeEqual(a, b);
}

async function writeRun(paths: LabPaths, kind: string, name: string, payload: string, apiKey?: string): Promise<void> {
  await mkdir(paths.runsDir, { recursive: true, mode: 0o700 });
  const safe = name.replace(/[^\w.-]+/g, "_").slice(0, 80);
  const file = join(paths.runsDir, `${new Date().toISOString().replace(/[:.]/g, "-")}-${kind}-${safe}.json`);
  const handle = await open(file, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW, 0o600);
  try {
    const text = payload;
    await handle.writeFile(apiKey ? text.replaceAll(apiKey, "[REDACTED]") : text);
  } finally {
    await handle.close();
  }
}

type PublicOutcome = Omit<Extract<LiveOutcome, { ok: true }>, "raw"> | Extract<LiveOutcome, { ok: false }>;
type LiveOutcome =
  | { ok: true; answers: LabAnswer[]; resolvedModel: string; costUsd: number; latencyMs: number; raw: JevAnswers }
  | { ok: false; error: string; latencyMs?: number };

async function callJev(apiKey: string, state: JsonValue, questions: JevQuestions): Promise<LiveOutcome> {
  let result: JevResult;
  try {
    result = await decide(apiKey, state, questions);
  } catch (error) {
    throw new LabError(error instanceof Error ? error.message : "Invalid questions.", 400);
  }
  if (!result.ok) return { ok: false, error: result.error, latencyMs: result.latencyMs };
  return {
    ok: true, answers: toLabAnswers(result.answers), resolvedModel: result.resolvedModel,
    costUsd: result.costUsd, latencyMs: result.latencyMs, raw: result.answers,
  };
}

function publicOutcome(outcome: LiveOutcome): PublicOutcome {
  if (!outcome.ok) return outcome;
  const { raw: _raw, ...rest } = outcome;
  return rest;
}

/** Request handler; exported so tests can drive it without a socket. */
export function createHandler(deps: LabDeps): (req: Request) => Promise<Response> {
  const { paths, token, apiKey } = deps;

  function respond(body: string, status: number, type: string): Response {
    let text = body;
    if (apiKey) text = text.replaceAll(apiKey, "[REDACTED]");
    return new Response(text, { status, headers: {
      "content-type": `${type}; charset=utf-8`, "cache-control": "no-store", "x-content-type-options": "nosniff",
      "x-frame-options": "DENY",
      "content-security-policy": "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'",
    } });
  }

  function json(body: string, status = 200): Response {
    return respond(body, status, "application/json");
  }

  async function readBody<T>(req: Request, schema: z.ZodType<T>): Promise<T> {
    const text = await req.text();
    if (text.length > MAX_BODY) throw new LabError("Body too large.", 413);
    let json: JsonValue;
    try { json = JSON.parse(text); } catch { throw new LabError("Body must be JSON.", 400); }
    const parsed = schema.safeParse(json);
    if (!parsed.success) throw new LabError(`Invalid request: ${parsed.error.issues.map(i => `${i.path.join(".")} ${i.message}`).join("; ")}`, 400);
    return parsed.data;
  }

  function requireLive(): string {
    if (!apiKey) throw new LabError("Live Jev calls are disabled: no OpenRouter key was available at startup.", 503);
    return apiKey;
  }

  async function route(req: Request, url: URL): Promise<Response> {
    const path = url.pathname;
    if (req.method === "GET" && (path === "/" || path === "/index.html")) {
      const page = await readFile(join(LAB_DIR, "public", "index.html"), "utf8");
      return respond(page.replaceAll("__JEV_LAB_TOKEN__", token).replaceAll("__JEV_LAB_LIVE__", apiKey ? "1" : "0"), 200, "text/html");
    }
    const asset = STATIC.get(path);
    if (req.method === "GET" && asset) return respond(await readFile(join(LAB_DIR, "public", path.slice(1)), "utf8"), 200, asset);

    if (req.method === "GET" && path === "/api/sources") return json(JSON.stringify({ sources: await listSources(paths), live: Boolean(apiKey) }));
    if (req.method === "GET" && path === "/api/cases") {
      const source = url.searchParams.get("source") ?? "";
      let cases = await loadCases(paths, source);
      const versions = source === SCOPE_SOURCE ? versionsOf(cases) : undefined;
      if (versions) cases = casesForVersion(cases, url.searchParams.get("version") ?? CURRENT_VERSION);
      return json(JSON.stringify({ versions, cases: cases.map(({ state: _s, questions: _q, answers: _a, ...summary }) => summary) }));
    }
    if (req.method === "GET" && path === "/api/case") {
      const found = (await loadCases(paths, url.searchParams.get("source") ?? "")).find(c => c.id === url.searchParams.get("id"));
      if (!found) throw new LabError("No such case.", 404);
      return json(JSON.stringify({ case: found }));
    }
    if (req.method === "GET" && path === "/api/metrics") {
      const source = url.searchParams.get("source") ?? "";
      const requested = Number(url.searchParams.get("threshold") ?? SCOPE_THRESHOLD);
      const threshold = Number.isFinite(requested) ? requested : SCOPE_THRESHOLD;
      let cases = await loadCases(paths, source);
      let version: string | undefined;
      if (source === SCOPE_SOURCE) {
        version = url.searchParams.get("version") ?? CURRENT_VERSION;
        cases = casesForVersion(cases, version);
        if (url.searchParams.get("sufficientOnly") !== "0") cases = cases.filter(c => c.sufficient === true);
      }
      return json(JSON.stringify({ threshold, version, metrics: computeMetrics(cases, threshold) }));
    }
    if (req.method === "GET" && path === "/api/queue") {
      const cases = casesForVersion(await loadCases(paths, SCOPE_SOURCE), CURRENT_VERSION);
      const build = url.searchParams.get("order") === "proposals" ? buildProposalQueue : buildQueue;
      const queue = build(cases, 50).map(({ state: _s, questions: _q, answers: _a, ...summary }) => summary);
      return json(JSON.stringify({ progress: scopeProgress(cases, MIN_SUFFICIENT, MIN_POSITIVE), queue }));
    }

    if (req.method !== "POST") throw new LabError("Not found.", 404);
    if (!tokenMatches(req.headers.get(TOKEN_HEADER), token)) throw new LabError("Missing or invalid lab token.", 403);
    const origin = req.headers.get("origin");
    if (origin && !HOST.test(new URL(origin).host)) throw new LabError("Cross-origin request refused.", 403);

    if (path === "/api/label") {
      const body = await readBody(req, labelBody);
      await applyLabel(paths, body.source, body.id, body.label, "human", body.note);
      return json(JSON.stringify({ ok: true }));
    }
    if (path === "/api/replay") {
      const body = await readBody(req, replayBody);
      const key = requireLive();
      const target = (await loadCases(paths, body.source)).find(c => c.id === body.id);
      if (!target) throw new LabError("No such case.", 404);
      if (!target.questions || target.state === null) throw new LabError("This case stored no Jev request to replay.", 400);
      const fresh = await callJev(key, target.state, target.questions);
      const payload = {
        source: body.source, id: body.id, stored: target.answers, fresh: publicOutcome(fresh),
        warning: "Stored state is redacted; a replay can differ from the original call.",
      };
      await writeRun(paths, "replay", body.id, JSON.stringify(payload, null, 2), key);
      return json(JSON.stringify(payload));
    }
    if (path === "/api/playground/run") {
      const body = await readBody(req, playgroundBody);
      const key = requireLive();
      // SAFETY: questionsSchema mirrors JevQuestions; decide() re-validates before any network call.
      const outcome = await callJev(key, body.state, body.questions as JevQuestions);
      await writeRun(paths, "playground", "adhoc", JSON.stringify({ state: body.state, questions: body.questions, outcome: publicOutcome(outcome) }, null, 2), key);
      return json(JSON.stringify({ outcome: publicOutcome(outcome) }));
    }
    if (path === "/api/caseset/save") {
      const body = await readBody(req, saveBody);
      await saveCase(paths, body.name, body.case);
      return json(JSON.stringify({ ok: true }));
    }
    if (path === "/api/caseset/run") {
      const body = await readBody(req, runSetBody);
      const key = requireLive();
      const rows = (await readCaseRows(paths, body.name)).slice(0, MAX_CASESET_RUN);
      const results = [];
      for (const row of rows) {
        // SAFETY: rows were validated by caseRow (questionsSchema) on read.
        const outcome = await callJev(key, row.state, row.questions as JevQuestions);
        const checks = outcome.ok ? scoreExpected(row.expected, outcome.raw) : [];
        results.push({ id: row.id, outcome: publicOutcome(outcome), checks });
      }
      const checks = results.flatMap(r => r.checks);
      const payload = { name: body.name, cases: results.length, checked: checks.length, passed: checks.filter(c => c.pass).length, results };
      await writeRun(paths, "caseset", body.name, JSON.stringify(payload, null, 2), key);
      return json(JSON.stringify(payload));
    }
    throw new LabError("Not found.", 404);
  }

  return async (req: Request): Promise<Response> => {
    const url = new URL(req.url);
    if (!HOST.test(req.headers.get("host") ?? url.host)) return json(JSON.stringify({ error: "Loopback host required." }), 403);
    try {
      return await route(req, url);
    } catch (error) {
      if (error instanceof LabError) return json(JSON.stringify({ error: error.message }), error.status);
      // Internal detail stays out of the response; it can echo file paths or provider text.
      return json(JSON.stringify({ error: "Internal error." }), 500);
    }
  };
}

/** OPENROUTER_API_KEY, else `omp token openrouter` stdout; undefined disables live features. */
async function resolveApiKey(): Promise<string | undefined> {
  const fromEnv = process.env.OPENROUTER_API_KEY?.trim();
  if (fromEnv) return fromEnv;
  try {
    const child = Bun.spawn(["omp", "token", "openrouter"], { stdout: "pipe", stderr: "ignore", stdin: "ignore" });
    const out = (await new Response(child.stdout).text()).trim();
    return (await child.exited) === 0 && out ? out : undefined;
  } catch { return undefined; }
}

if (import.meta.main) {
  const apiKey = await resolveApiKey();
  const token = randomBytes(24).toString("hex");
  const server = Bun.serve({ hostname: "127.0.0.1", port: PORT, fetch: createHandler({ paths: defaultPaths(), token, apiKey }) });
  console.log(`Jev lab workbench: http://127.0.0.1:${server.port}`);
  console.log(apiKey ? "Live Jev calls: enabled" : "Live Jev calls: DISABLED (no OpenRouter key found)");
}
