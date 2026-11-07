import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join, relative } from "node:path";
import { describe, test } from "node:test";
import { promisify } from "node:util";
import { z, type ExtensionAPI, type ExtensionContext } from "@oh-my-pi/pi-coding-agent";
import agentRails from "../agent/extensions/agent-rails";

const integrity = "skill://coding-guidance/references/implementation-integrity.md";
const structure = "skill://coding-guidance/references/structure-and-grouping.md";
const naming = "skill://coding-guidance/references/naming.md";
const tiers = "skill://test-guidance/references/tiers.md";
const verification = "skill://coding-guidance/references/verification.md";
const discovery = [
	"skill://codegraph/references/readiness-and-bootstrap.md",
	"skill://codegraph/references/query-selection.md",
];
type GateFixture = {
	schemaVersion: 2; checks?: { commit?: string[]; ci?: string[] }; testGlobs?: string[];
	pathCheck?: string[] | string; pathCheckTimeoutMs?: number; agentMerge?: "allowed" | "disabled";
};
type Input = { path?: string; paths?: string[]; input?: string; content?: string; command?: string; cwd?: string };
type Answer = { question?: string; selectedOptions?: string[]; timedOut?: boolean };
type Details = Answer & { applied?: boolean; files?: string[]; totalReplacements?: number; results?: Answer[] };
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
	"sudo env -i sh -c 'git push'", "gh -R o/r pr merge 3", "gh --repo=o/r pr create",
	"env -S 'git push'", "env --split-string='git push'", "env -S git push",
];

function harness() {
	const root = mkdtempSync(join(tmpdir(), "agent-rails-"));
	const machine = mkdtempSync(join(tmpdir(), "agent-rails-machine-"));
	mkdirSync(join(root, ".git"));
	mkdirSync(join(root, ".omp"));
	writeFileSync(join(root, "existing.ts"), "export const answer = 42;\n");
	const handlers = new Map<string, Handler>();
	const notes: string[] = [];
	const branch: object[] = [];
	let sessionId = "first";
	const context = {
		cwd: root,
		agent: { kind: "main" },
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
		async exec(command: string, args: string[], options: { cwd: string; timeout: number }) {
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
	// SAFETY: this fake supplies on, registerCommand and exec, the only API surfaces agentRails consumes.
	agentRails(api as ExtensionAPI, { machineDynaflowsPath: join(machine, "dynaflows.json") });
	let id = 0;
	async function emit(name: string, event: Omit<Event, "type"> = {}) {
		const handler = handlers.get(name);
		assert.ok(handler, `missing handler ${name}`);
		// SAFETY: the fake provides cwd, agent.kind, ui.notify and both sessionManager methods consumed by handlers.
		return handler({ type: name, ...event }, context as ExtensionContext);
	}
	return {
		root, notes, branch, context, emit, api,
		cleanup() { for (const dir of [root, machine]) rmSync(dir, { recursive: true, force: true }); },
		setSession(value: string) { sessionId = value; },
		gate(value: GateFixture) { writeFileSync(join(root, ".omp/gate.json"), JSON.stringify(value)); },
		dynaflows(enabled: boolean) { writeFileSync(join(root, ".omp/dynaflows.json"), JSON.stringify({ schemaVersion: 1, enabled })); },
		machineDynaflows(enabled: boolean) { writeFileSync(join(machine, "dynaflows.json"), JSON.stringify({ schemaVersion: 1, enabled })); },
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
			const result = await h.call("edit", { paths: ["existing.ts", "another.ts"] });
			assert.ok(result?.additionalContext?.includes(join(h.root, "existing.ts")));
			assert.ok(result?.additionalContext?.includes(join(h.root, "another.ts")));
			assert.ok(result?.additionalContext?.includes("use feature ownership"));
			assert.equal(await h.call("edit", { path: "existing.ts" }, {}, true), undefined);
		} finally { h.cleanup(); }
	});

	test("absent and invalid gates fall back to reference enforcement without throwing", async () => {
		const h = harness();
		try {
			assert.equal((await h.call("edit", { path: "existing.ts" }))?.block, true);
			assert.ok(h.notes.some(note => note.includes("absent")));
			writeFileSync(join(h.root, ".omp/gate.json"), "{broken");
			assert.equal((await h.call("edit", { path: "existing.ts" }))?.block, true);
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
				await execute("git", ["init"], { cwd: h.root });
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

	test("codegraph blocks discovery until both references, once per session and again after compaction", async () => {
		const h = harness();
		try {
			assert.equal(await h.call("find", { path: "omp://" }), undefined);
			for (const tool of ["find", "grep", "glob", "ast_grep"]) {
				const blocked = await h.call(tool, {});
				assert.equal(blocked?.block, true);
				for (const url of discovery) assert.ok(blocked?.reason?.includes(url));
			}
			await h.read(discovery[0]);
			assert.equal((await h.call("glob", { path: "**/*.ts" }))?.block, true);
			await h.read(discovery[1]);
			assert.equal(await h.call("glob", { path: "**/*.ts" }), undefined);
			assert.equal(await h.call("grep", {}), undefined);
			await h.emit("session_compact");
			assert.equal((await h.call("find", {}))?.block, true);
		} finally { h.cleanup(); }
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
				assert.ok(blocked?.reason?.includes("skill://git-tool"));
			}
			await h.read("skill://git-tool");
			assert.equal(await h.call("bash", { command: "git rebase origin/main" }), undefined);
		} finally { h.cleanup(); }
	});

	test("repo merge policy fails closed for publishing actions, respecting git -C", async () => {
		const h = harness();
		const other = harness();
		try {
			await h.read("skill://git-tool");
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
			for (const invalid of ["{broken", '{"schemaVersion":1}', '{"schemaVersion":3}', '{"schemaVersion":2,"checks":{"commit":"bun check"}}', '{"schemaVersion":2,"ciHost":"other"}']) {
				writeFileSync(join(h.root, ".omp/gate.json"), invalid);
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

	test("a declared commit check needs an installed executable pre-commit hook, honouring core.hooksPath", async () => {
		const h = harness();
		try {
			await execute("git", ["init"], { cwd: h.root });
			await h.read("skill://git-tool");
			const commit = () => h.call("bash", { command: "git commit -m x" });
			assert.equal(await commit(), undefined, "no gate.json: nothing to enforce");
			h.gate({ schemaVersion: 2, checks: { ci: ["bun", "ci"] } });
			assert.equal(await commit(), undefined, "no commit check declared");
			h.gate({ schemaVersion: 2, checks: { commit: ["bun", "check"] } });
			assert.ok((await commit())?.reason?.includes("no pre-commit hook is installed"));
			const hook = join(h.root, ".git/hooks/pre-commit");
			mkdirSync(join(h.root, ".git/hooks"), { recursive: true });
			writeFileSync(hook, "#!/bin/sh\nexit 0\n");
			chmodSync(hook, 0o644);
			assert.equal((await commit())?.block, true, "Git skips a non-executable hook");
			chmodSync(hook, 0o755);
			assert.equal(await commit(), undefined);
			await execute("git", ["config", "core.hooksPath", ".githooks"], { cwd: h.root });
			assert.equal((await commit())?.block, true, "core.hooksPath moves the hook Git runs");
			mkdirSync(join(h.root, ".githooks"));
			writeFileSync(join(h.root, ".githooks/pre-commit"), "#!/bin/sh\nexit 0\n", { mode: 0o755 });
			assert.equal(await commit(), undefined);
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

	test("$dynaflows persists a project setting that overrides the machine default; only main prompts toggle it", async () => {
		const h = harness();
		try {
			assert.equal(await h.emit("before_agent_start", { reason: "prompt", prompt: "pull latest" }), undefined, "off by default");
			h.machineDynaflows(true);
			assert.equal(await h.emit("before_agent_start", { reason: "revive", prompt: "" }), undefined);
			const pointer = JSON.stringify(await h.emit("before_agent_start", { reason: "prompt", prompt: "next" }));
			assert.ok(pointer.includes("skill://omp-dyna-workflows"), "machine default turns it on");
			assert.ok(pointer.includes("guardrail"), "missing guardrails start the sign-off flow");
			assert.equal(await h.emit("before_agent_start", { reason: "prompt", prompt: "again" }), undefined, "pointer once per session");
			const off = JSON.stringify(await h.emit("before_agent_start", { reason: "prompt", prompt: "stop $dynaflows off" }));
			assert.ok(off.includes("Dynaflows off") && !off.includes("skill://omp-dyna-workflows"));
			assert.deepEqual(JSON.parse(readFileSync(join(h.root, ".omp/dynaflows.json"), "utf8")), { schemaVersion: 1, enabled: false });
			assert.ok(readFileSync(join(h.root, ".git/info/exclude"), "utf8").split("\n").includes("/.omp/dynaflows.json"));
			h.setSession("restarted");
			assert.equal(await h.emit("before_agent_start", { reason: "prompt", prompt: "work" }), undefined, "project off outlives the session and overrides machine on");
			assert.equal(await h.emit("before_agent_start", { reason: "prompt", prompt: "price $dynaflowsy" }), undefined, "keyword needs a word boundary");
			assert.ok(JSON.stringify(await h.emit("before_agent_start", { reason: "prompt", prompt: "$dynaflows" })).includes("skill://omp-dyna-workflows"));
			h.setSession("sub"); h.context.agent.kind = "sub";
			assert.equal(await h.emit("before_agent_start", { reason: "prompt", prompt: "$dynaflows off" }), undefined, "subagents cannot toggle dynaflows");
			assert.equal(JSON.parse(readFileSync(join(h.root, ".omp/dynaflows.json"), "utf8")).enabled, true);
		} finally { h.cleanup(); }
	});

	test("dynaflows blocks guardrail writes until the owner picks a proposal option via ask", async () => {
		const h = harness();
		try {
			await h.read(integrity, structure, naming);
			assert.equal(await h.call("write", { path: ".omp/gate.json" }), undefined, "no sign-off with dynaflows off");
			h.dynaflows(true);
			writeFileSync(join(h.root, ".omp/guardrail-proposal.json"), JSON.stringify({
				schemaVersion: 1, minimal: ["scripts/fast-gate.sh"], full: ["scripts/fast-gate.sh", "scripts/full-gate.sh"],
			}));
			assert.ok((await h.call("write", { path: ".omp/gate.json" }))?.reason?.includes("sign-off"));
			assert.equal((await h.call("write", { path: "scripts/full-gate.sh" }))?.block, true);
			assert.equal(await h.call("write", { path: "other.ts" }), undefined, "non-guardrail paths are unaffected");
			await h.call("ask", {}, { question: "Which guardrail option?", selectedOptions: ["Minimal"], timedOut: true });
			assert.equal((await h.call("write", { path: ".omp/gate.json" }))?.block, true, "a timeout auto-selection is not sign-off");
			await h.call("ask", {}, { question: "Which lint profile?", selectedOptions: ["Minimal"] });
			assert.equal((await h.call("write", { path: ".omp/gate.json" }))?.block, true, "unrelated questions are not sign-off");
			await h.call("ask", {}, { results: [{ question: "Which guardrail option?", selectedOptions: ["Minimal"] }] });
			assert.equal(await h.call("write", { path: ".omp/gate.json" }), undefined);
			assert.equal(await h.call("write", { path: "scripts/fast-gate.sh" }), undefined);
			assert.ok((await h.call("write", { path: "scripts/full-gate.sh" }))?.reason?.includes("minimal"), "the unchosen option stays blocked");
			h.gate({ schemaVersion: 2, checks: { commit: ["bun", "check"] }, pathCheck: [process.execPath, "-e", "0"] });
			assert.equal(await h.call("write", { path: "scripts/full-gate.sh" }), undefined, "complete guardrails end the sign-off phase");
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
			assert.equal(await h.call("write", { path: join(h.root, "loop") }), undefined, "filesystem errors fail open for this supplemental check");
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

for (const scenario of [
	{ author: "task", patterns: ["anthropic/claude-author"], blocked: true },
	{ author: "task", patterns: ["openai/gpt-author"], blocked: false },
	{ author: "sonic", patterns: ["anthropic/claude-author"], blocked: true },
	{ author: "designer", patterns: ["anthropic/claude-author"], blocked: true },
	{ author: "designer", patterns: ["openai/gpt-author"], blocked: false },
	{ author: "scout", patterns: ["anthropic/claude-author"], blocked: false },
	{ author: "task", patterns: [], blocked: false },
	{ author: "task", patterns: ["openai/gpt-author", "anthropic/claude-fallback"], blocked: true },
]) {
	test(`verifier family policy: ${scenario.author} ${scenario.patterns.join(",")}`, async () => {
		const h = harness();
		try {
			await h.emit("before_subagent_spawn", { agent: scenario.author, patterns: scenario.patterns, invocationKind: "task" });
			await h.emit("session_tree");
			const verifier = { agent: "verifier", patterns: ["@verifier"], invocationKind: "eval" };
			assert.equal(await h.emit("before_subagent_spawn", verifier), undefined, "family policy needs dynaflows");
			h.dynaflows(true);
			const result = await h.emit("before_subagent_spawn", verifier);
			assert.equal(result?.block === true, scenario.blocked);
			if (scenario.blocked) assert.ok(result?.reason?.includes("different model family"));
			h.setSession("other");
			assert.equal(await h.emit("before_subagent_spawn", { agent: "verifier", patterns: ["@verifier"] }), undefined);
		} finally { h.cleanup(); }
	});
}

test("verifier filters same-family fallbacks from its model chain", async () => {
	const h = harness();
	try {
		h.dynaflows(true);
		await h.emit("before_subagent_spawn", { agent: "task", patterns: ["openai/gpt"] });
		const result = await h.emit("before_subagent_spawn", {
			agent: "verifier", patterns: ["openai/gpt-review", "anthropic/claude-review"],
		});
		assert.deepEqual(result?.model, ["anthropic/claude-review"]);
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
