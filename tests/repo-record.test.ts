import assert from "node:assert/strict";
import { join, resolve } from "node:path";
import { describe, test } from "node:test";
import { configRoot, ownerOnly, ownerPathProblem, parseOrigin, recordDir } from "../agent/lib/repo-record.ts";

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

describe("parseOrigin", () => {
	test("each GitHub form maps to lowercase owner/name", () => {
		for (const [url, slug] of Object.entries(origins)) assert.equal(parseOrigin(url), slug, url);
	});
	test("non-GitHub or malformed origins have no slug", () => {
		for (const url of refused) assert.equal(parseOrigin(url), null, url);
	});
});

describe("record resolution", () => {
	test("owner-only entries are repo-relative paths; a trailing slash covers a directory", () => {
		for (const path of ["scripts/checks/exceptions.json", "security/", "a"]) assert.equal(ownerPathProblem(path), undefined, path);
		for (const path of ["", "/", "/abs.json", "a//b", "./a", "a/../b", "..", "a\\b", "a\0b"]) assert.ok(ownerPathProblem(path), path);
		const owned = ["scripts/checks/exceptions.json", "security/"];
		for (const path of ["scripts/checks/exceptions.json", "security/a.json", "security/deep/b.json"]) assert.equal(ownerOnly(path, owned), true, path);
		for (const path of ["scripts/checks/exceptions.json.bak", "scripts/checks", "securityx/a.json", "security"]) assert.equal(ownerOnly(path, owned), false, path);
		assert.equal(ownerOnly("anything", []), false);
	});

	test("the default root is the checkout holding the module", () => {
		const root = resolve(import.meta.dir, "..");
		assert.equal(resolve(configRoot()), root);
		assert.equal(recordDir("Owner/Name"), join(root, "repos", "owner", "name"));
	});
});
