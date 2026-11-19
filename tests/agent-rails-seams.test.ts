import assert from "node:assert/strict";
import { execFile, execFileSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, renameSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { dirname, join, relative, resolve } from "node:path";
import { test } from "bun:test";
import { Settings, z, type ExtensionContext } from "@oh-my-pi/pi-coding-agent";
import agentRails from "../agent/extensions/agent-rails";
import dynaflowsExtension from "../agent/extensions/dynaflows/index";
import { parseOrigin } from "../agent/lib/repo-record.ts";

const integrity = "skill://dynaflows/references/delivery-principles.md";
const structure = "skill://dynaflows/references/structure-and-naming.md";
const enforcement = "skill://dynaflows/references/test-enforcement.md";
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
	type: string; cwd?: string; toolName?: string; toolCallId?: string; input?: Input;
	details?: Details; isError?: boolean; reason?: string;
	agent?: string; patterns?: string[]; modelRole?: string; invocationKind?: string; prompt?: string;
};
type Result = { block?: boolean; reason?: string; decision?: string; additionalContext?: string; message?: object; model?: string[] };
type Handler = Function;
type Tool = { name: string; execute(id: string, params: { enabled: boolean }, signal: undefined, onUpdate: undefined, ctx: ExtensionContext): Promise<{ content: { text: string }[] }> };
const execFailureSchema = z.object({
	code: z.number().optional().catch(undefined),
	killed: z.boolean().optional(),
	stdout: z.string().optional(),
	stderr: z.string().optional(),
});
type ExecuteOptions = { cwd?: string; timeout?: number };
function execute(command: string, args: string[], options: ExecuteOptions = {}): Promise<{ stdout: string; stderr: string }> {
	return new Promise((done, fail) => {
		execFile(command, args, { ...options, encoding: "utf8" }, (error, stdout, stderr) => {
			if (error) fail(Object.assign(error, { stdout, stderr }));
			else done({ stdout, stderr });
		});
	});
}

const wrappedPublishing = [
	"sudo git push", "sudo -u root git push", "env git push", "env -i git push", "env FOO=x git push",
	"time git push", "command git push", "nohup git push", "nice -n 5 git push", "exec git push",
	"xargs git push", "xargs -n 1 git push", "timeout 5 git push", "/usr/bin/git push",
	"if x; then git push; fi", "{ git push; }", "true; do git push; done", "false; else git push",
	"! git push", "bash -c 'git push'", 'sh -c "git push"', "zsh -lc 'git push'",
	"sudo env -i sh -c 'git push'",
	"env -S 'git push'", "env --split-string='git push'", "env -S git push",
];

const railsConfig = join(import.meta.dir, "../agent/rails.yml");
const defaultOrigin = "git@github.com:owner/repo.git";
// A record's gate.json as tests read it back: the switch typed, every other field kept.
const recordGateSchema = z.object({ dynaflows: z.object({ enabled: z.boolean() }) }).passthrough();

// Rails run every repo command as `env -u AGENT -- <argv>`; this returns <argv> and fails otherwise.
function repoCommand(command: string, args: string[]): [string, string[]] {
	if (command === "env") {
		assert.deepEqual(args.slice(0, 3), ["-u", "AGENT", "--"], "repo commands run without the harness variables");
		const [inner, ...rest] = args.slice(3);
		assert.ok(inner, "env runs a command");
		[command, args] = [inner, rest];
	}
	if (command === "uv" && args[4] === "path-check") {
		assert.deepEqual(args.slice(0, 6), ["run", "--project",
			resolve(import.meta.dir, "../agent/extensions/dynaflows/engine"), "guardrails", "path-check", "--repo"]);
		assert.equal(args[7], "--record");
		assert.match(args[8] ?? "", /\/repos\/[^/]+\/[^/]+$/);
		return ["path-check", args.slice(9)];
	}
	return [command, args];
}

// A temp repo whose origin names a per-repo record under a temp config root (never the real one).
// `configRoot` shares another harness's records; `guardrailsCommand: "default"` keeps the real
// guardrails invocation, otherwise the harness checks are `true`.
type ExtensionOptions = NonNullable<Parameters<typeof agentRails>[1]> & NonNullable<Parameters<typeof dynaflowsExtension>[1]>;
type CommandChoices = { configPath: string | undefined; ompCommand: string[] | undefined; guardrailsCommand: string[] | "default" };

function extensionOptions(machine: string, records: string, choices: CommandChoices): ExtensionOptions {
	const options: ExtensionOptions = {
		machineDynaflowsPath: join(machine, "dynaflows.json"), configRoot: records,
		ompCommand: choices.ompCommand ?? ["sh", "-c", 'cat "$1"', "omp", join(machine, "worktree-base")],
	};
	if (choices.configPath !== undefined) options.configPath = choices.configPath;
	if (choices.guardrailsCommand !== "default") options.guardrailsCommand = choices.guardrailsCommand;
	return options;
}

function harness({ configPath, configRoot, remote = defaultOrigin, guardrailsCommand = ["true"], ompCommand, extensions = "generic" }: {
	extensions?: "generic" | "dynaflows" | "both";
	configPath?: string; configRoot?: string; remote?: string; guardrailsCommand?: string[] | "default"; ompCommand?: string[];
} = {}) {
	const root = realpathSync(mkdtempSync(join(tmpdir(), "agent-rails-")));
	const machine = mkdtempSync(join(tmpdir(), "agent-rails-machine-"));
	const ownedRecords = configRoot === undefined;
	const records = configRoot ?? realpathSync(mkdtempSync(join(tmpdir(), "agent-rails-records-")));
	execFileSync("git", ["init", "-b", "agent/test", root]);
	execFileSync("git", ["remote", "add", "origin", remote], { cwd: root });
	writeFileSync(join(root, "existing.ts"), "export const answer = 42;\n");
	const handlers = new Map<string, Handler[]>();
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
	};
	// SAFETY: this fixture supplies every context surface consumed by the registered policies.
	const extensionContext = context as ExtensionContext;
	const api = {
		pi: { settings: Settings.isolated() },
		on(name: string, handler: Handler) {
			const registered = handlers.get(name) ?? [];
			registered.push(handler);
			handlers.set(name, registered);
		},
		registerCommand() {},
		registerTool(tool: Tool) { tools.set(tool.name, tool); },
		async exec(command: string, args: string[], options: { cwd: string; timeout?: number }) {
			const [name, paths] = repoCommand(command, args);
			if (name === "path-check") {
				assert.equal(command, "env");
				assert.deepEqual(args, ["-u", "AGENT", "--", "uv", "run", "--project",
					resolve(import.meta.dir, "../agent/extensions/dynaflows/engine"),
					"guardrails", "path-check", "--repo", options.cwd, "--record", recordDir(options.cwd), ...paths]);
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
	const options = extensionOptions(machine, records, { configPath, ompCommand, guardrailsCommand });
	writeFileSync(join(machine, "worktree-base"), "(not set)\n");
	// SAFETY: this fixture supplies the API surfaces consumed by both extension registrations.
	const extensionApi = api as Parameters<typeof dynaflowsExtension>[0] & Parameters<typeof agentRails>[0];
	if (extensions !== "dynaflows") agentRails(extensionApi, options);
	if (extensions !== "generic") dynaflowsExtension(extensionApi, options);
	// The record's gate.json: the fixture over a dynaflows-off default; `dynaflows()` wins.
	let fixture: GateFixture | undefined;
	let enabled: boolean | undefined;
	let slug = parseOrigin(remote);
	const recordDir = (cwd = root) => {
		const currentSlug = cwd === root ? slug : parseOrigin(execFileSync(
			"git", ["remote", "get-url", "origin"], { cwd, encoding: "utf8", timeout: 20000 },
		).trim());
		assert.ok(currentSlug, "the harness origin names a record");
		return join(records, "repos", currentSlug);
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
		let combined: Result | undefined;
		for (const handler of handlers.get(name) ?? []) {
			const result: Result | void = await handler({ type: name, ...event }, extensionContext);
			if (result?.block || result?.decision === "block") return result;
			if (result) combined = mergeResults(combined, result);
		}
		return combined;
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
			return (await tool.execute("tool-1", params, undefined, undefined, extensionContext)).content.map(part => part.text).join("");
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

type Harness = ReturnType<typeof harness>;


async function checkDestinationRepository(h: Harness, other: Harness, action: string): Promise<void> {
	h.gate({ schemaVersion: 2 });
	for (const prefix of [`cd '${other.root}'`, `pushd '${other.root}'`, `cd '${relative(h.root, other.root)}' && cd .`]) {
		assert.equal(await bashBlocked(h, `${prefix} && ${action}`), true, prefix);
	}
	for (const launcher of ["env -C", "sudo -D"]) {
		assert.equal(await bashBlocked(h, `${launcher} '${other.root}' ${action}`), true, launcher);
	}
	h.gate({ schemaVersion: 2, agentMerge: "disabled" });
	other.gate({ schemaVersion: 2 });
	assert.equal(await h.call("bash", { command: `cd '${other.root}' && ${action}` }), undefined);
	for (const launcher of ["env -C", "sudo -D"]) {
		assert.equal(await h.call("bash", { command: `${launcher} '${other.root}' ${action}` }), undefined, launcher);
	}
	other.gate({ schemaVersion: 2, agentMerge: "disabled" });
	for (const prefix of ["cd", "cd -", "cd missing-dir", 'cd "$TARGET"']) {
		assertBlocked(await h.call("bash", { command: `${prefix} && ${action}` }), "effective cwd", prefix);
	}
}

function reasonOf(result: Result | undefined): string {
	return result?.reason ?? "";
}

function isBlocked(result: Result | undefined): boolean {
	return result?.block === true;
}

function mergeResults(combined: Result | undefined, result: Result): Result {
	const merged = { ...combined, ...result };
	const previousContext = combined?.additionalContext;
	if (previousContext && result.additionalContext) merged.additionalContext = `${previousContext}\n${result.additionalContext}`;
	return merged;
}

function assertBlocked(result: Result | undefined, fragment: string, label?: string): string {
	assert.equal(isBlocked(result), true, label ?? fragment);
	assert.ok(reasonOf(result).includes(fragment), String(label ?? result?.reason));
	return result?.reason ?? "";
}

async function bashReason(h: Harness, command: string): Promise<string> {
	return (await h.call("bash", { command }))?.reason ?? "";
}

async function bashBlocked(h: Harness, command: string): Promise<boolean> {
	return (await h.call("bash", { command }))?.block === true;
}

function restoreEnvironment(name: string, previous: string | undefined): void {
	if (previous === undefined) delete process.env[name];
	else process.env[name] = previous;
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
type PrView = { head?: string; selector?: string; failure?: string; url?: string };

function mockPrCi(h: PrCiHarness, rollup: object[], view: PrView = {}) {
	const { head = ciHead, selector, failure, url = "https://github.com/owner/repo/pull/3" } = view;
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
const ciRunner = resolve(import.meta.dir, "../scripts/ci_runner.py");
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

export { harness, execute, reasonOf, isBlocked, wrappedPublishing, railsConfig, defaultOrigin, integrity, structure, enforcement, tiers, verification, ciHead, greenCi, mockPrCi, forbidGh, ciGate, ciRunner, originRemote, proveResult, structureResult };
export { repoCommand };
export type { Input };

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
				assertBlocked(await h.call("bash", { command }), "skill://git-tool", command);
			}
		} finally { h.cleanup(); }
	});

	test("worktree creation uses the configured base and lowercase origin name", async () => {
		const h = harness({ remote: "git@github.com:Owner/RePo.git" });
		const previous = process.env["OMP_WORKTREE_DIR"];
		delete process.env["OMP_WORKTREE_DIR"];
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
				assert.equal(isBlocked(blocked), true, target);
				assert.equal(blocked?.reason, `Agent rails: create worktrees at ${base}/repo/<slug>; got ${resolve(h.root, target)}.`);
			}
			for (const target of ["$HOME/worktrees/repo/x", "`pwd`/x"]) {
				assert.match(await bashReason(h, `git worktree add '${target}'`), /unexpanded shell text/);
			}
			h.worktreeBase("(not set)");
			assert.equal((await h.call("bash", { command: `git worktree add '${base}/repo/x'` }))?.reason,
				"Agent rails: worktree.base is unset; set it (agent/config.yml) before git worktree add.");
			h.worktreeBase(base);
			await execute("git", ["remote", "remove", "origin"], { cwd: h.root });
			assert.match(await bashReason(h, `git worktree add '${base}/repo/x'`), /no origin remote/);
			await execute("git", ["remote", "add", "origin", "https://example.com/owner/repo.git"], { cwd: h.root });
			assert.match(await bashReason(h, `git worktree add '${base}/repo/x'`), /not a GitHub/);
		} finally {
			restoreEnvironment("OMP_WORKTREE_DIR", previous);
			h.cleanup();
		}
	});

	test("worktree environment base overrides the setting and invalid env defers to it", async () => {
		const h = harness();
		const previous = process.env["OMP_WORKTREE_DIR"];
		try {
			await h.read("skill://git-tool");
			h.worktreeBase(join(h.root, "setting"));
			process.env["OMP_WORKTREE_DIR"] = join(h.root, "override");
			assert.equal(await h.call("bash", { command: `git worktree add '${h.root}/override/repo/x'` }), undefined);
			assert.equal(await bashBlocked(h, `git worktree add '${h.root}/setting/repo/x'`), true);
			process.env["OMP_WORKTREE_DIR"] = "relative";
			assert.equal(await h.call("bash", { command: `git worktree add '${h.root}/setting/repo/x'` }), undefined);
		} finally {
			restoreEnvironment("OMP_WORKTREE_DIR", previous);
			h.cleanup();
		}
	});

	test("worktree paths canonicalize through the filesystem's own spelling of the base", async () => {
		const h = harness();
		const previous = process.env["OMP_WORKTREE_DIR"];
		delete process.env["OMP_WORKTREE_DIR"];
		try {
			const base = join(h.root, "Worktrees");
			mkdirSync(join(base, "repo"), { recursive: true });
			symlinkSync(base, join(h.root, "alias"));
			await h.read("skill://git-tool");
			h.worktreeBase(base);
			assert.equal(await h.call("bash", { command: `git worktree add '${h.root}/alias/repo/x'` }), undefined);
		} finally {
			restoreEnvironment("OMP_WORKTREE_DIR", previous);
			h.cleanup();
		}
	});

	test("worktree setting command errors fail closed", async () => {
		const h = harness({ ompCommand: ["sh", "-c", "exit 7"] });
		const previous = process.env["OMP_WORKTREE_DIR"];
		delete process.env["OMP_WORKTREE_DIR"];
		try {
			await h.read("skill://git-tool");
			assert.match(await bashReason(h, `git worktree add '${h.root}/repo/x'`),
				/policy could not be evaluated.*cannot read worktree.base/);
		} finally {
			restoreEnvironment("OMP_WORKTREE_DIR", previous);
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
				assertBlocked(await h.call("bash", { command }), "ask the owner", command);
			}
			assert.equal(await h.call("bash", { command: "git commit -m fix" }), undefined);
			assert.equal(await h.call("bash", { command: `git -C '${other.root}' push` }), undefined);
			other.gate({ schemaVersion: 2, agentMerge: "disabled" });
			assert.equal(await bashBlocked(h, `git -C '${other.root}' push`), true);
			// cwd-changing commands must select the destination repo, not the session repo.
			for (const action of ["git push", "gh pr create", "gh pr merge 7"]) await checkDestinationRepository(h, other, action);
			for (const invalid of ["{broken", '{"schemaVersion":3}', '{"schemaVersion":2,"checks":{"commit":"bun check"}}', '{"schemaVersion":2,"otherField":"github"}', '{"schemaVersion":2,"ci":{"requiredContext":"gate"}}', '{"schemaVersion":2,"dynaflows":{"enabled":"yes"}}']) {
				h.rawGate(invalid);
				for (const command of commands) {
					const reason = assertBlocked(await h.call("bash", { command }), "gate.json", command);
					assert.ok(reason.includes("Error"));
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
				assertBlocked(await h.call("bash", { command }), "pre-commit hook", command);
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


	test("requires absolute subagent paths across mapped writes and edit payloads", async () => {
		const h = harness();
		try {
			h.context.agent.kind = "sub";
			await h.read(integrity, structure);
			const calls: [string, Input][] = [
				["write", { path: "new.ts" }],
				["edit", { paths: [join(h.root, "existing.ts"), "../other.ts"] }],
				["ast_edit", { paths: ["./existing.ts"] }],
				["edit", { input: "*** Begin Patch\n[existing.ts#ABCD]\nREM\n*** End Patch\n" }],
				["edit", { input: `*** Begin Patch\n[${join(h.root, "existing.ts")}#ABCD]\nMV moved.ts\n*** End Patch\n` }],
			];
			for (const [tool, input] of calls) {
				const result = await h.call(tool, input);
				assert.equal(isBlocked(result), true, `${tool}: ${JSON.stringify(input)}`);
				assert.ok(reasonOf(result).includes("absolute path inside your assigned worktree"));
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
			await h.read(integrity, structure);
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
					const reason = assertBlocked(await h.call(tool, tool === "write" ? { path } : { paths: [path] }), path, `${tool}: ${suffix}`);
					assert.ok(reason.includes(realpathSync(outside)));
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
			assert.ok(reasonOf(blocked).includes("drop hook bypass"), `${command}: ${blocked?.reason}`);
		}
		assert.ok((await h.call("bash", { command: "HUSKY=0 git merge x" }))?.reason?.includes("hook environment mutation"));
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
			assertBlocked(await h.emit("tool_call", { toolName: "bash", input: { command } }), "commits must run the pre-commit hook", command);
		}
		assertBlocked(await h.call("bash", { command: "mise exec -- gh pr create --repo x/y" }), "remote repository overrides");
		assertBlocked(await h.call("bash", { command: "LEFTHOOK=0 mise exec -- git commit -m x" }), "hook environment mutation");
		for (const command of ["mise exec node@20 git commit", "mise x git commit"]) {
			assertBlocked(await h.call("bash", { command }), "requires --");
		}
		for (const command of [
			"pnpm exec --filter web git commit --no-verify -m x",
			"uv run -p 3.12 git push", "uv run --env-file .env git push",
			"npx --yes --unknown value git commit --no-verify",
			"env FOO=x mise exec -- pnpm exec git commit --no-verify",
		]) {
			assertBlocked(await h.call("bash", { command }), "wrapped command could not be parsed safely", command);
		}
		assert.equal(await h.call("bash", { command: "pnpm exec eslint ." }), undefined);
	} finally { h.cleanup(); }
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
			assertBlocked(await h.call("bash", { command }), "pre-commit hook", command);
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
			const reason = assertBlocked(await h.call("bash", { command }), "definition contains a git/gh word", command);
			assert.ok(reason.includes("cannot be parsed safely"), command);
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
				assertBlocked(await h.call("bash", { command }), "commits must run the pre-commit hook", command);
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

test("an invalid rails.yml mapping fails the load", async () => {
	const source = readFileSync(railsConfig, "utf8");
	assert.equal(/merge\|rebase|cherry-pick|revert\|am\|pull/.test(source.match(/^gitCommand: .*$/m)?.[0] ?? ""), false,
		"pulling commits in is not a gated git command");
	const dir = mkdtempSync(join(tmpdir(), "agent-rails-config-"));
	try {
		const configPath = join(dir, "rails.yml");
		writeFileSync(configPath, source.replace(/^gitCommand: .*$/m, "gitCommand: '('"));
		const h = harness({ configPath });
		try {
			await h.emit("session_start");
			assert.ok(h.notes.some(note => note.includes("cannot load rails.yml")), h.notes.join("; "));
		} finally { h.cleanup(); }
	} finally { rmSync(dir, { recursive: true, force: true }); }
});

test("generic rails leave workflow process untouched without the package", async () => {
	const h = harness();
	try {
		h.dynaflows(true);
		h.gate({ schemaVersion: 2, checks: { commit: ["false"] } });
		h.pathFailure("must not run");
		await h.emit("session_start");
		assert.deepEqual(h.notes, []);
		assert.equal(await h.call("write", { path: "tests/new.test.ts" }), undefined);
		assert.equal(await h.emit("session_stop"), undefined);
		assert.equal(await h.emit("before_agent_start", { reason: "prompt", prompt: "$dynaflows off" }), undefined);
		assert.equal(h.record().dynaflows.enabled, true);
		assert.ok((await h.call("bash", { command: "git commit -m x" }))?.reason?.includes("skill://git-tool"));
		await h.read("skill://git-tool");
		assert.equal(await h.call("bash", { command: "git commit -m x" }), undefined);
		assert.deepEqual(h.notes, []);
	} finally { h.cleanup(); }
});
