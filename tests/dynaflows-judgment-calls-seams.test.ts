import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir, homedir } from "node:os";
import { join } from "node:path";
import { test } from "bun:test";
import { judgmentCallLog, latestJudgmentCall, readJudgmentCalls, type JudgmentCall } from "../agent/extensions/dynaflows/lib/judgment-calls.ts";

const digest = (char: string) => char.repeat(64);
function call(overrides: Partial<JudgmentCall> = {}): JudgmentCall {
	return {
		judgment: "dependency-severity", state_sha256: digest("a"), model: "typesafe/jev-1.14.0",
		question_version: digest("b"), probabilities: { reached: 0.2, impact: { rce: 0.9, dos: 0.1 } },
		outcomes: { reached: "no" }, timestamp: "2026-11-21T12:00:00+00:00", ...overrides,
	};
}
function log(lines: string[]): string {
	const dir = mkdtempSync(join(tmpdir(), "judgment-calls-"));
	mkdirSync(join(dir, "dynaflows"));
	const path = judgmentCallLog(dir);
	writeFileSync(path, lines.join("\n") + "\n");
	return path;
}

test("the log sits under the omp agent dir the engine writes to", () => {
	assert.equal(judgmentCallLog("/agent"), "/agent/dynaflows/judgment-calls.jsonl");
	assert.equal(judgmentCallLog(""), join(homedir(), ".omp/agent/dynaflows/judgment-calls.jsonl"));
	assert.equal(judgmentCallLog(undefined), join(homedir(), ".omp/agent/dynaflows/judgment-calls.jsonl"));
});

test("rails read logged decide() calls and find the newest for a state and question version", async () => {
	const path = log([
		JSON.stringify(call()),
		"",
		JSON.stringify(call({ outcomes: { reached: "escalate" } })),
		JSON.stringify(call({ state_sha256: digest("c"), outcomes: { reached: "yes" } })),
	]);
	assert.equal((await readJudgmentCalls(path)).length, 3);
	assert.deepEqual((await latestJudgmentCall("dependency-severity", digest("a"), digest("b"), path))?.outcomes, { reached: "escalate" });
	assert.equal(await latestJudgmentCall("dependency-severity", digest("a"), digest("d"), path), undefined);
	assert.equal(await latestJudgmentCall("structure-naming", digest("a"), digest("b"), path), undefined);
	assert.deepEqual(await readJudgmentCalls(join(tmpdir(), "absent", "judgment-calls.jsonl")), []);
});

test("a malformed or unknown-shaped record fails loudly, never as no decision", async () => {
	for (const bad of [
		"not json",
		JSON.stringify({ ...call(), outcomes: { reached: "maybe" } }),
		JSON.stringify(call({ probabilities: { reached: 1.5 } })),
		JSON.stringify(call({ state_sha256: "short" })),
		JSON.stringify(call({ judgment: "../x" })),
		JSON.stringify({ ...call(), extra: true }),
	]) {
		const path = log([JSON.stringify(call()), bad]);
		await assert.rejects(readJudgmentCalls(path), /line 2 is not a valid decide\(\) record/);
	}
});
