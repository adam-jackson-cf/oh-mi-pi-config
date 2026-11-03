import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
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
	pathCheck?: string[] | string; pathCheckTimeoutMs?: number;
};
type Input = { path?: string; paths?: string[]; content?: string; command?: string; cwd?: string };
type Details = { exitCode?: number; applied?: boolean; files?: string[]; totalReplacements?: number };
type Event = {
	type: string; toolName?: string; toolCallId?: string; input?: Input;
	details?: Details; isError?: boolean; reason?: string;
};
type Result = { block?: boolean; reason?: string; decision?: string; additionalContext?: string; message?: object };
type Handler = (event: Event, ctx: ExtensionContext) => Result | void | Promise<Result | void>;
const execFailureSchema = z.object({
	code: z.number().optional().catch(undefined),
	killed: z.boolean().optional(),
	stdout: z.string().optional(),
	stderr: z.string().optional(),
});
const execute = promisify(execFile);

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
		root, notes, branch, context, emit,
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

	test("handoff requires verification and an exact successful fast gate after the latest edit", async () => {
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
			await h.call("bash", { command: "bun check" }, { exitCode: 1 }, true);
			assert.equal((await h.emit("session_stop"))?.decision, "block");
			assert.equal(await h.emit("session_stop"), undefined, "refusal is once per edit revision");
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
			for (const command of ["git status && git diff", "echo 'git commits'", "npm run gh-pr-merge"]) {
				assert.equal(await h.call("bash", { command }), undefined, command);
			}
			for (const command of ["git commit -m 'feat: x'", "cd repo && git push -u origin agent/x", "gh pr merge 7 --rebase"]) {
				const blocked = await h.call("bash", { command });
				assert.equal(blocked?.block, true, command);
				assert.ok(blocked?.reason?.includes("skill://git-tool"));
			}
			await h.read("skill://git-tool");
			assert.equal(await h.call("bash", { command: "git rebase origin/main" }), undefined);
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
