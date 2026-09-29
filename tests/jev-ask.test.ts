import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, mkdtemp, readdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import * as hostZod from "@oh-my-pi/omptype/zod";
import type { ExtensionAPI, ExtensionContext } from "@oh-my-pi/pi-coding-agent";
import { askParameters, NUDGE_MARKER, registerJevAsk, type AskDetails, type AskParameters } from "../agent/extensions/jev-ask";
import { JEV_PINNED_MODEL, JEV_STATE_CHAR_LIMIT, type JevAnswers } from "../agent/extensions/lib/jev";

type ToolResult = { content: { type: string; text: string }[]; details?: AskDetails; isError?: boolean };
type CapturedTool = {
  name: string; description: string;
  execute(id: string, params: AskParameters, signal: undefined, onUpdate: undefined, ctx: ExtensionContext): Promise<ToolResult>;
};
type FakePi = {
  zod: typeof hostZod;
  registerTool(tool: CapturedTool): void;
  on(event: string, handler: PromptHandler): void;
};
type PromptEvent = { systemPrompt: string[] };
type PromptHandler = (event: PromptEvent) => { systemPrompt?: string[] } | undefined;
type Harness = { tool: CapturedTool; nudge: PromptHandler; ctx: ExtensionContext };

const SECRET_BODY = "PRIVATE_FILE_BODY_MARKER";

function harness(cwd: string, key: string | undefined = "sk-test-key-000000"): Harness {
  const tools: CapturedTool[] = [];
  const handlers: PromptHandler[] = [];
  const pi: FakePi = {
    zod: hostZod,
    registerTool: (tool) => { tools.push(tool); },
    on: (_event, handler) => { handlers.push(handler); },
  };
  // SAFETY: The extension only touches zod, registerTool and on; FakePi provides exactly those.
  registerJevAsk(pi as ExtensionAPI, async () => key);
  // SAFETY: The tool reads cwd, agent and sessionManager from the context; the fake provides those.
  const ctx = { cwd, agent: { kind: "main", name: "main" }, sessionManager: { getSessionId: () => "session-1" } } as ExtensionContext;
  return { tool: tools[0]!, nudge: handlers[0]!, ctx };
}

function answersFor(asked: AskParameters["questions"]): JevAnswers {
  const answers: JevAnswers = {};
  for (const [id, q] of Object.entries(asked)) {
    if (q.type === "noul") answers[id] = { type: "noul", noul: 0.93 };
    else if (q.type === "choice") {
      const options = Object.keys(q.criteria);
      const first = options[0]!;
      answers[id] = { type: "choice", choice: first, confidence: 0.9,
        probabilities: Object.fromEntries(options.map((k) => [k, k === first ? 0.9 : 0.1])) };
    } else answers[id] = { type: "score", score: 1.4, probabilities: { "0": 0.1, "1": 0.5, "2": 0.4 }, confidence: 0.5 };
  }
  return answers;
}

type Sent = { state: { path?: string; content?: string; files?: { path: string }[] } | string };

async function withJev<T>(run: (sent: Sent[]) => Promise<T>, fail = false): Promise<T> {
  const original = globalThis.fetch;
  const sent: Sent[] = [];
  // SAFETY: This test stub returns a real Response and the original fetch is restored below.
  globalThis.fetch = (async (_url, options) => {
    const body = JSON.parse(String(options!.body));
    sent.push({ state: body.state });
    if (fail) return new Response("provider echoed PRIVATE_FILE_BODY_MARKER", { status: 500 });
    return Response.json({ id: `resp-${sent.length}`, model: JEV_PINNED_MODEL, answers: answersFor(body.questions),
      usage: { input_tokens: 10, output_tokens: 2, cost: 0.00001 } });
  }) as typeof fetch;
  try { return await run(sent); } finally { globalThis.fetch = original; }
}

const questions: AskParameters["questions"] = {
  has_retry: { type: "noul", instructions: "Does the file retry?" },
  kind: { type: "choice", instructions: "Kind?", criteria: { code: "source", docs: "docs" } },
  quality: { type: "score", instructions: "Quality?", criteria: ["bad", "ok", "good"] },
};

async function fixture(): Promise<{ root: string; audit: string }> {
  const root = await mkdtemp(join(tmpdir(), "jev-ask-"));
  const audit = join(root, "audit");
  process.env.JEV_AUDIT_DIR = audit;
  await mkdir(join(root, "sub"), { recursive: true });
  await mkdir(join(root, "node_modules"), { recursive: true });
  await writeFile(join(root, "a.ts"), `export const a = "${SECRET_BODY}";\n`);
  await writeFile(join(root, "sub", "b.ts"), "export const b = 2;\n");
  await writeFile(join(root, "node_modules", "x.js"), "module.exports = 1;\n");
  await writeFile(join(root, "package-lock.json"), "{}\n");
  await writeFile(join(root, "bin.dat"), Buffer.from([1, 2, 0, 3]));
  await writeFile(join(root, "empty.txt"), "");
  await writeFile(join(root, "big.txt"), "x".repeat(JEV_STATE_CHAR_LIMIT + 1));
  return { root, audit };
}

test("globs expand under cwd and every pre-filter skip carries its reason", async () => {
  const { root } = await fixture();
  try {
    const { tool, ctx } = harness(root);
    const result = await withJev((sent) => tool.execute("1", { questions, paths: ["**/*", "missing.ts", "../outside.ts"] }, undefined, undefined, ctx)
      .then((r) => ({ r, sent })));
    const text = result.r.content[0]!.text;
    assert.deepEqual(result.r.details!.results.map((x) => x.key), ["a.ts", "sub/b.ts"]);
    const reasons = Object.fromEntries(result.r.details!.skipped.map((s) => [s.path, s.reason]));
    assert.equal(reasons["node_modules/"], "ignored directory");
    assert.equal(reasons["package-lock.json"], "lockfile");
    assert.equal(reasons["bin.dat"], "binary");
    assert.equal(reasons["empty.txt"], "empty");
    assert.match(reasons["big.txt"]!, /^too large: use grep\/read with ranges/);
    assert.equal(reasons["missing.ts"], "not found");
    assert.equal(reasons["../outside.ts"], "outside the workspace");
    assert.match(text, /Skipped:/);
    assert.match(text, /cost \$0\.000020, 2 calls/);
  } finally { delete process.env.JEV_AUDIT_DIR; await rm(root, { recursive: true, force: true }); }
});

test("per-file answers keep input order, format each level, and never echo file content", async () => {
  const { root } = await fixture();
  try {
    const { tool, ctx } = harness(root);
    const out = await withJev(async (sent) => ({ r: await tool.execute("1", { questions, paths: ["sub", "a.ts"] }, undefined, undefined, ctx), sent }));
    assert.deepEqual(out.r.details!.results.map((x) => x.key), ["sub/b.ts", "a.ts"]);
    assert.equal(out.sent.length, 2);
    const text = out.r.content[0]!.text;
    assert.match(text, /\| a\.ts \| yes 0\.93 \| code 0\.90 \| 1\.4\/2 \|/);
    assert.ok(!text.includes(SECRET_BODY));
    assert.ok(!JSON.stringify(out.r.details).includes(SECRET_BODY));
    assert.ok(out.sent.some((s) => JSON.stringify(s.state).includes(SECRET_BODY)));
  } finally { delete process.env.JEV_AUDIT_DIR; await rm(root, { recursive: true, force: true }); }
});

test("combined mode makes one call, and refuses when content will not fit", async () => {
  const { root } = await fixture();
  try {
    const { tool, ctx } = harness(root);
    const out = await withJev(async (sent) => ({ r: await tool.execute("1", { questions, paths: ["a.ts", "sub/b.ts"], mode: "combined" }, undefined, undefined, ctx), sent }));
    assert.equal(out.sent.length, 1);
    assert.deepEqual(out.r.details!.results.map((x) => x.key), ["(combined)"]);
    await writeFile(join(root, "m1.txt"), "y".repeat(60_000));
    await writeFile(join(root, "m2.txt"), "z".repeat(60_000));
    const tooBig = await withJev(async () => tool.execute("2", { questions, paths: ["m1.txt", "m2.txt"], mode: "combined" }, undefined, undefined, ctx));
    assert.equal(tooBig.isError, true);
    assert.match(tooBig.content[0]!.text, /per_file/);
  } finally { delete process.env.JEV_AUDIT_DIR; await rm(root, { recursive: true, force: true }); }
});

test("audit stores path, chars and sha256 for files and clipped redacted inline state", async () => {
  const { root, audit } = await fixture();
  try {
    const { tool, ctx } = harness(root);
    const inline = `token: abc123secretvalue\n${"w".repeat(5_000)}`;
    await withJev(() => tool.execute("1", { questions, paths: ["a.ts"], state: inline }, undefined, undefined, ctx));
    const dir = join(audit, "ask");
    const file = (await readdir(dir)).find((name) => name !== "labels.jsonl")!;
    const records = (await readFile(join(dir, file), "utf8")).trim().split("\n").map((line) => JSON.parse(line));
    assert.equal(records.length, 2);
    const raw = await readFile(join(root, "a.ts"), "utf8");
    const fileRecord = records.find((r) => r.subject === "a.ts");
    const inlineRecord = records.find((r) => r.subject === "inline state");
    assert.deepEqual(fileRecord.state, { path: "a.ts", chars: raw.length, sha256: createHash("sha256").update(raw).digest("hex") });
    assert.equal(fileRecord.policy, "ask");
    assert.equal(fileRecord.verdict, "answered");
    assert.equal(fileRecord.mode, "enforce");
    assert.ok(!JSON.stringify(records).includes(SECRET_BODY));
    assert.ok(!JSON.stringify(inlineRecord.state).includes("abc123secretvalue"));
    assert.ok(inlineRecord.state.length <= 2_100);
  } finally { delete process.env.JEV_AUDIT_DIR; await rm(root, { recursive: true, force: true }); }
});

test("missing credential and Jev failure return error results that point at read/grep", async () => {
  const { root } = await fixture();
  try {
    const noKey = harness(root, undefined);
    const missing = await noKey.tool.execute("1", { questions, paths: ["a.ts"] }, undefined, undefined, noKey.ctx);
    assert.equal(missing.isError, true);
    assert.match(missing.content[0]!.text, /read.*grep/);
    const { tool, ctx } = harness(root);
    const failed = await withJev(() => tool.execute("2", { questions, paths: ["a.ts"] }, undefined, undefined, ctx), true);
    assert.equal(failed.isError, true);
    assert.match(failed.content[0]!.text, /read.*grep/);
    assert.ok(!failed.content[0]!.text.includes(SECRET_BODY));
  } finally { delete process.env.JEV_AUDIT_DIR; await rm(root, { recursive: true, force: true }); }
});

test("the parameter schema rejects bad question ids, levels and oversize input", () => {
  const good = { questions };
  assert.equal(askParameters.safeParse(good).success, true);
  assert.equal(askParameters.safeParse({ questions: { "Bad-Id": questions.has_retry } }).success, false);
  assert.equal(askParameters.safeParse({ questions: { q: { type: "ternary", instructions: "x" } } }).success, false);
  assert.equal(askParameters.safeParse({ questions: {} }).success, false);
  const nine = Object.fromEntries(Array.from({ length: 9 }, (_, i) => [`q${i}`, questions.has_retry]));
  assert.equal(askParameters.safeParse({ questions: nine }).success, false);
  assert.equal(askParameters.safeParse({ questions, state: "s".repeat(8_001) }).success, false);
});

test("a one-level score question is rejected before any call", async () => {
  const { root } = await fixture();
  try {
    const { tool, ctx } = harness(root);
    const result = await withJev(() => tool.execute("1", { questions: { q: { type: "score", instructions: "x", criteria: ["only"] } }, paths: ["a.ts"] }, undefined, undefined, ctx));
    assert.equal(result.isError, true);
    assert.match(result.content[0]!.text, /2-10 levels/);
  } finally { delete process.env.JEV_AUDIT_DIR; await rm(root, { recursive: true, force: true }); }
});

test("the evidence-ladder nudge is appended once across repeated invocations", () => {
  const { nudge, tool } = harness(tmpdir());
  const base = ["base prompt"];
  const first = nudge({ systemPrompt: base })!.systemPrompt!;
  assert.equal(first.length, 2);
  assert.equal(nudge({ systemPrompt: first }), undefined);
  const joined = first.join("\n");
  assert.equal(joined.split(NUDGE_MARKER).length - 1, 1);
  assert.ok(first[1]!.length < 900);
  for (const word of ["codegraph", "find", "jev_ask", "judge_batch", "jevify", "read"]) assert.ok(first[1]!.includes(word), word);
  assert.match(tool.description, /Do NOT use it for exact symbol/);
});
