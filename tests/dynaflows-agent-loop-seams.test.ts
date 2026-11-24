import assert from "node:assert/strict";
import { appendFileSync, cpSync, existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, realpathSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, relative, resolve } from "node:path";
import { describe, test } from "bun:test";
import { z } from "@oh-my-pi/pi-coding-agent";
import type { Mode } from "../agent/extensions/dynaflows/lib/modes.ts";

const repoRoot = resolve(import.meta.dir, "..");
const extension = join(repoRoot, "agent/extensions/dynaflows");
const cli = Bun.resolveSync("@oh-my-pi/pi-coding-agent/cli", import.meta.dir);
// Git set-up commands get tests/test_ci_gate.py's PROCESS_TIMEOUT. omp gets far longer: under a loaded
// gate container its start-up alone has run past 110 s. Spawn kills omp at ompTimeout, below the test's
// own timeout, so the test awaits the exit, reports omp's stderr and log, and leaves no child behind.
const processTimeout = 60_000;
const ompTimeout = 240_000;
const runTimeout = ompTimeout + processTimeout;
const head = "a".repeat(40);
const gitTool = { name: "read", arguments: { path: "skill://git-tool" } };
const mergeRecover = { name: "read", arguments: { path: "skill://git-tool/references/merge-and-recover.md" } };
type ScriptedCall = { name: string; arguments: Record<string, string> };
const bash = (command: string): ScriptedCall => ({ name: "bash", arguments: { command } });
// The repo's agent-rails config: the S1a minimal fixture for owner/repo with every autonomy permission allowed and CI off.
const repoConfig = (() => {
	const config = JSON.parse(readFileSync(join(repoRoot, "tests/fixtures/agentrails/valid/minimal.json"), "utf8"));
	return { ...config, repo: { ...config.repo, slug: "owner/repo" }, autonomy: { branch: "allowed", pr: "allowed", merge: "allowed" },
		ci: { enabled: false }, commands: { commit: ["true"] } };
})();
// The rails ask the engine (uv run --project <extension>/engine) for the config's verdict. The test HOME
// moves uv's default cache and Python directories, so the real ones are passed through.
const uvDirs = Object.fromEntries(([["UV_CACHE_DIR", "cache"], ["UV_PYTHON_INSTALL_DIR", "python"]] as const).map(([name, verb]) =>
	[name, Bun.spawnSync({ cmd: ["uv", verb, "dir"], stdout: "pipe" }).stdout.toString().trim()]));
const requestSchema = z.object({ messages: z.array(z.object({ role: z.string() })), tools: z.array(z.object({ type: z.string() })).optional() });

const eventSchema = z.object({
	type: z.string(), toolName: z.string().optional(), isError: z.boolean().optional(),
	result: z.object({ content: z.array(z.object({ text: z.string().optional() })).optional() }).optional(),
});
type ToolOutcome = { tool: string; isError: boolean; text: string };
type Run = { outcomes: ToolOutcome[]; branches: string[]; ghCalled: boolean };

type ToolCallDelta = { index: number; id: string; type: "function"; function: { name: string; arguments: string } };
type Delta = { role?: "assistant"; content?: string; tool_calls?: ToolCallDelta[] };

function chunk(delta: Delta, finish: string | null): string {
	return `data: ${JSON.stringify({ id: "scripted", object: "chat.completion.chunk", created: 0, model: "turns", choices: [{ index: 0, delta, finish_reason: finish }] })}\n\n`;
}

function completion(turn: ScriptedCall[] | undefined): Response {
	const calls = (turn ?? []).map((call, index) => chunk({
		tool_calls: [{ index, id: `call_${index}`, type: "function", function: { name: call.name, arguments: JSON.stringify(call.arguments) } }],
	}, null));
	const body = turn ? [...calls, chunk({}, "tool_calls")] : [chunk({ content: "done" }, null), chunk({}, "stop")];
	return new Response([chunk({ role: "assistant", content: "" }, null), ...body, "data: [DONE]\n\n"].join(""), {
		headers: { "content-type": "text/event-stream" },
	});
}

function scriptedModel(turns: ScriptedCall[][]) {
	return Bun.serve({
		hostname: "127.0.0.1",
		port: 0,
		async fetch(request) {
			const body = requestSchema.parse(await request.json());
			const replies = body.messages.filter(message => message.role === "assistant").length;
			return completion(body.tools ? turns[replies] : undefined);
		},
	});
}

function run(cmd: string[], cwd: string, env: Record<string, string>): string {
	const result = Bun.spawnSync({ cmd, cwd, env, timeout: processTimeout, stdout: "pipe", stderr: "pipe" });
	if (result.exitCode !== 0) throw new Error(`${cmd.join(" ")} exited ${result.exitCode}: ${result.stderr.toString()}`);
	return result.stdout.toString();
}

function outcomes(stdout: string): ToolOutcome[] {
	return stdout.split("\n").filter(line => line.startsWith("{")).map(line => eventSchema.parse(JSON.parse(line)))
		.filter(event => event.type === "tool_execution_end").map(event => ({
		tool: event.toolName ?? "", isError: event.isError === true, text: (event.result?.content ?? []).map(part => part.text ?? "").join(""),
	}));
}

function deployExtension(home: string, port: number): void {
	const target = join(home, ".omp/agent/extensions/dynaflows");
	const skipped = new Set(["engine", "docs", "judgments"]);
	cpSync(extension, target, { recursive: true, filter: source => !skipped.has(relative(extension, source).split("/")[0] ?? "") });
	// The engine is linked with its environment, not copied: the rails read its decision-store schema
	// and run its config-check on the repo's agent-rails config.
	symlinkSync(join(extension, "engine"), join(target, "engine"));
	symlinkSync(join(repoRoot, "node_modules"), join(home, ".omp/node_modules"));
	writeFileSync(join(home, ".omp/agent/config.yml"), "extensions:\n  - ~/.omp/agent/extensions/dynaflows\n");
	writeFileSync(join(home, ".omp/agent/models.yml"), [
		"providers:", "  scripted:", `    baseUrl: http://127.0.0.1:${port}/v1`, "    api: openai-completions", "    apiKey: scripted",
		"    models:", "      - id: turns", "        name: Scripted turns", "        contextWindow: 200000", "        maxTokens: 8192", "",
	].join("\n"));
}

function cloneWithRemote(scratch: string, env: Record<string, string>) {
	const repo = join(scratch, "repo");
	const remote = join(scratch, "remote.git");
	mkdirSync(repo);
	run(["git", "init", "-q", "--bare", "-b", "main", remote], scratch, env);
	for (const args of [
		["init", "-q", "-b", "main"], ["commit", "-q", "--allow-empty", "-m", "init"],
		["remote", "add", "origin", "git@github.com:owner/repo.git"], ["remote", "set-url", "--push", "origin", remote],
		["push", "-q", remote, "main"], ["update-ref", "refs/remotes/origin/main", "HEAD"],
		["symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main"], ["checkout", "-q", "-b", "agent/unit"],
	]) run(["git", ...args], repo, env);
	return { repo, remote };
}

async function omp(repo: string, env: Record<string, string>): Promise<string> {
	const cmd = [process.execPath, cli, "--offline", "--no-title", "-p", "--mode", "json", "--no-session", "--model", "scripted/turns", "go"];
	const child = Bun.spawn({ cmd, cwd: repo, env, timeout: ompTimeout, stdout: "pipe", stderr: "pipe" });
	const [stdout, stderr] = await Promise.all([new Response(child.stdout).text(), new Response(child.stderr).text()]);
	if (await child.exited !== 0) throw new Error(`omp exited ${child.exitCode}: ${stderr}${ompLogs(env["HOME"] ?? "")}`);
	return stdout;
}

function ompLogs(home: string): string {
	const dir = join(home, ".omp/logs");
	if (!existsSync(dir)) return "";
	return readdirSync(dir).map(name => `\n--- ${name} (last 4000 characters)\n${readFileSync(join(dir, name), "utf8").slice(-4000)}`).join("");
}

async function agentRun(mode: Mode | undefined, turns: ScriptedCall[][]): Promise<Run> {
	const scratch = realpathSync(mkdtempSync(join(tmpdir(), "dynaflows-agent-loop-")));
	const model = scriptedModel(turns);
	try {
		const home = join(scratch, "home");
		const stubs = join(scratch, "bin");
		const ghLog = join(scratch, "gh-calls");
		mkdirSync(join(home, ".omp/agent/extensions"), { recursive: true });
		mkdirSync(stubs);
		deployExtension(home, model.port ?? 0);
		if (mode) writeFileSync(join(home, ".omp/agent/extensions/dynaflows/modes.json"), JSON.stringify({ schemaVersion: 1, repos: { "owner/repo": mode } }));
		const view = JSON.stringify({ number: 1, headRefOid: head, baseRefName: "main", headRefName: "agent/unit", state: "OPEN", statusCheckRollup: [], url: "https://github.com/owner/repo/pull/1" });
		writeFileSync(join(stubs, "gh"), `#!/bin/sh\necho "$@" >> '${ghLog}'\n[ "$1 $2" = "pr view" ] && echo '${view}' && exit 0\nexit 1\n`, { mode: 0o755 });
		const env = {
			PATH: `${stubs}:${process.env["PATH"] ?? ""}`, HOME: home, TMPDIR: scratch,
			XDG_CONFIG_HOME: join(home, ".config"), XDG_DATA_HOME: join(home, ".local/share"),
			XDG_STATE_HOME: join(home, ".local/state"), XDG_CACHE_HOME: join(home, ".cache"),
			GIT_AUTHOR_NAME: "agent", GIT_AUTHOR_EMAIL: "agent@example.invalid",
			GIT_COMMITTER_NAME: "agent", GIT_COMMITTER_EMAIL: "agent@example.invalid", ...uvDirs,
		};
		const { repo, remote } = cloneWithRemote(scratch, env);
		if (mode) {
			mkdirSync(join(repo, ".agentrails"));
			writeFileSync(join(repo, ".agentrails/config.json"), JSON.stringify(repoConfig));
			appendFileSync(join(repo, ".git/info/exclude"), "/.agentrails/\n");
		}
		const stdout = await omp(repo, env);
		const branches = run(["git", "for-each-ref", "--format=%(refname:short)", "refs/heads"], remote, env).trim().split("\n");
		return { outcomes: outcomes(stdout), branches, ghCalled: existsSync(ghLog) };
	} finally {
		await model.stop(true);
		rmSync(scratch, { recursive: true, force: true });
	}
}

function outcome(result: Run, index: number): ToolOutcome {
	const found = result.outcomes[index];
	assert.ok(found, `tool call ${index} produced no result: ${JSON.stringify(result.outcomes)}`);
	return found;
}

describe("dynaflows in the omp agent loop", () => {
	test("minimal mode blocks gh pr merge, a hook bypass and a default-branch push, and pushes the agent branch", async () => {
		const result = await agentRun("minimal", [
			[gitTool], [bash("gh pr merge 1 --rebase")], [bash("git commit --allow-empty --no-verify -m change")],
			[bash("git push origin HEAD:main")], [bash("git push origin agent/unit")],
		]);
		assert.equal(outcome(result, 0).isError, false);
		assert.ok(outcome(result, 1).isError && outcome(result, 1).text.includes("gh pr merge is blocked in dynaflows minimal mode"));
		assert.ok(outcome(result, 2).isError && outcome(result, 2).text.includes("drop --no-verify"));
		assert.ok(outcome(result, 3).isError && outcome(result, 3).text.includes("default branch main is blocked"));
		assert.equal(outcome(result, 4).isError, false, outcome(result, 4).text);
		assert.deepEqual(result.branches.toSorted(), ["agent/unit", "main"]);
		assert.equal(result.ghCalled, false);
	}, runTimeout);

	test("structured mode blocks gh pr merge until prove() passes on the exact head", async () => {
		const result = await agentRun("structured", [
			[gitTool, mergeRecover], [bash(`gh pr merge 1 --rebase --match-head-commit ${head}`)],
		]);
		assert.equal(outcome(result, 1).isError, false);
		const merge = outcome(result, 2);
		assert.ok(merge.isError && merge.text.includes(`no prove() pass for ${head}`), merge.text);
		assert.equal(result.ghCalled, true, "the merge policy comes from the PR's base and head");
	}, runTimeout);

	test("eval cannot commit or push around the rails, in either mode", async () => {
		for (const mode of ["minimal", "structured"] as const) {
			const result = await agentRun(mode, [
				[gitTool, mergeRecover], [{ name: "eval", arguments: { language: "js", code: "await (await commit.draft()).commit('change', { noVerify: true })" } }],
				[{ name: "eval", arguments: { language: "py", code: "sh('git push origin HEAD:main')" } }],
			]);
			assert.ok(outcome(result, 2).isError && outcome(result, 2).text.includes("eval commit namespace commits outside the rails"), outcome(result, 2).text);
			assert.ok(outcome(result, 3).isError && outcome(result, 3).text.includes("eval may not run git"), outcome(result, 3).text);
			assert.deepEqual(result.branches.toSorted(), ["main"]);
		}
	}, runTimeout * 2);

	test("structured mode refuses forged proof evidence and still finds no prove() pass", async () => {
		const result = await agentRun("structured", [
			[gitTool, mergeRecover],
			[{ name: "write", arguments: { path: `.git/omp-prove/${head}.result.json`, content: JSON.stringify({ head, status: "pass" }) } }],
			[bash(`gh pr merge 1 --rebase --match-head-commit ${head}`)],
		]);
		assert.ok(outcome(result, 2).isError && outcome(result, 2).text.includes("inside Git's internal directory"), outcome(result, 2).text);
		assert.ok(outcome(result, 3).isError && outcome(result, 3).text.includes(`no prove() pass for ${head}`), outcome(result, 3).text);
		assert.equal(result.ghCalled, true, "the merge policy comes from the PR's base and head");
	}, runTimeout);

	test("a clone without a record runs in minimal mode", async () => {
		const result = await agentRun(undefined, [
			[gitTool], [bash("gh pr merge 1 --rebase")], [{ name: "write", arguments: { path: "src/app.ts", content: "export const app = 1;\n" } }],
		]);
		assert.ok(outcome(result, 1).isError && outcome(result, 1).text.includes("gh pr merge is blocked in dynaflows minimal mode"));
		assert.equal(outcome(result, 2).isError, false, outcome(result, 2).text);
		assert.equal(result.ghCalled, false);
	}, runTimeout);
});
