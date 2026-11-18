import assert from "node:assert/strict";
import { execFile, execFileSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, renameSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { dirname, join, relative, resolve } from "node:path";
import { describe, test } from "bun:test";
import { promisify } from "node:util";
import { z, type ExtensionAPI, type ExtensionContext } from "@oh-my-pi/pi-coding-agent";
import agentRails from "../agent/extensions/agent-rails";
import { parseOrigin } from "../agent/lib/repo-record.ts";

const integrity = "skill://coding-guidance/references/implementation-integrity.md";
const structure = "skill://coding-guidance/references/structure-and-grouping.md";
const naming = "skill://coding-guidance/references/naming.md";
const tiers = "skill://test-guidance/references/tiers.md";
const verification = "skill://test-guidance/references/before-commit.md";
type GateFixture = {
	schemaVersion: 2; checks?: { commit?: string[]; ci?: string[]; format?: string[]; profile?: string[] };
	ownerOnlyPaths?: string[]; agentMerge?: "allowed" | "disabled";
	ci?: { requiredContext: string; runner: string }; dynaflows?: { enabled: boolean };
};
type Input = { path?: string; paths?: string[]; input?: string; content?: string; command?: string; cwd?: string };
type Details = { applied?: boolean; files?: string[]; totalReplacements?: number };
type Event = {
	type: string; toolName?: string; toolCallId?: string; input?: Input;
	details?: Details; isError?: boolean; reason?: string;
	agent?: string; patterns?: string[]; modelRole?: string; invocationKind?: string; prompt?: string;
};
type Result = { block?: boolean; reason?: string; decision?: string; additionalContext?: string; message?: object; model?: string[] };
type Handler = (event: Event, ctx: ExtensionContext) => Result | void | Promise<Result | void>;
type Tool = { name: string; execute(id: string, params: { enabled: boolean }, signal: undefined, onUpdate: undefined, ctx: ExtensionContext): Promise<{ content: { text: string }[] }> };
const execFailureSchema = z.object({
	code: z.number().optional().catch(undefined),
	killed: z.boolean().optional(),
	stdout: z.string().optional(),
	stderr: z.string().optional(),
});
const execute = promisify(execFile);

const wrappedPublishing = [
	"sudo git push", "sudo -u root git push", "env git push", "env -i git push", "env FOO=x git push",
	"time git push", "command git push", "nohup git push", "nice -n 5 git push", "exec git push",
	"xargs git push", "xargs -n 1 git push", "timeout 5 git push", "/usr/bin/git push",
	"if x; then git push; fi", "{ git push; }", "true; do git push; done", "false; else git push",
	"! git push", "bash -c 'git push'", 'sh -c "git push"', "zsh -lc 'git push'",
	"sudo env -i sh -c 'git push'",
	"env -S 'git push'", "env --split-string='git push'", "env -S git push",
];

const railsConfig = join(import.meta.dir, "../agent/agent-rails.yml");
const defaultOrigin = "git@github.com:owner/repo.git";
// A record's gate.json as tests read it back: the switch typed, every other field kept.
const recordGateSchema = z.object({ dynaflows: z.object({ enabled: z.boolean() }) }).passthrough();

// Rails run every repo command as `env -u AGENT -- <argv>`; this returns <argv> and fails otherwise.
function repoCommand(command: string, args: string[]): [string, string[]] {
	if (command === "env") {
		assert.deepEqual(args.slice(0, 3), ["-u", "AGENT", "--"], "repo commands run without the harness variables");
		[command, ...args] = args.slice(3);
	}
	if (command === "uv" && args[4] === "path-check") {
		assert.deepEqual(args.slice(0, 6), ["run", "--project",
			resolve(import.meta.dir, "../agent/skills/setup-repo-proofs/assets/python-reference"), "omp-guardrails", "path-check", "--repo"]);
		assert.equal(args[7], "--record");
		assert.match(args[8], /\/repos\/[^/]+\/[^/]+$/);
		return ["path-check", args.slice(9)];
	}
	return [command, args];
}

// A temp repo whose origin names a per-repo record under a temp config root (never the real one).
// `configRoot` shares another harness's records; `guardrailsCommand: "default"` keeps the real
// omp-guardrails invocation, otherwise the harness checks are `true`.
function harness({ configPath, configRoot, remote = defaultOrigin, guardrailsCommand = ["true"], ompCommand }: {
	configPath?: string; configRoot?: string; remote?: string; guardrailsCommand?: string[] | "default"; ompCommand?: string[];
} = {}) {
	const root = realpathSync(mkdtempSync(join(tmpdir(), "agent-rails-")));
	const machine = mkdtempSync(join(tmpdir(), "agent-rails-machine-"));
	const ownedRecords = configRoot === undefined;
	const records = configRoot ?? realpathSync(mkdtempSync(join(tmpdir(), "agent-rails-records-")));
	execFileSync("git", ["init", "-b", "agent/test", root]);
	execFileSync("git", ["remote", "add", "origin", remote], { cwd: root });
	writeFileSync(join(root, "existing.ts"), "export const answer = 42;\n");
	const handlers = new Map<string, Handler>();
	const tools = new Map<string, Tool>();
	const notes: string[] = [];
	const errors: string[] = [];
	const branch: object[] = [];
	let sessionId = "first";
	let pathFailure: string | undefined;
	const identity: ExtensionContext["agent"] = { kind: "main", id: root, name: "main", depth: 0 };
	const context = {
		cwd: root,
		agent: identity,
		ui: { notify(message: string, level?: string) { notes.push(message); if (level === "error") errors.push(message); } },
		sessionManager: { getSessionId: () => sessionId, getBranch: () => branch },
		models: {
			current: () => ({ provider: "openai", id: "gpt" }),
			resolve: (pattern: string) => {
				const [provider, id] = pattern.split("/");
				return id ? { provider, id } : undefined;
			},
			family: (model: { provider: string }) => model.provider,
		},
	};
	const api = {
		on(name: string, handler: Handler) { handlers.set(name, handler); },
		registerCommand() {},
		registerTool(tool: Tool) { tools.set(tool.name, tool); },
		async exec(command: string, args: string[], options: { cwd: string; timeout?: number }) {
			const [name, paths] = repoCommand(command, args);
			if (name === "path-check") {
				assert.equal(command, "env");
				assert.deepEqual(args, ["-u", "AGENT", "--", "uv", "run", "--project",
					resolve(import.meta.dir, "../agent/skills/setup-repo-proofs/assets/python-reference"),
					"omp-guardrails", "path-check", "--repo", options.cwd, "--record", recordDir(), ...paths]);
				return { code: pathFailure ? 1 : 0, killed: false, stdout: "",
					stderr: pathFailure ? `${paths.join("\n")}: ${pathFailure}` : "" };
			}
			try {
				const { stdout, stderr } = await execute(command, args, options);
				return { code: 0, killed: false, stdout, stderr };
			} catch (error) {
				const failure = execFailureSchema.parse(error);
				return { code: failure.code ?? 1, killed: failure.killed ?? false,
					stdout: failure.stdout ?? "", stderr: failure.stderr ?? String(error) };
			}
		},
	};
	const options: Parameters<typeof agentRails>[1] = { machineDynaflowsPath: join(machine, "dynaflows.json"), configPath, configRoot: records };
	options.ompCommand = ompCommand ?? ["sh", "-c", 'cat "$1"', "omp", join(machine, "worktree-base")];
	writeFileSync(join(machine, "worktree-base"), "(not set)\n");
	if (guardrailsCommand !== "default") options.guardrailsCommand = guardrailsCommand;
	// SAFETY: this fake supplies on, registerCommand, registerTool and exec, the only API surfaces agentRails consumes.
	agentRails(api as ExtensionAPI, options);
	// The record's gate.json: the fixture over a dynaflows-off default; `dynaflows()` wins.
	let fixture: GateFixture | undefined;
	let enabled: boolean | undefined;
	let slug = parseOrigin(remote);
	const recordDir = () => {
		assert.ok(slug, "the harness origin names a record");
		return join(records, "repos", slug);
	};
	const writeRecord = (text: string) => {
		mkdirSync(recordDir(), { recursive: true });
		writeFileSync(join(recordDir(), "gate.json"), text);
	};
	const writeGate = () => {
		// A complete record: checks.commit defaults to `true`, ownerOnlyPaths to none.
		const gate = { checks: { commit: ["true"] }, ownerOnlyPaths: [], dynaflows: { enabled: false }, ...(fixture ?? { schemaVersion: 2 }) };
		if (enabled !== undefined) gate.dynaflows = { enabled };
		writeRecord(JSON.stringify(gate));
	};
	let id = 0;
	async function emit(name: string, event: Omit<Event, "type"> = {}) {
		const handler = handlers.get(name);
		assert.ok(handler, `missing handler ${name}`);
		// SAFETY: the fake provides cwd, agent.kind, ui.notify and both sessionManager methods consumed by handlers.
		return handler({ type: name, ...event }, context as ExtensionContext);
	}
	return {
		root, notes, errors, branch, context, emit, api, options, records, recordDir,
		pathFailure(message?: string) { pathFailure = message; },
		cleanup() { for (const dir of [root, machine, ...(ownedRecords ? [records] : [])]) rmSync(dir, { recursive: true, force: true }); },
		setSession(value: string) { sessionId = value; },
		// Runs a registered tool as the current agent and returns its text.
		async tool(name: string, params: { enabled: boolean }) {
			const tool = tools.get(name);
			assert.ok(tool, `missing tool ${name}`);
			// SAFETY: the fake context provides what the tool consumes, as for handlers.
			return (await tool.execute("tool-1", params, undefined, undefined, context as ExtensionContext)).content.map(part => part.text).join("");
		},
		gate(value: GateFixture) { fixture = value; writeGate(); },
		worktreeBase(value: string) { writeFileSync(join(machine, "worktree-base"), `${value}\n`); },
		dynaflows(value: boolean) { enabled = value; writeGate(); },
		// Raw gate.json text, for invalid fixtures.
		rawGate(text: string) { writeRecord(text); },
		record() { return recordGateSchema.parse(JSON.parse(readFileSync(join(recordDir(), "gate.json"), "utf8"))); },
		// Moves origin; an existing record moves with it, as a re-pointed clone keeps its record.
		async origin(url: string) {
			const previous = slug ? join(records, "repos", slug) : undefined;
			await execute("git", ["remote", "set-url", "origin", url], { cwd: root });
			slug = parseOrigin(url);
			if (previous && existsSync(previous) && slug && previous !== recordDir()) {
				mkdirSync(dirname(recordDir()), { recursive: true });
				renameSync(previous, recordDir());
			}
		},
		machineDynaflows(value: boolean) { writeFileSync(join(machine, "dynaflows.json"), JSON.stringify({ schemaVersion: 1, enabled: value })); },
		async read(...urls: string[]) {
			for (const path of urls) await emit("tool_result", {
				toolName: "read", toolCallId: `read-${++id}`, input: { path }, isError: false,
			});
		},
		async call(toolName: string, input: Input, details: Details = {}, isError = false) {
			const event = { toolName, toolCallId: `call-${++id}`, input };
			const blocked = await emit("tool_call", event);
			if (blocked?.block) return blocked;
			return emit("tool_result", { ...event, details, isError });
		},
	};
}

const ciHead = "a".repeat(40);
const greenCi = [
	{ __typename: "CheckRun", name: "test", status: "COMPLETED", conclusion: "SUCCESS" },
	{ __typename: "CheckRun", name: "optional", status: "COMPLETED", conclusion: "NEUTRAL" },
	{ __typename: "CheckRun", name: "unused", status: "COMPLETED", conclusion: "SKIPPED" },
	{ __typename: "StatusContext", context: "external", state: "SUCCESS" },
];

type PrCiHarness = {
	root: string;
	api: {
		exec(command: string, args: string[], options: { cwd: string; timeout?: number }):
			Promise<{ code: number; killed: boolean; stdout: string; stderr: string }>;
	};
};

// Fakes `gh pr view`; every other command runs for real.
function mockPrCi(h: PrCiHarness, rollup: object[], head = ciHead, selector?: string, failure?: string,
	url = "https://github.com/owner/repo/pull/3") {
	const exec = h.api.exec.bind(h.api);
	h.api.exec = async (command, args, options) => {
		if (command !== "gh") return exec(command, args, options);
		assert.equal(options.cwd, h.root);
		assert.equal(options.timeout, 20000);
		assert.deepEqual(args.slice(0, -1), ["pr", "view", ...(selector ? [selector] : []), "--json"]);
		assert.ok(args.at(-1)?.split(",").includes("headRefOid"));
		assert.ok(args.at(-1)?.split(",").includes("statusCheckRollup"));
		return failure
			? { code: 1, killed: false, stdout: "", stderr: failure }
			: { code: 0, killed: false, stdout: JSON.stringify({ headRefOid: head, statusCheckRollup: rollup, url }), stderr: "" };
	};
}

// Refuses every gh call: plain publishing (no ci block, dynaflows off) must never query GitHub.
function forbidGh(h: PrCiHarness) {
	const exec = h.api.exec.bind(h.api);
	h.api.exec = async (command, args, options) => {
		assert.notEqual(command, "gh", "plain publishing must not query gh");
		return exec(command, args, options);
	};
}

const ciGate = { requiredContext: "gate", runner: "ci-gate" };
const ciRunner = resolve(import.meta.dir, "../scripts/ci-runner.py");
async function originRemote(h: { origin(url: string): Promise<void> }, remote = defaultOrigin) {
	await h.origin(remote);
}

// prove()'s result file for `head`, the one pass signal the merge guard reads.
function proveResult(h: { root: string }, head = ciHead, status = "pass") {
	mkdirSync(join(h.root, ".git/omp-prove"), { recursive: true });
	writeFileSync(join(h.root, ".git/omp-prove", `${head}.result.json`), JSON.stringify({ status, head, blocking: [] }));
}

function structureResult(h: { root: string }, head = ciHead) {
	mkdirSync(join(h.root, ".git/omp-structure"), { recursive: true });
	writeFileSync(join(h.root, ".git/omp-structure", `${head}.json`),
		JSON.stringify({ head, halt: false, status: "shadow", findings: [] }));
}

describe("agent rails", () => {
	test("blocks new code and test files until every mapped reference is successfully read", async () => {
		const h = harness();
		try {
			h.dynaflows(true);
			const input = { path: "tests/new.test.ts", content: "test('behaviour', () => {});" };
			const blocked = await h.call("write", input);
			assert.equal(blocked?.block, true);
			for (const url of [integrity, structure, naming, tiers]) assert.ok(blocked?.reason?.includes(url));
			await h.emit("tool_result", { toolName: "read", input: { path: tiers }, isError: true });
			await h.read(integrity, structure, naming);
			assert.ok((await h.call("write", input))?.reason?.includes(tiers));
			await h.read(`${tiers}:1-40:raw`);
			assert.equal(await h.call("write", input), undefined);
		} finally { h.cleanup(); }
	});

	test("dynaflows gates both existing code and new tests on their mapped reads", async () => {
		const h = harness();
		try {
			h.dynaflows(true);
			assert.ok((await h.call("edit", { path: "existing.ts" }))?.reason?.includes(integrity));
			await h.read(integrity, structure, naming);
			assert.equal(await h.call("edit", { path: "existing.ts" }), undefined);
			assert.ok((await h.call("write", { path: "tests/contract.test.ts" }))?.reason?.includes(tiers));
			await h.read(tiers);
			assert.equal(await h.call("write", { path: "tests/contract.test.ts" }), undefined);
		} finally { h.cleanup(); }
	});

	test("runs the harness path check with all changed paths, surfacing command failures", async () => {
		const h = harness();
		try {
			h.pathFailure("use feature ownership");
			h.gate({ schemaVersion: 2 });
			h.dynaflows(true);
			await h.read(integrity, structure, naming);
			writeFileSync(join(h.root, "another.ts"), "export {};\n");
			const result = await h.call("edit", { paths: ["existing.ts", "another.ts"] });
			assert.ok(result?.additionalContext?.includes(join(h.root, "existing.ts")));
			assert.ok(result?.additionalContext?.includes(join(h.root, "another.ts")));
			assert.ok(result?.additionalContext?.includes("use feature ownership"));
			assert.equal(await h.call("edit", { path: "existing.ts" }, {}, true), undefined);
		} finally { h.cleanup(); }
	});

	test("absent and invalid records leave edit reads off without throwing", async () => {
		const h = harness();
		try {
			assert.equal(await h.call("edit", { path: "existing.ts" }), undefined);
			assert.equal(h.notes.length, 0, "a repo without a record is normal, not noteworthy");
			h.rawGate("{broken");
			assert.equal(await h.call("edit", { path: "existing.ts" }), undefined);
			assert.ok(h.notes.some(note => note.includes(join(h.recordDir(), "gate.json"))));
			h.rawGate(JSON.stringify({ schemaVersion: 2, checks: { commit: "not an argv array" } }));
			assert.equal(await h.call("edit", { path: "existing.ts" }), undefined);
			assert.ok(h.notes.some(note => note.includes("invalid")));
			await h.read(integrity);
			assert.equal(await h.call("edit", { path: "existing.ts" }), undefined);
		} finally { h.cleanup(); }
	});

	for (const dynaflows of [false, true]) {
		test(`handoff needs only the verification read after code edits, never a check run (dynaflows ${dynaflows ? "on" : "off"})`, async () => {
			const h = harness();
			try {
				h.dynaflows(dynaflows);
				h.gate({ schemaVersion: 2, checks: { commit: ["bun", "check"], ci: ["bun", "ci"] } });
				await h.emit("session_start");
				writeFileSync(join(h.root, "existing.ts"), "pulled change\n");
				writeFileSync(join(h.root, "session.log"), "growing log\n");
				assert.equal(await h.emit("session_stop"), undefined, "tree changes outside this session's edits never block");
				await h.read(integrity, structure, naming);
				await h.call("edit", { path: "existing.ts" });
				const blocked = await h.emit("session_stop");
				if (dynaflows) {
					assert.equal(blocked?.decision, "block");
					assert.ok(blocked?.reason?.includes(verification));
					assert.ok(!blocked?.reason?.includes("bun"), "no repository check is demanded at handoff");
				} else assert.equal(blocked, undefined);
				await h.read(verification);
				writeFileSync(join(h.root, "session.log"), "grew again\n");
				assert.equal(await h.emit("session_stop"), undefined);
			} finally { h.cleanup(); }
		});
	}

	test("discovery tools are never gated by reference reads, before or after compaction", async () => {
		const h = harness();
		try {
			for (const tool of ["find", "grep", "glob", "ast_grep"]) {
				assert.equal(await h.call(tool, {}), undefined);
				assert.equal(await h.call(tool, { path: h.root }), undefined);
			}
			await h.emit("session_compact");
			assert.equal(await h.call("grep", { path: h.root }), undefined);
			assert.equal(await h.call("glob", { path: "**/*.ts" }), undefined);
		} finally { h.cleanup(); }
	});

	test("newFile reads apply only to new code files inside a repository", async () => {
		const h = harness();
		const scratch = realpathSync(mkdtempSync(join(tmpdir(), "agent-rails-scratch-")));
		try {
			h.dynaflows(true);
			assert.equal(await h.call("write", { path: join(scratch, "notes.md"), content: "# notes\n" }), undefined,
				"a new file outside any repository needs no reads");
			await h.read(integrity);
			assert.equal(await h.call("write", { path: join(scratch, "scratch.ts"), content: "export {};\n" }), undefined,
				"a new code file outside any repository needs no newFile reads");
			assert.equal(await h.call("write", { path: "notes.md", content: "# notes\n" }), undefined,
				"a new non-code file inside a repository needs no reads");
			const blocked = await h.call("write", { path: "src/new.ts", content: "export {};\n" });
			assert.equal(blocked?.block, true);
			for (const url of [structure, naming]) assert.ok(blocked?.reason?.includes(url));
		} finally { h.cleanup(); rmSync(scratch, { recursive: true, force: true }); }
	});

	test("integration git commands need git-tool, while inspection and look-alikes pass", async () => {
		const h = harness();
		try {
			for (const command of [
				"git status && git diff", "git log --grep=commit", 'rg "git push"', 'echo "git commit"',
				"echo 'git commits'", "npm run gh-pr-merge", "git switch existing", "git checkout existing",
				"git -C /w status", "git -c k=v log", "gh pr view", "git status; git merge x", "false || git rebase x",
				"git cherry-pick abc", "git revert HEAD", "git am patch", "git pull",
				"command -v git push", "sudo git status", "env -i git diff", 'sh -c "git log --grep=commit"',
			]) {
				assert.equal(await h.call("bash", { command }), undefined, command);
			}
			for (const command of [
				"git commit -m 'feat: x'", "cd repo && git push -u origin agent/x", "gh pr merge 7 --rebase",
				"git -C /w commit", "git -c k=v commit", "git --no-pager commit", "git status\ngit commit",
				"FOO=1 git push", "git switch -c agent/x", "git switch -C agent/x", "git switch --create=agent/x",
				"git checkout -b agent/x", "git checkout -B agent/x", "git worktree add /w",
				"git --git-dir=/w/.git --work-tree=/w push", "git --git-dir /w/.git --work-tree /w push",
				'git -C "/some path" -c core.pager=cat --no-pager push',
				"git status; git push", "true | git push", "(git push)",
				"FOO=x BAR=y gh pr create",
				'FOO="a b" git push', "FOO='x;y' BAR=z git commit", "git -C'/some path' push",
				...wrappedPublishing,
			]) {
				const blocked = await h.call("bash", { command });
				assert.equal(blocked?.block, true, command);
				assert.ok(blocked?.reason?.includes("skill://git-tool"), blocked?.reason ?? command);
			}
		} finally { h.cleanup(); }
	});

	test("worktree creation uses the configured base and lowercase origin name", async () => {
		const h = harness({ remote: "git@github.com:Owner/RePo.git" });
		const previous = process.env.OMP_WORKTREE_DIR;
		delete process.env.OMP_WORKTREE_DIR;
		try {
			await h.read("skill://git-tool");
			const base = join(h.root, "Worktrees");
			h.worktreeBase(base);
			for (const args of [
				`-b agent/x '${base}/repo/x'`,
				`--detach '${base}/repo/detached' HEAD`,
				`--reason 'bounded experiment' --detach '${base}/repo/reason' HEAD`,
				`--reason=experiment -- '${base}/repo/explicit'`,
				`-B agent/x '${relative(h.root, base)}/repo/relative'`,
			]) assert.equal(await h.call("bash", { command: `git worktree add ${args}` }), undefined, args);
			for (const target of ["../repo-x", `${base}/x`, `${base}/other/x`, `${base}/repo/x/nested`]) {
				const blocked = await h.call("bash", { command: `git worktree add '${target}'` });
				assert.equal(blocked?.block, true, target);
				assert.equal(blocked?.reason, `Agent rails: create worktrees at ${base}/repo/<slug>; got ${resolve(h.root, target)}.`);
			}
			for (const target of ["$HOME/worktrees/repo/x", "`pwd`/x"]) {
				assert.match((await h.call("bash", { command: `git worktree add '${target}'` }))?.reason ?? "", /unexpanded shell text/);
			}
			h.worktreeBase("(not set)");
			assert.equal((await h.call("bash", { command: `git worktree add '${base}/repo/x'` }))?.reason,
				"Agent rails: worktree.base is unset; set it (agent/config.yml) before git worktree add.");
			h.worktreeBase(base);
			await execute("git", ["remote", "remove", "origin"], { cwd: h.root });
			assert.match((await h.call("bash", { command: `git worktree add '${base}/repo/x'` }))?.reason ?? "", /no origin remote/);
			await execute("git", ["remote", "add", "origin", "https://example.com/owner/repo.git"], { cwd: h.root });
			assert.match((await h.call("bash", { command: `git worktree add '${base}/repo/x'` }))?.reason ?? "", /not a GitHub/);
		} finally {
			if (previous === undefined) delete process.env.OMP_WORKTREE_DIR; else process.env.OMP_WORKTREE_DIR = previous;
			h.cleanup();
		}
	});

	test("worktree environment base overrides the setting and invalid env defers to it", async () => {
		const h = harness();
		const previous = process.env.OMP_WORKTREE_DIR;
		try {
			await h.read("skill://git-tool");
			h.worktreeBase(join(h.root, "setting"));
			process.env.OMP_WORKTREE_DIR = join(h.root, "override");
			assert.equal(await h.call("bash", { command: `git worktree add '${h.root}/override/repo/x'` }), undefined);
			assert.equal((await h.call("bash", { command: `git worktree add '${h.root}/setting/repo/x'` }))?.block, true);
			process.env.OMP_WORKTREE_DIR = "relative";
			assert.equal(await h.call("bash", { command: `git worktree add '${h.root}/setting/repo/x'` }), undefined);
		} finally {
			if (previous === undefined) delete process.env.OMP_WORKTREE_DIR; else process.env.OMP_WORKTREE_DIR = previous;
			h.cleanup();
		}
	});

	const caseProbe = mkdtempSync(join(tmpdir(), "agent-rails-case-"));
	let caseInsensitive: boolean;
	try {
		mkdirSync(join(caseProbe, "MixedCase"));
		caseInsensitive = existsSync(join(caseProbe, "mixedcase"));
	} finally { rmSync(caseProbe, { recursive: true, force: true }); }
	test.skipIf(!caseInsensitive)("worktree paths canonicalize mixed case on case-insensitive filesystems", async () => {
		const h = harness();
		const previous = process.env.OMP_WORKTREE_DIR;
		delete process.env.OMP_WORKTREE_DIR;
		try {
			const base = join(h.root, "Worktrees");
			mkdirSync(join(base, "repo"), { recursive: true });
			await h.read("skill://git-tool");
			h.worktreeBase(base);
			assert.equal(await h.call("bash", { command: `git worktree add '${h.root}/worktrees/REPO/x'` }), undefined);
		} finally {
			if (previous === undefined) delete process.env.OMP_WORKTREE_DIR; else process.env.OMP_WORKTREE_DIR = previous;
			h.cleanup();
		}
	});

	test("worktree setting command errors fail closed", async () => {
		const h = harness({ ompCommand: ["sh", "-c", "exit 7"] });
		const previous = process.env.OMP_WORKTREE_DIR;
		delete process.env.OMP_WORKTREE_DIR;
		try {
			await h.read("skill://git-tool");
			assert.match((await h.call("bash", { command: `git worktree add '${h.root}/repo/x'` }))?.reason ?? "",
				/policy could not be evaluated.*cannot read worktree.base/);
		} finally {
			if (previous === undefined) delete process.env.OMP_WORKTREE_DIR; else process.env.OMP_WORKTREE_DIR = previous;
			h.cleanup();
		}
	});

	test("repo merge policy fails closed for publishing actions, respecting git -C", async () => {
		const h = harness();
		const other = harness({ configRoot: h.records, remote: "git@github.com:owner/other.git" });
		try {
			await h.read("skill://git-tool");
			forbidGh(h);
			const commands = ["git push", "gh pr create", "gh pr merge 7", ...wrappedPublishing];
			for (const command of commands) assert.equal(await h.call("bash", { command }), undefined);
			h.gate({ schemaVersion: 2 });
			for (const command of commands) assert.equal(await h.call("bash", { command }), undefined);
			h.gate({ schemaVersion: 2, agentMerge: "disabled" });
			for (const command of commands) {
				const blocked = await h.call("bash", { command });
				assert.equal(blocked?.block, true, command);
				assert.ok(blocked?.reason?.includes("ask the owner"));
			}
			assert.equal(await h.call("bash", { command: "git commit -m fix" }), undefined);
			assert.equal(await h.call("bash", { command: `git -C '${other.root}' push` }), undefined);
			other.gate({ schemaVersion: 2, agentMerge: "disabled" });
			assert.equal((await h.call("bash", { command: `git -C '${other.root}' push` }))?.block, true);
			// cwd-changing commands must select the destination repo, not the session repo.
			for (const action of ["git push", "gh pr create", "gh pr merge 7"]) {
				h.gate({ schemaVersion: 2 });
				for (const prefix of [`cd '${other.root}'`, `pushd '${other.root}'`, `cd '${relative(h.root, other.root)}' && cd .`]) {
					assert.equal((await h.call("bash", { command: `${prefix} && ${action}` }))?.block, true, prefix);
				}
				for (const launcher of ["env -C", "sudo -D"]) {
					assert.equal((await h.call("bash", { command: `${launcher} '${other.root}' ${action}` }))?.block, true, launcher);
				}
				h.gate({ schemaVersion: 2, agentMerge: "disabled" });
				other.gate({ schemaVersion: 2 });
				assert.equal(await h.call("bash", { command: `cd '${other.root}' && ${action}` }), undefined);
				for (const launcher of ["env -C", "sudo -D"]) {
					assert.equal(await h.call("bash", { command: `${launcher} '${other.root}' ${action}` }), undefined, launcher);
				}
				other.gate({ schemaVersion: 2, agentMerge: "disabled" });
				for (const prefix of ["cd", "cd -", "cd missing-dir", 'cd "$TARGET"']) {
					const blocked = await h.call("bash", { command: `${prefix} && ${action}` });
					assert.equal(blocked?.block, true, prefix);
					assert.ok(blocked?.reason?.includes("effective cwd"));
				}
			}
			for (const invalid of ["{broken", '{"schemaVersion":3}', '{"schemaVersion":2,"checks":{"commit":"bun check"}}', '{"schemaVersion":2,"otherField":"github"}', '{"schemaVersion":2,"ci":{"requiredContext":"gate"}}', '{"schemaVersion":2,"dynaflows":{"enabled":"yes"}}']) {
				h.rawGate(invalid);
				for (const command of commands) {
					const blocked = await h.call("bash", { command });
					assert.equal(blocked?.block, true, command);
					assert.ok(blocked?.reason?.includes("gate.json"));
					assert.ok(blocked?.reason?.includes("Error"));
				}
			}
		} finally { h.cleanup(); other.cleanup(); }
	});

	test("commits must run the pre-commit hook: bypass flags, hook-path overrides and any touch of a skip variable block", async () => {
		const h = harness();
		try {
			await h.read("skill://git-tool");
			for (const command of [
				"git commit --no-verify -m x", "git commit -n -m x", "git commit -anm x", "git commit --no-ver -m x",
				"git -c core.hooksPath=/dev/null commit -m x", "git -c CORE.HOOKSPATH=x commit -m x",
				"git --config-env=core.hooksPath=HOOKS commit -m x", "LEFTHOOK=0 git commit -m x",
				"SKIP=checks git commit -m x", "HUSKY=0 git commit -m x", "env PRE_COMMIT_ALLOW_NO_CONFIG=1 git commit -m x",
				"GIT_CONFIG_COUNT=1 git commit -m x", "bash -c 'git commit --no-verify -m x'",
				"GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath bash -c 'git commit -m x'",
				"LEFTHOOK=0 sh -c 'cd . && git commit -m x'", "export HUSKY=0; git commit -m x",
				"env -S 'SKIP=x' git commit -m x", "export HUSKY=1; HUSKY=0; git commit -m x",
				"HUSKY=0; export HUSKY; git commit -m x", "unset HUSKY; export HUSKY; HUSKY=0; git commit -m x",
				// Conservative: the command touches a skip variable, so the value and later shell state are ignored.
				"LEFTHOOK=1 git commit -m x", "export HUSKY=0; export -n HUSKY; git commit -m x",
				"export HUSKY=0; unset HUSKY; git commit -m x", "(export HUSKY=0); git commit -m x",
				"env 'HUSKY'=0 git commit -m x", `env HUS"KY"=0 git commit -m x`, `export HUS"KY"=0; git commit -m x`,
				"declare -x HUSKY=0; git commit -m x", "env --unset=HUSKY git commit -m x", "env -uHUSKY git commit -m x",
			]) {
				const blocked = await h.call("bash", { command });
				assert.equal(blocked?.block, true, command);
				assert.ok(blocked?.reason?.includes("pre-commit hook"), command);
			}
			for (const command of [
				"git commit -m 'no -n here'", "git commit -m -n", "git commit -F -n", "git commit --message -n",
				"git commit --mes -n", "git commit --mess=-n", "git commit -uno -m x", "git commit -m x -- -n",
				"echo 'git commit --no-verify'", "export A=1; git commit -m x", "git commit -m 'fix the husky hook'",
				"git commit -m 'drop $SKIP from docs'", "PATH=/usr/bin git commit -m x",
				"git commit -m 'document export HUSKY'", "git commit -m 'document unset SKIP'",
				"git commit -m 'document HUSKY=0'",
			]) assert.equal(await h.call("bash", { command }), undefined, command);
		} finally { h.cleanup(); }
	});

	test("dynaflows commits run the record's checks.commit, then the whole-repo harness checks, without AGENT", async () => {
		const scratch = realpathSync(mkdtempSync(join(tmpdir(), "agent-rails-commit-")));
		const log = join(scratch, "runs.log");
		// Appends its label, argv and AGENT to the log, then exits with $FAIL_<label> (default 0).
		const script = join(scratch, "check.sh");
		writeFileSync(script, `#!/bin/sh\nlabel=$1; shift\necho "$label $* AGENT=\${AGENT-unset}" >> '${log}'\necho "$label finding: src/x.py" >&2\neval "exit \\\${FAIL_$label:-0}"\n`, { mode: 0o755 });
		const h = harness({ guardrailsCommand: [script, "harness"] });
		const previous = process.env.AGENT;
		process.env.AGENT = "1";
		try {
			await h.read("skill://git-tool");
			const commit = () => h.call("bash", { command: "git commit -m x" });
			const runs = () => existsSync(log) ? readFileSync(log, "utf8").trim().split("\n") : [];
			h.gate({ schemaVersion: 2, checks: { commit: [script, "commit"] } });
			assert.equal(await commit(), undefined);
			assert.deepEqual(runs(), [], "dynaflows off: rails run no repo or harness check");
			h.dynaflows(true);
			assert.equal(await commit(), undefined);
			assert.deepEqual(runs(), ["commit  AGENT=unset", `harness --repo ${h.root} --record ${h.recordDir()} AGENT=unset`],
				"the repo command first, then the harness over the whole repo with the record; AGENT never reaches either");
			rmSync(log);
			process.env.FAIL_commit = "3";
			let blocked = await commit();
			assert.equal(blocked?.block, true);
			assert.ok(blocked?.reason?.includes(`checks.commit (${script} commit) failed in ${h.root} (exit 3)`), blocked?.reason);
			assert.ok(blocked?.reason?.includes("commit finding: src/x.py"), "the agent sees the repo command's output");
			assert.ok(!blocked?.reason?.includes("adopt mode"), "a repo command failure is fixed in the repo");
			assert.deepEqual(runs(), ["commit  AGENT=unset"], "a failed repo command stops before the harness checks");
			rmSync(log);
			delete process.env.FAIL_commit;
			process.env.FAIL_harness = "1";
			blocked = await commit();
			assert.equal(blocked?.block, true);
			assert.ok(blocked?.reason?.includes(`harness checks (omp-guardrails) failed in ${h.root} (exit 1)`), blocked?.reason);
			assert.ok(blocked?.reason?.includes("harness finding: src/x.py"), blocked?.reason);
			assert.ok(blocked?.reason?.includes("checks.commit passed. If a repo checker already enforces a failing step's rules"),
				"a harness-only finding points at adopt mode before the agent reshapes repo code");
			h.gate({ schemaVersion: 2 });
			rmSync(log);
			assert.equal((await commit())?.block, true, "harness checks run even when the record declares no checks.commit");
			assert.deepEqual(runs(), [`harness --repo ${h.root} --record ${h.recordDir()} AGENT=unset`]);
			delete process.env.FAIL_harness;
			h.machineDynaflows(false);
			rmSync(log);
			assert.equal(await commit(), undefined);
			assert.deepEqual(runs(), [], "the machine default off turns enforcement off with dynaflows");
		} finally {
			if (previous === undefined) delete process.env.AGENT; else process.env.AGENT = previous;
			delete process.env.FAIL_commit; delete process.env.FAIL_harness;
			h.cleanup(); rmSync(scratch, { recursive: true, force: true });
		}
	});

	test("dynaflows commits run the harness path check over the paths the commit can take, first", async () => {
		const h = harness();
		try {
			await h.read("skill://git-tool");
			writeFileSync(join(h.root, "tracked.ts"), "export {};\n");
			await execute("git", ["add", "existing.ts", "tracked.ts"], { cwd: h.root });
			await execute("git", ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"], { cwd: h.root });
			const order: string[] = [];
			let checked: string[] = [];
			let pathFails = false;
			const exec = h.api.exec.bind(h.api);
			h.api.exec = async (command, args, options) => {
				const [name, rest] = repoCommand(command, args);
				if (name === "path-check") {
					order.push("path check"); checked = rest;
					return { code: pathFails ? 1 : 0, killed: false, stdout: "", stderr: pathFails ? "bad path" : "" };
				}
				if (name === "commit-check") { order.push("commit"); return { code: 0, killed: false, stdout: "", stderr: "" }; }
				return exec(command, args, options);
			};
			h.gate({ schemaVersion: 2, checks: { commit: ["commit-check"] } });
			h.dynaflows(true);
			writeFileSync(join(h.root, "existing.ts"), "export const answer = 43;\n");
			writeFileSync(join(h.root, "staged.ts"), "export {};\n");
			await execute("git", ["add", "staged.ts"], { cwd: h.root });
			writeFileSync(join(h.root, "untracked.ts"), "export {};\n");
			assert.equal(await h.call("bash", { command: "git commit -am x" }), undefined);
			assert.deepEqual(order, ["path check", "commit"], "the path check runs before checks.commit");
			assert.deepEqual(checked.sort(), [join(h.root, "existing.ts"), join(h.root, "staged.ts")],
				"staged and unstaged tracked changes are checked; untracked files are not");
			pathFails = true;
			order.length = 0;
			const blocked = await h.call("bash", { command: "git commit -am x" });
			assert.equal(blocked?.block, true);
			assert.ok(blocked?.reason?.includes("path check failed") && blocked?.reason?.includes("bad path"), blocked?.reason);
			assert.deepEqual(order, ["path check"], "a path check failure stops before checks.commit");
		} finally { h.cleanup(); }
	});

	test("owner-only commit refusal belongs only to dynaflows-on repos", async () => {
		const h = harness();
		const owned = "scripts/checks/dependency-audit-exceptions.json";
		const git = (...args: string[]) => execFileSync("git", args, { cwd: h.root });
		try {
			await h.read("skill://git-tool");
			mkdirSync(join(h.root, "scripts/checks"), { recursive: true });
			mkdirSync(join(h.root, "security"), { recursive: true });
			writeFileSync(join(h.root, owned), "[]\n");
			writeFileSync(join(h.root, "security/policy.json"), "{}\n");
			git("add", "-A");
			git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", "commit", "-qm", "base");
			h.gate({ schemaVersion: 2, ownerOnlyPaths: [owned, "security/"] });
			const commit = () => h.call("bash", { command: "git commit -m x" });
			writeFileSync(join(h.root, "existing.ts"), "export const answer = 43;\n");
			git("add", "existing.ts");
			assert.equal(await commit(), undefined, "a commit that leaves owner-only paths alone passes");
			for (const dynaflows of [false, true]) {
				h.dynaflows(dynaflows);
				writeFileSync(join(h.root, owned), '[{"advisory": "GHSA-x"}]\n');
				git("add", owned);
				const staged = await commit();
				if (dynaflows) {
					assert.equal(staged?.block, true);
					assert.ok(staged?.reason?.includes(`owner-only path ${owned}`), staged?.reason);
					assert.ok(staged?.reason?.includes("raise the change to the owner"), staged?.reason);
					assert.ok(staged?.reason?.includes(join(h.recordDir(), "gate.json")));
				} else assert.equal(staged, undefined);
				git("reset", "-q", owned);
				assert.equal((await commit())?.block, dynaflows ? true : undefined, "unstaged owner-only changes follow the same switch");
				git("checkout", "--", owned);
				assert.equal(await commit(), undefined, "restored: the commit passes");
			}
			writeFileSync(join(h.root, "security/new.json"), "{}\n");
			git("add", "security/new.json");
			const added = await commit();
			assert.ok(added?.reason?.includes("owner-only path security/new.json"), "a directory entry covers new files under it");
			git("rm", "-q", "--cached", "security/new.json");
			rmSync(join(h.root, "security/new.json"));
			git("mv", owned, "scripts/checks/moved.json");
			const moved = await commit();
			assert.ok(moved?.reason?.includes(owned), `moving an owner-only file away is refused: ${moved?.reason}`);
		} finally { h.cleanup(); }
	});

	test("dynaflows commits need no sole-command, message or staging shape; only hook bypass is refused", async () => {
		const h = harness();
		try {
			await h.read("skill://git-tool");
			h.gate({ schemaVersion: 2, checks: { commit: ["true"] } });
			h.dynaflows(true);
			for (const command of [
				"git add -A && git commit -m x", "git -c core.editor=true commit", "git commit", "git commit -e -m x",
				"git --exec-path=/usr/lib/git-core commit -m x",
			]) assert.equal(await h.call("bash", { command }), undefined, command);
			for (const command of ["git commit --no-verify -m x", "git commit -nm x", "git -c core.hooksPath=/dev/null commit -m x"]) {
				assert.ok((await h.call("bash", { command }))?.reason?.includes("pre-commit hook"), command);
			}
		} finally { h.cleanup(); }
	});

	test("the default harness check is omp-guardrails from the harness's own reference project", async () => {
		const h = harness({ guardrailsCommand: "default" });
		try {
			await h.read("skill://git-tool");
			h.dynaflows(true);
			const seen: string[][] = [];
			const exec = h.api.exec.bind(h.api);
			h.api.exec = async (command, args, options) => {
				const [name, rest] = repoCommand(command, args);
				if (name !== "uv") return exec(command, args, options);
				assert.equal(options.cwd, h.root);
				seen.push(rest);
				return { code: 0, killed: false, stdout: "", stderr: "" };
			};
			assert.equal(await h.call("bash", { command: "git commit -m x" }), undefined);
			const project = resolve(import.meta.dir, "../agent/skills/setup-repo-proofs/assets/python-reference");
			assert.deepEqual(seen, [["run", "--project", project, "omp-guardrails", "check", "--repo", h.root, "--record", h.recordDir()]]);
		} finally { h.cleanup(); }
	});

	test("a record missing checks.commit, or carrying an unknown field, refuses commits and pushes by name", async () => {
		const h = harness();
		try {
			await h.read("skill://git-tool");
			const complete = { schemaVersion: 2, checks: { commit: ["true"] }, ownerOnlyPaths: [], dynaflows: { enabled: true } };
			for (const [gate, named] of [
				[{ ...complete, ownerOnlyPaths: undefined }, "ownerOnlyPaths"], [{ ...complete, ownerOnlyPaths: ["../outside.json"] }, "../outside.json"],
				[{ ...complete, checks: { ci: ["ci"] } }, "checks"],
				[{ ...complete, checks: undefined }, "checks"], [{ ...complete, unknownField: 3 }, "unknownField"],
				[{ ...complete, checks: { commit: ["true"], lint: ["x"] } }, "checks.lint"],
			] as const) {
				h.rawGate(JSON.stringify(gate));
				for (const command of ["git commit -m x", "git push"]) {
					const refused = await h.call("bash", { command });
					assert.equal(refused?.block, true, `${named}: ${command}`);
					assert.ok(refused?.reason?.includes(named), refused?.reason);
				}
			}
			h.rawGate(JSON.stringify(complete));
			assert.equal(await h.call("bash", { command: "git commit -m x" }), undefined);
		} finally { h.cleanup(); }
	});

	test("failed path checks persist at handoff and rerun to observe out-of-tool repairs", async () => {
		const h = harness();
		try {
			h.pathFailure("bad path");
			h.gate({ schemaVersion: 2 });
			h.dynaflows(true);
			await h.read(integrity, structure, naming);
			await h.read(verification);
			assert.ok((await h.call("edit", { path: "existing.ts" }))?.additionalContext?.includes("path check failed"));
			assert.equal((await h.emit("session_stop"))?.decision, "block");
			assert.equal((await h.emit("session_stop"))?.decision, "block");
			h.pathFailure();
			assert.equal(await h.emit("session_stop"), undefined);
			h.pathFailure("bad existing.ts path");
			assert.ok((await h.call("edit", { path: "existing.ts" }))?.additionalContext?.includes("path check failed"));
			rmSync(join(h.root, "existing.ts"));
			assert.equal(await h.emit("session_stop"), undefined, "deleted failed paths are not passed back to the checker");
		} finally { h.cleanup(); }
	});


	test("isolates sessions, restores successful branch reads, and forgets compacted reads", async () => {
		const h = harness();
		try {
			h.dynaflows(true);
			await h.read(integrity);
			h.setSession("second");
			assert.equal((await h.call("edit", { path: "existing.ts" }))?.block, true);
			h.setSession("first");
			assert.equal(await h.call("edit", { path: "existing.ts" }), undefined);
			h.branch.push(
				{ type: "message", message: { role: "assistant", content: [
					{ type: "toolCall", id: "restored", name: "read", arguments: { path: integrity } },
				] } },
				{ type: "message", message: { role: "toolResult", toolCallId: "restored", isError: false } },
			);
			await h.emit("session_tree");
			assert.equal(await h.call("edit", { path: "existing.ts" }), undefined);
			h.branch.push({ type: "compaction" });
			await h.emit("session_branch");
			assert.equal((await h.call("edit", { path: "existing.ts" }))?.block, true);
		} finally { h.cleanup(); }
	});

	test("$dynaflows sets the record's switch; the machine default can only turn it off; only main prompts toggle it", async () => {
		const h = harness();
		try {
			h.machineDynaflows(true);
			assert.equal(await h.emit("before_agent_start", { reason: "prompt", prompt: "pull latest" }), undefined, "no record: off whatever the machine says");
			await h.emit("before_agent_start", { reason: "prompt", prompt: "$dynaflows off" });
			assert.ok(h.notes.some(note => note.includes("has no per-repo record, so dynaflows is already off")), h.notes.join("; "));
			await h.emit("before_agent_start", { reason: "prompt", prompt: "$dynaflows on" });
			assert.ok(h.notes.some(note => note.includes("has no per-repo record; starting repository setup")), h.notes.join("; "));
			assert.equal(existsSync(h.recordDir()), false, "the toggle never creates a record");
			h.gate({ schemaVersion: 2 });
			assert.equal(await h.emit("before_agent_start", { reason: "prompt", prompt: "next" }), undefined, "the record says off");
			h.dynaflows(true);
			assert.equal(await h.emit("before_agent_start", { reason: "revive", prompt: "" }), undefined);
			const pointer = JSON.stringify(await h.emit("before_agent_start", { reason: "prompt", prompt: "next" }));
			assert.ok(pointer.includes("skill://omp-dyna-workflows"), "the record turns it on");
			assert.equal(await h.emit("before_agent_start", { reason: "prompt", prompt: "again" }), undefined, "pointer once per session");
			const off = JSON.stringify(await h.emit("before_agent_start", { reason: "prompt", prompt: "$dynaflows off" }));
			assert.ok(off.includes("Dynaflows off") && !off.includes("skill://omp-dyna-workflows"));
			assert.deepEqual(h.record(), { checks: { commit: ["true"] }, ownerOnlyPaths: [], dynaflows: { enabled: false }, schemaVersion: 2 },
				"only the switch changes");
			h.setSession("restarted");
			assert.equal(await h.emit("before_agent_start", { reason: "prompt", prompt: "work" }), undefined, "record off outlives the session and beats machine on");
			assert.equal(await h.emit("before_agent_start", { reason: "prompt", prompt: "price $dynaflowsy" }), undefined, "keyword needs a word boundary");
			assert.ok(JSON.stringify(await h.emit("before_agent_start", { reason: "prompt", prompt: "$dynaflows" })).includes("skill://omp-dyna-workflows"));
			h.setSession("machine-off");
			h.machineDynaflows(false);
			assert.equal(await h.emit("before_agent_start", { reason: "prompt", prompt: "work" }), undefined, "machine off beats record on");
			h.setSession("sub"); h.context.agent.kind = "sub";
			for (const prompt of ["$dynaflows off", "stop $dynaflows", "turn off $dynaflows"]) {
				assert.equal(await h.emit("before_agent_start", { reason: "prompt", prompt }), undefined, `subagents cannot toggle dynaflows: ${prompt}`);
			}
			assert.deepEqual(h.record().dynaflows, { enabled: true });
		} finally { h.cleanup(); }
	});

	test("every on and off directive form sets the record's switch", async () => {
		const h = harness();
		try {
			h.gate({ schemaVersion: 2 });
			const on = ["$dynaflows", "$DYNAFLOWS ON", "  $dynaflows enable  "];
			for (const verb of ["on", "enable", "init", "initialise", "initialize", "start", "setup", "set up", "turn on"]) {
				on.push(`$dynaflows ${verb}`, `${verb} $dynaflows`, `${verb.toUpperCase()}  $Dynaflows`);
			}
			const off = ["$dynaflows off", "$dynaflows disable", "$dynaflows stop", "disable $dynaflows", "stop $dynaflows", "Turn  Off $dynaflows"];
			for (const prompt of on) {
				h.dynaflows(false);
				await h.emit("before_agent_start", { reason: "prompt", prompt });
				assert.deepEqual(h.record().dynaflows, { enabled: true }, prompt);
			}
			for (const prompt of off) {
				h.dynaflows(true);
				const result = JSON.stringify(await h.emit("before_agent_start", { reason: "prompt", prompt }));
				assert.deepEqual(h.record().dynaflows, { enabled: false }, prompt);
				assert.ok(result.includes("Dynaflows off"), prompt);
			}
		} finally { h.cleanup(); }
	});

	test("enabling dynaflows starts from the repo's stage", async () => {
		const h = harness();
		try {
			const start = async (prompt: string) => JSON.stringify(await h.emit("before_agent_start", { reason: "prompt", prompt }));
			// No record: setup starts at Step 0, drafting the switch on; nothing is written and no error shows.
			const setup = await start("init $dynaflows");
			assert.ok(setup.includes("Start skill://setup-repo-proofs setup now, from Step 0") && setup.includes("owner/repo"), setup);
			assert.ok(setup.includes('\\"dynaflows\\": {\\"enabled\\": true}'), setup);
			assert.equal(existsSync(h.recordDir()), false, "enabling never creates a record");
			assert.ok(h.notes.some(note => note.includes("starting repository setup")), h.notes.join("; "));
			assert.deepEqual(h.errors, [], "no record is a stage, not an error");
			// A refused record: setup resumes and names the reason.
			h.rawGate('{"schemaVersion":2,"dynaflows":{"enabled":false}}');
			const resume = await start("$dynaflows enable");
			assert.ok(resume.includes("Resume skill://setup-repo-proofs setup now") && resume.includes("checks"), resume);
			h.rawGate("{broken");
			const broken = await start("$dynaflows enable");
			assert.ok(broken.includes("Resume skill://setup-repo-proofs setup now") && broken.includes("is refused: "), broken);
			// A valid record and a directive-only prompt: assess the stage and start it.
			h.gate({ schemaVersion: 2 });
			const kickoff = await start("$dynaflows on");
			assert.ok(kickoff.includes("Dynaflows on for owner/repo") && kickoff.includes("skill://omp-dyna-workflows"), kickoff);
			assert.ok(kickoff.includes("Report the stage you found and start it") && kickoff.includes("ask the owner for the task"), kickoff);
			assert.equal(await h.emit("before_agent_start", { reason: "prompt", prompt: "next" }), undefined, "the kickoff counts as the pointer");
			// A directive beside a task: the prompt's task is routed.
			const route = await start("enable $dynaflows\nfix the login bug");
			assert.ok(route.includes("Dynaflows on. Route this work with skill://omp-dyna-workflows") && !route.includes("Report the stage"), route);
			// The machine default keeps it off and says so.
			h.machineDynaflows(false);
			const kept = await start("$dynaflows");
			assert.ok(kept.includes("Dynaflows off for owner/repo: the machine default") && kept.includes("Do not apply omp-dyna-workflows"), kept);
			assert.deepEqual(h.record().dynaflows, { enabled: true });
		} finally { h.cleanup(); }
	});

	test("only a standalone directive line sets the switch; asks inside sentences are left to the agent", async () => {
		const h = harness();
		try {
			h.gate({ schemaVersion: 2 });
			h.dynaflows(false);
			for (const prompt of ["I want to enable dynaflows", "please enable $dynaflows", "what does `$dynaflows init` do?",
				"don't enable dynaflows", "type enable $dynaflows to start", "$dynaflows on."]) {
				await h.emit("before_agent_start", { reason: "prompt", prompt });
				assert.deepEqual(h.record().dynaflows, { enabled: false }, prompt);
			}
			for (const prompt of ["enable $dynaflows", "  Turn On   $dynaflows ", "$dynaflows init"]) {
				h.dynaflows(false);
				await h.emit("before_agent_start", { reason: "prompt", prompt });
				assert.deepEqual(h.record().dynaflows, { enabled: true }, prompt);
			}
			const route = JSON.stringify(await h.emit("before_agent_start", { reason: "prompt", prompt: "enable $dynaflows\nfix the login bug" }));
			assert.ok(route.includes("Route this work with") && !route.includes("Report the stage"), route);
		} finally { h.cleanup(); }
	});

	test("contradictory asks change nothing and tell the agent to ask the owner", async () => {
		const h = harness();
		try {
			h.gate({ schemaVersion: 2 });
			for (const prompt of ["enable $dynaflows\ndisable $dynaflows", "$dynaflows on\n$dynaflows off"]) {
				const result = JSON.stringify(await h.emit("before_agent_start", { reason: "prompt", prompt }));
				assert.ok(result.includes("both enable and disable dynaflows") && result.includes("Ask the owner"), result);
				assert.deepEqual(h.record().dynaflows, { enabled: false }, prompt);
			}
			assert.ok(h.errors.some(error => error.includes("contradictory dynaflows directives")), h.errors.join("; "));
		} finally { h.cleanup(); }
	});

	test("the dynaflows tool sets the switch and returns the next step; subagents cannot use it", async () => {
		const h = harness();
		try {
			const setup = await h.tool("dynaflows", { enabled: true });
			assert.ok(setup.includes("from Step 0") && setup.includes(h.recordDir()), setup);
			h.gate({ schemaVersion: 2 });
			const kickoff = await h.tool("dynaflows", { enabled: true });
			assert.deepEqual(h.record().dynaflows, { enabled: true });
			assert.ok(kickoff.includes("Dynaflows on for owner/repo") && kickoff.includes("Report the stage you found"), kickoff);
			assert.ok((await h.tool("dynaflows", { enabled: false })).includes("Dynaflows off"));
			assert.deepEqual(h.record().dynaflows, { enabled: false });
			h.context.agent.kind = "sub";
			assert.ok((await h.tool("dynaflows", { enabled: true })).includes("only the main agent"));
			assert.deepEqual(h.record().dynaflows, { enabled: false });
		} finally { h.cleanup(); }
	});

	test("a repo without an origin remote has no record, so dynaflows is off", async () => {
		const h = harness();
		try {
			h.dynaflows(true);
			assert.ok(JSON.stringify(await h.emit("before_agent_start", { reason: "prompt", prompt: "work" })).includes("skill://omp-dyna-workflows"));
			await execute("git", ["remote", "remove", "origin"], { cwd: h.root });
			h.setSession("no-origin");
			assert.equal(await h.emit("before_agent_start", { reason: "prompt", prompt: "work" }), undefined);
			await h.read("skill://git-tool");
			assert.equal(await h.call("bash", { command: "git commit -m x" }), undefined);
		} finally { h.cleanup(); }
	});

	test("dynaflows off is never silent: one note at session start and one at every commit name the slug and reason", async () => {
		const h = harness();
		try {
			const gate = join(h.recordDir(), "gate.json");
			const cases: [string, () => Promise<void> | void, string][] = [
				["missing record", () => {}, `dynaflows off for owner/repo: no per-repo record at ${gate}`],
				["record off", () => h.gate({ schemaVersion: 2 }), `dynaflows off for owner/repo: the record ${gate} sets dynaflows.enabled false`],
				["machine off", () => { h.dynaflows(true); h.machineDynaflows(false); }, "dynaflows off for owner/repo: the machine default "],
				["non-GitHub origin", async () => {
					h.machineDynaflows(true);
					await execute("git", ["remote", "set-url", "origin", "https://gitlab.com/owner/repo.git"], { cwd: h.root });
				}, "dynaflows off for none: origin https://gitlab.com/owner/repo.git is not a GitHub owner/name URL"],
				["no origin", () => execute("git", ["remote", "remove", "origin"], { cwd: h.root }).then(() => {}), "dynaflows off for none: no origin remote"],
			];
			for (const [name, arrange, text] of cases) {
				await arrange();
				h.notes.length = 0;
				h.setSession(name);
				await h.emit("session_start");
				await h.read("skill://git-tool");
				assert.equal(h.notes.filter(note => note.includes(text)).length, 1, `${name}: ${h.notes.join("; ")}`);
				for (let commit = 0; commit < 2; commit++) assert.equal(await h.call("bash", { command: "git commit -m x" }), undefined, name);
				const commits = h.notes.filter(note => note.includes(text) && note.includes("commit checks do not run"));
				assert.equal(commits.length, 2, `${name}: every commit says why: ${h.notes.join("; ")}`);
			}
			h.machineDynaflows(true);
			await execute("git", ["remote", "add", "origin", defaultOrigin], { cwd: h.root });
			h.dynaflows(true);
			h.notes.length = 0;
			h.setSession("on");
			await h.emit("session_start");
			await h.read("skill://git-tool");
			assert.equal(await h.call("bash", { command: "git commit -m x" }), undefined);
			assert.deepEqual(h.notes.filter(note => note.includes("dynaflows off")), [], "dynaflows on adds no off note");
		} finally { h.cleanup(); }
	});

	test("requires absolute subagent paths across mapped writes and edit payloads", async () => {
		const h = harness();
		try {
			h.context.agent.kind = "sub";
			await h.read(integrity, structure, naming);
			const calls: [string, Input][] = [
				["write", { path: "new.ts" }],
				["edit", { paths: [join(h.root, "existing.ts"), "../other.ts"] }],
				["ast_edit", { paths: ["./existing.ts"] }],
				["edit", { input: "*** Begin Patch\n[existing.ts#ABCD]\nREM\n*** End Patch\n" }],
				["edit", { input: `*** Begin Patch\n[${join(h.root, "existing.ts")}#ABCD]\nMV moved.ts\n*** End Patch\n` }],
			];
			for (const [tool, input] of calls) {
				const result = await h.call(tool, input);
				assert.equal(result?.block, true, `${tool}: ${JSON.stringify(input)}`);
				assert.ok(result?.reason?.includes("absolute path inside your assigned worktree"));
			}
			for (const path of ["local://brief.txt", "agent://Main", "xd://report_issue", "proc://job/kill", "memory://root/note", "skill://example"]) {
				assert.equal(await h.call("write", { path }), undefined, path);
			}
			assert.equal(await h.call("write", { path: `~/${relative(homedir(), join(h.root, "existing.ts"))}` }), undefined, "home-relative paths expand before the absolute-path check");
			assert.equal(await h.call("write", { path: join(tmpdir(), "agent-rails-scratch.txt") }), undefined);
		} finally { h.cleanup(); }
	});

	test("contains subagent symlink writes within the lexical repo, including new descendants", async () => {
		const h = harness();
		const outside = mkdtempSync(join(tmpdir(), "agent-rails-outside-"));
		try {
			h.context.agent.kind = "sub";
			await h.read(integrity, structure, naming);
			writeFileSync(join(outside, "existing.ts"), "outside\n");
			mkdirSync(join(outside, ".git"));
			symlinkSync(outside, join(h.root, "escape"));
			symlinkSync(join(outside, "existing.ts"), join(h.root, "escape-file.ts"));
			symlinkSync(join(h.root, "existing.ts"), join(h.root, "inside-file.ts"));
			mkdirSync(join(h.root, "owned"));
			symlinkSync(join(h.root, "owned"), join(h.root, "inside"));
			for (const suffix of ["escape/existing.ts", "escape/new/deep/file.ts", "escape-file.ts"]) {
				const path = join(h.root, suffix);
				for (const tool of ["write", "edit", "ast_edit"]) {
					const result = await h.call(tool, tool === "write" ? { path } : { paths: [path] });
					assert.equal(result?.block, true, `${tool}: ${suffix}`);
					assert.ok(result?.reason?.includes(path));
					assert.ok(result?.reason?.includes(realpathSync(outside)));
				}
			}
			for (const suffix of ["inside-file.ts", "inside/new/deep/file.ts"]) {
				assert.equal(await h.call("write", { path: join(h.root, suffix) }), undefined, suffix);
			}
			symlinkSync("loop", join(h.root, "loop"));
			assert.equal((await h.call("write", { path: join(h.root, "loop") }))?.block, true, "filesystem errors fail closed");
			h.context.agent.kind = "main";
			assert.equal(await h.call("write", { path: join(h.root, "escape-file.ts") }), undefined, "main sessions are unaffected");
		} finally { h.cleanup(); rmSync(outside, { recursive: true, force: true }); }
	});

	test("checks AST mutation on resolve, not its preview", async () => {
		const h = harness();
		try {
			h.pathFailure("bad path");
			h.gate({ schemaVersion: 2 });
			h.dynaflows(true);
			await h.read(integrity, structure, naming);
			assert.equal(await h.call("ast_edit", { paths: ["existing.ts"] }, {
				applied: false, files: ["existing.ts"], totalReplacements: 1,
			}), undefined);
			assert.equal(await h.emit("session_stop"), undefined);
			const result = await h.call("write", { path: "xd://resolve", content: "Apply the rewrite" }, {
				applied: true, files: ["existing.ts"],
			});
			assert.ok(result?.additionalContext?.includes("bad path"));
			assert.equal((await h.emit("session_stop"))?.decision, "block");
		} finally { h.cleanup(); }
	});
});

test("path checks forget deleted and ignored failures without skipping live repository paths", async () => {
	const h = harness();
	try {
		await execute("git", ["init"], { cwd: h.root });
		writeFileSync(join(h.root, ".gitignore"), "ignored.ts\n");
		writeFileSync(join(h.root, "ignored.ts"), "scratch");
		let checked: string[][] = [];
		const exec = h.api.exec.bind(h.api);
		h.api.exec = async (command, args, options) => {
			const [name, rest] = repoCommand(command, args);
			if (name === "path-check") { checked.push(rest); return { code: 1, killed: false, stdout: "", stderr: "bad live path" }; }
			return exec(command, args, options);
		};
		h.gate({ schemaVersion: 2 });
		h.dynaflows(true);
		await h.read(integrity, structure, naming);
		await h.call("edit", { paths: ["existing.ts", "ignored.ts", "deleted.ts"] });
		assert.deepEqual(checked, [[join(h.root, "existing.ts")]]);
		rmSync(join(h.root, "existing.ts"));
		await h.call("edit", { path: "ignored.ts" });
		await h.read(verification);
		assert.equal(await h.emit("session_stop"), undefined);
		assert.equal(checked.length, 1);
	} finally { h.cleanup(); }
});

test("path checks exclude Git directory internals while checking live files", async () => {
	const h = harness();
	try {
		const internal = join(h.root, ".git/info/exclude");
		const live = join(h.root, "live.ts");
		writeFileSync(internal, "# session edit\n");
		writeFileSync(live, "export const live = true;\n");
		const checked: string[][] = [];
		const exec = h.api.exec.bind(h.api);
		h.api.exec = async (command, args, options) => {
			const [name, rest] = repoCommand(command, args);
			if (name === "path-check") { checked.push(rest); return { code: 0, killed: false, stdout: "", stderr: "" }; }
			return exec(command, args, options);
		};
		h.gate({ schemaVersion: 2 });
		h.dynaflows(true);
		await h.read(integrity, structure, naming);
		await h.call("edit", { paths: [internal, live] });
		assert.deepEqual(checked, [[live]], "git directory paths must not reach the path check");
	} finally { h.cleanup(); }
});

test("path checks forget failures for files restored to HEAD", async () => {
	const h = harness();
	try {
		const path = join(h.root, "existing.ts");
		const committed = readFileSync(path, "utf8");
		await execute("git", ["add", "--", "existing.ts"], { cwd: h.root });
		await execute("git", ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "tracked fixture"], { cwd: h.root });
		writeFileSync(path, "export const answer = 43;\n");
		const checked: string[][] = [];
		const exec = h.api.exec.bind(h.api);
		h.api.exec = async (command, args, options) => {
			const [name, rest] = repoCommand(command, args);
			if (name === "path-check") { checked.push(rest); return { code: 1, killed: false, stdout: "", stderr: "bad tracked path" }; }
			return exec(command, args, options);
		};
		h.gate({ schemaVersion: 2 });
		h.dynaflows(true);
		await h.read(integrity, structure, naming);
		const failure = await h.call("edit", { path });
		assert.ok(failure?.additionalContext?.includes("bad tracked path"));
		assert.deepEqual(checked, [[path]]);
		writeFileSync(path, committed);
		await h.read(verification);
		assert.equal(await h.emit("session_stop"), undefined, "a file restored to HEAD must clear its path check failure");
		assert.deepEqual(checked, [[path]], "a file restored to HEAD must clear its path check failure");
	} finally { h.cleanup(); }
});

test("path checks keep ignored files excluded when a path crosses a symlink", async () => {
	const h = harness();
	const outside = mkdtempSync(join(tmpdir(), "agent-rails-pathcheck-"));
	try {
		writeFileSync(join(h.root, ".gitignore"), "node_modules/\n");
		mkdirSync(join(h.root, "node_modules/pkg"), { recursive: true });
		const ignored = join(h.root, "node_modules/pkg/index.js");
		writeFileSync(ignored, "module.exports = true;\n");
		writeFileSync(join(outside, "file"), "live\n");
		// An escaping symlink retains its lexical path when rails prepares the Git query.
		symlinkSync(outside, join(h.root, "link"), "dir");
		const checked: string[][] = [];
		const exec = h.api.exec.bind(h.api);
		h.api.exec = async (command, args, options) => {
			const [name, rest] = repoCommand(command, args);
			if (name === "path-check") { checked.push(rest); return { code: 0, killed: false, stdout: "", stderr: "" }; }
			return exec(command, args, options);
		};
		h.gate({ schemaVersion: 2 });
		h.dynaflows(true);
		await h.read(integrity, structure, naming);
		await h.call("edit", { paths: [ignored, join(h.root, "link/file")] });
		assert.ok(!checked.some(args => args.some(path => path.includes("node_modules/"))),
			"ignored paths must not reach the path check when another path breaks git check-ignore");
	} finally { h.cleanup(); rmSync(outside, { recursive: true, force: true }); }
});

test("dynaflows merge requires prove()'s result file to say pass for the exact head", async () => {
	const h = harness();
	try {
		await execute("git", ["init"], { cwd: h.root });
		await h.read("skill://git-tool");
		const head = "a".repeat(40);
		const command = `env gh pr merge 3 --match-head-commit=${head}`;
		mockPrCi(h, greenCi, head, "3");
		assert.equal(await h.call("bash", { command }), undefined, "dynaflows off: no merge guard");
		h.dynaflows(true);
		assert.ok((await h.call("bash", { command: "gh pr merge 3" }))?.reason?.includes("--match-head-commit"));
		const missing = await h.call("bash", { command });
		assert.ok(missing?.reason?.includes(`no prove() pass for ${head}`), missing?.reason);
		assert.ok(missing?.reason?.includes(join(".git", "omp-prove", `${head}.result.json`)), missing?.reason);
		const result = join(h.root, ".git/omp-prove", `${head}.result.json`);
		mkdirSync(dirname(result), { recursive: true });
		const cases: [string, string][] = [
			["malformed", "{broken"],
			["failed", JSON.stringify({ status: "fail", head })],
			["wrong head", JSON.stringify({ status: "pass", head: "b".repeat(40) })],
			["no head", JSON.stringify({ status: "pass" })],
		];
		for (const [name, content] of cases) {
			writeFileSync(result, content);
			assert.ok((await h.call("bash", { command }))?.reason?.includes(`no prove() pass for ${head}`), name);
		}
		proveResult(h, head);
		structureResult(h, head);
		assert.equal(await h.call("bash", { command }), undefined);
		assert.equal(await h.call("bash", { command: `gh pr merge 3 --match-head-commit ${head}` }), undefined);
	} finally { h.cleanup(); }
});

test("dynaflows merge requires a valid exact-head structure result and blocks ready findings", async () => {
	const h = harness();
	try {
		await h.read("skill://git-tool");
		proveResult(h);
		mockPrCi(h, greenCi, ciHead, "3");
		const command = `gh pr merge 3 --match-head-commit ${ciHead}`;
		assert.equal(await h.call("bash", { command }), undefined, "dynaflows off needs no structure file");
		h.dynaflows(true);
		const missing = await h.call("bash", { command });
		assert.equal(missing?.block, true);
		assert.ok(missing?.reason?.includes(`no valid structure_review() result for ${ciHead}`));
		structureResult(h);
		const path = join(h.root, ".git/omp-structure", `${ciHead}.json`);
		for (const content of [
			"{broken",
			JSON.stringify({ head: "b".repeat(40), halt: false, status: "shadow", findings: [] }),
			JSON.stringify({ head: ciHead, halt: "false", status: "shadow", findings: [] }),
			JSON.stringify({ head: ciHead, halt: false, status: "pass", findings: [] }),
		]) {
			writeFileSync(path, content);
			assert.equal((await h.call("bash", { command }))?.block, true);
		}
		const finding = { unit: "src/orders", question: "folder_name_coverage", status: "ready", score: 0.9, threshold: 0.7 };
		writeFileSync(path, JSON.stringify({ head: ciHead, halt: true, status: "blocked", findings: [finding] }));
		const blocked = await h.call("bash", { command });
		assert.equal(blocked?.block, true);
		assert.ok(blocked?.reason?.includes("src/orders: folder_name_coverage"));
		assert.ok(blocked?.reason?.includes("fix them and rerun prove() and structure_review() on the new head"));
		h.dynaflows(false);
		assert.equal(await h.call("bash", { command }), undefined, "dynaflows off ignores halt");
		h.dynaflows(true);
		writeFileSync(path, JSON.stringify({ head: ciHead, halt: false, status: "incomplete",
			findings: [{ ...finding, status: "provisional" }], errors: ["judge offline"] }));
		assert.equal(await h.call("bash", { command }), undefined, "provisional findings and infrastructure errors stay shadow-only");
		structureResult(h);
		assert.equal(await h.call("bash", { command }), undefined);
	} finally { h.cleanup(); }
});

// The generic CI rule (no ci block declared) on a dynaflows merge with prove() passed.
for (const scenario of [
	{ name: "failed CheckRun", rollup: [{ __typename: "CheckRun", name: "test", status: "COMPLETED", conclusion: "FAILURE" }], fragment: "test: COMPLETED/FAILURE" },
	{ name: "pending CheckRun", rollup: [{ __typename: "CheckRun", name: "test", status: "IN_PROGRESS", conclusion: null }], fragment: "test: IN_PROGRESS" },
	{ name: "failed StatusContext", rollup: [{ __typename: "StatusContext", context: "external", state: "FAILURE" }], fragment: "external: FAILURE" },
	{ name: "pending StatusContext", rollup: [{ __typename: "StatusContext", context: "external", state: "PENDING" }], fragment: "external: PENDING" },
	{ name: "only skipped", rollup: [{ __typename: "CheckRun", name: "unused", status: "COMPLETED", conclusion: "SKIPPED" }], fragment: ciHead },
	{ name: "empty declared CI", rollup: [], boundary: "gate", fragment: `no CI checks reported for ${ciHead}` },
	{ name: "empty workflow directory", rollup: [], boundary: "workflows", fragment: `no CI checks reported for ${ciHead}` },
	{ name: "empty no-CI boundary", rollup: [] },
	{ name: "green", rollup: greenCi },
] as const) {
	test(`merge CI rule: ${scenario.name}`, async () => {
		const h = harness();
		try {
			await h.read("skill://git-tool");
			if ("boundary" in scenario && scenario.boundary === "gate") h.gate({ schemaVersion: 2, checks: { commit: ["true"], ci: ["bun", "ci"] } });
			if ("boundary" in scenario && scenario.boundary === "workflows") mkdirSync(join(h.root, ".github/workflows"), { recursive: true });
			h.dynaflows(true);
			proveResult(h);
			structureResult(h);
			mockPrCi(h, [...scenario.rollup], ciHead, "3");
			const result = await h.call("bash", { command: `gh pr merge 3 --match-head-commit ${ciHead}` });
			if ("fragment" in scenario) {
				assert.equal(result?.block, true);
				assert.equal(result?.reason?.includes(scenario.fragment), true, scenario.fragment);
				assert.equal(result?.reason?.includes(ciHead), true, "diagnostic identifies the PR head");
			} else assert.equal(result, undefined);
		} finally { h.cleanup(); }
	});
}

// gate.ci: the declared context must be SUCCESS on the exact head, as a commit status or a check run.
const requiredRefusal = (state: string) => `Agent rails: required CI context "gate" is ${state} for ${ciHead} in base-owner/base-repo (runner ci-gate). Submit and wait: python3 ${ciRunner} submit ci-gate base-owner/base-repo ${ciHead}, then python3 ${ciRunner} wait ci-gate base-owner/base-repo ${ciHead} --context gate; see skill://git-tool/references/merge-and-recover.md for failures.`;
for (const scenario of [
	{ name: "missing among other green checks", rollup: [{ __typename: "CheckRun", name: "audit-packages", status: "COMPLETED", conclusion: "SUCCESS" }], state: "missing" },
	{ name: "missing with no checks", rollup: [], state: "missing" },
	{ name: "pending StatusContext", rollup: [{ __typename: "StatusContext", context: "gate", state: "PENDING" }], state: "PENDING" },
	{ name: "failed StatusContext", rollup: [{ __typename: "StatusContext", context: "gate", state: "FAILURE" }], state: "FAILURE" },
	{ name: "failed CheckRun", rollup: [{ __typename: "CheckRun", name: "gate", status: "COMPLETED", conclusion: "FAILURE" }], state: "COMPLETED/FAILURE" },
	{ name: "pending CheckRun", rollup: [{ __typename: "CheckRun", name: "gate", status: "QUEUED", conclusion: null }], state: "QUEUED/" },
	{ name: "success as StatusContext", rollup: [
		{ __typename: "StatusContext", context: "gate", state: "SUCCESS" },
		{ __typename: "CheckRun", name: "audit-packages", status: "COMPLETED", conclusion: "SUCCESS" },
	] },
	{ name: "success as CheckRun", rollup: [{ __typename: "CheckRun", name: "gate", status: "COMPLETED", conclusion: "SUCCESS" }] },
	// Only the declared context decides: optional checks, red or pending, never block.
	{ name: "success while other checks are red or pending", rollup: [
		{ __typename: "StatusContext", context: "gate", state: "SUCCESS" },
		{ __typename: "CheckRun", name: "lint", status: "COMPLETED", conclusion: "FAILURE" },
		{ __typename: "StatusContext", context: "docs-preview", state: "PENDING" },
	] },
] as const) {
	test(`required context merge: ${scenario.name}`, async () => {
		const h = harness();
		try {
			await h.read("skill://git-tool");
			await originRemote(h, "git@github.com:base-owner/base-repo.git");
			h.gate({ schemaVersion: 2, ci: ciGate });
			h.dynaflows(true);
			proveResult(h);
			structureResult(h);
			mockPrCi(h, [...scenario.rollup], ciHead, "3", undefined, "https://github.com/base-owner/base-repo/pull/3");
			const result = await h.call("bash", { command: `gh pr merge 3 --match-head-commit ${ciHead}` });
			if ("state" in scenario) {
				assert.equal(result?.block, true, "the required context must block");
				assert.equal(result?.reason, requiredRefusal(scenario.state));
			} else assert.equal(result, undefined);
		} finally { h.cleanup(); }
	});
}

test("without a ci block every reported check still decides the merge", async () => {
	const h = harness();
	try {
		await h.read("skill://git-tool");
		h.dynaflows(true);
		proveResult(h);
		structureResult(h);
		mockPrCi(h, [{ __typename: "StatusContext", context: "gate", state: "SUCCESS" },
			{ __typename: "CheckRun", name: "lint", status: "COMPLETED", conclusion: "FAILURE" }], ciHead, "3");
		const result = await h.call("bash", { command: `gh pr merge 3 --match-head-commit ${ciHead}` });
		assert.equal(result?.block, true);
		assert.ok(result?.reason?.includes(`CI is not green for ${ciHead}: lint: COMPLETED/FAILURE`), result?.reason);
	} finally { h.cleanup(); }
});

test("agents can write records before enabling dynaflows and after disabling it", async () => {
	const h = harness();
	try {
		await h.read(integrity, structure, naming);
		const gate = join(h.recordDir(), "gate.json");
		const draft = JSON.stringify({ schemaVersion: 2, checks: { commit: ["true"] }, ownerOnlyPaths: [], dynaflows: { enabled: true } });
		assert.equal((await h.call("write", { path: gate, content: draft }))?.block, undefined);
		const kickoff = JSON.stringify(await h.emit("before_agent_start", { reason: "prompt", prompt: "enable $dynaflows" }));
		assert.ok(kickoff.includes("from Step 0") && kickoff.includes("Write the record yourself") && kickoff.includes("Never ask the owner to save"), kickoff);
		for (const path of [gate, join(h.recordDir(), "profile.md")]) {
			assert.equal((await h.call("write", { path, content: draft }))?.block, undefined);
		}
		const other = await h.call("write", { path: join(h.records, "repos", "owner", "other", "gate.json"), content: draft });
		assert.equal(other?.block, undefined);
		h.context.agent.kind = "sub";
		assert.equal((await h.call("write", { path: gate, content: draft }))?.block, undefined);
		h.context.agent.kind = "main";
		mkdirSync(h.recordDir(), { recursive: true });
		writeFileSync(gate, draft);
		await h.emit("before_agent_start", { reason: "prompt", prompt: "disable $dynaflows" });
		assert.deepEqual(h.record().dynaflows, { enabled: false });
		assert.equal((await h.call("write", { path: gate, content: draft }))?.block, undefined);
		h.setSession("other");
		assert.equal((await h.call("write", { path: gate, content: draft }))?.block, undefined);
	} finally { h.cleanup(); }
});

test("plain merges with no ci block and dynaflows off never query GitHub", async () => {
	const h = harness();
	try {
		await h.read("skill://git-tool");
		h.gate({ schemaVersion: 2 });
		forbidGh(h);
		assert.equal(await h.call("bash", { command: "gh pr merge 3 --rebase" }), undefined);
	} finally { h.cleanup(); }
});

test("ci-runner commands come from the extension's own location, never a host literal", async () => {
	const source = readFileSync(join(import.meta.dir, "../agent/extensions/agent-rails.ts"), "utf8");
	assert.ok(source.includes('new URL("../../scripts/ci-runner.py", import.meta.url)'), "path is derived from import.meta.url");
	for (const literal of [homedir(), "/Users/", "ssh ", "BatchMode", "--timeout", "ci-gate.py"]) {
		assert.equal(source.includes(literal), false, `rails source must not hold ${literal}`);
	}
	assert.equal(ciRunner, resolve(import.meta.dir, "..", "scripts", "ci-runner.py"));
});

test("a gate with every optional check and a ci block allows publishing", async () => {
	const h = harness();
	try {
		await h.read("skill://git-tool");
		h.gate({ schemaVersion: 2, ci: ciGate, checks: { commit: ["true"], format: ["ruff", "format"], profile: ["omp-guardrails", "profile-check"] } });
		assert.equal(await h.call("bash", { command: "git push" }), undefined);
	} finally { h.cleanup(); }
});

for (const remote of ["git@github.com:OWNER/REPO.git", "https://github.com/owner/repo.git"]) {
	test(`PR URL repository: ${remote}`, async () => {
		const h = harness();
		try {
			await h.read("skill://git-tool");
			await originRemote(h, remote);
			h.gate({ schemaVersion: 2, ci: ciGate });
			h.dynaflows(true);
			proveResult(h);
			structureResult(h);
			const url = "https://github.com/other/checkout/pull/3";
			mockPrCi(h, [{ __typename: "StatusContext", context: "gate", state: "SUCCESS" }], ciHead, url, undefined, url);
			const result = await h.call("bash", { command: `gh pr merge ${url} --match-head-commit ${ciHead}` });
			assert.equal(result?.block, true, "cross-repo URL must not use checkout policy");
			assert.ok(result?.reason?.includes("run from a checkout of other/checkout"), result?.reason ?? "missing refusal reason");
		} finally { h.cleanup(); }
	});
}

test("PR URL selector accepts the checkout repository case-insensitively", async () => {
	const h = harness();
	try {
		await h.read("skill://git-tool");
		await originRemote(h, "git@github.com:OWNER/REPO.git");
		h.gate({ schemaVersion: 2, ci: ciGate });
		h.dynaflows(true);
		proveResult(h);
		structureResult(h);
		const url = "https://github.com/owner/repo/pull/3";
		mockPrCi(h, [{ __typename: "StatusContext", context: "gate", state: "SUCCESS" }], ciHead, url);
		assert.equal(await h.call("bash", { command: `gh pr merge ${url} --match-head-commit ${ciHead}` }), undefined);
	} finally { h.cleanup(); }
});

test("required context blocks merge despite a prove() pass", async () => {
	const h = harness();
	try {
		await h.read("skill://git-tool");
		h.dynaflows(true);
		h.gate({ schemaVersion: 2, ci: ciGate });
		proveResult(h);
		structureResult(h);
		mockPrCi(h, [{ __typename: "CheckRun", name: "audit-packages", status: "COMPLETED", conclusion: "SUCCESS" }], ciHead, "3");
		const result = await h.call("bash", { command: `gh pr merge 3 --match-head-commit ${ciHead}` });
		assert.equal(result?.block, true, "the required context must block merge despite a prove() pass");
		assert.ok(result?.reason?.includes(`required CI context "gate" is missing for ${ciHead} in owner/repo`), result?.reason ?? "missing refusal reason");
	} finally { h.cleanup(); }
});

test("PR query contract: requests the PR URL", async () => {
	const h = harness();
	try {
		await h.read("skill://git-tool");
		await originRemote(h);
		h.dynaflows(true);
		proveResult(h);
		structureResult(h);
		mockPrCi(h, greenCi, ciHead, "3");
		const exec = h.api.exec.bind(h.api);
		let fields: string | undefined;
		h.api.exec = async (command, args, options) => {
			if (command === "gh" && args[1] === "view") fields = args.at(-1);
			return exec(command, args, options);
		};
		assert.equal(await h.call("bash", { command: `gh pr merge 3 --match-head-commit ${ciHead}` }), undefined);
		assert.equal(fields, "headRefOid,statusCheckRollup,url");
	} finally { h.cleanup(); }
});

test("a merge reports a gh failure while reading the PR", async () => {
	const h = harness();
	try {
		await h.read("skill://git-tool");
		h.dynaflows(true);
		proveResult(h);
		structureResult(h);
		mockPrCi(h, [], ciHead, "3", "not authenticated");
		const result = await h.call("bash", { command: `gh pr merge 3 --match-head-commit ${ciHead}` });
		assert.ok(result?.reason?.includes("cannot read PR head/CI via gh: not authenticated"), result?.reason);
	} finally { h.cleanup(); }
});

for (const scenario of ["head mismatch", "red CI", "green CI"] as const) {
	test(`merge CI gate: ${scenario}`, async () => {
		const h = harness();
		try {
			await h.read("skill://git-tool");
			h.dynaflows(true);
			proveResult(h);
			structureResult(h);
			const head = scenario === "head mismatch" ? "b".repeat(40) : ciHead;
			mockPrCi(h, scenario === "red CI"
				? [{ __typename: "CheckRun", name: "test", status: "COMPLETED", conclusion: "FAILURE" }] : greenCi, head, "3");
			const result = await h.call("bash", { command: `gh pr merge 3 --match-head-commit ${ciHead}` });
			if (scenario === "green CI") assert.equal(result, undefined);
			else {
				assert.equal(result?.block, true);
				const fragment = scenario === "head mismatch" ? `PR head is ${head}, not ${ciHead}; prove() the current head` : "test: COMPLETED/FAILURE";
				assert.equal(result?.reason?.includes(fragment), true, fragment);
			}
		} finally { h.cleanup(); }
	});
}

test("dynaflows off: merges have no guard, even with a declared gate.ci", async () => {
	const h = harness();
	try {
		await h.read("skill://git-tool");
		h.gate({ schemaVersion: 2, ci: ciGate });
		forbidGh(h);
		assert.equal(await h.call("bash", { command: "gh pr merge 3 --merge" }), undefined);
		h.gate({ schemaVersion: 2, ci: ciGate, agentMerge: "disabled" });
		assert.ok((await h.call("bash", { command: "gh pr merge 3 --merge" }))?.reason?.includes("agentMerge is disabled"));
	} finally { h.cleanup(); }
});

for (const scenario of [
	{ name: "missing flag", args: "", fragment: "requires --match-head-commit <40-hex verified SHA>" },
	{ name: "bare variable", args: "--match-head-commit $H", fragment: "received unexpanded shell text `$H`; rails check the literal command, so pass the 40-hex SHA itself" },
	{ name: "quoted variable", args: '--match-head-commit "$H"', fragment: "received unexpanded shell text `$H`; rails check the literal command, so pass the 40-hex SHA itself" },
	{ name: "non-hex", args: "--match-head-commit nope", fragment: "must be a lowercase 40-hex SHA, got nope" },
	{ name: "missing prove() result", args: `--match-head-commit ${ciHead}`, fragment: `no prove() pass for ${ciHead}` },
] as const) {
	test(`merge diagnostics gate: ${scenario.name}`, async () => {
		const h = harness();
		try {
			await h.read("skill://git-tool");
			h.dynaflows(true);
			mockPrCi(h, greenCi, ciHead, "3");
			const result = await h.call("bash", { command: `gh pr merge 3 ${scenario.args}` });
			assert.equal(result?.block, true);
			assert.equal(result?.reason?.includes(scenario.fragment), true, scenario.fragment);
		} finally { h.cleanup(); }
	});
}

test("path checks canonicalize a symlinked repository root before executing", async () => {
	const h = harness();
	const aliases = mkdtempSync(join(tmpdir(), "agent-rails-alias-"));
	try {
		const alias = join(aliases, "repo");
		symlinkSync(h.root, alias);
		h.context.cwd = alias;
		h.gate({ schemaVersion: 2 });
		h.dynaflows(true);
		await h.read(integrity, structure, naming);
		const exec = h.api.exec.bind(h.api);
		let checked = false;
		h.api.exec = async (command, args, options) => {
			const [name, rest] = repoCommand(command, args);
			if (name === "path-check") {
				checked = true;
				assert.equal(options.cwd, h.root);
				assert.deepEqual(rest, [join(h.root, "existing.ts")]);
				return { code: 0, killed: false, stdout: "", stderr: "" };
			}
			return exec(command, args, options);
		};
		assert.equal(await h.call("edit", { path: join(alias, "existing.ts") }), undefined);
		assert.equal(checked, true);
	} finally { h.cleanup(); rmSync(aliases, { recursive: true, force: true }); }
});

test("pulling commits in needs no read and no check beyond hook-bypass flags", async () => {
	const h = harness();
	try {
		h.gate({ schemaVersion: 2, checks: { commit: ["false"] } });
		h.dynaflows(true);
		forbidGh(h);
		for (const command of ["git cherry-pick -n HEAD", "git merge origin/main", "git rebase origin/main", "git revert HEAD",
			"git am patch", "git pull", "git merge-base HEAD HEAD"]) {
			assert.equal(await h.call("bash", { command }), undefined, command);
		}
		for (const command of ["git merge --no-verify branch", "git pull --no-verify", "git rebase --no-ver main", "git -c core.hooksPath=/dev/null merge x"]) {
			const blocked = await h.call("bash", { command });
			assert.ok(blocked?.reason?.includes("drop hook bypass"), `${command}: ${blocked?.reason}`);
		}
		assert.ok((await h.call("bash", { command: "HUSKY=0 git merge x" }))?.reason?.includes("hook environment mutation"));
	} finally { h.cleanup(); }
});

test("path check runs under the one fixed rails timeout; a killed checker is surfaced, not thrown", async () => {
	const h = harness();
	try {
		const timeouts: (number | undefined)[] = [];
		const exec = h.api.exec.bind(h.api);
		h.api.exec = async (command, args, options) => {
			const [name] = repoCommand(command, args);
			if (name === "path-check") { timeouts.push(options.timeout); return { code: 1, killed: true, stdout: "", stderr: "" }; }
			return exec(command, args, options);
		};
		h.gate({ schemaVersion: 2 });
		h.dynaflows(true);
		await h.read(integrity, structure, naming);
		assert.ok((await h.call("edit", { path: "existing.ts" }))?.additionalContext?.includes("path check failed"));
		assert.deepEqual(timeouts, [20000]);
	} finally { h.cleanup(); }
});

test("child handoff requires its own verification read and repaired path checks", async () => {
	const h = harness();
	try {
		h.context.agent.kind = "sub";
		assert.equal(await h.emit("session_stop"), undefined);
		h.pathFailure("bad path");
		h.gate({ schemaVersion: 2, checks: { commit: ["not-run"], ci: ["not-run"] } });
		h.dynaflows(true);
		await h.read(integrity, structure, naming);
		await h.call("edit", { path: join(h.root, "existing.ts") });
		assert.equal((await h.emit("session_stop"))?.decision, "block");
		h.pathFailure();
		assert.ok((await h.emit("session_stop"))?.reason?.includes(verification));
		await h.read(verification);
		assert.equal(await h.emit("session_stop"), undefined);
	} finally { h.cleanup(); }
});

test("standalone hook environment changes and eval cannot bypass policy", async () => {
	const h = harness();
	try {
		await h.read("skill://git-tool");
		for (const command of ["HUSKY=0", "export SKIP=x", "declare -x LEFTHOOK=0", "unset HUSKY", "env -u HUSKY true", "eval 'export HUSKY=0'", "sh -c 'unset HUSKY'", "eval git commit -n"]) {
			assert.equal((await h.call("bash", { command }))?.block, true, command);
		}
		for (const command of ["echo 'export HUSKY=0'", "echo 'eval git commit -n'", "git commit -m 'export HUSKY=0'"]) {
			assert.equal(await h.call("bash", { command }), undefined, command);
		}
		h.gate({ schemaVersion: 2, agentMerge: "disabled" });
		for (const command of ["eval git push", "eval 'gh pr merge 3'", "gh -R owner/other pr merge 3"]) {
			assert.equal((await h.call("bash", { command }))?.block, true, command);
		}
	} finally { h.cleanup(); }
});

test("toggle asks ignore mentions without a verb and reject contradictions and nonrepositories", async () => {
	const h = harness();
	const scratch = realpathSync(mkdtempSync(join(tmpdir(), "rails-toggle-")));
	try {
		h.gate({ schemaVersion: 2 });
		h.dynaflows(true);
		const record = h.record();
		for (const prompt of ['"$dynaflows"', "$dynaflows status", "$dynaflows on\n$dynaflows off", "init $dynaflows\nstop $dynaflows",
			"$dynaflows enabled", "off $dynaflows", "$dynaflowson"]) {
			await h.emit("before_agent_start", { reason: "prompt", prompt });
			assert.deepEqual(h.record(), record, prompt);
		}
		// Prose around a directive makes it a sentence, which the agent judges instead.
		for (const prompt of ["please $dynaflows on", "please enable $dynaflows", "`$dynaflows init`", "$dynaflows on."]) {
			await h.emit("before_agent_start", { reason: "prompt", prompt });
			assert.deepEqual(h.record(), record, prompt);
		}
		await h.emit("before_agent_start", { reason: "prompt", prompt: "turn off $dynaflows" });
		assert.deepEqual(h.record().dynaflows, { enabled: false });
		assert.equal(existsSync(join(h.root, ".omp")), false, "the repo tree is never written");
		h.context.cwd = scratch;
		await h.emit("before_agent_start", { reason: "prompt", prompt: "$dynaflows" });
		assert.equal(existsSync(join(scratch, ".omp")), false);
		assert.ok(h.notes.some(note => note.includes("Git project")));
	} finally { h.cleanup(); rmSync(scratch, { recursive: true, force: true }); }
});

test("linked worktrees resolve the main checkout's record", async () => {
	const h = harness();
	const linked = `${h.root}-linked`;
	try {
		h.gate({ schemaVersion: 2 });
		await execute("git", ["-c", "user.name=Test", "-c", "user.email=test@example.test", "commit", "--allow-empty", "-m", "seed"], { cwd: h.root });
		await execute("git", ["worktree", "add", "-b", "agent/source", linked], { cwd: h.root });
		h.context.cwd = linked;
		await h.emit("before_agent_start", { reason: "prompt", prompt: "$dynaflows on" });
		assert.deepEqual(h.record().dynaflows, { enabled: true }, "the linked worktree toggles the shared record");
		assert.equal(existsSync(join(linked, ".omp")), false);
		const head = (await execute("git", ["rev-parse", "HEAD"], { cwd: linked })).stdout.trim();
		await h.read("skill://git-tool");
		const blocked = await h.call("bash", { command: `gh pr merge 3 --match-head-commit=${head}` });
		assert.ok(blocked?.reason?.includes(join(realpathSync(h.root), ".git", "omp-prove", `${head}.result.json`)),
			`the result file lives in the shared Git common directory: ${blocked?.reason}`);
	} finally { h.cleanup(); rmSync(linked, { recursive: true, force: true }); }
});

test("Python proof gates and rails accept the same schema fixtures", async () => {
	// Each case sits over a complete dynaflows-off record; a case's undefined value drops the field.
	const record = { checks: { commit: ["true"] }, ownerOnlyPaths: [], dynaflows: { enabled: false } };
	const cases: object[] = [
		{ schemaVersion: 2 }, { schemaVersion: 2, checks: {} }, { schemaVersion: 2, checks: undefined },
		{ schemaVersion: 2, checks: { commit: ["bun", "check"], ci: ["ci"], format: ["fmt"], profile: ["profile"] }, agentMerge: "allowed", ci: { requiredContext: "gate", runner: "ci-gate" } },
		{ schemaVersion: 2, ci: { requiredContext: "build", runner: "github-actions" } },
		{ schemaVersion: 2, unknownField: 3 }, { schemaVersion: 2, extraField: 1000 }, { schemaVersion: 2, checks: { commit: ["c"], lint: ["l"] } },
		{ schemaVersion: 2, otherField: "github" }, { schemaVersion: 2, otherField: null },
		{ schemaVersion: 2, ci: null }, { schemaVersion: 2, ci: {} }, { schemaVersion: 2, ci: { requiredContext: " ", runner: "ci-gate" } },
		{ schemaVersion: 2, ci: { requiredContext: "gate" } }, { schemaVersion: 2, ci: { requiredContext: "gate", runner: 1 } },
		{ schemaVersion: 2, checks: { format: [] } }, { schemaVersion: 2, checks: { profile: [" "] } }, { schemaVersion: 2, checks: { format: "fmt" } },
		{ schemaVersion: 2, checks: null }, { schemaVersion: 2, checks: { commit: null } },
		{ schemaVersion: 2, checks: { ci: [] } },
		{ schemaVersion: 2, agentMerge: "maybe" },
		{ schemaVersion: true }, { schemaVersion: 3 },
		{ schemaVersion: 2, dynaflows: { enabled: true } }, { schemaVersion: 2, dynaflows: null },
		{ schemaVersion: 2, dynaflows: {} }, { schemaVersion: 2, dynaflows: { enabled: "yes" } },
		{ schemaVersion: 2, ownerOnlyPaths: undefined }, { schemaVersion: 2, ownerOnlyPaths: null }, { schemaVersion: 2, ownerOnlyPaths: "a.json" },
		{ schemaVersion: 2, ownerOnlyPaths: ["scripts/checks/exceptions.json", "security/"] }, { schemaVersion: 2, ownerOnlyPaths: ["/abs.json"] },
		{ schemaVersion: 2, ownerOnlyPaths: ["a/./b"] }, { schemaVersion: 2, ownerOnlyPaths: ["a.json", "a.json"] }, { schemaVersion: 2, ownerOnlyPaths: [1] },
	].map(value => ({ ...record, ...value }));
	const h = harness();
	try {
		await h.read("skill://git-tool");
		const rails: boolean[] = [];
		for (const value of cases) {
			h.rawGate(JSON.stringify(value));
			rails.push(!(await h.call("bash", { command: "git push" }))?.block);
		}
		const modulePath = join(import.meta.dir, "../agent/skills/omp-dyna-workflows/templates/workflows.py");
		const script = "import importlib.util,json,sys; spec=importlib.util.spec_from_file_location('workflows',sys.argv[1]); module=importlib.util.module_from_spec(spec); sys.modules['workflows']=module; spec.loader.exec_module(module); print(json.dumps([module.gate_policy(v) is not None for v in json.loads(sys.argv[2])]))";
		const result = await execute("python3", ["-c", script, modulePath, JSON.stringify(cases)]);
		assert.deepEqual(JSON.parse(result.stdout), rails);
	} finally { h.cleanup(); }
});

test("mise commits run the record's checks in a real repository", async () => {
	const h = harness();
	try {
		await h.read("skill://git-tool");
		h.gate({ schemaVersion: 2, checks: { commit: ["true"] }, dynaflows: { enabled: true } });
		assert.equal(await h.emit("tool_call", { toolName: "bash", input: { command: "mise exec -- git commit -m x" } }), undefined);
		h.gate({ schemaVersion: 2, checks: { commit: ["false"] }, dynaflows: { enabled: true } });
		assert.ok((await h.emit("tool_call", { toolName: "bash", input: { command: "mise exec -- git commit -m x" } }))?.reason?.includes("checks.commit (false) failed"));
	} finally { h.cleanup(); }
});

test("tool-runner launchers cannot bypass commit hooks or publishing checks", async () => {
	const h = harness();
	try {
		await h.read("skill://git-tool");
		for (const command of [
			"mise exec -- git commit --no-verify -m x",
			"mise exec node@20 -- git commit --no-verify",
			"mise x -E test --jobs=2 -- git commit --no-verify",
			"pnpm exec git commit --no-verify", "npx git commit --no-verify",
			"bunx --package git git commit --no-verify", "pnpm dlx --package git git commit --no-verify",
			"uv run --python 3.12 git commit --no-verify", "poetry run git commit --no-verify",
		]) {
			const result = await h.emit("tool_call", { toolName: "bash", input: { command } });
			assert.equal(result?.block, true, command);
			assert.ok(result?.reason?.includes("commits must run the pre-commit hook"), command);
		}
		const remote = await h.call("bash", { command: "mise exec -- gh pr create --repo x/y" });
		assert.equal(remote?.block, true);
		assert.ok(remote?.reason?.includes("remote repository overrides"));
		const environment = await h.call("bash", { command: "LEFTHOOK=0 mise exec -- git commit -m x" });
		assert.equal(environment?.block, true);
		assert.ok(environment?.reason?.includes("hook environment mutation"));
		for (const command of ["mise exec node@20 git commit", "mise x git commit"]) {
			const result = await h.call("bash", { command });
			assert.equal(result?.block, true);
			assert.ok(result?.reason?.includes("requires --"));
		}
		for (const command of [
			"pnpm exec --filter web git commit --no-verify -m x",
			"uv run -p 3.12 git push", "uv run --env-file .env git push",
			"npx --yes --unknown value git commit --no-verify",
			"env FOO=x mise exec -- pnpm exec git commit --no-verify",
		]) {
			const result = await h.call("bash", { command });
			assert.equal(result?.block, true, command);
			assert.ok(result?.reason?.includes("wrapped command could not be parsed safely"), command);
		}
		assert.equal(await h.call("bash", { command: "pnpm exec eslint ." }), undefined);
	} finally { h.cleanup(); }
});

test("mise directory options resolve the wrapped commit repository", async () => {
	const h = harness();
	const target = harness({ configRoot: h.records, remote: "git@github.com:owner/target.git" });
	try {
		await h.read("skill://git-tool");
		target.gate({ schemaVersion: 2, checks: { commit: ["true"] } });
		target.gate({ schemaVersion: 2, checks: { commit: ["false"] }, dynaflows: { enabled: true } });
		for (const command of [
			`mise x -C '${target.root}' -- git commit -m x`,
			`mise exec --cd='${target.root}' node@20 -- git commit -m x`,
		]) {
			const enforced = await h.call("bash", { command });
			assert.ok(enforced?.reason?.includes(`checks.commit (false) failed in ${target.root}`), `the wrapped repo's record is enforced: ${command}`);
		}
		const unresolved = await h.call("bash", { command: "mise x -C /nonexistent-rails-directory -- git commit -m x" });
		assert.equal(unresolved?.block, true);
		assert.ok(unresolved?.reason?.includes("effective cwd"));
	} finally { h.cleanup(); target.cleanup(); }
});

test("command substitutions enforce hook checks without executing shell expansion", async () => {
	const h = harness();
	try {
		await h.read("skill://git-tool");
		for (const command of [
			"echo $(git commit --no-verify -m x)",
			"echo `git commit --no-verify -m x`",
			'echo "$(git commit --no-verify -m x)"',
			'echo "`git commit --no-verify -m x`"',
			"echo $(echo $(git commit --no-verify -m x))",
			'echo "$(echo "$(git commit --no-verify -m x)")"',
			"echo `echo \\`git commit --no-verify -m x\\``",
			"echo $(printf ')'; git commit --no-verify -m x)",
			"echo $(export HUSKY=0); git commit -m x",
		]) {
			const result = await h.call("bash", { command });
			assert.equal(result?.block, true, command);
			assert.ok(result?.reason?.includes("pre-commit hook"), command);
		}
		for (const command of [
			"echo $(date)", "echo `date`", 'echo "$(date)"',
			"echo '$(git commit --no-verify -m x)'",
			"echo '`git commit --no-verify -m x`'",
			"echo \\$(date)", "echo \\`date\\`",
		]) assert.equal(await h.call("bash", { command }), undefined, command);
	} finally { h.cleanup(); }
});

test("persistent aliases and functions containing git or gh fail closed", async () => {
	const h = harness();
	try {
		await h.read("skill://git-tool");
		for (const command of [
			"alias ci=$(printf git)",
			"alias ci='git commit -m x'", "alias review='gh pr view'",
			"alias -p ci='git status'", "alias ll='ls -l' ci='git status'",
			"ci() { git commit -m x; }", "ci(){ git status; }",
			"ci()\n{ git status; }", "if true; then ci() { git status; }; fi",
			"function ci { gh pr view; }", "function ci() { git status; }",
			"function ci {\nif true; then { echo git; }; fi\n}",
			"ci() { echo $(git status); }",
			'ci() { g"it" status; }', "alias ci='g\"it\" status'",
		]) {
			const result = await h.call("bash", { command });
			assert.equal(result?.block, true, command);
			assert.ok(result?.reason?.includes("definition contains a git/gh word"), command);
			assert.ok(result?.reason?.includes("cannot be parsed safely"), command);
		}
		for (const command of [
			"alias ll='ls -l'", "ll() { ls -l; }", "function ll { ls -l; }",
			"function ll() { echo $(date); }", "ll() { cd /missing; }; git commit -m x",
			"echo 'alias ci=git'", "echo 'ci() { git status; }'",
		]) assert.equal(await h.call("bash", { command }), undefined, command);
	} finally { h.cleanup(); }
});

test("uv and poetry separators disambiguate unknown launcher options without consuming Git arguments", async () => {
	const h = harness();
	try {
		await h.read("skill://git-tool");
		for (const launcher of ["uv run", "poetry run"]) {
			for (const args of [
				"--some-unknown-flag value -- git commit --no-verify",
				"-- git commit --no-verify", "git commit --no-verify -- file",
			]) {
				const command = `${launcher} ${args}`;
				const result = await h.call("bash", { command });
				assert.equal(result?.block, true, command);
				assert.ok(result?.reason?.includes("commits must run the pre-commit hook"), command);
			}
			assert.equal(await h.call("bash", { command: `${launcher} -- date` }), undefined);
		}
	} finally { h.cleanup(); }
});

test("uv and poetry separator commits pass the tool-call handler", async () => {
	for (const launcher of ["uv run", "poetry run"]) {
		const h = harness();
		try {
			await h.read("skill://git-tool");
			assert.equal(await h.emit("tool_call", {
				toolName: "bash", input: { command: `${launcher} -- git commit -m x` },
			}), undefined, launcher);
		} finally { h.cleanup(); }
	}
});

test("an invalid agent-rails.yml mapping fails the load", async () => {
	const source = readFileSync(railsConfig, "utf8");
	assert.equal(/merge\|rebase|cherry-pick|revert\|am\|pull/.test(source.match(/^gitCommand: .*$/m)?.[0] ?? ""), false,
		"pulling commits in is not a gated git command");
	const dir = mkdtempSync(join(tmpdir(), "agent-rails-config-"));
	try {
		const configPath = join(dir, "agent-rails.yml");
		writeFileSync(configPath, source.replace(/^gitCommand: .*$/m, "gitCommand: '('"));
		const h = harness({ configPath });
		try {
			await h.emit("session_start");
			assert.ok(h.notes.some(note => note.includes("cannot load agent-rails.yml")), h.notes.join("; "));
		} finally { h.cleanup(); }
	} finally { rmSync(dir, { recursive: true, force: true }); }
});

for (const record of ["absent", "off", "on"] as const) {
	test(`repo-scoped harness process follows dynaflows (${record}) while Git reads always apply`, async () => {
		const h = harness();
		try {
			if (record !== "absent") h.dynaflows(record === "on");
			h.pathFailure("path policy violation");
			const edit = await h.call("write", { path: "tests/new.test.ts" });
			if (record === "on") {
				assert.equal(edit?.block, true);
				for (const url of [integrity, structure, naming, tiers]) assert.ok(edit?.reason?.includes(url));
				await h.read(integrity, structure, naming, tiers);
				writeFileSync(join(h.root, "existing.ts"), "export const answer = 43;\n");
				assert.ok((await h.call("edit", { path: "existing.ts" }))?.additionalContext?.includes("path policy violation"));
				assert.equal((await h.emit("session_stop"))?.decision, "block");
				h.pathFailure();
				assert.ok((await h.emit("session_stop"))?.reason?.includes(verification));
				await h.read(verification);
			} else {
				assert.equal(edit, undefined);
				assert.equal(await h.call("edit", { path: "existing.ts" }), undefined);
			}
			assert.equal(await h.emit("session_stop"), undefined);
			const git = await h.call("bash", { command: "git push" });
			assert.equal(git?.block, true);
			assert.ok(git?.reason?.includes("skill://git-tool"));
		} finally { h.cleanup(); }
	});
}

test("edit and handoff policy follows each edited repo, not the session cwd", async () => {
	const h = harness();
	const other = harness({ configRoot: h.records, remote: "git@github.com:owner/other.git" });
	try {
		h.dynaflows(true);
		other.dynaflows(false);
		h.pathFailure("enabled repo violation");
		assert.equal(await h.call("edit", { path: join(other.root, "existing.ts") }), undefined);
		assert.equal(await h.emit("session_stop"), undefined, "editing only an off repo does not require a handoff read");
		assert.ok((await h.call("edit", { path: join(h.root, "existing.ts") }))?.reason?.includes(integrity));
		await h.read(integrity, structure, naming);
		assert.ok((await h.call("edit", { path: join(h.root, "existing.ts") }))?.additionalContext?.includes("enabled repo violation"));
		assert.equal((await h.emit("session_stop"))?.decision, "block");
		h.dynaflows(false);
		assert.equal(await h.emit("session_stop"), undefined, "turning off suppresses both outstanding retries and handoff reads");
		assert.equal(await h.call("edit", { path: join(h.root, "existing.ts") }), undefined);
	} finally { h.cleanup(); other.cleanup(); }
});
