import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { describe, test } from "node:test";
import { configRoot, dynaflowsEnabled, findRecord, parseOrigin, RecordError, recordDir, repoSlug } from "../agent/lib/repo-record.ts";

const origins = {
	"https://github.com/Owner-1/Repo.Name": "owner-1/repo.name",
	"https://github.com/Owner-1/Repo.Name.git": "owner-1/repo.name",
	"git@github.com:Owner-1/Repo_Name": "owner-1/repo_name",
	"git@github.com:Owner-1/Repo_Name.git": "owner-1/repo_name",
	"ssh://git@github.com/Owner-1/repo-name": "owner-1/repo-name",
	"ssh://git@github.com/Owner-1/repo-name.git\n": "owner-1/repo-name",
} satisfies Record<string, string>;
const refused = [
	"https://gitlab.com/owner/name.git",
	"https://github.com/owner",
	"https://github.com/owner/name/extra",
	"https://github.com/../name",
	"git@github.com:owner/..",
	"file:///tmp/owner/name",
	"",
];

const git = (...args: string[]) => execFileSync("git", args, { stdio: "pipe" });

function fixture() {
	const temp = realpathSync(mkdtempSync(join(tmpdir(), "repo-record-")));
	const config = join(temp, "config");
	const repo = join(temp, "clone");
	git("init", "-q", "-b", "main", repo);
	git("-C", repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "--allow-empty", "-m", "init");
	// Gate text is written raw so a test can also store malformed JSON.
	const writeRecord = (slug: string, gateText: string) => {
		const record = join(config, "repos", slug);
		mkdirSync(record, { recursive: true });
		writeFileSync(join(record, "gate.json"), gateText);
		return record;
	};
	return { temp, config, repo, writeRecord, cleanup: () => rmSync(temp, { recursive: true, force: true }) };
}

describe("parseOrigin", () => {
	test("each GitHub form maps to lowercase owner/name", () => {
		for (const [url, slug] of Object.entries(origins)) assert.equal(parseOrigin(url), slug, url);
	});
	test("non-GitHub or malformed origins have no slug", () => {
		for (const url of refused) assert.equal(parseOrigin(url), null, url);
	});
});

describe("record resolution", () => {
	test("no origin means no record and dynaflows off", async () => {
		const f = fixture();
		try {
			assert.equal(await repoSlug(f.repo), null);
			assert.equal(await findRecord(f.repo, f.config), null);
			assert.equal(await dynaflowsEnabled(f.repo, f.config), false);
		} finally { f.cleanup(); }
	});

	test("origin without a record means dynaflows off", async () => {
		const f = fixture();
		try {
			git("-C", f.repo, "remote", "add", "origin", "git@github.com:Owner/Name.git");
			assert.equal(await repoSlug(f.repo), "owner/name");
			assert.equal(await findRecord(f.repo, f.config), null);
			assert.equal(await dynaflowsEnabled(f.repo, f.config), false);
		} finally { f.cleanup(); }
	});

	test("a worktree resolves to the main checkout's record", async () => {
		const f = fixture();
		try {
			git("-C", f.repo, "remote", "add", "origin", "https://github.com/Owner/Name.git");
			const record = f.writeRecord("owner/name", JSON.stringify({ schemaVersion: 2, dynaflows: { enabled: true } }));
			const worktree = join(f.temp, "worktree");
			git("-C", f.repo, "worktree", "add", "-q", "-b", "agent/x", worktree);
			for (const clone of [f.repo, worktree]) {
				assert.equal(await findRecord(clone, f.config), record);
				assert.equal(await dynaflowsEnabled(clone, f.config), true);
			}
		} finally { f.cleanup(); }
	});

	test("a disabled record is off and a malformed record is an error", async () => {
		const f = fixture();
		try {
			git("-C", f.repo, "remote", "add", "origin", "https://github.com/owner/name");
			f.writeRecord("owner/name", JSON.stringify({ schemaVersion: 2, dynaflows: { enabled: false } }));
			assert.equal(await dynaflowsEnabled(f.repo, f.config), false);
			f.writeRecord("owner/name", JSON.stringify({ schemaVersion: 2 }));
			await assert.rejects(dynaflowsEnabled(f.repo, f.config), RecordError);
			f.writeRecord("owner/name", "{");
			await assert.rejects(dynaflowsEnabled(f.repo, f.config), RecordError);
		} finally { f.cleanup(); }
	});

	test("the default root is the checkout holding the module", () => {
		const root = resolve(import.meta.dir, "..");
		assert.equal(resolve(configRoot()), root);
		assert.equal(recordDir("Owner/Name"), join(root, "repos", "owner", "name"));
	});
});
