import assert from "node:assert/strict";
import { execFile, execFileSync } from "node:child_process";
import { chmodSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, realpathSync, rmSync, unlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { afterAll, beforeAll, describe, test } from "bun:test";
import { z } from "@oh-my-pi/pi-coding-agent";
import { ensureExcluded, OriginError, repoSlug, unresolvedText, type ExecApi } from "../agent/extensions/agent-rails/lib/git-paths.ts";
import { CONFIG_EXCLUDE, configPathFor, QUESTIONNAIRE_VERSION, resolveConfig, type ConfigState } from "../agent/extensions/agent-rails/lib/repo-config.ts";

const ENGINE = resolve(import.meta.dir, "../agent/extensions/agent-rails/engine");
const FIXTURES = resolve(import.meta.dir, "fixtures/agentrails");
const expected: Record<string, string[]> = JSON.parse(readFileSync(join(FIXTURES, "expected-problems.json"), "utf8"));
const TIMEOUT_MS = 30_000;

const execFailure = z.object({ code: z.number().optional(), killed: z.boolean().optional() });
const git = (...args: string[]) => execFileSync("git", args, { stdio: "pipe", encoding: "utf8" });

// The real exec, counting engine runs: every uv call is a config-check.
function realExec(): ExecApi & { engineRuns: () => number } {
	let runs = 0;
	return {
		engineRuns: () => runs,
		exec(command: string, args: string[], options?: { cwd?: string; timeout?: number }) {
			if (command === "uv") runs++;
			return new Promise(done => {
				execFile(command, args, { cwd: options?.cwd, timeout: options?.timeout, encoding: "utf8" }, (error, stdout, stderr) => {
					const failure = error === null ? { code: 0 } : execFailure.parse(error);
					done({ code: failure.code ?? 1, killed: failure.killed ?? false, stdout, stderr });
				});
			});
		},
	};
}

type Repos = { temp: string; main: string; worktree: string; config: string };
let repos: Repos;

function repository(path: string, origin?: string): string {
	git("init", "-q", "-b", "main", path);
	if (origin !== undefined) git("-C", path, "remote", "add", "origin", origin);
	return path;
}

beforeAll(() => {
	const temp = realpathSync(mkdtempSync(join(tmpdir(), "repo-config-")));
	const main = repository(join(temp, "main"), "https://github.com/example-owner/ledger.git");
	git("-C", main, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "core.hooksPath=/dev/null", "commit", "-q", "--allow-empty", "-m", "init");
	const worktree = join(temp, "worktree");
	git("-C", main, "worktree", "add", "-q", "-b", "agent/x", worktree);
	repos = { temp, main, worktree, config: configPathFor(main) };
	mkdirSync(join(main, ".agentrails"));
});
afterAll(() => rmSync(repos.temp, { recursive: true, force: true }));

const fixture = (kind: "valid" | "invalid", name: string) => readFileSync(join(FIXTURES, kind, name), "utf8");
const slugOf = (text: string) => /"slug":\s*"([^"]+)"/.exec(text)?.[1] ?? "example-owner/ledger";

function place(text: string): void {
	writeFileSync(repos.config, text);
	git("-C", repos.main, "remote", "set-url", "origin", `https://github.com/${slugOf(text)}.git`);
}

function problems(state: ConfigState): string[] {
	assert.equal(state.kind, "invalid", JSON.stringify(state));
	return state.kind === "invalid" ? state.problems : [];
}

describe("repo config from a real repository", () => {
	const valid = readdirSync(join(FIXTURES, "valid")).toSorted();
	const invalid = readdirSync(join(FIXTURES, "invalid")).toSorted();

	test("the corpus is complete", () => {
		assert.ok(valid.length >= 4 && invalid.length >= 30);
		assert.deepEqual(invalid, Object.keys(expected).toSorted());
	});

	test.each(valid)("valid fixture %s resolves ok from the main checkout and the linked worktree", async name => {
		const text = fixture("valid", name);
		place(text);
		for (const cwd of [repos.main, repos.worktree]) {
			const state = await resolveConfig(realExec(), ENGINE, cwd);
			assert.equal(state.kind, "ok", JSON.stringify(state));
			if (state.kind !== "ok") return;
			assert.equal(state.main, repos.main);
			assert.equal(state.path, repos.config);
			assert.deepEqual(state.config, JSON.parse(text));
			assert.match(state.sha, /^[0-9a-f]{64}$/);
		}
	}, TIMEOUT_MS);

	test.each(invalid)("invalid fixture %s gives the engine's problems", async name => {
		const text = fixture("invalid", name);
		place(text);
		const found = problems(await resolveConfig(realExec(), ENGINE, repos.worktree));
		const slug = slugOf(text);
		const mismatch = slug === slug.toLowerCase() ? [] : [`repo.slug: is '${slug}' but the origin remote names '${slug.toLowerCase()}'`];
		// A JSON parse problem ends with the parser's position, which the corpus leaves out.
		const want = [...(expected[name] ?? []), ...mismatch];
		assert.equal(found.length, want.length, JSON.stringify(found));
		want.forEach((problem, index) => assert.ok(found[index]?.startsWith(problem), `${found[index]} starts with ${problem}`));
	}, TIMEOUT_MS);

	test("QUESTIONNAIRE_VERSION equals the engine's questionnaire version", async () => {
		const config = JSON.parse(fixture("valid", "minimal.json"));
		place(JSON.stringify({ ...config, onboarding: { ...config.onboarding, questionnaireVersion: QUESTIONNAIRE_VERSION } }));
		assert.equal((await resolveConfig(realExec(), ENGINE, repos.main)).kind, "ok");
		place(JSON.stringify({ ...config, onboarding: { ...config.onboarding, questionnaireVersion: QUESTIONNAIRE_VERSION + 1 } }));
		assert.deepEqual(problems(await resolveConfig(realExec(), ENGINE, repos.main)), [
			`onboarding.questionnaireVersion: is ${QUESTIONNAIRE_VERSION + 1} but the extension's questionnaire is version ${QUESTIONNAIRE_VERSION}; ask the owner to run /agentrails configure`,
		]);
	}, TIMEOUT_MS);

	test("the engine runs once per content: an edit reruns it, a deleted file is missing and drops the cache", async () => {
		const pi = realExec();
		const text = fixture("valid", "minimal.json");
		place(text);
		assert.equal((await resolveConfig(pi, ENGINE, repos.main)).kind, "ok");
		assert.equal((await resolveConfig(pi, ENGINE, repos.worktree)).kind, "ok");
		assert.equal(pi.engineRuns(), 1, "an unchanged file is served from the cache");
		writeFileSync(repos.config, `${text}\n`);
		assert.equal((await resolveConfig(pi, ENGINE, repos.main)).kind, "ok");
		assert.equal(pi.engineRuns(), 2, "an edit reruns the engine");
		unlinkSync(repos.config);
		assert.deepEqual(await resolveConfig(pi, ENGINE, repos.worktree), { kind: "missing", main: repos.main, path: repos.config });
		assert.equal(pi.engineRuns(), 2, "a missing file needs no engine run");
		writeFileSync(repos.config, `${text}\n`);
		assert.equal((await resolveConfig(pi, ENGINE, repos.main)).kind, "ok");
		assert.equal(pi.engineRuns(), 3, "the verdict for the deleted file was dropped");
		unlinkSync(repos.config);
	}, TIMEOUT_MS);
});

// The engine's own reading of a directory, for parity with resolveConfig's git-only cases.
async function engineKind(cwd: string): Promise<string> {
	const result = await realExec().exec("uv", ["run", "--project", ENGINE, "guardrails", "config-check", "--repo", cwd], { cwd: repos.temp });
	const outcome: { kind: string } = JSON.parse(result.stdout.trim());
	return outcome.kind;
}

describe("repositories with no config", () => {
	test("outside and unresolved repositories resolve as the engine reads them, without running it", async () => {
		const away = join(repos.temp, "plain");
		mkdirSync(away);
		const bare = join(repos.temp, "bare.git");
		git("init", "-q", "--bare", bare);
		const cases: [string, ConfigState["kind"]][] = [
			[away, "outside"],
			[repository(join(repos.temp, "no-origin")), "outside"],
			[repository(join(repos.temp, "gitlab"), "https://gitlab.com/example-owner/ledger.git"), "outside"],
			[bare, "unresolved"],
		];
		for (const [cwd, kind] of cases) {
			const pi = realExec();
			const state = await resolveConfig(pi, ENGINE, cwd);
			assert.equal(state.kind, kind, `${cwd}: ${JSON.stringify(state)}`);
			assert.equal(pi.engineRuns(), 0);
			assert.equal(await engineKind(cwd), kind, cwd);
		}
	}, TIMEOUT_MS);

	test("a GitHub repository without the file is missing", async () => {
		const pi = realExec();
		assert.deepEqual(await resolveConfig(pi, ENGINE, repos.worktree), { kind: "missing", main: repos.main, path: repos.config });
		assert.equal(pi.engineRuns(), 0);
	}, TIMEOUT_MS);
});

describe("ensureExcluded", () => {
	test("writes the config exclude once to the common-dir exclude from a linked worktree", async () => {
		const pi = realExec();
		await ensureExcluded(pi, repos.worktree, CONFIG_EXCLUDE);
		await ensureExcluded(pi, repos.main, CONFIG_EXCLUDE);
		const exclude = readFileSync(join(repos.main, ".git", "info", "exclude"), "utf8");
		assert.equal(exclude.split("\n").filter(line => line === CONFIG_EXCLUDE).length, 1, exclude);
		writeFileSync(repos.config, "{}\n");
		assert.equal(git("-C", repos.main, "status", "--porcelain", "--untracked-files=all"), "");
		unlinkSync(repos.config);
	}, TIMEOUT_MS);
});

describe("origin remote", () => {
	test("repoSlug reads the origin of a clone and its worktree; no origin is null", async () => {
		const temp = realpathSync(mkdtempSync(join(tmpdir(), "origin-slug-")));
		try {
			const clone = repository(join(temp, "clone"));
			git("-C", clone, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "core.hooksPath=/dev/null", "commit", "-q", "--allow-empty", "-m", "init");
			assert.equal(await repoSlug(clone), null);
			mkdirSync(join(temp, "not-a-repo"));
			assert.equal(await repoSlug(join(temp, "not-a-repo")), null, "outside a Git repository");
			git("-C", clone, "remote", "add", "origin", "git@github.com:Owner/Name.git");
			const worktree = join(temp, "worktree");
			git("-C", clone, "worktree", "add", "-q", "-b", "agent/x", worktree);
			for (const repo of [clone, worktree]) assert.equal(await repoSlug(repo), "owner/name", repo);
			assert.equal(unresolvedText("why"), "Agent rails: could not resolve this repository's agent-rails config: why; retry.");
		} finally { rmSync(temp, { recursive: true, force: true }); }
	}, TIMEOUT_MS);

	test("an origin read that fails for another reason throws OriginError, never 'no origin remote'", async () => {
		const temp = realpathSync(mkdtempSync(join(tmpdir(), "origin-error-")));
		const previous = process.env["PATH"];
		try {
			const clone = repository(join(temp, "clone"), "git@github.com:Owner/Name.git");
			const bin = join(temp, "bin");
			mkdirSync(bin);
			for (const [script, words] of [["echo 'fatal: unable to read config' >&2; exit 128", "exited 128: fatal: unable to read config"], ["kill -KILL $$", "was killed (SIGKILL)"]] as const) {
				writeFileSync(join(bin, "git"), `#!/bin/sh\n${script}\n`);
				chmodSync(join(bin, "git"), 0o755);
				process.env["PATH"] = `${bin}:${previous ?? ""}`;
				await assert.rejects(repoSlug(clone), (error: Error) => error instanceof OriginError && error.message === `git remote get-url origin in ${clone} ${words}`);
				process.env["PATH"] = previous;
			}
		} finally { process.env["PATH"] = previous; rmSync(temp, { recursive: true, force: true }); }
	}, TIMEOUT_MS);
});
