import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, relative } from "node:path";
import { describe, test } from "node:test";
import { promisify } from "node:util";
import { z, type ExtensionAPI, type ExtensionContext } from "@oh-my-pi/pi-coding-agent";
import skillGate from "../agent/extensions/skill-gate";

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
	schemaVersion: 1; fastGate?: string[]; testGlobs?: string[];
	pathCheck?: string[] | string; pathCheckTimeoutMs?: number; agentMerge?: "allowed" | "disabled";
	sensitive?: unknown;
};
type Input = { path?: string; paths?: string[]; content?: string; command?: string; cwd?: string };
type Details = { exitCode?: number; applied?: boolean; files?: string[]; totalReplacements?: number };
type Event = {
	type: string; toolName?: string; toolCallId?: string; input?: Input;
	details?: Details; isError?: boolean; reason?: string;
	agent?: string; patterns?: string[]; modelRole?: string; invocationKind?: string;
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
	const root = mkdtempSync(join(tmpdir(), "skill-gate-"));
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
	// SAFETY: this fake supplies on, registerCommand and exec, the only API surfaces skillGate consumes.
	skillGate(api as ExtensionAPI);
	let id = 0;
	async function emit(name: string, event: Omit<Event, "type"> = {}) {
		const handler = handlers.get(name);
		assert.ok(handler, `missing handler ${name}`);
		// SAFETY: the fake provides cwd, agent.kind, ui.notify and both sessionManager methods consumed by handlers.
		return handler({ type: name, ...event }, context as ExtensionContext);
	}
	return {
		root, notes, branch, context, emit, api,
		cleanup() { rmSync(root, { recursive: true, force: true }); },
		setSession(value: string) { sessionId = value; },
		gate(value: GateFixture) { writeFileSync(join(root, ".omp/gate.json"), JSON.stringify(value)); },
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

describe("skill gate", () => {
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
			h.gate({ schemaVersion: 1, testGlobs: ["checks/**"] });
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
			h.gate({ schemaVersion: 1, pathCheck: [process.execPath, checker] });
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
			h.gate({ schemaVersion: 1, pathCheck: "not an argv array" });
			assert.equal((await h.call("edit", { path: "existing.ts" }))?.block, true);
			assert.ok(h.notes.some(note => note.includes("invalid")));
			await h.read(integrity);
			assert.equal(await h.call("edit", { path: "existing.ts" }), undefined);
		} finally { h.cleanup(); }
	});

	test("handoff remains blocked until verification and an exact fast gate run after the latest edit", async () => {
		const h = harness();
		try {
			h.gate({ schemaVersion: 1, fastGate: ["bun", "check"] });
			await h.read(integrity);
			await h.call("bash", { command: "bun check" }, { exitCode: 0 });
			await h.call("edit", { path: "existing.ts" });
			const blocked = await h.emit("session_stop");
			assert.equal(blocked?.decision, "block");
			assert.ok(blocked?.reason?.includes("bun check"));
			assert.ok(blocked?.reason?.includes(verification));
			await h.read(verification);
			await h.call("bash", { command: "bun check" }, { exitCode: 0 });
			assert.equal(await h.emit("session_stop"), undefined);
			await h.call("edit", { path: "existing.ts" });
			await h.call("bash", { command: "bun check && echo done" }, { exitCode: 0 });
			assert.equal((await h.emit("session_stop"))?.decision, "block");
			assert.equal((await h.emit("session_stop"))?.decision, "block", "retry does not bypass unmet requirements");
			await h.call("bash", { command: "bun check" }, { exitCode: 1 }, true);
			assert.equal(await h.emit("session_stop"), undefined, "a failed gate still supplies verification evidence");
		} finally { h.cleanup(); }
	});

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
			h.gate({ schemaVersion: 1 });
			for (const command of commands) assert.equal(await h.call("bash", { command }), undefined);
			h.gate({ schemaVersion: 1, agentMerge: "disabled" });
			for (const command of commands) {
				const blocked = await h.call("bash", { command });
				assert.equal(blocked?.block, true, command);
				assert.ok(blocked?.reason?.includes("ask the owner"));
			}
			assert.equal(await h.call("bash", { command: "git commit -m fix" }), undefined);
			assert.equal(await h.call("bash", { command: `git -C '${other.root}' push` }), undefined);
			other.gate({ schemaVersion: 1, agentMerge: "disabled" });
			assert.equal((await h.call("bash", { command: `git -C '${other.root}' push` }))?.block, true);
			// cwd-changing commands must select the destination repo, not the session repo.
			for (const action of ["git push", "gh pr create", "gh pr merge 7"]) {
				h.gate({ schemaVersion: 1 });
				for (const prefix of [`cd '${other.root}'`, `pushd '${other.root}'`, `cd '${relative(h.root, other.root)}' && cd .`]) {
					assert.equal((await h.call("bash", { command: `${prefix} && ${action}` }))?.block, true, prefix);
				}
				for (const launcher of ["env -C", "sudo -D"]) {
					assert.equal((await h.call("bash", { command: `${launcher} '${other.root}' ${action}` }))?.block, true, launcher);
				}
				h.gate({ schemaVersion: 1, agentMerge: "disabled" });
				other.gate({ schemaVersion: 1 });
				assert.equal(await h.call("bash", { command: `cd '${other.root}' && ${action}` }), undefined);
				for (const launcher of ["env -C", "sudo -D"]) {
					assert.equal(await h.call("bash", { command: `${launcher} '${other.root}' ${action}` }), undefined, launcher);
				}
				other.gate({ schemaVersion: 1, agentMerge: "disabled" });
				for (const prefix of ["cd", "cd -", "cd missing-dir", 'cd "$TARGET"']) {
					const blocked = await h.call("bash", { command: `${prefix} && ${action}` });
					assert.equal(blocked?.block, true, prefix);
					assert.ok(blocked?.reason?.includes("effective cwd"));
				}
			}
			for (const invalid of ["{broken", '{"schemaVersion":2}']) {
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

	test("failed path checks persist at handoff and rerun to observe out-of-tool repairs", async () => {
		const h = harness();
		try {
			const checker = join(h.root, "check.cjs");
			writeFileSync(checker, "process.exit(1)");
			h.gate({ schemaVersion: 1, pathCheck: [process.execPath, checker] });
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

	test("main handoff detects delegated tree changes but allows unchanged pre-existing dirt", async () => {
		const h = harness();
		try {
			await execute("git", ["init"], { cwd: h.root });
			await execute("git", ["add", "existing.ts"], { cwd: h.root });
			await execute("git", ["-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture"], { cwd: h.root });
			h.gate({ schemaVersion: 1, fastGate: ["bun", "check"] });
			writeFileSync(join(h.root, "existing.ts"), "pre-existing dirt\n");
			await h.emit("session_start");
			assert.equal(await h.emit("session_stop"), undefined);
			writeFileSync(join(h.root, "existing.ts"), "delegated edit\n");
			assert.equal((await h.emit("session_stop"))?.decision, "block");
			assert.equal((await h.emit("session_stop"))?.decision, "block");
			await h.read(verification);
			await h.call("bash", { command: "bun check" }, { exitCode: 1 }, true);
			assert.equal(await h.emit("session_stop"), undefined);
			writeFileSync(join(h.root, "existing.ts"), "another delegated edit\n");
			assert.equal((await h.emit("session_stop"))?.decision, "block");
			await h.call("bash", { command: "bun check" }, { exitCode: 0 });
			assert.equal(await h.emit("session_stop"), undefined);
		} finally { h.cleanup(); }
	});

	test("fingerprints work before the first commit and detect changes to existing untracked files", async () => {
		const h = harness();
		try {
			await execute("git", ["init"], { cwd: h.root });
			h.gate({ schemaVersion: 1, fastGate: ["bun", "check"] });
			await h.emit("session_start");
			assert.equal(await h.emit("session_stop"), undefined, "unchanged unborn repo is allowed");
			writeFileSync(join(h.root, "existing.ts"), "untracked content changed\n");
			assert.equal((await h.emit("session_stop"))?.decision, "block");
			await h.read(verification);
			await h.call("bash", { command: "bun check" }, { exitCode: 0 });
			assert.equal(await h.emit("session_stop"), undefined);
			await execute("git", ["add", "existing.ts"], { cwd: h.root });
			assert.equal((await h.emit("session_stop"))?.decision, "block", "staging is fingerprinted without HEAD");
			await h.call("bash", { command: "bun check" }, { exitCode: 0 });
			assert.equal(await h.emit("session_stop"), undefined);
			writeFileSync(join(h.root, "existing.ts"), "tracked content changed before first commit\n");
			assert.equal((await h.emit("session_stop"))?.decision, "block");
			await h.call("bash", { command: "bun check" }, { exitCode: 0 });
			assert.equal(await h.emit("session_stop"), undefined);
		} finally { h.cleanup(); }
	});

	test("fingerprint subprocess errors and timeouts do not block fastGate or replace explicit edit gates", async () => {
		for (const failure of ["exit", "timeout", "throw"]) {
			const h = harness();
			try {
				await execute("git", ["init"], { cwd: h.root });
				h.gate({ schemaVersion: 1, fastGate: ["bun", "check"] });
				await h.emit("session_start");
				const exec = h.api.exec;
				h.api.exec = async (command, args, options) => {
					if (command !== "git" || args[0] !== "diff") return exec(command, args, options);
					if (failure === "throw") throw new Error("cannot execute git");
					return { code: failure === "exit" ? 1 : 0, killed: failure === "timeout", stdout: "", stderr: failure };
				};
				await h.read(integrity, verification);
				await h.call("edit", { path: "existing.ts" });
				assert.equal((await h.emit("session_stop"))?.decision, "block", "explicit edit gate remains active");
				assert.equal(await h.call("bash", { command: "bun check" }, { exitCode: 0 }), undefined);
				assert.equal(await h.emit("session_stop"), undefined, failure);
			} finally { h.cleanup(); }
		}
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

	test("injects workflow pointer only for an ordinary main prompt once, excluding subagents", async () => {
		const h = harness();
		try {
			assert.equal(await h.emit("before_agent_start", { reason: "revive" }), undefined);
			assert.ok(JSON.stringify(await h.emit("before_agent_start", { reason: "prompt" })).includes("skill://omp-dyna-workflows"));
			assert.equal(await h.emit("before_agent_start", { reason: "prompt" }), undefined);
			h.setSession("sub"); h.context.agent.kind = "sub";
			assert.equal(await h.emit("before_agent_start", { reason: "prompt" }), undefined);
			await h.read(integrity); await h.call("edit", { path: "existing.ts" });
			assert.equal(await h.emit("session_stop"), undefined);
		} finally { h.cleanup(); }
	});

	test("checks AST mutation on resolve, not its preview", async () => {
		const h = harness();
		try {
			h.gate({ schemaVersion: 1, pathCheck: [process.execPath, "-e", "console.error('bad path'); process.exit(1)"] });
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
	{ command: "gh pr merge 42", files: "auth/login.ts\n", blocked: true, selector: "42", repo: [] },
	{ command: "gh pr merge", files: "src/public.ts\n", blocked: false, selector: undefined, repo: [] },
	{ command: "gh -R owner/repo pr merge 42", files: "secrets.env.local\n", blocked: true, selector: "42", repo: ["-R", "owner/repo"] },
	{ command: "gh pr merge 42 --repo=owner/repo", files: "authentication.ts\n", blocked: false, selector: "42", repo: ["--repo=owner/repo"] },
	{ command: "cd nested && gh pr merge 42", files: "auth/a.ts\n", blocked: true, selector: "42", repo: [], nested: true },
	{ command: "gh pr merge --subject 'release title' 42", files: "secrets.env\n", blocked: true, selector: "42", repo: [] },
	{ command: "gh pr -R owner/repo merge 42", files: "src/a.ts\n", blocked: false, selector: "42", repo: ["-R", "owner/repo"] },
	{ command: "gh pr merge 42", files: "src/auth/login.ts\n", blocked: true, selector: "42", repo: [] },
	{ command: "gh pr merge 42", files: "config/secrets.env.local\n", blocked: true, selector: "42", repo: [] },
	{ command: "gh pr merge 42", files: "src/authentication/x.ts\n", blocked: false, selector: "42", repo: [] },
	{ command: "gh pr merge 42", files: "auth\nsrc/auth\n", blocked: false, selector: "42", repo: [] },
	{ command: "gh pr merge 42", files: "config/secrets.env/public.ts\n", blocked: false, selector: "42", repo: [] },
	{ command: "gh pr merge 42", files: `${Array.from({ length: 112 }, (_, index) => `src/public-${index}.ts`).join("\n")}\nconfig/secrets.env\n`, blocked: true, selector: "42", repo: [] },
]) {
	test(`sensitive merge policy: ${scenario.command}`, async () => {
		const h = harness();
		try {
			await execute("git", ["init"], { cwd: h.root });
			mkdirSync(join(h.root, "nested"));
			h.gate({ schemaVersion: 1, sensitive: ["auth/", "secrets.env"] });
			await h.read("skill://git-tool");
			const exec = h.api.exec;
			const queries: { args: string[]; cwd: string }[] = [];
			h.api.exec = async (command, args, options) => {
				if (command !== "gh") return exec(command, args, options);
				queries.push({ args, cwd: options.cwd });
				return {
					code: 0, killed: false, stderr: "",
					stdout: args[0] === "api" ? scenario.files : JSON.stringify({ number: 42, url: "https://github.com/owner/repo/pull/42" }),
				};
			};
			const result = await h.call("bash", { command: scenario.command });
			assert.equal(result?.block === true, scenario.blocked);
			if (scenario.blocked) assert.ok(result?.reason?.includes("owner must merge PRs touching sensitive paths"));
			assert.deepEqual(queries, [{
				args: [...scenario.repo, "pr", "view", ...(scenario.selector ? [scenario.selector] : []), "--json", "number,url"],
				cwd: scenario.nested ? join(h.root, "nested") : h.root,
			}, {
				args: ["api", "--hostname", "github.com", "--paginate", "repos/owner/repo/pulls/42/files", "--jq", ".[].filename"],
				cwd: scenario.nested ? join(h.root, "nested") : h.root,
			}]);
		} finally { h.cleanup(); }
	});
}

for (const stage of ["metadata", "files", "malformed-metadata", "invalid-repository", "metadata-timeout", "files-timeout"]) {
	test(`sensitive merge fails closed on ${stage}`, async () => {
		const h = harness();
		try {
			await execute("git", ["init"], { cwd: h.root });
			h.gate({ schemaVersion: 1, sensitive: ["auth/"] });
			await h.read("skill://git-tool");
			const exec = h.api.exec;
			h.api.exec = async (command, args, options) => {
				if (command !== "gh") return exec(command, args, options);
				const files = args[0] === "api";
				const failed = stage === (files ? "files" : "metadata");
				const killed = stage === (files ? "files-timeout" : "metadata-timeout");
				return {
					code: failed ? 1 : 0, killed, stderr: failed ? "query failed" : "",
					stdout: files ? "src/public.ts\n" : stage === "malformed-metadata" ? "broken" :
						JSON.stringify({ number: 42, url: stage === "invalid-repository" ? "https://github.com/not-a-pr" : "https://github.com/owner/repo/pull/42" }),
				};
			};
			const result = await h.call("bash", { command: "gh pr merge 42" });
			assert.equal(result?.block, true);
			assert.ok(result?.reason?.includes("policy could not be evaluated"));
		} finally { h.cleanup(); }
	});
}

for (const sensitive of ["auth/", 1, null, [true]]) {
	test(`rejects invalid sensitive field: ${JSON.stringify(sensitive)}`, async () => {
		const h = harness();
		try {
			await execute("git", ["init"], { cwd: h.root });
			h.gate({ schemaVersion: 1, sensitive });
			await h.read("skill://git-tool");
			const result = await h.call("bash", { command: "gh pr merge 42" });
			assert.equal(result?.block, true);
			assert.ok(result?.reason?.includes("invalid gate"));
		} finally { h.cleanup(); }
	});
}

for (const scenario of [
	{ author: "task", patterns: ["anthropic/claude-author"], blocked: true },
	{ author: "task", patterns: ["openai/gpt-author"], blocked: false },
	{ author: "sonic", patterns: ["anthropic/claude-author"], blocked: true },
	{ author: "sonic", patterns: ["openai/gpt-author"], blocked: false },
	{ author: "scout", patterns: ["anthropic/claude-author"], blocked: false },
	{ author: "task", patterns: [], blocked: false },
	{ author: "task", patterns: ["openai/gpt-author", "anthropic/claude-fallback"], blocked: true },
]) {
	test(`verifier family policy: ${scenario.author} ${scenario.patterns.join(",")}`, async () => {
		const h = harness();
		try {
			await h.emit("before_subagent_spawn", { agent: scenario.author, patterns: scenario.patterns, invocationKind: "task" });
			await h.emit("session_tree");
			const result = await h.emit("before_subagent_spawn", { agent: "verifier", patterns: ["@verifier"], invocationKind: "eval" });
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
		await h.emit("before_subagent_spawn", { agent: "task", patterns: ["openai/gpt"] });
		const result = await h.emit("before_subagent_spawn", {
			agent: "verifier", patterns: ["openai/gpt-review", "anthropic/claude-review"],
		});
		assert.deepEqual(result?.model, ["anthropic/claude-review"]);
	} finally { h.cleanup(); }
});

test("skill gate rejects a fast gate started before an overlapping edit", async () => {
	const h = harness();
	try {
		h.gate({ schemaVersion: 1, fastGate: ["bun", "check"] });
		await h.read(integrity, verification);
		await h.call("edit", { path: "existing.ts" });
		const event = { toolName: "bash", toolCallId: "overlapping", input: { command: "bun check" } };
		await h.emit("tool_call", event);
		await h.call("edit", { path: "existing.ts" });
		await h.emit("tool_result", { ...event, details: { exitCode: 0 }, isError: false });
		assert.equal((await h.emit("session_stop"))?.decision, "block");
	} finally { h.cleanup(); }
});

test("skill gate surfaces a timed-out path checker without throwing", async () => {
	const h = harness();
	// Exercise the real subprocess kill boundary; fake JS timers cannot control execFile's OS child.
	try {
		h.gate({
			schemaVersion: 1,
			pathCheck: [process.execPath, "-e", "setTimeout(() => {}, 60000)"],
			pathCheckTimeoutMs: 30,
		});
		assert.ok((await h.call("edit", { path: "existing.ts" }))?.additionalContext?.includes("pathCheck failed"));
	} finally { h.cleanup(); }
});
