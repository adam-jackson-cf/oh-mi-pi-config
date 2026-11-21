import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, test } from "node:test";
import { findRecord, loadGate, minimalText, RecordError, repoSlug, resolveRecord } from "../agent/extensions/dynaflows/lib/repo-record.ts";
import { defaultModesPath, repoMode, writeMode } from "../agent/extensions/dynaflows/lib/modes.ts";

const completeGate = { schemaVersion: 2, checks: { commit: ["make", "gate"] } };

async function recordState(repo: string, config: string): Promise<"absent" | "loaded"> {
	const record = await findRecord(repo, config);
	if (record === null) return "absent";
	await loadGate(record);
	return "loaded";
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
			assert.equal(await recordState(f.repo, f.config), "absent");
		} finally { f.cleanup(); }
	});

	test("origin without a record means minimal mode", async () => {
		const f = fixture();
		try {
			git("-C", f.repo, "remote", "add", "origin", "git@github.com:Owner/Name.git");
			assert.equal(await repoSlug(f.repo), "owner/name");
			assert.equal(await findRecord(f.repo, f.config), null);
			assert.equal(await recordState(f.repo, f.config), "absent");
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
				assert.equal(await recordState(clone, f.config), "loaded");
			}
		} finally { f.cleanup(); }
	});

	test("a complete record loads and a malformed record is an error", async () => {
		const f = fixture();
		try {
			git("-C", f.repo, "remote", "add", "origin", "https://github.com/owner/name");
			f.writeRecord("owner/name", JSON.stringify(completeGate));
			assert.equal(await recordState(f.repo, f.config), "loaded");
			f.writeRecord("owner/name", JSON.stringify({ schemaVersion: 2 }));
			await assert.rejects(recordState(f.repo, f.config), RecordError);
			f.writeRecord("owner/name", "{");
			await assert.rejects(recordState(f.repo, f.config), RecordError);
		} finally { f.cleanup(); }
	});

	test("a record's afterMerge and mergePolicies load; mergePolicies defaults to none", async () => {
		const f = fixture();
		try {
			const policy = { base: "nuc-config", headPrefix: "agent/nuc-sync-", method: "merge", proof: false };
			const record = f.writeRecord("owner/name", JSON.stringify({ ...completeGate, afterMerge: { argv: ["make", "sync"], paths: ["agent/**"] }, mergePolicies: [policy] }));
			const gate = await loadGate(record);
			assert.deepEqual([gate.afterMerge, gate.mergePolicies], [{ argv: ["make", "sync"], paths: ["agent/**"] }, [policy]]);
			f.writeRecord("owner/name", JSON.stringify(completeGate));
			assert.deepEqual([(await loadGate(record)).afterMerge, (await loadGate(record)).mergePolicies], [undefined, []]);
			f.writeRecord("owner/name", JSON.stringify({ ...completeGate, mergePolicies: [{ ...policy, method: "squash" }] }));
			await assert.rejects(loadGate(record), /mergePolicies/);
		} finally { f.cleanup(); }
	});

	test("a record declaring the dependency audit argv loads", async () => {
		const f = fixture();
		try {
			git("-C", f.repo, "remote", "add", "origin", "https://github.com/owner/name");
			f.writeRecord("owner/name", JSON.stringify({ ...completeGate, checks: { commit: ["make"], audit: ["make", "audit"] } }));
			assert.equal(await recordState(f.repo, f.config), "loaded");
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
				[{ ...completeGate, dynaflows: { mode: "structured" } }, "dynaflows"],
				[{ ...completeGate, checks: { commit: ["make"], audit: [] } }, "audit"],
			] as const) {
				f.writeRecord("owner/name", JSON.stringify(invalid));
				await assert.rejects(recordState(f.repo, f.config), (error: Error) => error instanceof RecordError && error.message.includes(named), named);
			}
		} finally { f.cleanup(); }
	});
});

describe("mode file", () => {
	test("the tracked mode file seeds the structured repositories and lists no minimal one", () => {
		assert.deepEqual(JSON.parse(readFileSync(defaultModesPath(), "utf8")), {
			schemaVersion: 1,
			repos: {
				"adam-jackson-cf/enaible-analyzers": "structured", "adam-jackson-cf/oh-mi-pi-config": "structured",
				"adam-jackson-cf/raidar": "structured", "adam-jackson-cf/scout": "structured",
			},
		});
	});

	test("an unlisted repository is minimal; writes keep the file sorted and drop minimal entries", async () => {
		const temp = realpathSync(mkdtempSync(join(tmpdir(), "modes-")));
		const path = join(temp, "modes.json");
		try {
			writeFileSync(path, JSON.stringify({ schemaVersion: 1, repos: {} }));
			assert.equal(await repoMode(path, "owner/name"), "minimal");
			await writeMode(path, "Owner/Zed", "structured");
			await writeMode(path, "owner/alpha", "structured");
			assert.equal(await repoMode(path, "OWNER/ZED"), "structured");
			assert.equal(readFileSync(path, "utf8"), `${JSON.stringify({ schemaVersion: 1, repos: { "owner/alpha": "structured", "owner/zed": "structured" } }, null, 2)}\n`);
			await writeMode(path, "owner/zed", "minimal");
			assert.deepEqual(JSON.parse(readFileSync(path, "utf8")).repos, { "owner/alpha": "structured" });
		} finally { rmSync(temp, { recursive: true, force: true }); }
	});

	test("a missing or malformed mode file is an error, never minimal", async () => {
		const temp = realpathSync(mkdtempSync(join(tmpdir(), "modes-")));
		const path = join(temp, "modes.json");
		try {
			await assert.rejects(repoMode(path, "owner/name"), /cannot be read/);
			for (const text of ["{", JSON.stringify({ schemaVersion: 2, repos: {} }), JSON.stringify({ schemaVersion: 1, repos: { "owner/name": "on" } }),
				JSON.stringify({ schemaVersion: 1, repos: {}, machine: "minimal" }), JSON.stringify({ schemaVersion: 1, repos: { "Owner/Name": "structured" } })]) {
				writeFileSync(path, text);
				await assert.rejects(repoMode(path, "owner/name"), /the mode file/, text);
				await assert.rejects(writeMode(path, "owner/name", "structured"), /the mode file/, text);
			}
		} finally { rmSync(temp, { recursive: true, force: true }); }
	});
});
