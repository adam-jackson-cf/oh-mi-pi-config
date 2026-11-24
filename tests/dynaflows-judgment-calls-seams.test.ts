import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import type { Database } from "bun:sqlite";
import { test } from "bun:test";
import { agentDir, openStore, storePath, withStore } from "../agent/extensions/dynaflows/lib/decision-store.ts";
import { latestJudgmentCall, readJudgmentCalls } from "../agent/extensions/dynaflows/lib/judgment-calls.ts";

const digest = (char: string) => char.repeat(64);
const callId = (char: string) => char.repeat(32);
type Row = {
	call: string; question: string; ts?: string; state?: string; version?: string; judgment?: string; status?: string;
	outcome?: string | null; probability?: number | null; choice?: Record<string, number>; mode?: string;
};
// One decisions row as the engine writes it; an error row has no answer, a choice row no outcome.
function columns(row: Row, index: number) {
	const base = {
		id: `d${index}`, call: row.call, ts: row.ts ?? "2026-11-21T12:00:00+00:00", judgment: row.judgment ?? "dependency-severity",
		question: row.question, mode: row.mode ?? "enforced", version: row.version ?? digest("b"), state: row.state ?? digest("a"),
		status: "ok", error: null, outcome: null, probability: null, choice: null,
	};
	if (row.status) return { ...base, status: row.status, error: "judge offline" };
	if (row.choice) return { ...base, choice: JSON.stringify(row.choice) };
	return { ...base, outcome: row.outcome ?? "pass", probability: row.probability ?? 0.2 };
}
function store(rows: Row[]): string {
	const path = join(mkdtempSync(join(tmpdir(), "judgment-calls-")), "dynaflows.db");
	withStore(db => {
		const insert = db.query(`INSERT INTO decisions (id, call_id, ts, source, judgment, question, mode, status, error, outcome, probability,
			choice_probabilities, model, question_version, judgment_version, trigger, machine, state_sha256)
			VALUES ($id, $call, $ts, 'jev', $judgment, $question, $mode, $status, $error, $outcome, $probability,
			$choice, 'typesafe/jev-1.14.0', $question_version, $version, 'manual', 'test-host', $state)`);
		rows.forEach((row, index) => insert.run({ ...columns(row, index), question_version: digest("e") }));
	}, path);
	return path;
}

test("the store sits under the omp agent dir the engine writes to", () => {
	assert.equal(storePath("/agent"), "/agent/dynaflows/dynaflows.db");
	assert.equal(agentDir("/agent"), "/agent", "the review state and the decision store share one agent dir rule");
	assert.equal(agentDir(" "), join(homedir(), ".omp/agent"));
	assert.equal(storePath(""), join(homedir(), ".omp/agent/dynaflows/dynaflows.db"));
	assert.equal(storePath(undefined), join(homedir(), ".omp/agent/dynaflows/dynaflows.db"));
});

test("rails read logged decide() calls from the store and find the newest for a state and question version", () => {
	const path = store([
		{ call: callId("1"), question: "reached", outcome: "pass" },
		{ call: callId("1"), question: "impact", choice: { rce: 0.9, dos: 0.1 } },
		{ call: callId("2"), question: "reached", outcome: "escalate", probability: 0.5 },
		{ call: callId("3"), question: "reached", outcome: "flag", probability: 0.9, state: digest("c"), mode: "shadow" },
		{ call: callId("4"), question: "reached", status: "error", ts: "2026-11-21T12:00:09+00:00" },
	]);
	const calls = readJudgmentCalls(path);
	assert.deepEqual(calls.map(call => call.call_id), [callId("1"), callId("2"), callId("3")], "an error call never produced an outcome");
	assert.deepEqual(calls[0], {
		call_id: callId("1"), judgment: "dependency-severity", state_sha256: digest("a"), model: "typesafe/jev-1.14.0", question_version: digest("b"),
		probabilities: { reached: 0.2, impact: { rce: 0.9, dos: 0.1 } }, outcomes: { reached: "no" }, modes: { reached: "enforced", impact: "enforced" },
		timestamp: "2026-11-21T12:00:00+00:00",
	});
	assert.deepEqual(calls[2]?.modes, { reached: "shadow" });
	assert.deepEqual(latestJudgmentCall("dependency-severity", digest("a"), digest("b"), path)?.outcomes, { reached: "escalate" });
	assert.equal(latestJudgmentCall("dependency-severity", digest("a"), digest("d"), path), undefined);
	assert.equal(latestJudgmentCall("structure-naming", digest("a"), digest("b"), path), undefined);
	assert.deepEqual(readJudgmentCalls(join(tmpdir(), "absent", "dynaflows.db")), []);
});

test("a stored call outside the registry's shape fails loudly, never as no decision", () => {
	for (const bad of [
		{ call: callId("5"), question: "reached", probability: 1.5 },
		{ call: callId("5"), question: "reached", judgment: "../x" },
		{ call: callId("5"), question: "reached", state: "short" },
	]) {
		const path = store([{ call: callId("1"), question: "reached" }, bad]);
		assert.throws(() => readJudgmentCalls(path), /decision store call 5{32} is not a valid decide\(\) call/);
	}
});

test("a store at another schema version, the v1 store included, is refused, never read or upgraded", () => {
	for (const version of [1, 99]) {
		const path = store([{ call: callId("1"), question: "reached" }]);
		withStore(db => db.exec(`PRAGMA user_version = ${version}`), path);
		assert.throws(() => openStore(path), new RegExp(`schema version ${version}; this harness reads version 2 only`));
		assert.throws(() => readJudgmentCalls(path), new RegExp(`schema version ${version}`));
	}
});

test("every store handle is closed after use, also when the use throws", () => {
	const path = store([{ call: callId("1"), question: "reached" }]);
	const handles: Database[] = [];
	withStore(db => { handles.push(db); }, path);
	assert.throws(() => withStore(db => { handles.push(db); throw new Error("query failed"); }, path), /query failed/);
	for (const handle of handles) assert.throws(() => handle.query("SELECT 1").get(), /closed/i);
});
