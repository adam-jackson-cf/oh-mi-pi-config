import assert from "node:assert/strict";
import { execFile, execFileSync } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, renameSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { dirname, join, relative, resolve } from "node:path";
import { describe, test } from "node:test";
import { promisify } from "node:util";
import { z, type ExtensionAPI, type ExtensionContext } from "@oh-my-pi/pi-coding-agent";
import agentRails from "../agent/extensions/agent-rails";
import { parseOrigin } from "../agent/lib/repo-record.ts";

const integrity = "skill://coding-guidance/references/implementation-integrity.md";
const structure = "skill://coding-guidance/references/structure-and-grouping.md";
const naming = "skill://coding-guidance/references/naming.md";
const tiers = "skill://test-guidance/references/tiers.md";
const verification = "skill://coding-guidance/references/verification.md";
type GateFixture = {
	schemaVersion: 2; checks?: { commit?: string[]; ci?: string[]; format?: string[]; profile?: string[] }; testGlobs?: string[];
	pathCheck?: string[] | string; pathCheckTimeoutMs?: number; agentMerge?: "allowed" | "disabled";
	ci?: { requiredContext: string; runner: string }; dynaflowsContract?: number; dynaflows?: { enabled: boolean };
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
const currentContract = z.object({ dynaflowsContract: z.number().int().positive() })
	.parse(Bun.YAML.parse(readFileSync(railsConfig, "utf8"))).dynaflowsContract;
const defaultOrigin = "git@github.com:owner/repo.git";
// A record's gate.json as tests read it back: the switch typed, every other field kept.
const recordGateSchema = z.object({ dynaflows: z.object({ enabled: z.boolean() }) }).passthrough();

// Rails run every repo command as `env -u AGENT -- <argv>`; this returns <argv> and fails otherwise.
function repoCommand(command: string, args: string[]): [string, string[]] {
	if (command !== "env") return [command, args];
	assert.deepEqual(args.slice(0, 3), ["-u", "AGENT", "--"], "repo commands run without the harness variables");
	return [args[3], args.slice(4)];
}

// A temp repo whose origin names a per-repo record under a temp config root (never the real one).
// `configRoot` shares another harness's records; `guardrailsCommand: "default"` keeps the real
// omp-guardrails invocation, otherwise the harness checks are `true`.
function harness({ configPath, configRoot, remote = defaultOrigin, guardrailsCommand = ["true"] }: {
	configPath?: string; configRoot?: string; remote?: string; guardrailsCommand?: string[] | "default";
} = {}) {
	const root = realpathSync(mkdtempSync(join(tmpdir(), "agent-rails-")));
	const machine = mkdtempSync(join(tmpdir(), "agent-rails-machine-"));
	const ownedRecords = configRoot === undefined;
	const records = configRoot ?? realpathSync(mkdtempSync(join(tmpdir(), "agent-rails-records-")));
	execFileSync("git", ["init", "-b", "agent/test", root]);
	execFileSync("git", ["remote", "add", "origin", remote], { cwd: root });
	writeFileSync(join(root, "existing.ts"), "export const answer = 42;\n");
	const handlers = new Map<string, Handler>();
	const notes: string[] = [];
	const branch: object[] = [];
	let sessionId = "first";
	const identity: ExtensionContext["agent"] = { kind: "main", id: root, name: "main", depth: 0 };
	const context = {
		cwd: root,
		agent: identity,
		ui: { notify(message: string) { notes.push(message); } },
		sessionManager: { getSessionId: () => sessionId, getBranch: () => branch },
		models: {
			current: () => ({ provider: "openai", id: "gpt" }),
			resolve: (pattern: string) => {
				if (pattern === "@verifier") return { provider: "anthropic", id: "claude" };
				const [provider, id] = pattern.split("/");
				return id ? { provider, id } : undefined;
			},
			family: (model: { provider: string }) => model.provider,
		},
	};
	const api = {
		on(name: string, handler: Handler) { handlers.set(name, handler); },
		registerCommand() {},
		async exec(command: string, args: string[], options: { cwd: string; timeout?: number }) {
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
	if (guardrailsCommand !== "default") options.guardrailsCommand = guardrailsCommand;
	// SAFETY: this fake supplies on, registerCommand and exec, the only API surfaces agentRails consumes.
	agentRails(api as ExtensionAPI, options);
	// The record's gate.json: the fixture over a current-contract, dynaflows-off default; `dynaflows()` wins.
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
		const gate = { dynaflowsContract: currentContract, dynaflows: { enabled: false }, ...(fixture ?? { schemaVersion: 2 }) };
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
		root, notes, branch, context, emit, api, options, records, recordDir,
		cleanup() { for (const dir of [root, machine, ...(ownedRecords ? [records] : [])]) rmSync(dir, { recursive: true, force: true }); },
		setSession(value: string) { sessionId = value; },
		gate(value: GateFixture) { fixture = value; writeGate(); },
		dynaflows(value: boolean) { enabled = value; writeGate(); },
		// Raw gate.json text, for invalid and contract fixtures.
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

// Fakes `gh pr view` and `gh pr list --search <sha>` (status posts); every other command runs for real.
function mockPrCi(h: PrCiHarness, rollup: object[], head = ciHead, selector?: string, failure?: string,
	url = "https://github.com/owner/repo/pull/3", listed: object[] = [{ number: 3, headRefOid: head }]) {
	const exec = h.api.exec.bind(h.api);
	h.api.exec = async (command, args, options) => {
		if (command !== "gh") return exec(command, args, options);
		assert.equal(options.cwd, h.root);
		assert.equal(options.timeout, 20000);
		if (args[1] === "list") {
			assert.match(args[3], /^[0-9a-f]{40}$/);
			assert.deepEqual(args, ["pr", "list", "--search", args[3], "--state", "open", "--json", "number,headRefOid"]);
			return { code: 0, killed: false, stdout: JSON.stringify(listed), stderr: "" };
		}
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

const verified = { __typename: "StatusContext", context: "agent-verified", state: "SUCCESS" };
const ciGate = { requiredContext: "gate", runner: "ci-gate" };
const ciRunner = resolve(import.meta.dir, "../scripts/ci-runner.py");
const statusPost = (sha = ciHead, repo = "owner/repo") => `gh api repos/${repo}/statuses/${sha} -f state=success -f context=agent-verified -f description='prove() pass'`;
const statusOnly = "Agent rails: agent-verified is a SHA-bound commit status; prove() posts it (non-dynaflows: gh api repos/OWNER/REPO/statuses/SHA -f state=success -f context=agent-verified)";
async function originRemote(h: { origin(url: string): Promise<void> }, remote = defaultOrigin) {
	await h.origin(remote);
}

function passMarker(h: { root: string }, head = ciHead) {
	mkdirSync(join(h.root, ".git/omp-prove"), { recursive: true });
	writeFileSync(join(h.root, ".git/omp-prove", `${head}.json`),
		JSON.stringify({ schemaVersion: 1, head, kind: "prove", status: "pass" }));
}

describe("agent rails", () => {
	test("blocks new code and test files until every mapped reference is successfully read", async () => {
		const h = harness();
		try {
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

	test("enforces first edit for existing code and honours repo-specific test placement", async () => {
		const h = harness();
		try {
			h.gate({ schemaVersion: 2, testGlobs: ["checks/**"] });
			assert.ok((await h.call("edit", { path: "existing.ts" }))?.reason?.includes(integrity));
			await h.read(integrity, structure, naming);
			assert.equal(await h.call("edit", { path: "existing.ts" }), undefined);
			assert.ok((await h.call("write", { path: "checks/contract.ts" }))?.reason?.includes(tiers));
			await h.read(tiers);
			assert.equal(await h.call("write", { path: "checks/contract.ts" }), undefined);
		} finally { h.cleanup(); }
	});

	test("runs pathCheck with all changed paths, surfacing actual command failures", async () => {
		const h = harness();
		try {
			const checker = join(h.root, "check.cjs");
			writeFileSync(checker, "console.error(process.argv.slice(2).join('\\n') + ': use feature ownership'); process.exit(1);");
			h.gate({ schemaVersion: 2, pathCheck: [process.execPath, checker] });
			writeFileSync(join(h.root, "another.ts"), "export {};\n");
			const result = await h.call("edit", { paths: ["existing.ts", "another.ts"] });
			assert.ok(result?.additionalContext?.includes(join(h.root, "existing.ts")));
			assert.ok(result?.additionalContext?.includes(join(h.root, "another.ts")));
			assert.ok(result?.additionalContext?.includes("use feature ownership"));
			assert.equal(await h.call("edit", { path: "existing.ts" }, {}, true), undefined);
		} finally { h.cleanup(); }
	});

	test("absent and invalid records fall back to reference enforcement without throwing", async () => {
		const h = harness();
		try {
			assert.equal((await h.call("edit", { path: "existing.ts" }))?.block, true);
			assert.deepEqual(h.notes, [], "a repo without a record is normal, not noteworthy");
			h.rawGate("{broken");
			assert.equal((await h.call("edit", { path: "existing.ts" }))?.block, true);
			assert.ok(h.notes.some(note => note.includes(join(h.recordDir(), "gate.json"))));
			h.gate({ schemaVersion: 2, pathCheck: "not an argv array" });
			assert.equal((await h.call("edit", { path: "existing.ts" }))?.block, true);
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
				await h.read(integrity);
				await h.call("edit", { path: "existing.ts" });
				const blocked = await h.emit("session_stop");
				assert.equal(blocked?.decision, "block");
				assert.ok(blocked?.reason?.includes(verification));
				assert.ok(!blocked?.reason?.includes("bun"), "no repository check is demanded at handoff");
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
				"git -C /w status", "git -c k=v log", "gh pr view",
				"command -v git push", "sudo git status", "env -i git diff", 'sh -c "git log --grep=commit"',
			]) {
				assert.equal(await h.call("bash", { command }), undefined, command);
			}
			for (const command of [
				"git commit -m 'feat: x'", "cd repo && git push -u origin agent/x", "gh pr merge 7 --rebase",
				"git -C /w commit", "git -c k=v commit", "git --no-pager commit", "git status\ngit commit",
				"FOO=1 git push", "git switch -c agent/x", "git switch -C agent/x", "git switch --create=agent/x",
				"git checkout -b agent/x", "git checkout -B agent/x", "git worktree add /w", "git cherry-pick abc",
				"git --git-dir=/w/.git --work-tree=/w push", "git --git-dir /w/.git --work-tree /w push",
				'git -C "/some path" -c core.pager=cat --no-pager push',
				"git status; git merge x", "false || git rebase x", "true | git push", "(git push)",
				"FOO=x BAR=y gh pr create",
				'FOO="a b" git push', "FOO='x;y' BAR=z git commit", "git -C'/some path' push",
				...wrappedPublishing,
			]) {
				const blocked = await h.call("bash", { command });
				assert.equal(blocked?.block, true, command);
				assert.ok(blocked?.reason?.includes("skill://git-tool"), blocked?.reason ?? command);
			}
			await h.read("skill://git-tool");
			assert.equal(await h.call("bash", { command: "git rebase origin/main" }), undefined);
		} finally { h.cleanup(); }
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
			for (const invalid of ["{broken", '{"schemaVersion":1}', '{"schemaVersion":3}', '{"schemaVersion":2,"checks":{"commit":"bun check"}}', '{"schemaVersion":2,"ciHost":"github"}', '{"schemaVersion":2,"ci":{"requiredContext":"gate"}}', '{"schemaVersion":2,"dynaflows":{"enabled":"yes"}}']) {
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
			assert.deepEqual(runs(), ["commit  AGENT=unset"], "a failed repo command stops before the harness checks");
			rmSync(log);
			delete process.env.FAIL_commit;
			process.env.FAIL_harness = "1";
			blocked = await commit();
			assert.equal(blocked?.block, true);
			assert.ok(blocked?.reason?.includes(`harness checks (omp-guardrails) failed in ${h.root} (exit 1)`), blocked?.reason);
			assert.ok(blocked?.reason?.includes("harness finding: src/x.py"), blocked?.reason);
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

	test("dynaflows commit checks see what gets committed: chained or wrapped commits are refused", async () => {
		const scratch = realpathSync(mkdtempSync(join(tmpdir(), "agent-rails-sole-")));
		// Fails unless the repo's `value` file reads "good".
		const script = join(scratch, "check.sh");
		writeFileSync(script, `#!/bin/sh\ntest "$(cat value)" = good || { echo "value is not good" >&2; exit 1; }\n`, { mode: 0o755 });
		const h = harness();
		try {
			await h.read("skill://git-tool");
			writeFileSync(join(h.root, "value"), "good");
			await execute("git", ["add", "value"], { cwd: h.root });
			await execute("git", ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"], { cwd: h.root });
			h.gate({ schemaVersion: 2, checks: { commit: [script] } });
			h.dynaflows(true);
			const sole = "`git commit` must be the whole command";
			for (const command of [
				"printf bad > value && git commit -qam unchecked", "true || git commit -m x", "true; git commit -m x",
				"echo x | git commit -F -", "true\ngit commit -m x", "(git commit -m x)", "git commit -m \"$(printf bad > value)\"",
				"git commit -m `printf bad > value`", "echo -m | xargs git commit", "eval 'printf bad > value; git commit -am x'",
				"sh -c 'printf bad > value && git commit -am x'", "bash -c 'git commit -m x'", "git commit -m x > log",
				"git commit -m x & printf bad > value", "{ git commit -m x; }",
			]) {
				const blocked = await h.call("bash", { command });
				assert.equal(blocked?.block, true, command);
				assert.ok(blocked?.reason?.includes(sole), `${command}: ${blocked?.reason}`);
				assert.ok(blocked?.reason?.includes("Stage your changes first"), command);
			}
			assert.equal(readFileSync(join(h.root, "value"), "utf8"), "good", "rails never ran the chained command");
			assert.equal(await h.call("bash", { command: "git commit -m 'plain; message | with $(text)'" }), undefined,
				"a plain commit on a clean tree passes; quoted prose is not a command");
			assert.equal(await h.call("bash", { command: "mise exec -- git commit -m x" }), undefined, "a tool launcher runs git directly");
		} finally { h.cleanup(); rmSync(scratch, { recursive: true, force: true }); }
	});

	test("dynaflows commits refuse assignments, program-running git options, editors and hook bypass", async () => {
		const h = harness();
		try {
			await h.read("skill://git-tool");
			h.gate({ schemaVersion: 2, checks: { commit: ["true"] } });
			h.dynaflows(true);
			const refusals: [string, string][] = [
				["GIT_EDITOR='printf bad > value' git commit -m x", "leading VAR=value assignment"],
				["PATH=/usr/bin git commit -m x", "leading VAR=value assignment"],
				["FOO=1 mise exec -- git commit -m x", "leading VAR=value assignment"],
				["git -c core.editor=vi commit -m x", "global option -c"],
				["git -ccore.editor=vi commit -m x", "global option -ccore.editor=vi"],
				["git --config-env=core.editor=ED commit -m x", "global option --config-env=core.editor=ED"],
				["git --config-env core.editor=ED commit -m x", "global option --config-env"],
				["git --exec-path=/tmp/x commit -m x", "global option --exec-path=/tmp/x"],
				["git commit", "-m/--message or -F/--file"],
				["git commit -a", "-m/--message or -F/--file"],
				["git commit -C HEAD", "-m/--message or -F/--file"],
				["git commit -m x -e", "must not open an editor"],
				["git commit -em x", "must not open an editor"],
				["git commit --edit -m x", "must not open an editor"],
				["git commit -c HEAD -m x", "must not open an editor"],
				["git commit --reedit-message=HEAD -m x", "must not open an editor"],
				["git commit --no-verify -m x", "pre-commit hook"],
				["git commit -nm x", "pre-commit hook"],
			];
			for (const [command, reason] of refusals) {
				const blocked = await h.call("bash", { command });
				assert.equal(blocked?.block, true, command);
				assert.ok(blocked?.reason?.includes(reason), `${command}: ${blocked?.reason}`);
			}
			for (const command of [
				"git commit -m x", "git commit -F msg.txt", "git commit --message=x", "git commit --file msg.txt",
				"git commit -m x --no-edit", "git commit -m '-e'", "git commit -me", `git -C '${h.root}' commit -m x`,
				"mise exec -- git commit -m x",
			]) assert.equal(await h.call("bash", { command }), undefined, command);
		} finally { h.cleanup(); }
	});

	test("dynaflows commits refuse unstaged tracked changes unless -a/--all; untracked files are fine", async () => {
		const scratch = realpathSync(mkdtempSync(join(tmpdir(), "agent-rails-unstaged-")));
		const script = join(scratch, "check.sh");
		writeFileSync(script, `#!/bin/sh\ntest "$(cat value)" = good || { echo "value is not good" >&2; exit 1; }\n`, { mode: 0o755 });
		const h = harness();
		try {
			await h.read("skill://git-tool");
			writeFileSync(join(h.root, "value"), "good");
			await execute("git", ["add", "value"], { cwd: h.root });
			await execute("git", ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"], { cwd: h.root });
			h.gate({ schemaVersion: 2, checks: { commit: [script] } });
			h.dynaflows(true);
			writeFileSync(join(h.root, "untracked.txt"), "new");
			assert.equal(await h.call("bash", { command: "git commit -m x" }), undefined, "untracked files do not block");
			writeFileSync(join(h.root, "value"), "bad");
			for (const command of ["git commit -m x", "git commit -m -a", "git commit --message --all"]) {
				const blocked = await h.call("bash", { command });
				assert.equal(blocked?.block, true, command);
				assert.ok(blocked?.reason?.includes("tracked files have unstaged changes"), blocked?.reason);
				assert.ok(blocked?.reason?.includes("`git diff --quiet` must pass"), blocked?.reason);
			}
			for (const command of ["git commit -am x", "git commit --all -m x"]) {
				const blocked = await h.call("bash", { command });
				assert.ok(blocked?.reason?.includes("value is not good"), `${command}: -a commits the tree, so the checks see it: ${blocked?.reason}`);
			}
			await execute("git", ["add", "value"], { cwd: h.root });
			assert.ok((await h.call("bash", { command: "git commit -m x" }))?.reason?.includes("value is not good"), "staged content is checked");
			writeFileSync(join(h.root, "value"), "good");
			await execute("git", ["add", "value"], { cwd: h.root });
			assert.equal(await h.call("bash", { command: "git commit -m x" }), undefined, "a plain commit of staged, passing content passes");
		} finally { h.cleanup(); rmSync(scratch, { recursive: true, force: true }); }
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
			const project = resolve(import.meta.dir, "../agent/skills/setup-repository-guardrails/assets/python-reference");
			assert.deepEqual(seen, [["run", "--project", project, "omp-guardrails", "check", "--repo", h.root, "--record", h.recordDir()]]);
		} finally { h.cleanup(); }
	});

	test("an invalid or below-contract record refuses dynaflows commits", async () => {
		const h = harness();
		try {
			await h.read("skill://git-tool");
			h.gate({ schemaVersion: 2, dynaflowsContract: currentContract - 1, dynaflows: { enabled: true } });
			let blocked = await h.call("bash", { command: "git commit -m x" });
			assert.equal(blocked?.block, true);
			assert.ok(blocked?.reason?.includes(`Dynaflows contract v${currentContract - 1} recorded; current is v${currentContract}`), blocked?.reason);
			h.rawGate(JSON.stringify({ schemaVersion: 2, dynaflowsContract: currentContract }));
			blocked = await h.call("bash", { command: "git commit -m x" });
			assert.ok(blocked?.reason?.includes('dynaflows must be {"enabled": true|false}'), blocked?.reason);
			h.gate({ schemaVersion: 2 });
			assert.equal(await h.call("bash", { command: "git commit -m x" }), undefined);
		} finally { h.cleanup(); }
	});

	test("failed path checks persist at handoff and rerun to observe out-of-tool repairs", async () => {
		const h = harness();
		try {
			const checker = join(h.root, "check.cjs");
			writeFileSync(checker, "process.exit(1)");
			h.gate({ schemaVersion: 2, pathCheck: [process.execPath, checker] });
			await h.read(verification);
			assert.ok((await h.call("edit", { path: "existing.ts" }))?.additionalContext?.includes("pathCheck failed"));
			assert.equal((await h.emit("session_stop"))?.decision, "block");
			assert.equal((await h.emit("session_stop"))?.decision, "block");
			writeFileSync(checker, "process.exit(0)");
			assert.equal(await h.emit("session_stop"), undefined);
			writeFileSync(checker, "process.exit(process.argv.slice(2).some(p => p.endsWith('existing.ts')) ? 1 : 0)");
			assert.ok((await h.call("edit", { path: "existing.ts" }))?.additionalContext?.includes("pathCheck failed"));
			rmSync(join(h.root, "existing.ts"));
			assert.equal(await h.emit("session_stop"), undefined, "deleted failed paths are not passed back to the checker");
		} finally { h.cleanup(); }
	});


	test("isolates sessions, restores successful branch reads, and forgets compacted reads", async () => {
		const h = harness();
		try {
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
			await h.emit("before_agent_start", { reason: "prompt", prompt: "$dynaflows on" });
			assert.ok(h.notes.some(note => note.includes("has no per-repo record")), h.notes.join("; "));
			assert.equal(existsSync(h.recordDir()), false, "the toggle never creates a record");
			h.gate({ schemaVersion: 2 });
			assert.equal(await h.emit("before_agent_start", { reason: "prompt", prompt: "next" }), undefined, "the record says off");
			h.dynaflows(true);
			assert.equal(await h.emit("before_agent_start", { reason: "revive", prompt: "" }), undefined);
			const pointer = JSON.stringify(await h.emit("before_agent_start", { reason: "prompt", prompt: "next" }));
			assert.ok(pointer.includes("skill://omp-dyna-workflows"), "the record turns it on");
			assert.ok(pointer.includes("lacks checks.commit or pathCheck") && pointer.includes("skill://setup-repository-guardrails"),
				"an incomplete record points at setup");
			assert.equal(await h.emit("before_agent_start", { reason: "prompt", prompt: "again" }), undefined, "pointer once per session");
			const off = JSON.stringify(await h.emit("before_agent_start", { reason: "prompt", prompt: "$dynaflows off" }));
			assert.ok(off.includes("Dynaflows off") && !off.includes("skill://omp-dyna-workflows"));
			assert.deepEqual(h.record(), { dynaflowsContract: currentContract, dynaflows: { enabled: false }, schemaVersion: 2 },
				"only the switch changes");
			h.setSession("restarted");
			assert.equal(await h.emit("before_agent_start", { reason: "prompt", prompt: "work" }), undefined, "record off outlives the session and beats machine on");
			assert.equal(await h.emit("before_agent_start", { reason: "prompt", prompt: "price $dynaflowsy" }), undefined, "keyword needs a word boundary");
			assert.ok(JSON.stringify(await h.emit("before_agent_start", { reason: "prompt", prompt: "$dynaflows" })).includes("skill://omp-dyna-workflows"));
			h.setSession("machine-off");
			h.machineDynaflows(false);
			assert.equal(await h.emit("before_agent_start", { reason: "prompt", prompt: "work" }), undefined, "machine off beats record on");
			h.setSession("sub"); h.context.agent.kind = "sub";
			assert.equal(await h.emit("before_agent_start", { reason: "prompt", prompt: "$dynaflows off" }), undefined, "subagents cannot toggle dynaflows");
			assert.deepEqual(h.record().dynaflows, { enabled: true });
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
			h.gate({ schemaVersion: 2, pathCheck: [process.execPath, "-e", "console.error('bad path'); process.exit(1)"] });
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

for (const family of ["openai", "anthropic"]) {
	test(`persists scoped ${family} authors and preserves independent model selectors`, async () => {
		const h = harness();
		const unrelated = harness();
		try {
			h.dynaflows(true);
			h.context.agent.kind = "sub";
			h.context.models.current = () => ({ provider: family, id: "author" });
			h.gate({ schemaVersion: 2, pathCheck: [process.execPath, "-e", "0"] });
			assert.equal(await h.call("edit", { path: join(h.root, "existing.ts") }), undefined);
			// SAFETY: this is the same on/registerCommand/exec harness used for the initial factory.
			agentRails(h.api as ExtensionAPI, h.options);
			const selector = `${family === "openai" ? "anthropic" : "openai"}/review@route:medium`;
			const result = await h.emit("before_subagent_spawn", { agent: "verifier", patterns: [`${family}/same`, selector] });
			assert.deepEqual(result?.model, [selector], JSON.stringify(result));
			assert.equal((await h.emit("before_subagent_spawn", { agent: "verifier", patterns: [`${family}/same`] }))?.block, true);
			unrelated.dynaflows(true);
			assert.ok((await unrelated.emit("before_subagent_spawn", { agent: "verifier", patterns: [selector] }))?.reason?.includes("authorship unknown"));
		} finally { h.cleanup(); unrelated.cleanup(); }
	});
}

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
			if (name === "checker") { checked.push(rest); return { code: 1, killed: false, stdout: "", stderr: "bad live path" }; }
			return exec(command, args, options);
		};
		h.gate({ schemaVersion: 2, pathCheck: ["checker"] });
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
			if (name === "checker") { checked.push(rest); return { code: 0, killed: false, stdout: "", stderr: "" }; }
			return exec(command, args, options);
		};
		h.gate({ schemaVersion: 2, pathCheck: ["checker"] });
		await h.call("edit", { paths: [internal, live] });
		assert.deepEqual(checked, [[live]], "git directory paths must not reach pathCheck");
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
			if (name === "checker") { checked.push(rest); return { code: 1, killed: false, stdout: "", stderr: "bad tracked path" }; }
			return exec(command, args, options);
		};
		h.gate({ schemaVersion: 2, pathCheck: ["checker"] });
		const failure = await h.call("edit", { path });
		assert.ok(failure?.additionalContext?.includes("bad tracked path"));
		assert.deepEqual(checked, [[path]]);
		writeFileSync(path, committed);
		await h.read(verification);
		assert.equal(await h.emit("session_stop"), undefined, "a file restored to HEAD must clear its pathCheck failure");
		assert.deepEqual(checked, [[path]], "a file restored to HEAD must clear its pathCheck failure");
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
			if (name === "checker") { checked.push(rest); return { code: 0, killed: false, stdout: "", stderr: "" }; }
			return exec(command, args, options);
		};
		h.gate({ schemaVersion: 2, pathCheck: ["checker"] });
		await h.call("edit", { paths: [ignored, join(h.root, "link/file")] });
		assert.ok(!checked.some(args => args.some(path => path.includes("node_modules/"))),
			"ignored paths must not reach pathCheck when another path breaks git check-ignore");
	} finally { h.cleanup(); rmSync(outside, { recursive: true, force: true }); }
});

test("dynaflows merge requires an exact-head valid proof marker in the Git common directory", async () => {
	const h = harness();
	try {
		await execute("git", ["init"], { cwd: h.root });
		await h.read("skill://git-tool");
		const head = "a".repeat(40);
		const command = `env gh pr merge 3 --match-head-commit=${head}`;
		mockPrCi(h, [...greenCi, verified], head, "3");
		assert.equal(await h.call("bash", { command }), undefined, "off retains existing behaviour");
		h.dynaflows(true);
		assert.ok((await h.call("bash", { command: "gh pr merge 3" }))?.reason?.includes("--match-head-commit"));
		assert.ok((await h.call("bash", { command }))?.reason?.includes(head));
		mkdirSync(join(h.root, ".git/omp-prove"));
		const marker = join(h.root, ".git/omp-prove", `${head}.json`);
		for (const content of ["{broken", JSON.stringify({ schemaVersion: 1, head, kind: "prove", status: "fail" })]) {
			writeFileSync(marker, content);
			assert.equal((await h.call("bash", { command }))?.block, true);
		}
		for (const kind of ["prove", "docs-only"]) {
			writeFileSync(marker, JSON.stringify({ schemaVersion: 1, head, kind, status: "pass" }));
			assert.equal(await h.call("bash", { command }), undefined);
			assert.equal(await h.call("bash", { command: `gh pr merge 3 --match-head-commit ${head}` }), undefined);
		}
	} finally { h.cleanup(); }
});

// The generic CI rule, reached by an agent-verified status post on the PR's exact head.
for (const scenario of [
	{ name: "failed CheckRun", rollup: [{ __typename: "CheckRun", name: "test", status: "COMPLETED", conclusion: "FAILURE" }], fragment: "test: COMPLETED/FAILURE" },
	{ name: "pending CheckRun", rollup: [{ __typename: "CheckRun", name: "test", status: "IN_PROGRESS", conclusion: null }], fragment: "test: IN_PROGRESS" },
	{ name: "failed StatusContext", rollup: [{ __typename: "StatusContext", context: "external", state: "FAILURE" }], fragment: "external: FAILURE" },
	{ name: "pending StatusContext", rollup: [{ __typename: "StatusContext", context: "external", state: "PENDING" }], fragment: "external: PENDING" },
	{ name: "only skipped", rollup: [{ __typename: "CheckRun", name: "unused", status: "COMPLETED", conclusion: "SKIPPED" }], fragment: ciHead },
	{ name: "agent-verified is not CI", rollup: [verified], boundary: "gate", fragment: `no CI checks reported for ${ciHead}` },
	{ name: "empty declared CI", rollup: [], boundary: "gate", fragment: `no CI checks reported for ${ciHead}` },
	{ name: "empty workflow directory", rollup: [], boundary: "workflows", fragment: `no CI checks reported for ${ciHead}` },
	{ name: "empty no-CI boundary", rollup: [] },
	{ name: "green", rollup: greenCi },
] as const) {
	test(`status post CI gate: ${scenario.name}`, async () => {
		const h = harness();
		try {
			h.dynaflows(false);
			await originRemote(h);
			if ("boundary" in scenario && scenario.boundary === "gate") h.gate({ schemaVersion: 2, checks: { ci: ["bun", "ci"] } });
			if ("boundary" in scenario && scenario.boundary === "workflows") mkdirSync(join(h.root, ".github/workflows"), { recursive: true });
			mockPrCi(h, [...scenario.rollup], ciHead, "3");
			const result = await h.call("bash", { command: statusPost() });
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
	{ name: "agent-verified does not stand in", rollup: [verified], state: "missing" },
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
	for (const action of ["status post", "merge"] as const) {
		test(`required context ${action}: ${scenario.name}`, async () => {
			const h = harness();
			try {
				await h.read("skill://git-tool");
				h.dynaflows(false);
				await originRemote(h, "git@github.com:base-owner/base-repo.git");
				h.gate({ schemaVersion: 2, ci: ciGate });
				const rollup = action === "merge" ? [...scenario.rollup, verified] : [...scenario.rollup];
				mockPrCi(h, rollup, ciHead, "3", undefined, "https://github.com/base-owner/base-repo/pull/3");
				const result = await h.call("bash", { command: action === "merge"
					? `gh pr merge 3 --match-head-commit ${ciHead}` : statusPost(ciHead, "base-owner/base-repo") });
				if ("state" in scenario) {
					assert.equal(result?.block, true, "the required context must block");
					assert.equal(result?.reason, requiredRefusal(scenario.state));
				} else assert.equal(result, undefined);
			} finally { h.cleanup(); }
		});
	}
}

test("without a ci block every reported check still decides the merge", async () => {
	const h = harness();
	try {
		await h.read("skill://git-tool");
		h.dynaflows(true);
		passMarker(h);
		mockPrCi(h, [{ __typename: "StatusContext", context: "gate", state: "SUCCESS" },
			{ __typename: "CheckRun", name: "lint", status: "COMPLETED", conclusion: "FAILURE" }, verified], ciHead, "3");
		const result = await h.call("bash", { command: `gh pr merge 3 --match-head-commit ${ciHead}` });
		assert.equal(result?.block, true);
		assert.ok(result?.reason?.includes(`CI is not green for ${ciHead}: lint: COMPLETED/FAILURE`), result?.reason);
	} finally { h.cleanup(); }
});

// Owner-approval write guard: records never reach a PR, so the owner's hand edit is the approval act
// and rails refuse an agent write that adds or changes an approval field in a record file.
type Approved = { approval: string };
type ProfileFixture = {
	guardrails: {
		test_evidence: { approved_skips: (Approved & { id: string; reason: string })[]; slow_ms: number };
		coverage_ratchet: { approvals: (Approved & { path: string })[] };
	};
	verification_surface: Approved & { none: string };
	fast_tier_budget: { machines: (Approved & { id: string; wall_s: number })[] };
	house_style: { version: number; exceptions: { rule: string; path: string; adr: string }[] };
	user_rows?: { id: string; user_approval: string }[];
};
const profile = (values: ProfileFixture) => `---\n${JSON.stringify(values, null, 2)}\n---\n\n# Test profile\n`;
const skip = { id: "tests/test_vendor.py::*", reason: "vendor outage", approval: "owner 2026-11-01" };
const baseProfile: ProfileFixture = {
	guardrails: { test_evidence: { approved_skips: [skip], slow_ms: 200 }, coverage_ratchet: { approvals: [] } },
	verification_surface: { none: "library only", approval: "owner 2026-10-01" },
	fast_tier_budget: { machines: [{ id: "m1", wall_s: 30, approval: "owner 2026-10-02" }] },
	house_style: { version: 1, exceptions: [] },
};
const withProfile = (change: (value: ProfileFixture) => void) => {
	const value = structuredClone(baseProfile);
	change(value);
	return profile(value);
};
const baseGate = { schemaVersion: 2, dynaflowsContract: currentContract, dynaflows: { enabled: false } };
for (const scenario of [
	{ name: "added approved skip", file: "profile.md", content: withProfile(value => value.guardrails.test_evidence.approved_skips.push({ ...skip, id: "tests/test_new.py::*" })), field: "guardrails.test_evidence.approved_skips[]" },
	{ name: "widened approved skip", file: "profile.md", content: withProfile(value => { value.guardrails.test_evidence.approved_skips[0].id = "tests/**"; }), field: "approved_skips[]" },
	{ name: "changed exemption approval", file: "profile.md", content: withProfile(value => { value.verification_surface.approval = "agent says ok"; }), field: "verification_surface" },
	{ name: "added coverage ratchet approval", file: "profile.md", content: withProfile(value => { value.guardrails.coverage_ratchet.approvals = [{ path: "src/**", approval: "ok" }]; }), field: "guardrails.coverage_ratchet.approvals[]" },
	{ name: "raised approved machine budget", file: "profile.md", content: withProfile(value => { value.fast_tier_budget.machines[0].wall_s = 90; }), field: "fast_tier_budget.machines[]" },
	{ name: "added house style exception", file: "profile.md", content: withProfile(value => { value.house_style.exceptions = [{ rule: "TEST.no-evasion", path: "tests/x.py", adr: "adr/1.md" }]; }), field: "house_style.exceptions[]" },
	{ name: "added user_approval row", file: "profile.md", content: withProfile(value => { value.user_rows = [{ id: "PRETTIER.version", user_approval: "approved" }]; }), field: "user_rows[]" },
	{ name: "approval field in gate.json", file: "gate.json", content: JSON.stringify({ ...baseGate, approved_contexts: ["lint"] }), field: "approved_contexts[]" },
	{ name: "unreadable profile", file: "profile.md", content: "# no front matter\n", field: "cannot parse the new" },
	{ name: "removed approved skip", file: "profile.md", content: withProfile(value => { value.guardrails.test_evidence.approved_skips = []; }) },
	{ name: "non-approval profile change", file: "profile.md", content: withProfile(value => { value.guardrails.test_evidence.slow_ms = 100; }) },
	{ name: "non-approval gate change", file: "gate.json", content: JSON.stringify({ ...baseGate, dynaflows: { enabled: true } }) },
	{ name: "unchanged profile", file: "profile.md", content: profile(baseProfile) },
] as const) {
	test(`record approval write guard: ${scenario.name}`, async () => {
		const h = harness();
		try {
			h.gate({ schemaVersion: 2 });
			writeFileSync(join(h.recordDir(), "profile.md"), profile(baseProfile));
			const path = join(h.recordDir(), scenario.file);
			const before = readFileSync(path, "utf8");
			const result = await h.call("write", { path, content: scenario.content });
			if ("field" in scenario) {
				assert.equal(result?.block, true, "an approval change is the owner's hand edit");
				assert.ok(result?.reason?.includes(scenario.field), result?.reason);
				assert.ok(result?.reason?.includes(path), result?.reason);
				assert.equal(readFileSync(path, "utf8"), before);
			} else assert.equal(result, undefined);
		} finally { h.cleanup(); }
	});
}

test("record approval write guard: new records, partial edits, symlinked roots and repo files", async () => {
	const h = harness();
	const aliases = realpathSync(mkdtempSync(join(tmpdir(), "agent-rails-records-alias-")));
	try {
		await h.read(integrity, structure, naming);
		const fresh = join(h.records, "repos", "owner", "fresh", "profile.md");
		assert.ok((await h.call("write", { path: fresh, content: profile(baseProfile) }))?.reason?.includes("approved_skips[]"),
			"a new record file adds every approval it holds");
		assert.equal(await h.call("write", { path: join(h.records, "repos", "owner", "fresh", "gate.json"), content: JSON.stringify(baseGate) }), undefined);
		h.gate({ schemaVersion: 2 });
		const gate = join(h.recordDir(), "gate.json");
		for (const [tool, input] of [["edit", { path: gate }], ["ast_edit", { paths: [gate] }], ["write", { path: gate }]] as const) {
			const result = await h.call(tool, input);
			assert.equal(result?.block, true, tool);
			assert.ok(result?.reason?.includes("change it with write (the whole file)"), result?.reason);
		}
		const alias = join(aliases, "records");
		symlinkSync(h.records, alias);
		assert.equal((await h.call("write", { path: join(alias, "repos", "owner", "repo", "gate.json"),
			content: JSON.stringify({ ...baseGate, approved_contexts: ["lint"] }) }))?.block, true, "a symlinked path still reaches the record");
		const fileAlias = join(aliases, "alias.json");
		symlinkSync(gate, fileAlias);
		const aliasResult = await h.call("write", { path: fileAlias, content: JSON.stringify({ ...baseGate, approved_contexts: ["lint"] }) });
		assert.equal(aliasResult?.block, true, "a symlink to a record file is a record file");
		assert.ok(aliasResult?.reason?.includes("approved_contexts[]"), aliasResult?.reason);
		const profileAlias = join(aliases, "notes.txt");
		symlinkSync(join(h.recordDir(), "profile.md"), profileAlias);
		assert.ok((await h.call("write", { path: profileAlias, content: profile(baseProfile) }))?.reason?.includes("approved_skips[]"),
			"a dangling alias of a new record profile parses as front matter and adds its approvals");
		assert.equal(await h.call("write", { path: join(h.root, "profile.md"), content: profile(baseProfile) }), undefined,
			"a repo file named like a record file is not a record");
		assert.equal(await h.call("write", { path: join(h.records, "repos", "owner", "repo", "notes.md"), content: "approval: none" }), undefined);
	} finally { h.cleanup(); rmSync(aliases, { recursive: true, force: true }); }
});

// Bash and other non-edit tools can write anywhere, so rails compare record approvals around each call.
async function aroundBash(h: ReturnType<typeof harness>, change: () => void) {
	const event = { toolName: "bash", toolCallId: `bash-${randomUUID()}`, input: { command: "true" } };
	assert.equal(await h.emit("tool_call", event), undefined);
	change();
	return h.emit("tool_result", { ...event, details: {}, isError: false });
}
test("record approval guard restores approval changes made by bash", async () => {
	const h = harness();
	try {
		h.gate({ schemaVersion: 2 });
		const path = join(h.recordDir(), "profile.md");
		writeFileSync(path, profile(baseProfile));
		const before = readFileSync(path, "utf8");
		const added = await aroundBash(h, () => writeFileSync(path, withProfile(value => {
			value.guardrails.test_evidence.approved_skips.push({ ...skip, id: "tests/**" });
		})));
		assert.ok(added?.additionalContext?.includes(path), added?.additionalContext);
		assert.ok(added?.additionalContext?.includes("restored"), added?.additionalContext);
		assert.equal(readFileSync(path, "utf8"), before, "an approval added by bash is restored");
		const fresh = join(h.records, "repos", "owner", "fresh", "gate.json");
		const created = await aroundBash(h, () => {
			mkdirSync(dirname(fresh), { recursive: true });
			writeFileSync(fresh, JSON.stringify({ ...baseGate, approved_contexts: ["lint"] }));
		});
		assert.ok(created?.additionalContext?.includes(fresh), created?.additionalContext);
		assert.equal(existsSync(fresh), false, "a record file bash created with approvals is removed");
		const plain = withProfile(value => { value.guardrails.test_evidence.slow_ms = 100; });
		const kept = await aroundBash(h, () => writeFileSync(path, plain));
		assert.ok(!kept?.additionalContext?.includes("restored"), kept?.additionalContext);
		assert.equal(readFileSync(path, "utf8"), plain, "a non-approval change by bash stays");
		const removed = withProfile(value => { value.guardrails.test_evidence.approved_skips = []; });
		await aroundBash(h, () => writeFileSync(path, removed));
		assert.equal(readFileSync(path, "utf8"), removed, "removing an approval stays");
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

test("a retired ciHost gate is refused with the migration message", async () => {
	const h = harness();
	try {
		await h.read("skill://git-tool");
		for (const ciHost of ["github", "nuc"]) {
			h.rawGate(JSON.stringify({ schemaVersion: 2, dynaflowsContract: currentContract, dynaflows: { enabled: false }, ciHost }));
			const result = await h.call("bash", { command: "git push" });
			assert.equal(result?.block, true);
			assert.ok(result?.reason?.includes("ciHost is retired; declare ci.requiredContext and ci.runner"), result?.reason);
		}
		h.gate({ schemaVersion: 2, ci: ciGate, checks: { format: ["ruff", "format"], profile: ["omp-guardrails", "profile-check"] } });
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
			const url = "https://github.com/other/checkout/pull/3";
			mockPrCi(h, [{ __typename: "StatusContext", context: "gate", state: "SUCCESS" }, verified], ciHead, url, undefined, url);
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
		const url = "https://github.com/owner/repo/pull/3";
		mockPrCi(h, [{ __typename: "StatusContext", context: "gate", state: "SUCCESS" }, verified], ciHead, url);
		assert.equal(await h.call("bash", { command: `gh pr merge ${url} --match-head-commit ${ciHead}` }), undefined);
	} finally { h.cleanup(); }
});

test("required context blocks merge despite a valid proof marker with dynaflows on", async () => {
	const h = harness();
	try {
		await h.read("skill://git-tool");
		h.dynaflows(true);
		h.gate({ schemaVersion: 2, ci: ciGate });
		passMarker(h);
		mockPrCi(h, [{ __typename: "CheckRun", name: "audit-packages", status: "COMPLETED", conclusion: "SUCCESS" }, verified], ciHead, "3");
		const result = await h.call("bash", { command: `gh pr merge 3 --match-head-commit ${ciHead}` });
		assert.equal(result?.block, true, "the required context must block merge despite a valid proof marker");
		assert.ok(result?.reason?.includes(`required CI context "gate" is missing for ${ciHead} in owner/repo`), result?.reason ?? "missing refusal reason");
	} finally { h.cleanup(); }
});

test("PR query contract: requests the PR URL", async () => {
	const h = harness();
	try {
		await originRemote(h);
		mockPrCi(h, greenCi, ciHead, "3");
		const exec = h.api.exec.bind(h.api);
		let fields: string | undefined;
		h.api.exec = async (command, args, options) => {
			if (command === "gh" && args[1] === "view") fields = args.at(-1);
			return exec(command, args, options);
		};
		await h.call("bash", { command: statusPost() });
		assert.equal(fields, "headRefOid,statusCheckRollup,url");
	} finally { h.cleanup(); }
});

for (const marker of ["missing", "wrong head", "malformed", "failed", "wrong kind", "valid"] as const) {
	test(`status post proof gate: ${marker}`, async () => {
		const h = harness();
		try {
			h.dynaflows(true);
			await originRemote(h);
			mockPrCi(h, greenCi, ciHead, "3");
			if (marker === "wrong head") passMarker(h, "b".repeat(40));
			else if (marker !== "missing") {
				passMarker(h);
				const path = join(h.root, ".git/omp-prove", `${ciHead}.json`);
				if (marker === "malformed") writeFileSync(path, "{broken");
				if (marker === "failed") writeFileSync(path, JSON.stringify({ schemaVersion: 1, head: ciHead, kind: "prove", status: "fail" }));
				if (marker === "wrong kind") writeFileSync(path, JSON.stringify({ schemaVersion: 1, head: ciHead, kind: "unknown", status: "pass" }));
			}
			const result = await h.call("bash", { command: statusPost() });
			if (marker === "valid") assert.equal(result, undefined);
			else {
				assert.equal(result?.block, true);
				assert.equal(result?.reason?.includes(`no valid pass marker for ${ciHead}`), true, "no valid pass marker");
			}
		} finally { h.cleanup(); }
	});
}

// The label is retired: every way of adding it is refused before any gh query.
for (const command of [
	"gh pr edit 3 --add-label agent-verified",
	"gh pr edit 3 --add-label=agent-verified",
	"gh pr edit 3 --add-label other --add-label=review,agent-verified",
	"gh issue edit https://github.com/owner/repo/pull/3 --add-label agent-verified",
	"gh pr edit --add-label agent-verified",
	"gh api repos/owner/repo/issues/3/labels -f labels[]=agent-verified",
	"gh api -X PUT /repos/owner/repo/issues/3/labels/ -f 'labels[]=agent-verified'",
]) {
	test(`label blocked: ${command}`, async () => {
		const h = harness();
		try {
			h.dynaflows(true);
			passMarker(h);
			const exec = h.api.exec.bind(h.api);
			h.api.exec = async (name, args, options) => {
				assert.notEqual(name, "gh", "the retired label must not query gh");
				return exec(name, args, options);
			};
			assert.deepEqual(await h.call("bash", { command }), { block: true, reason: statusOnly });
		} finally { h.cleanup(); }
	});
}

for (const scenario of [
	{ name: "label removal", command: "gh pr edit 3 --remove-label agent-verified" },
	{ name: "unrelated label", command: "gh issue edit 3 --add-label reviewed" },
	{ name: "other status context", command: `gh api repos/owner/repo/statuses/${ciHead} -f state=success -f context=lint` },
	{ name: "status read", command: `gh api -X GET repos/owner/repo/statuses/${ciHead} --jq '.[]|select(.context=="agent-verified")'` },
	{ name: "combined status read", command: `gh api repos/owner/repo/commits/${ciHead}/status --jq '.statuses[]|select(.context=="agent-verified")'` },
] as const) {
	test(`agent-verified unaffected: ${scenario.name}`, async () => {
		const h = harness();
		try {
			h.dynaflows(true);
			const exec = h.api.exec.bind(h.api);
			h.api.exec = async (name, args, options) => {
				assert.notEqual(name, "gh", "unaffected actions must not query gh");
				return exec(name, args, options);
			};
			assert.equal(await h.call("bash", { command: scenario.command }), undefined);
		} finally { h.cleanup(); }
	});
}

for (const scenario of [
	{ name: "raw field forms", command: `gh api --method POST https://api.github.com/repos/owner/repo/statuses/${ciHead} --raw-field=state=success -Fcontext=agent-verified` },
	{ name: "gh placeholders", command: `gh api 'repos/{owner}/{repo}/statuses/${ciHead}' -f state=success -f context=agent-verified` },
	{ name: "red CI", rollup: [{ __typename: "CheckRun", name: "test", status: "COMPLETED", conclusion: "FAILURE" }], fragment: "test: COMPLETED/FAILURE" },
	{ name: "no PR at head", listed: [{ number: 4, headRefOid: "b".repeat(40) }], fragment: `no open PR has head ${ciHead}` },
	{ name: "several PRs at head", listed: [{ number: 3, headRefOid: ciHead }, { number: 4, headRefOid: ciHead }], fragment: `several open PRs have head ${ciHead}` },
	{ name: "PR head moved", head: "b".repeat(40), listed: [{ number: 3, headRefOid: ciHead }], fragment: `PR head is ${"b".repeat(40)}, not ${ciHead}` },
	{ name: "another repository", command: statusPost(ciHead, "other/repo"), fragment: "run from a checkout of other/repo" },
	{ name: "unexpanded SHA", command: "gh api repos/owner/repo/statuses/$SHA -f context=agent-verified", fragment: "received unexpanded shell text `$SHA`" },
	{ name: "short SHA", command: "gh api repos/owner/repo/statuses/abc123 -f context=agent-verified", fragment: "needs a lowercase 40-hex SHA, got abc123" },
	{ name: "unreadable payload", command: `gh api repos/owner/repo/statuses/${ciHead} --input body.json`, fragment: "no --input or @file" },
	{ name: "file field", command: `gh api repos/owner/repo/statuses/${ciHead} -F context=@ctx.txt`, fragment: "no --input or @file" },
] as const) {
	test(`status post routing: ${scenario.name}`, async () => {
		const h = harness();
		try {
			h.dynaflows(true);
			passMarker(h);
			await originRemote(h);
			mockPrCi(h, "rollup" in scenario ? [...scenario.rollup] : greenCi, "head" in scenario ? scenario.head : ciHead, "3",
				undefined, undefined, "listed" in scenario ? [...scenario.listed] : undefined);
			const result = await h.call("bash", { command: "command" in scenario ? scenario.command : statusPost() });
			if ("fragment" in scenario) {
				assert.equal(result?.block, true);
				assert.ok(result?.reason?.includes(scenario.fragment), result?.reason);
			} else assert.equal(result, undefined);
		} finally { h.cleanup(); }
	});
}

test("status post reports a gh failure while finding the PR", async () => {
	const h = harness();
	try {
		await originRemote(h);
		const exec = h.api.exec.bind(h.api);
		h.api.exec = async (command, args, options) => command === "gh"
			? { code: 1, killed: false, stdout: "", stderr: "not authenticated" } : exec(command, args, options);
		const result = await h.call("bash", { command: statusPost() });
		assert.equal(result?.block, true);
		assert.ok(result?.reason?.includes(`cannot find the PR for ${ciHead} via gh: not authenticated`), result?.reason);
	} finally { h.cleanup(); }
});

for (const scenario of ["head mismatch", "red CI", "green CI", "missing agent-verified", "failed agent-verified"] as const) {
	test(`merge CI gate: ${scenario}`, async () => {
		const h = harness();
		try {
			await h.read("skill://git-tool");
			h.dynaflows(true);
			passMarker(h);
			const head = scenario === "head mismatch" ? "b".repeat(40) : ciHead;
			const evidence = scenario === "missing agent-verified" ? []
				: [{ ...verified, state: scenario === "failed agent-verified" ? "FAILURE" : "SUCCESS" }];
			mockPrCi(h, scenario === "red CI"
				? [{ __typename: "CheckRun", name: "test", status: "COMPLETED", conclusion: "FAILURE" }, ...evidence] : [...greenCi, ...evidence], head, "3");
			const result = await h.call("bash", { command: `gh pr merge 3 --match-head-commit ${ciHead}` });
			if (scenario === "green CI") assert.equal(result, undefined);
			else {
				assert.equal(result?.block, true);
				const fragment = scenario === "head mismatch" ? `PR head is ${head}, not ${ciHead}; re-verify the current head`
					: scenario === "red CI" ? "test: COMPLETED/FAILURE"
					: `agent-verified status is ${scenario === "failed agent-verified" ? "FAILURE" : "missing"} for ${ciHead} in owner/repo`;
				assert.equal(result?.reason?.includes(fragment), true, fragment);
			}
		} finally { h.cleanup(); }
	});
}

test("a declared gate.ci gates merges with dynaflows off, without needing a pass marker", async () => {
	const h = harness();
	try {
		await h.read("skill://git-tool");
		h.dynaflows(false);
		h.gate({ schemaVersion: 2, ci: ciGate });
		assert.ok((await h.call("bash", { command: "gh pr merge 3" }))?.reason?.includes("requires --match-head-commit"));
		mockPrCi(h, [{ __typename: "StatusContext", context: "gate", state: "SUCCESS" }], ciHead, "3");
		assert.ok((await h.call("bash", { command: `gh pr merge 3 --match-head-commit ${ciHead}` }))?.reason?.includes("agent-verified status is missing"));
		mockPrCi(h, [{ __typename: "StatusContext", context: "gate", state: "SUCCESS" }, verified], ciHead, "3");
		assert.equal(await h.call("bash", { command: `gh pr merge 3 --match-head-commit ${ciHead}` }), undefined);
		h.gate({ schemaVersion: 2 });
		assert.equal(await h.call("bash", { command: "gh pr merge 3" }), undefined, "no gate.ci and dynaflows off keep plain merges");
	} finally { h.cleanup(); }
});

for (const scenario of [
	{ name: "missing flag", args: "", fragment: "requires --match-head-commit <40-hex verified SHA>" },
	{ name: "bare variable", args: "--match-head-commit $H", fragment: "received unexpanded shell text `$H`; rails check the literal command, so pass the 40-hex SHA itself" },
	{ name: "quoted variable", args: '--match-head-commit "$H"', fragment: "received unexpanded shell text `$H`; rails check the literal command, so pass the 40-hex SHA itself" },
	{ name: "non-hex", args: "--match-head-commit nope", fragment: "must be a lowercase 40-hex SHA, got nope" },
	{ name: "missing marker", args: `--match-head-commit ${ciHead}`, fragment: `no valid pass marker for ${ciHead}` },
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
		h.gate({ schemaVersion: 2, pathCheck: ["checker"] });
		const exec = h.api.exec.bind(h.api);
		let checked = false;
		h.api.exec = async (command, args, options) => {
			const [name, rest] = repoCommand(command, args);
			if (name === "checker") {
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

test("history operations record the acting family and command-specific bypass semantics", async () => {
	const h = harness();
	try {
		h.dynaflows(true);
		await h.read("skill://git-tool");
		assert.equal(await h.call("bash", { command: "git cherry-pick -n HEAD" }), undefined);
		assert.equal((await h.call("bash", { command: "git merge --no-verify branch" }))?.block, true);
		assert.equal((await h.emit("before_subagent_spawn", { agent: "verifier", patterns: ["openai/review"] }))?.block, true);
		for (const command of ["git revert HEAD", "git am patch", "git pull"]) {
			h.setSession(command);
			assert.ok((await h.call("bash", { command }))?.reason?.includes("skill://git-tool"));
		}
	} finally { h.cleanup(); }
});

test("merge-base queries leave authorship empty while history operations record the acting family", async () => {
	const h = harness();
	try {
		h.dynaflows(true);
		await h.read("skill://git-tool");
		const key = createHash("sha256").update(`${h.root}\0refs/heads/agent/test`).digest("hex");
		const ledger = join(h.root, ".git/omp-authors", `${key}.json`);
		for (const command of ["git merge-base HEAD HEAD", "git merge-base --is-ancestor HEAD HEAD"]) {
			assert.equal(await h.call("bash", { command }), undefined);
		}
		assert.equal(existsSync(ledger), false, "read-only git merge-base must not create an author ledger");
		assert.equal(await h.call("bash", { command: "git cherry-pick -n HEAD" }), undefined);
		assert.deepEqual(JSON.parse(readFileSync(ledger, "utf8")).authors.map((author: { family: string }) => author.family), ["openai"]);
	} finally { h.cleanup(); }
});

test("agent rails surfaces a timed-out path checker without throwing", async () => {
	const h = harness();
	// Exercise the real subprocess kill boundary; fake JS timers cannot control execFile's OS child.
	try {
		h.gate({
			schemaVersion: 2,
			pathCheck: [process.execPath, "-e", "setTimeout(() => {}, 60000)"],
			pathCheckTimeoutMs: 30,
		});
		assert.ok((await h.call("edit", { path: "existing.ts" }))?.additionalContext?.includes("pathCheck failed"));
	} finally { h.cleanup(); }
});

test("main scratch edits and non-repository commits do not count as authorship", async () => {
	const h = harness();
	const scratch = realpathSync(mkdtempSync(join(tmpdir(), "agent-rails-scratch-")));
	try {
		h.dynaflows(true);
		h.context.models.current = () => ({ provider: "anthropic", id: "main" });
		await h.read(integrity, structure, naming, "skill://git-tool");
		const verifier = { agent: "verifier", patterns: ["anthropic/review"] };
		const path = join(scratch, "pr-body.txt");
		writeFileSync(path, "PR description");
		for (const tool of ["write", "edit"]) {
			assert.equal(await h.call(tool, { path }), undefined);
			const result = await h.emit("before_subagent_spawn", verifier);
			assert.ok(result?.reason?.includes("authorship unknown"), JSON.stringify(result));
		}
		assert.equal(await h.call("ast_edit", { paths: [path] }, { applied: true, files: [path] }), undefined);
		assert.ok((await h.emit("before_subagent_spawn", verifier))?.reason?.includes("authorship unknown"));
		assert.equal(await h.call("bash", { command: "git commit -m scratch", cwd: scratch }), undefined);
		assert.ok((await h.emit("before_subagent_spawn", verifier))?.reason?.includes("authorship unknown"));
		assert.equal(await h.call("edit", { path: join(h.root, "existing.ts") }), undefined);
		assert.equal((await h.emit("before_subagent_spawn", verifier))?.block, true, "main repository edits still count");
	} finally { h.cleanup(); rmSync(scratch, { recursive: true, force: true }); }
});

test("child handoff requires its own verification read and repaired path checks", async () => {
	const h = harness();
	try {
		h.context.agent.kind = "sub";
		assert.equal(await h.emit("session_stop"), undefined);
		const checker = join(h.root, "check.cjs");
		writeFileSync(checker, "process.exit(1)");
		h.gate({ schemaVersion: 2, pathCheck: [process.execPath, checker], checks: { commit: ["not-run"], ci: ["not-run"] } });
		await h.call("edit", { path: join(h.root, "existing.ts") });
		assert.equal((await h.emit("session_stop"))?.decision, "block");
		writeFileSync(checker, "process.exit(0)");
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

test("toggle commands ignore prose and reject contradictions and nonrepositories", async () => {
	const h = harness();
	const scratch = realpathSync(mkdtempSync(join(tmpdir(), "rails-toggle-")));
	try {
		h.gate({ schemaVersion: 2 });
		const record = h.record();
		for (const prompt of ["please $dynaflows on", '"$dynaflows"', "$dynaflows status", "$dynaflows on\n$dynaflows off"]) {
			await h.emit("before_agent_start", { reason: "prompt", prompt });
			assert.deepEqual(h.record(), record, prompt);
		}
		assert.equal(existsSync(join(h.root, ".omp")), false, "the repo tree is never written");
		h.context.cwd = scratch;
		await h.emit("before_agent_start", { reason: "prompt", prompt: "$dynaflows" });
		assert.equal(existsSync(join(scratch, ".omp")), false);
		assert.ok(h.notes.some(note => note.includes("Git project")));
	} finally { h.cleanup(); rmSync(scratch, { recursive: true, force: true }); }
});

for (const scenario of ["external", "unverified", "other commit author", "other PR author", "other branch", "gh failure", "empty range", "ledger"] as const) {
	test(`Dependabot missing-ledger verifier policy: ${scenario}`, async () => {
		const h = harness();
		try {
			h.dynaflows(true);
			const commit = async (message: string) => {
				await execute("git", ["-c", "user.name=Test", "-c", "user.email=test@example.test", "commit", "--allow-empty", "-m", message], { cwd: h.root });
				return (await execute("git", ["rev-parse", "HEAD"], { cwd: h.root })).stdout.trim();
			};
			const parent = await commit("base");
			const first = await commit("first bot");
			const head = await commit("second bot");
			const executeReal = h.api.exec;
			const checked: string[] = [];
			h.api.exec = async (command, args, options) => {
				if (command !== "gh") return executeReal(command, args, options);
				assert.equal(options.cwd, h.root);
				assert.ok(!args.includes("-R"));
				if (scenario === "ledger") throw new Error("existing ledger must not query gh");
				if (scenario === "gh failure") return { code: 1, killed: false, stdout: "", stderr: "unavailable" };
				const response = (() => {
					if (args[0] === "api") {
						const sha = args[1].split("/").at(-1)!;
						checked.push(sha);
						return { author: { login: scenario === "other commit author" && sha === first ? "human" : "dependabot[bot]" },
							commit: { verification: { verified: !(scenario === "unverified" && sha === first) } } };
					}
					if (args[1] === "view") return { baseRefOid: scenario === "empty range" ? head : parent };
					return [{ author: { is_bot: true, login: scenario === "other PR author" ? "human" : "app/dependabot" },
						headRefName: scenario === "other branch" ? "agent/bump" : "dependabot/bun/js-minor-8e002485ce", headRefOid: head }];
				})();
				return { code: 0, killed: false, stdout: JSON.stringify(response), stderr: "" };
			};
			if (scenario === "ledger") {
				await h.read(integrity, structure, naming);
				await h.call("edit", { path: join(h.root, "existing.ts") });
			}
			const result = await h.emit("before_subagent_spawn", { agent: "verifier", patterns: ["openai/review"] });
			if (scenario === "external") {
				assert.equal(result, undefined);
				assert.deepEqual(new Set(checked), new Set([first, head]));
				const dir = join(h.root, ".git/omp-prove");
				mkdirSync(dir, { recursive: true });
				writeFileSync(join(dir, "verifier-context.json"), JSON.stringify({
					schemaVersion: 1, worktree: h.root, branch: "refs/heads/agent/test", parent, head,
				}));
				assert.equal(await h.emit("before_subagent_spawn", { agent: "verifier", patterns: [] }), undefined);
			} else {
				assert.equal(result?.block, true, JSON.stringify(result));
				const reasons = {
					unverified: "not verified", "other commit author": "author is not dependabot[bot]",
					"other PR author": "PR author is not Dependabot", "other branch": "not dependabot/",
					"gh failure": "gh failed: unavailable", ledger: "different model family",
					"empty range": "empty external bot commit range",
				};
				assert.ok(result?.reason?.includes(reasons[scenario]), result?.reason);
				if (scenario !== "ledger") assert.ok(result?.reason?.includes("authorship unknown"));
				if (scenario === "empty range") assert.deepEqual(checked, [], "empty ranges must not query commit metadata");
			}
		} finally { h.cleanup(); }
	});
}

test("linked worktrees resolve the main checkout's record and verifier context selects only the named source", async () => {
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
		writeFileSync(join(linked, "source.md"), "substantive documentation");
		await h.read(structure, naming);
		h.context.models.current = () => ({ provider: "anthropic", id: "writer" });
		assert.equal(await h.call("edit", { path: join(linked, "source.md") }), undefined);
		h.context.cwd = h.root;
		const head = (await execute("git", ["rev-parse", "HEAD"], { cwd: linked })).stdout.trim();
		const contextDir = join(h.root, ".git/omp-prove");
		mkdirSync(contextDir, { recursive: true });
		const contextPath = join(contextDir, "verifier-context.json");
		const scope = { schemaVersion: 1, worktree: realpathSync(linked), branch: "refs/heads/agent/source", head, parent: head };
		writeFileSync(contextPath, JSON.stringify(scope));
		assert.equal((await h.emit("before_subagent_spawn", { agent: "verifier", patterns: ["anthropic/review"] }))?.block, true);
		assert.equal(await h.emit("before_subagent_spawn", { agent: "verifier", patterns: ["openai/sol:medium"] }), undefined);
		writeFileSync(contextPath, JSON.stringify({ ...scope, head: "a".repeat(40) }));
		assert.ok((await h.emit("before_subagent_spawn", { agent: "verifier", patterns: ["openai/sol:medium"] }))?.reason?.includes("stale verifier context"));
		writeFileSync(contextPath, JSON.stringify({ ...scope, worktree: `${linked}-missing` }));
		assert.ok((await h.emit("before_subagent_spawn", { agent: "verifier", patterns: ["openai/sol:medium"] }))?.reason?.includes("stale verifier context"));
		h.context.cwd = linked;
		await h.read("skill://git-tool");
		assert.equal((await h.call("bash", { command: `gh pr merge 3 --match-head-commit=${head}` }))?.block, true);
	} finally { h.cleanup(); rmSync(linked, { recursive: true, force: true }); }
});

test("prove dispatch enforces verifier family independence with dynaflows off", async () => {
	const h = harness();
	const unknown = harness();
	try {
		for (const source of [h, unknown]) {
			source.dynaflows(false);
			await execute("git", ["-c", "user.name=Test", "-c", "user.email=test@example.test", "commit", "--allow-empty", "-m", "seed"], { cwd: source.root });
			const head = (await execute("git", ["rev-parse", "HEAD"], { cwd: source.root })).stdout.trim();
			const contextDir = join(source.root, ".git/omp-prove");
			mkdirSync(contextDir, { recursive: true });
			writeFileSync(join(contextDir, "verifier-context.json"), JSON.stringify({
				schemaVersion: 1, worktree: source.root, branch: "refs/heads/agent/test", parent: head, head,
			}));
		}
		await h.read(integrity, structure, naming);
		assert.equal(await h.call("edit", { path: join(h.root, "existing.ts") }), undefined);
		const sameFamily = await h.emit("before_subagent_spawn", { agent: "verifier", patterns: ["openai/review"] });
		assert.equal(sameFamily?.block, true, "prove() must block an author-family verifier even with dynaflows off");
		assert.ok(sameFamily?.reason?.includes("different model family"), JSON.stringify(sameFamily));
		assert.equal(await h.emit("before_subagent_spawn", { agent: "verifier", patterns: ["anthropic/review"] }), undefined);
		assert.deepEqual((await h.emit("before_subagent_spawn", {
			agent: "verifier", patterns: ["openai/review", "anthropic/review"],
		}))?.model, ["anthropic/review"]);
		const missingLedger = await unknown.emit("before_subagent_spawn", { agent: "verifier", patterns: ["anthropic/review"] });
		assert.equal(missingLedger?.block, true, "prove() must fail closed without an author ledger even with dynaflows off");
		assert.ok(missingLedger?.reason?.includes("authorship unknown"), JSON.stringify(missingLedger));
	} finally { h.cleanup(); unknown.cleanup(); }
});

test("dynaflows off ignores unresolved models while on rejects every unresolved candidate", async () => {
	const h = harness();
	try {
		assert.equal(await h.emit("before_subagent_spawn", { agent: "verifier", patterns: ["unresolved"] }), undefined);
		await h.read(integrity);
		await h.call("edit", { path: "existing.ts" });
		h.dynaflows(true);
		assert.ok((await h.emit("before_subagent_spawn", { agent: "verifier", patterns: ["anthropic/review", "unresolved"] }))?.reason?.includes("every verifier"));
		assert.equal((await h.emit("before_subagent_spawn", { agent: "verifier", patterns: [] }))?.block, true);
	} finally { h.cleanup(); }
});

test("Python proof gates and rails accept the same schema fixtures", async () => {
	// Each case sits over a current-contract, dynaflows-off record; the contract floor itself is
	// checked apart from the schema, in the contract tests below.
	const record = { dynaflowsContract: currentContract, dynaflows: { enabled: false } };
	const cases: object[] = [
		{ schemaVersion: 2 }, { schemaVersion: 2, checks: {} },
		{ schemaVersion: 2, checks: { commit: ["bun", "check"], ci: ["ci"], format: ["fmt"], profile: ["profile"] }, pathCheck: ["check"], pathCheckTimeoutMs: 1, testGlobs: ["", "tests/**"], agentMerge: "allowed", ci: { requiredContext: "gate", runner: "ci-gate" } },
		{ schemaVersion: 2, pathCheckTimeoutMs: 9007199254740991, ci: { requiredContext: "build", runner: "github-actions" } },
		{ schemaVersion: 2, ciHost: "github" }, { schemaVersion: 2, ciHost: "nuc" }, { schemaVersion: 2, ciHost: null },
		{ schemaVersion: 2, ci: null }, { schemaVersion: 2, ci: {} }, { schemaVersion: 2, ci: { requiredContext: " ", runner: "ci-gate" } },
		{ schemaVersion: 2, ci: { requiredContext: "gate" } }, { schemaVersion: 2, ci: { requiredContext: "gate", runner: 1 } },
		{ schemaVersion: 2, checks: { format: [] } }, { schemaVersion: 2, checks: { profile: [" "] } }, { schemaVersion: 2, checks: { format: "fmt" } },
		{ schemaVersion: 2, checks: null }, { schemaVersion: 2, checks: { commit: null } },
		{ schemaVersion: 2, checks: { ci: [] } }, { schemaVersion: 2, pathCheck: [" "] },
		{ schemaVersion: 2, pathCheckTimeoutMs: true }, { schemaVersion: 2, pathCheckTimeoutMs: 1.5 },
		{ schemaVersion: 2, pathCheckTimeoutMs: 0 }, { schemaVersion: 2, pathCheckTimeoutMs: 9007199254740992 },
		{ schemaVersion: 2, testGlobs: [1] }, { schemaVersion: 2, testGlobs: null },
		{ schemaVersion: 2, agentMerge: "maybe" },
		{ schemaVersion: true }, { schemaVersion: 3 },
		{ schemaVersion: 2, dynaflowsContract: 9007199254740991 },
		{ schemaVersion: 2, dynaflowsContract: 0 }, { schemaVersion: 2, dynaflowsContract: -1 }, { schemaVersion: 2, dynaflowsContract: 1.5 },
		{ schemaVersion: 2, dynaflowsContract: "2" }, { schemaVersion: 2, dynaflowsContract: true }, { schemaVersion: 2, dynaflowsContract: null },
		{ schemaVersion: 2, dynaflowsContract: 9007199254740992 },
		{ schemaVersion: 2, dynaflows: { enabled: true } }, { schemaVersion: 2, dynaflows: null },
		{ schemaVersion: 2, dynaflows: {} }, { schemaVersion: 2, dynaflows: { enabled: "yes" } },
	].map(value => ({ ...record, ...value }));
	cases.push({ schemaVersion: 2, dynaflowsContract: currentContract });
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

test("record edits do not author a change; docs do, and corrupt or different branch ledgers fail closed", async () => {
	const h = harness();
	try {
		h.dynaflows(true);
		await h.read(structure, naming);
		// The config root is itself a Git checkout, as oh-mi-pi-config is; its ignored records are harness config.
		execFileSync("git", ["init", "-b", "main", h.records]);
		const gate = join(h.recordDir(), "gate.json");
		assert.equal(await h.call("write", { path: gate, content: JSON.stringify({ ...h.record(), pathCheck: ["check"] }) }), undefined);
		assert.equal(existsSync(join(h.records, ".git/omp-authors")), false, "a record write records no author");
		const verifier = { agent: "verifier", patterns: ["anthropic/review"] };
		assert.ok((await h.emit("before_subagent_spawn", verifier))?.reason?.includes("authorship unknown"));
		assert.equal(await h.call("write", { path: join(h.root, "README.md") }), undefined);
		assert.equal(await h.emit("before_subagent_spawn", verifier), undefined);
		const key = createHash("sha256").update(`${h.root}\0refs/heads/agent/test`).digest("hex");
		const ledger = join(h.root, ".git/omp-authors", `${key}.json`);
		assert.equal(JSON.parse(readFileSync(ledger, "utf8")).authors[0].family, "openai");
		await execute("git", ["symbolic-ref", "HEAD", "refs/heads/agent/other"], { cwd: h.root });
		assert.ok((await h.emit("before_subagent_spawn", verifier))?.reason?.includes("authorship unknown"));
		await execute("git", ["symbolic-ref", "HEAD", "refs/heads/agent/test"], { cwd: h.root });
		writeFileSync(ledger, "{broken");
		assert.ok((await h.emit("before_subagent_spawn", verifier))?.reason?.includes("authorship unknown"));
	} finally { h.cleanup(); }
});

test("mise commits pass the tool-call handler and record authors in a real repository", async () => {
	const h = harness();
	try {
		await h.read("skill://git-tool");
		h.gate({ schemaVersion: 2, checks: { commit: ["true"] }, dynaflows: { enabled: true } });
		const result = await h.emit("tool_call", { toolName: "bash", input: { command: "mise exec -- git commit -m x" } });
		assert.equal(result, undefined);
		const common = (await execute("git", ["rev-parse", "--git-common-dir"], { cwd: h.root })).stdout.trim();
		const key = createHash("sha256").update(`${h.root}\0refs/heads/agent/test`).digest("hex");
		const ledger = join(h.root, common, "omp-authors", `${key}.json`);
		assert.equal(JSON.parse(readFileSync(ledger, "utf8")).authors[0].family, "openai");
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
		for (const command of [
			`mise x -C '${target.root}' -- git commit -m x`,
			`mise exec --cd='${target.root}' node@20 -- git commit -m x`,
		]) {
			assert.equal(await h.call("bash", { command }), undefined);
			const key = createHash("sha256").update(`${target.root}\0refs/heads/agent/test`).digest("hex");
			const ledger = JSON.parse(readFileSync(join(target.root, ".git/omp-authors", `${key}.json`), "utf8"));
			assert.equal(ledger.worktree, target.root);
		}
		target.gate({ schemaVersion: 2, checks: { commit: ["false"] }, dynaflows: { enabled: true } });
		const enforced = await h.call("bash", { command: `mise x -C '${target.root}' -- git commit -m x` });
		assert.ok(enforced?.reason?.includes(`checks.commit (false) failed in ${target.root}`), "the wrapped repo's record is enforced");
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

test("uv and poetry separator commits record author ledgers", async () => {
	for (const launcher of ["uv run", "poetry run"]) {
		const h = harness();
		try {
			await h.read("skill://git-tool");
			assert.equal(await h.emit("tool_call", {
				toolName: "bash", input: { command: `${launcher} -- git commit -m x` },
			}), undefined);
			const key = createHash("sha256").update(`${h.root}\0refs/heads/agent/test`).digest("hex");
			const ledger = JSON.parse(readFileSync(join(h.root, ".git/omp-authors", `${key}.json`), "utf8"));
			assert.equal(ledger.authors[0].family, "openai", launcher);
		} finally { h.cleanup(); }
	}
});

describe("dynaflows contract version", () => {
	const stale = "Run omp-guardrails init --extend to bring the per-repo record to the current contract " +
		"(it edits the local record, never the repo); ask the owner only when it adds a gate or profile field the record lacks";
	const newer = "The record expects a newer dynaflows contract; update omp";
	const scenarios = [
		{ name: "absent", recorded: undefined, refused: true, expected: `Dynaflows contract vnone recorded; current is v${currentContract}. ${stale}` },
		{ name: "lower", recorded: currentContract - 1, refused: true, expected: `Dynaflows contract v${currentContract - 1} recorded; current is v${currentContract}. ${stale}` },
		{ name: "equal", recorded: currentContract, refused: false, expected: undefined },
		{ name: "higher", recorded: currentContract + 1, refused: false, expected: `Dynaflows contract v${currentContract + 1} recorded; current is v${currentContract}. ${newer}` },
	];
	for (const scenario of scenarios) {
		test(`dynaflows on, recorded contract ${scenario.name}: ${scenario.refused ? "refused" : scenario.expected ? "notes once" : "stays silent"}`, async () => {
			assert.ok(currentContract > 1, "a lower contract must still be a positive int");
			const h = harness();
			try {
				h.gate({ schemaVersion: 2, dynaflowsContract: scenario.recorded });
				h.dynaflows(true);
				const pointer = await h.emit("before_agent_start", { reason: "prompt", prompt: "work" });
				await h.emit("before_agent_start", { reason: "prompt", prompt: "more work" });
				const contractNotes = h.notes.filter(note => note.includes("Dynaflows contract"));
				if (scenario.refused) {
					assert.equal(pointer, undefined, "a refused record leaves dynaflows off");
					assert.deepEqual(contractNotes, [`Agent rails: ${join(h.recordDir(), "gate.json")}: ${scenario.expected}.; dynaflows off.`], "one note per session");
					await h.read("skill://git-tool");
					const blocked = await h.call("bash", { command: "git push" });
					assert.equal(blocked?.block, true);
					assert.ok(blocked?.reason?.includes(scenario.expected ?? ""), blocked?.reason);
				} else if (scenario.expected) {
					assert.deepEqual(contractNotes, [`Agent rails: ${scenario.expected}.`], "one note per session");
					assert.ok(JSON.stringify(pointer).includes(scenario.expected), "the agent sees the same text");
				} else {
					assert.deepEqual(contractNotes, []);
					assert.equal(JSON.stringify(pointer).includes("Dynaflows contract"), false);
				}
			} finally { h.cleanup(); }
		});
	}

	test("gate.json accepts only a positive integer dynaflowsContract", async () => {
		const h = harness();
		try {
			await h.read("skill://git-tool");
			for (const dynaflowsContract of [0, -1, 1.5, "2", true, null, 9007199254740992]) {
				h.rawGate(JSON.stringify({ schemaVersion: 2, dynaflowsContract, dynaflows: { enabled: false } }));
				const blocked = await h.call("bash", { command: "git push" });
				assert.equal(blocked?.block, true, JSON.stringify(dynaflowsContract));
				assert.ok(blocked?.reason?.includes("dynaflowsContract"), blocked?.reason);
			}
			h.gate({ schemaVersion: 2, dynaflowsContract: currentContract });
			assert.equal(await h.call("bash", { command: "git push" }), undefined);
		} finally { h.cleanup(); }
	});

	test("agent-rails.yml declares the contract once as a positive integer; invalid values fail the load", async () => {
		const source = readFileSync(railsConfig, "utf8");
		assert.equal(source.match(/^dynaflowsContract: \d+$/gm)?.length, 1, "workflows.py reads this single line");
		const dir = mkdtempSync(join(tmpdir(), "agent-rails-config-"));
		try {
			for (const value of ["0", "-1", "1.5", "'2'", "true", null]) {
				const configPath = join(dir, "agent-rails.yml");
				writeFileSync(configPath, value === null
					? source.replace(/^dynaflowsContract: .*\n/m, "")
					: source.replace(/^dynaflowsContract: .*$/m, `dynaflowsContract: ${value}`));
				const h = harness({ configPath });
				try {
					await h.emit("session_start");
					assert.ok(h.notes.some(note => note.includes("cannot load agent-rails.yml") && note.includes("dynaflowsContract")), `${value}: ${h.notes.join("; ")}`);
				} finally { h.cleanup(); }
			}
		} finally { rmSync(dir, { recursive: true, force: true }); }
	});
});
