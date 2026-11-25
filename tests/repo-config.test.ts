import assert from "node:assert/strict";
import { join, resolve } from "node:path";
import { describe, test } from "bun:test";
import { RailError } from "../agent/extensions/agent-rails/lib/rails-ledger.ts";
import { commonDir, configRoot, mainCheckout, originSlug, OutsideRepository, parseOrigin, type ExecApi } from "../agent/extensions/agent-rails/lib/git-paths.ts";
import { CONFIG_DIR, CONFIG_EXCLUDE, configPathFor, resolveConfig } from "../agent/extensions/agent-rails/lib/repo-config.ts";

type Reply = { code: number; stdout?: string; stderr?: string; killed?: boolean };
type Call = { command: string; args: string[] };

// An exec double: each git subcommand answers from the table; every call is recorded.
function execDouble(replies: Record<string, Reply>): ExecApi & { calls: Call[] } {
	const calls: Call[] = [];
	return {
		calls,
		async exec(command: string, args: string[]) {
			calls.push({ command, args });
			const reply = replies[[command, ...args].join(" ")];
			if (!reply) throw new Error(`unexpected exec: ${command} ${args.join(" ")}`);
			return { code: reply.code, killed: reply.killed ?? false, stdout: reply.stdout ?? "", stderr: reply.stderr ?? "" };
		},
	};
}

const COMMON = "git rev-parse --path-format=absolute --git-common-dir";
const ORIGIN = "git remote get-url origin";
const root = resolve(import.meta.dir, "..");
const outside = { code: 128, stderr: "fatal: not a git repository (or any of the parent directories): .git\n" };

describe("git paths", () => {
	test("commonDir and mainCheckout resolve a checkout's .git to its parent", async () => {
		const pi = execDouble({ [COMMON]: { code: 0, stdout: `${join(root, ".git")}\n` } });
		assert.equal(await commonDir(pi, root), join(root, ".git"));
		assert.equal(await mainCheckout(pi, root), root);
	});

	test("a common dir that is not .git has no main checkout", async () => {
		const pi = execDouble({ [COMMON]: { code: 0, stdout: import.meta.dir } });
		const error = await mainCheckout(pi, root).catch((caught: Error) => caught);
		assert.ok(error instanceof RailError, String(error));
		assert.equal(error.code, "config-unresolved");
		assert.ok(error.message.includes("bare repository"), error.message);
	});

	test("outside a repository is OutsideRepository; any other git failure is a plain error", async () => {
		const away = await commonDir(execDouble({ [COMMON]: outside }), "/nowhere").catch((caught: Error) => caught);
		assert.ok(away instanceof OutsideRepository, String(away));
		const broken = await commonDir(execDouble({ [COMMON]: { code: 128, stderr: "fatal: detected dubious ownership" } }), root).catch((caught: Error) => caught);
		assert.ok(broken instanceof Error && !(broken instanceof OutsideRepository), String(broken));
		const killed = await commonDir(execDouble({ [COMMON]: { code: 0, stdout: root, killed: true } }), root).catch((caught: Error) => caught);
		assert.ok(killed instanceof Error && !(killed instanceof OutsideRepository), String(killed));
	});

	test("originSlug reads GitHub origins, null for none or another host, and throws otherwise", async () => {
		assert.equal(await originSlug(execDouble({ [ORIGIN]: { code: 0, stdout: "git@github.com:Owner/Name.git\n" } }), root), "owner/name");
		assert.equal(await originSlug(execDouble({ [ORIGIN]: { code: 0, stdout: "https://gitlab.com/owner/name.git\n" } }), root), null);
		assert.equal(await originSlug(execDouble({ [ORIGIN]: { code: 2, stderr: "error: No such remote 'origin'\n" } }), root), null);
		await assert.rejects(originSlug(execDouble({ [ORIGIN]: { code: 128, stderr: "fatal: bad config" } }), root), /get-url origin/);
	});

	test("parseOrigin maps each GitHub form to lowercase owner/name", () => {
		const origins = {
			"https://github.com/Owner-1/Repo.Name": "owner-1/repo.name",
			"https://github.com/Owner-1/Repo.Name.git": "owner-1/repo.name",
			"git@github.com:Owner-1/Repo_Name": "owner-1/repo_name",
			"git@github.com:Owner-1/Repo_Name.git": "owner-1/repo_name",
			"ssh://git@github.com/Owner-1/repo-name": "owner-1/repo-name",
			"ssh://git@github.com/Owner-1/repo-name.git\n": "owner-1/repo-name",
		} satisfies Record<string, string>;
		for (const [url, slug] of Object.entries(origins)) assert.equal(parseOrigin(url), slug, url);
	});

	test("parseOrigin gives no slug for non-GitHub or malformed origins", () => {
		for (const url of [
			"https://gitlab.com/owner/name.git", "https://github.com/owner", "https://github.com/owner/name/extra",
			"https://github.com/../name", "git@github.com:owner/..", "file:///tmp/owner/name", "",
		]) assert.equal(parseOrigin(url), null, url);
	});

	test("configRoot is the checkout holding the extension", () => {
		assert.equal(resolve(configRoot()), root);
	});
});

describe("repo config paths", () => {
	test("the config lives in .agentrails/config.json of the main checkout and is excluded as a directory", () => {
		assert.equal(CONFIG_DIR, ".agentrails");
		assert.equal(configPathFor("/repo"), "/repo/.agentrails/config.json");
		assert.equal(CONFIG_EXCLUDE, "/.agentrails/");
	});

	test("outside, no-origin, non-GitHub and bare cases never call the engine", async () => {
		const cases: [Record<string, Reply>, string, RegExp][] = [
			[{ [COMMON]: outside }, "outside", /not in a git repository/],
			[{ [COMMON]: { code: 0, stdout: join(root, ".git") }, [ORIGIN]: { code: 2, stderr: "error: No such remote 'origin'" } }, "outside", /no GitHub origin/],
			[{ [COMMON]: { code: 0, stdout: join(root, ".git") }, [ORIGIN]: { code: 0, stdout: "https://gitlab.com/a/b.git" } }, "outside", /no GitHub origin/],
			[{ [COMMON]: { code: 0, stdout: import.meta.dir } }, "unresolved", /no main checkout/],
			[{ [COMMON]: { code: 0, stdout: join(root, ".git") }, [ORIGIN]: { code: 128, stderr: "fatal: bad config" } }, "unresolved", /get-url origin/],
		];
		for (const [replies, kind, reason] of cases) {
			const pi = execDouble(replies);
			const state = await resolveConfig(pi, "/engine", root);
			assert.equal(state.kind, kind, JSON.stringify(state));
			assert.ok("reason" in state && reason.test(state.reason), JSON.stringify(state));
			assert.ok(pi.calls.every(call => call.command === "git"), JSON.stringify(pi.calls));
		}
	});
});
