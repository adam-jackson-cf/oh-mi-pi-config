import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, test } from "node:test";
import { findRecord, loadGate, minimalText, type Mode, RecordError, repoSlug, resolveRecord } from "../agent/extensions/dynaflows/lib/repo-record.ts";

const completeGate = { schemaVersion: 2, checks: { commit: ["make", "gate"] }, dynaflows: { mode: "structured" } };

async function recordMode(repo: string, config: string): Promise<Mode> {
	const record = await findRecord(repo, config);
	return record === null ? "minimal" : (await loadGate(record)).dynaflows.mode;
}

const git = (...args: string[]) => execFileSync("git", args, { stdio: "pipe" });

function fixture() {
	const temp = realpathSync(mkdtempSync(join(tmpdir(), "repo-record-")));
	const config = join(temp, "config");
	const repo = join(temp, "clone");
	git("init", "-q", "-b", "main", repo);
	git("-C", repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "--allow-empty", "-m", "init");
	const writeRecord = (slug: string, gateText: string) => {
		const record = join(config, "repos", slug);
		mkdirSync(record, { recursive: true });
		writeFileSync(join(record, "gate.json"), gateText);
		return record;
	};
	return { temp, config, repo, writeRecord, cleanup: () => rmSync(temp, { recursive: true, force: true }) };
}

describe("record resolution", () => {
	test("resolveRecord names the slug and the reason a clone has no record, in the twin's words", async () => {
		const f = fixture();
		try {
			assert.deepEqual(await resolveRecord(f.repo, f.config), { slug: null, dir: null, reason: "no origin remote" });
			git("-C", f.repo, "remote", "add", "origin", "https://gitlab.com/owner/name.git");
			assert.deepEqual(await resolveRecord(f.repo, f.config),
				{ slug: null, dir: null, reason: "origin https://gitlab.com/owner/name.git is not a GitHub owner/name URL" });
			git("-C", f.repo, "remote", "set-url", "origin", "git@github.com:Owner/Name.git");
			const gate = join(f.config, "repos", "owner", "name", "gate.json");
			assert.deepEqual(await resolveRecord(f.repo, f.config), { slug: "owner/name", dir: null, reason: `no per-repo record at ${gate}` });
			const record = f.writeRecord("owner/name", "{}");
			assert.deepEqual(await resolveRecord(f.repo, f.config), { slug: "owner/name", dir: record });
			assert.equal(minimalText(null, "no origin remote"), "dynaflows minimal mode for none: no origin remote");
			assert.equal(minimalText("owner/name", "why"), "dynaflows minimal mode for owner/name: why");
		} finally { f.cleanup(); }
	});

	test("no origin means no record and minimal mode", async () => {
		const f = fixture();
		try {
			assert.equal(await repoSlug(f.repo), null);
			assert.equal(await findRecord(f.repo, f.config), null);
			assert.equal(await recordMode(f.repo, f.config), "minimal");
		} finally { f.cleanup(); }
	});

	test("origin without a record means minimal mode", async () => {
		const f = fixture();
		try {
			git("-C", f.repo, "remote", "add", "origin", "git@github.com:Owner/Name.git");
			assert.equal(await repoSlug(f.repo), "owner/name");
			assert.equal(await findRecord(f.repo, f.config), null);
			assert.equal(await recordMode(f.repo, f.config), "minimal");
		} finally { f.cleanup(); }
	});

	test("a worktree resolves to the main checkout's record", async () => {
		const f = fixture();
		try {
			git("-C", f.repo, "remote", "add", "origin", "https://github.com/Owner/Name.git");
			const record = f.writeRecord("owner/name", JSON.stringify(completeGate));
			const worktree = join(f.temp, "worktree");
			git("-C", f.repo, "worktree", "add", "-q", "-b", "agent/x", worktree);
			for (const clone of [f.repo, worktree]) {
				assert.equal(await findRecord(clone, f.config), record);
				assert.equal(await recordMode(clone, f.config), "structured");
			}
		} finally { f.cleanup(); }
	});

	test("a minimal record is minimal and a malformed or enabled-shaped record is an error", async () => {
		const f = fixture();
		try {
			git("-C", f.repo, "remote", "add", "origin", "https://github.com/owner/name");
			f.writeRecord("owner/name", JSON.stringify({ ...completeGate, dynaflows: { mode: "minimal" } }));
			assert.equal(await recordMode(f.repo, f.config), "minimal");
			f.writeRecord("owner/name", JSON.stringify({ schemaVersion: 2 }));
			await assert.rejects(recordMode(f.repo, f.config), RecordError);
			f.writeRecord("owner/name", "{");
			await assert.rejects(recordMode(f.repo, f.config), RecordError);
			f.writeRecord("owner/name", JSON.stringify({ ...completeGate, dynaflows: { enabled: true } }));
			await assert.rejects(recordMode(f.repo, f.config), (error: Error) => error instanceof RecordError && error.message.includes("unknown field dynaflows.enabled must be removed"));
		} finally { f.cleanup(); }
	});

	test("a record declaring the dependency audit argv loads", async () => {
		const f = fixture();
		try {
			git("-C", f.repo, "remote", "add", "origin", "https://github.com/owner/name");
			f.writeRecord("owner/name", JSON.stringify({ ...completeGate, checks: { commit: ["make"], audit: ["make", "audit"] } }));
			assert.equal(await recordMode(f.repo, f.config), "structured");
		} finally { f.cleanup(); }
	});

	test("a record missing a required field or carrying an unknown one does not load", async () => {
		const f = fixture();
		try {
			git("-C", f.repo, "remote", "add", "origin", "https://github.com/owner/name");
			for (const [invalid, named] of [
				[{ ...completeGate, checks: { ci: ["ci"] } }, "checks"],
				[{ ...completeGate, checks: undefined }, "checks"], [{ ...completeGate, unknownField: 3 }, "unknownField"],
				[{ ...completeGate, extraField: 1000 }, "extraField"], [{ ...completeGate, otherField: "github" }, "otherField"],
				[{ ...completeGate, checks: { commit: ["make"], lint: ["make"] } }, "checks.lint"],
				[{ ...completeGate, ownerOnlyPaths: [] }, "ownerOnlyPaths"],
				[{ ...completeGate, checks: { commit: ["make"], audit: [] } }, "audit"],
			] as const) {
				f.writeRecord("owner/name", JSON.stringify(invalid));
				await assert.rejects(recordMode(f.repo, f.config), (error: Error) => error instanceof RecordError && error.message.includes(named), named);
			}
		} finally { f.cleanup(); }
	});
});
