import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "bun:test";
import { openStore, storePath } from "../agent/extensions/agent-rails/lib/decision-store.ts";
import { formatReport, readLedger, reportArgs, summarise } from "../agent/extensions/agent-rails/lib/ledger-report.ts";
import type { LedgerKind } from "../agent/extensions/agent-rails/lib/rails-ledger.ts";

// The store does not constrain code, so a row can carry one the report refuses.
type Line = [day: string, repo: string | null, kind: LedgerKind, code: string, calls?: number, refusals?: number];

const fixture: Line[] = [
	["10", "owner/a", "block", "commit-scope"],
	["10", "owner/a", "block", "commit-scope"],
	["10", "owner/a", "resolved", "commit-scope", 2, 2],
	["11", "owner/a", "block", "commit-scope"],
	["11", "owner/a", "resolved", "commit-scope", 1, 1],
	["12", "owner/b", "context", "path-check"],
	["12", "owner/b", "resolved", "path-check", 5, 1],
	["12", null, "block", "push-default-branch"],
	["13", "owner/b", "block", "commit-checks"],
	["13", "owner/b", "block", "commit-scope"],
	["13", "owner/b", "resolved", "commit-scope", 4, 1],
];

// A decision store under a temporary agent dir, seeded with lines; removed after body.
function withSeededStore(lines: Line[], body: (path: string) => void): void {
	const dir = mkdtempSync(join(tmpdir(), "agent-rails-ledger-report-"));
	const path = storePath(dir);
	const db = openStore(path);
	try {
		const insert = db.query("INSERT INTO rail_events (ts, session, repo, tool, kind, code, reason, calls, refusals, machine) " +
			"VALUES ($ts, 's1', $repo, 'bash', $kind, $code, $reason, $calls, $refusals, 'test-machine')");
		for (const [day, repo, kind, code, calls, refusals] of lines) {
			insert.run({ ts: `2026-11-${day}T10:00:00.000Z`, repo, kind, code,
				reason: kind === "resolved" ? null : `Agent rails: ${code}`, calls: calls ?? null, refusals: refusals ?? null });
		}
	} finally { db.close(); }
	try { body(path); }
	finally { rmSync(dir, { recursive: true, force: true }); }
}

test("the report counts refusals by code and by repository, with the satisfied share and median calls", () => {
	withSeededStore(fixture, path => {
		const report = summarise(readLedger(path));
		assert.deepEqual([report.refusals, report.blocks, report.postEdit, report.satisfied, report.medianCalls], [7, 6, 1, 5, 3]);
		assert.deepEqual(report.codes, [
			{ code: "commit-scope", refusals: 4, blocks: 4, postEdit: 0, satisfied: 4, medianCalls: 2 },
			{ code: "path-check", refusals: 1, blocks: 0, postEdit: 1, satisfied: 1, medianCalls: 5 },
			{ code: "commit-checks", refusals: 1, blocks: 1, postEdit: 0, satisfied: 0, medianCalls: null },
			{ code: "push-default-branch", refusals: 1, blocks: 1, postEdit: 0, satisfied: 0, medianCalls: null },
		]);
		assert.deepEqual(report.groups, [
			{ repo: "owner/a", refusals: 3, satisfied: 3 },
			{ repo: "owner/b", refusals: 3, satisfied: 2 },
			{ repo: "(none)", refusals: 1, satisfied: 0 },
		]);
		const printed = formatReport(report, path);
		assert.ok(printed.startsWith(`Rails ledger ${path}\n7 refusals (6 blocks, 1 post-edit), 5 later satisfied (71%), median 3 calls to resolve\n`), printed);
		assert.match(printed, /^commit-scope +4 +4 +0 +4 +100% +2$/m);
		assert.match(printed, /^commit-checks +1 +1 +0 +0 +0% +-$/m);
		assert.match(printed, /^repo +refusals +satisfied +rate$/m, "the groups table has one row per repository, no mode column");
		assert.match(printed, /^owner\/a +3 +3 +100%$/m);
		assert.match(printed, /^\(none\) +1 +0 +0%$/m);
	});
});

test("--since keeps only rows on or after the date", () => {
	withSeededStore(fixture, path => {
		const { since } = reportArgs(["--since", "2026-11-12", "--store", path]);
		const report = summarise(readLedger(path, since), since);
		assert.equal(report.since, "2026-11-12");
		assert.deepEqual([report.refusals, report.satisfied, report.medianCalls], [4, 2, 4.5]);
		assert.ok(formatReport(report, path).startsWith(`Rails ledger ${path} since 2026-11-12\n`));
	});
});

test("the report reads the agent dir's decision store by default and refuses malformed arguments, rows and a missing store", () => {
	assert.deepEqual(reportArgs([]), { since: null, store: storePath() });
	assert.equal(reportArgs(["--store", "/tmp/x.db"]).store, "/tmp/x.db");
	for (const argv of [["--since", "yesterday"], ["--since"], ["--verbose", "1"], ["--ledger", "/tmp/x.jsonl"]]) assert.throws(() => reportArgs(argv), argv.join(" "));
	assert.throws(() => readLedger(join(tmpdir(), "agent-rails-no-such-agent-dir", "agent-rails.db")), /no decision store at/);
	withSeededStore([...fixture, ["14", "owner/a", "block", ""]], path => {
		assert.throws(() => readLedger(path), /rail_events row 12 is invalid/);
	});
	assert.deepEqual(summarise([]).codes, []);
});
