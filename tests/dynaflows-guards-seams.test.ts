import assert from "node:assert/strict";
import { execFile, execFileSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, renameSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { dirname, join, relative, resolve } from "node:path";
import { test } from "bun:test";
import { Settings, z, type ExtensionContext } from "@oh-my-pi/pi-coding-agent";
import dynaflowsExtension from "../agent/extensions/dynaflows/index";
import { parseOrigin } from "../agent/extensions/dynaflows/lib/repo-record.ts";
import type { Mode } from "../agent/extensions/dynaflows/lib/modes.ts";

const integrity = "skill://structured-workflow/references/delivery-principles.md";
const structure = "skill://structured-workflow/references/structure-and-naming.md";
const enforcement = "skill://structured-workflow/references/test-enforcement.md";
const tiers = "skill://test-guidance/references/tiers.md";
const verification = "skill://test-guidance/references/before-commit.md";
type GateFixture = {
	schemaVersion: 2; checks?: { commit?: string[]; ci?: string[]; format?: string[]; profile?: string[] };
	agentMerge?: "allowed" | "disabled";
	ci?: { requiredContext: string; runner: string };
	afterMerge?: { argv: string[]; paths?: string[] };
	mergePolicies?: { base: string; headPrefix: string; method: "rebase" | "merge"; proof: boolean }[];
};
type Input = {
	path?: string; paths?: string[]; input?: string; content?: string; command?: string; cwd?: string; questions?: { question: string }[]; intent?: string;
	code?: string; op?: string; language?: string;
};
type Details = { applied?: boolean; files?: string[]; totalReplacements?: number };
type Event = {
	type: string; cwd?: string; toolName?: string; toolCallId?: string; input?: Input;
	details?: Details; isError?: boolean; reason?: string;
	agent?: string; patterns?: string[]; modelRole?: string; invocationKind?: string; prompt?: string; willContinue?: boolean;
};
type Result = { block?: boolean; reason?: string; decision?: string; additionalContext?: string; message?: object; model?: string[] };
type Handler = Function;
type FixtureContext = {
	cwd: string; agent: ExtensionContext["agent"];
	ui: { notify(message: string, level?: string): void };
	sessionManager: { getSessionId(): string; getBranch(): object[] };
};
type Tool = { name: string; execute: Function };
const toolResultSchema = z.object({ content: z.array(z.object({ text: z.string() })) });
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
	"timeout 5 git push", "/usr/bin/git push",
	"if x; then git push; fi", "{ git push; }", "true; do git push; done", "false; else git push",
	"! git push", "bash -c 'git push'", 'sh -c "git push"', "zsh -lc 'git push'",
	"sudo env -i sh -c 'git push'",
	"env -S 'git push'", "env --split-string='git push'", "env -S git push",
];

const mergeRecover = "skill://git-tool/references/merge-and-recover.md";
const railsConfig = join(import.meta.dir, "../agent/extensions/dynaflows/rails.yml");
const defaultOrigin = "git@github.com:owner/repo.git";
const recordGateSchema = z.object({ schemaVersion: z.number() }).passthrough();
const modesFileSchema = z.object({ schemaVersion: z.literal(1), repos: z.record(z.string(), z.enum(["minimal", "structured"])) }).strict();

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

type ExtensionOptions = NonNullable<Parameters<typeof dynaflowsExtension>[1]>;
type CommandChoices = { configPath: string | undefined; ompCommand: string[] | undefined; guardrailsCommand: string[] | "default" };

// Judged rails run the real judgment registry under the root uv project; only the judge transport is stubbed.
const decideScript = resolve(import.meta.dir, "../agent/extensions/dynaflows/lib/decide.py");
const decideCommand = ["uv", "run", "--locked", "--project", resolve(import.meta.dir, ".."), "python", decideScript];
const judgeModel = "typesafe/jev-1.14.0";
type JudgeStub = { scores: Record<string, number>; calls: { question: string; state: unknown }[]; model: string; failure?: string | undefined };
function judgeStub(): JudgeStub { return { scores: {}, calls: [], model: judgeModel }; }

// Fixture thresholds that mark each rail judgment ready, so harness rails take the enforced path; the
// shipped judgments are provisional and run in shadow (tests pass judgmentsDir: shippedJudgments).
const railQuestions = { "extension-impact": "impact", "adr-needed": "adr_needed", "in-scope": "ask_first",
	"implementation-integrity": "weakens_verification", "completion-evidence": "evidence_gap" };
const shippedJudgments = join(import.meta.dir, "../agent/extensions/dynaflows/judgments");
function readyJudgments(machine: string): string {
	const dir = join(machine, "judgments");
	for (const [name, question] of Object.entries(railQuestions)) {
		mkdirSync(join(dir, name), { recursive: true });
		writeFileSync(join(dir, name, "thresholds.json"), JSON.stringify({
			status: "ready", decisions: { [question]: { status: "ready", minimums: { precision: 0.8, recall: 0.9, assumed: false } } },
		}));
	}
	return dir;
}

function extensionOptions(machine: string, records: string, choices: CommandChoices, judged: JudgeStub): ExtensionOptions {
	const options: ExtensionOptions = {
		judgmentsDir: readyJudgments(machine),
		modesPath: join(records, "modes.json"), configRoot: records,
		// The judgment call log goes to this harness's machine dir, never ~/.omp/agent.
		decideCommand: ["sh", "-c", 'PI_CODING_AGENT_DIR="$0" exec "$@"', machine, ...decideCommand],
		judge: () => async (state, questions) => {
			if (judged.failure) throw new Error(judged.failure);
			const answers = Object.fromEntries(Object.keys(questions).map(question => {
				judged.calls.push({ question, state });
				return [question, { type: "bool" as const, bool: judged.scores[question] ?? 0 }];
			}));
			return { answers, model: judged.model };
		},
		reviewsPath: join(machine, "reviews.json"), extensionDir: join(machine, "extension"), guardrailsProfileCommand: ["true"],
		ompCommand: choices.ompCommand ?? ["sh", "-c", 'cat "$1"', "omp", join(machine, "worktree-base")],
	};
	if (choices.configPath !== undefined) options.configPath = choices.configPath;
	if (choices.guardrailsCommand !== "default") options.guardrailsCommand = choices.guardrailsCommand;
	return options;
}

function harness({ configPath, configRoot, remote = defaultOrigin, guardrailsCommand = ["true"], ompCommand, extra = {} }: {
	configPath?: string; configRoot?: string; remote?: string; guardrailsCommand?: string[] | "default"; ompCommand?: string[];
	extra?: Partial<ExtensionOptions>;
} = {}) {
	const root = realpathSync(mkdtempSync(join(tmpdir(), "dynaflows-guards-")));
	const machine = mkdtempSync(join(tmpdir(), "dynaflows-guards-machine-"));
	const ownedRecords = configRoot === undefined;
	const records = configRoot ?? realpathSync(mkdtempSync(join(tmpdir(), "dynaflows-guards-records-")));
	execFileSync("git", ["init", "-b", "agent/test", root]);
	execFileSync("git", ["remote", "add", "origin", remote], { cwd: root });
	execFileSync("git", ["symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main"], { cwd: root });
	writeFileSync(join(root, "existing.ts"), "export const answer = 42;\n");
	const handlers = new Map<string, Handler[]>();
	const tools = new Map<string, Tool>();
	const notes: string[] = [];
	const errors: string[] = [];
	const branch: object[] = [];
	let sessionId = "first";
	let pathFailure: string | undefined;
	const identity: ExtensionContext["agent"] = { kind: "main", id: root, name: "main", depth: 0 };
	const context: FixtureContext = {
		cwd: root,
		agent: identity,
		ui: { notify(message: string, level?: string) { notes.push(message); if (level === "error") errors.push(message); } },
		sessionManager: { getSessionId: () => sessionId, getBranch: () => branch },
	};
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
	const judged = judgeStub();
	const options = { ...extensionOptions(machine, records, { configPath, ompCommand, guardrailsCommand }, judged), ...extra };
	writeFileSync(join(machine, "worktree-base"), "(not set)\n");
	mkdirSync(join(machine, "extension"));
	writeFileSync(join(machine, "extension", "index.ts"), "export {};\n");
	const modesPath = join(records, "modes.json");
	if (!existsSync(modesPath)) writeFileSync(modesPath, JSON.stringify({ schemaVersion: 1, repos: {} }));
	dynaflowsExtension(api, options);
	let fixture: GateFixture | undefined;
	let slug = parseOrigin(remote);
	const modes = () => modesFileSchema.parse(JSON.parse(readFileSync(modesPath, "utf8"))).repos;
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
		writeRecord(JSON.stringify({ checks: { commit: ["true"] }, ...(fixture ?? { schemaVersion: 2 }) }));
	};
	let id = 0;
	async function emit(name: string, event: Omit<Event, "type"> = {}) {
		let combined: Result | undefined;
		for (const handler of handlers.get(name) ?? []) {
			const result: Result | void = await handler({ type: name, ...event }, context);
			if (result?.block || result?.decision === "block") return result;
			if (result) combined = mergeResults(combined, result);
		}
		return combined;
	}
	return {
		root, notes, errors, branch, context, emit, api, options, records, recordDir, judged,
		pathFailure(message?: string) { pathFailure = message; },
		cleanup() { for (const dir of [root, machine, ...(ownedRecords ? [records] : [])]) rmSync(dir, { recursive: true, force: true }); },
		setSession(value: string) { sessionId = value; },
		machine,
		async tool(name: string, params: { mode?: Mode; rollout?: true; acknowledge?: true; content?: string }) {
			const tool = tools.get(name);
			assert.ok(tool, `missing tool ${name}`);
			return toolResultSchema.parse(await tool.execute("tool-1", params, undefined, undefined, context)).content.map(part => part.text).join("");
		},
		gate(value: GateFixture) { fixture = value; writeGate(); },
		worktreeBase(value: string) { writeFileSync(join(machine, "worktree-base"), `${value}\n`); },
		mode(value: Mode) {
			assert.ok(slug, "the harness origin names a mode entry");
			writeFileSync(modesPath, JSON.stringify({ schemaVersion: 1, repos: { ...modes(), [slug]: value } }));
			writeGate();
		},
		modeOf(): Mode { return slug ? modes()[slug] ?? "minimal" : "minimal"; },
		modesPath,
		rawGate(text: string) { writeRecord(text); },
		record() { return recordGateSchema.parse(JSON.parse(readFileSync(join(recordDir(), "gate.json"), "utf8"))); },
		async origin(url: string) {
			const previous = slug ? join(records, "repos", slug) : undefined;
			await execute("git", ["remote", "set-url", "origin", url], { cwd: root });
			slug = parseOrigin(url);
			if (previous && existsSync(previous) && slug && previous !== recordDir()) {
				mkdirSync(dirname(recordDir()), { recursive: true });
				renameSync(previous, recordDir());
			}
		},
		modesText(text: string) { writeFileSync(modesPath, text); },
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
	// omp delivers every handler's before_agent_start message.
	if (combined?.message && result.message) merged.message = { messages: [combined.message, result.message] };
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

type PrView = { head?: string; selector?: string; failure?: string; url?: string; base?: string; headRef?: string; state?: string; body?: string; files?: object[] };
// A PR whose description has the request and evidence sections the completion pre-checks read.
const prBody = "## Summary\n\nFixes the answer.\n\n## Evidence\n\n- `bun test` passed on the head.";
const prFiles = [{ path: "existing.ts", additions: 1, deletions: 0, changeType: "MODIFIED" }];

function mockPrCi(h: PrCiHarness, rollup: object[], view: PrView = {}) {
	const { head = ciHead, selector, failure, url = "https://github.com/owner/repo/pull/3", base = "main", headRef = "agent/test", state = "OPEN",
		body = prBody, files = prFiles } = view;
	const exec = h.api.exec.bind(h.api);
	h.api.exec = async (command, args, options) => {
		if (command !== "gh") return exec(command, args, options);
		if (args[0] === "repo") return { code: 0, killed: false, stdout: JSON.stringify({ name: "repo", description: "a fixture repository" }), stderr: "" };
		assert.equal(options.cwd, h.root);
		assert.equal(options.timeout, 20000);
		assert.deepEqual(args.slice(0, -1), ["pr", "view", ...(selector ? [selector] : []), "--json"]);
		assert.ok(args.at(-1)?.split(",").includes("headRefOid"));
		assert.ok(args.at(-1)?.split(",").includes("statusCheckRollup"));
		return failure
			? { code: 1, killed: false, stdout: "", stderr: failure }
			: { code: 0, killed: false, stdout: JSON.stringify({
				number: 3, title: "Fix the answer", body, commits: [{ messageHeadline: "fix: the answer" }], files,
				headRefOid: head, baseRefName: base, headRefName: headRef, state, statusCheckRollup: rollup, url,
			}), stderr: "" };
	};
}

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

type EvidenceHarness = { root: string; emit(name: string, event: Omit<Event, "type">): Promise<Result | undefined> };
let evidenceCall = 0;

// Writes proof evidence the way prove() and structure_review() do: inside an eval cell the rails watch.
async function watchedProofCall(h: EvidenceHarness, write: () => void, code = "prove(repo, parent, head, frozen, objective, target_ref='origin/main')") {
	const event = { toolName: "eval", toolCallId: `proof-${++evidenceCall}`, input: { code } };
	assert.equal(await h.emit("tool_call", event), undefined, "the proof cell itself is allowed");
	write();
	await h.emit("tool_result", { ...event, isError: false });
}

async function proveResult(h: EvidenceHarness, head = ciHead, status = "pass") {
	await watchedProofCall(h, () => {
		mkdirSync(join(h.root, ".git/omp-prove"), { recursive: true });
		writeFileSync(join(h.root, ".git/omp-prove", `${head}.result.json`), JSON.stringify({ status, head, blocking: [] }));
	});
}

async function structureResult(h: EvidenceHarness, head = ciHead) {
	await watchedProofCall(h, () => {
		mkdirSync(join(h.root, ".git/omp-structure"), { recursive: true });
		writeFileSync(join(h.root, ".git/omp-structure", `${head}.json`),
			JSON.stringify({ head, halt: false, status: "shadow", findings: [] }));
	}, "await structure_review(repo, parent, head)");
}

export { prBody, prFiles, decideCommand, judgeModel, shippedJudgments };
export type { JudgeStub };
export { harness, mergeRecover, execute, reasonOf, isBlocked, wrappedPublishing, railsConfig, defaultOrigin, integrity, structure, enforcement, tiers, verification, ciHead, greenCi, mockPrCi, forbidGh, ciGate, ciRunner, originRemote, proveResult, structureResult, watchedProofCall };
export { repoCommand };
export type { Input };

	test("integration git commands need git-tool, while inspection and look-alikes pass", async () => {
		const h = harness();
		try {
			for (const command of [
				"git status && git diff", "git log --grep=commit", 'rg "git push"', 'echo "git commit"',
				"echo 'git commits'", "npm run gh-pr-merge", "git switch existing", "git checkout existing",
				"git -C /w status", "git -c color.ui=never log", "gh pr view", "git stash list", "git stash", "git fetch origin", "git fetch origin main",
				"git fetch https://github.com/o/r main", "gh repo view", "gh issue list", "gh run list", "gh release view v1", "git config --get core.hooksPath",
				"git tag", "git tag -l", "git tag --list 'v*'", "git tag -n5", "git tag --contains HEAD", "git branch", "git branch -a",
				"git branch --list", "git worktree list", "gh api repos/o/r/pulls", "gh api -X GET repos/o/r", "gh api --method=HEAD x",
				"gh pr checks 3", "gh pr diff 3",
								"command -v git push", "sudo git status", "env -i git diff", 'sh -c "git log --grep=commit"',
			]) {
				assert.equal(await h.call("bash", { command }), undefined, command);
			}
			for (const command of [
				"git commit -m 'feat: x'", "cd repo && git push -u origin agent/x", "gh pr merge 7 --rebase",
				"git -C /w commit", "git -c user.name=x commit", "git --no-pager commit", "git status\ngit commit",
				"FOO=1 git push", "git switch -c agent/x", "git switch -C agent/x", "git switch --create=agent/x",
				"git checkout -b agent/x", "git checkout -B agent/x", "git worktree add /w",
				'git -C "/some path" -c core.pager=cat --no-pager push',
				"git status; git push", "true | git push", "(git push)",
				"FOO=x BAR=y gh pr create",
				"git status; git merge x", "false || git rebase x", "git cherry-pick abc", "git reset --hard HEAD~1", "git reset",
				"git tag v1", "git tag -a v1 -m x", "git tag -d v1", "git branch -d old", "git branch -D old", "git branch --delete old",
				"git branch -v -D old", "git worktree remove /w", "gh pr edit 3 --title x", "gh pr close 3",
				'FOO="a b" git push', "FOO='x;y' BAR=z git commit", "git -C'/some path' push",
				"git revert HEAD", "git am patch", "git pull", "git branch -f main HEAD", "git branch -m old new",
				"git branch -M old new", "git stash drop", "git stash clear", "git stash pop", "git restore .", "git clean -fdx", "git fetch origin main:main",
				"git fetch origin +refs/heads/main:refs/heads/main", "gh pr ready 3", "gh pr review 3 --approve", "gh pr comment 3 -b x", "gh pr reopen 3",
				"gh release create v1", "gh workflow run ci.yml", "gh issue create -t x", "gh run rerun 5",
				...wrappedPublishing,
			]) {
				assertBlocked(await h.call("bash", { command }), "skill://git-tool", command);
			}
			// Writes the rails refuse outright, git-tool read or not.
			for (const command of ["gh api -X POST repos/o/r/issues", "gh api --method PATCH repos/o/r", "gh api --method=DELETE x", "gh api -XPUT x", "gh api repos/o/r/issues -f title=x", "gh api graphql -F query=@q.graphql", "gh api x --input body.json", "git update-ref refs/heads/main HEAD", "gh repo delete owner/repo", "gh secret set X"]) {
				assertBlocked(await h.call("bash", { command }), "Agent rails", command);
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
			const unmerged = commands.filter(command => command !== "gh pr merge 7");
			for (const command of unmerged) assert.equal(await h.call("bash", { command }), undefined, command);
			assertBlocked(await h.call("bash", { command: "gh pr merge 7" }), "minimal mode");
			h.gate({ schemaVersion: 2 });
			for (const command of unmerged) assert.equal(await h.call("bash", { command }), undefined, command);
			h.gate({ schemaVersion: 2, agentMerge: "disabled" });
			for (const command of commands) {
				assertBlocked(await h.call("bash", { command }), "ask the owner", command);
			}
			assert.equal(await h.call("bash", { command: "git commit -m fix" }), undefined);
			assert.equal(await h.call("bash", { command: `git -C '${other.root}' push` }), undefined);
			other.gate({ schemaVersion: 2, agentMerge: "disabled" });
			assert.equal(await bashBlocked(h, `git -C '${other.root}' push`), true);
			for (const action of ["git push", "gh pr create"]) await checkDestinationRepository(h, other, action);
			for (const invalid of ["{broken", '{"schemaVersion":3}', '{"schemaVersion":2,"checks":{"commit":"bun check"}}', '{"schemaVersion":2,"otherField":"github"}', '{"schemaVersion":2,"ci":{"requiredContext":"gate"}}', '{"schemaVersion":2,"dynaflows":{"mode":"structured"}}']) {
				h.rawGate(invalid);
				for (const command of commands) {
					const reason = assertBlocked(await h.call("bash", { command }), "gate.json", command);
					assert.ok(reason.includes("Error"));
				}
			}
		} finally { h.cleanup(); other.cleanup(); }
	}, 30_000);

	test("commits must run the pre-commit hook: bypass flags, hook-path overrides and any touch of a skip variable block", async () => {
		const h = harness();
		try {
			await h.read("skill://git-tool");
			for (const command of [
				"git commit --no-verify -m x", "git commit -n -m x", "git commit -anm x", "git commit --no-ver -m x",
				"git -c core.hooksPath=/dev/null commit -m x", "git -c CORE.HOOKSPATH=x commit -m x",
				"LEFTHOOK=0 git commit -m x",
				"SKIP=checks git commit -m x", "HUSKY=0 git commit -m x", "env PRE_COMMIT_ALLOW_NO_CONFIG=1 git commit -m x",
				"GIT_CONFIG_COUNT=1 git commit -m x", "bash -c 'git commit --no-verify -m x'",
				"GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath bash -c 'git commit -m x'",
				"LEFTHOOK=0 sh -c 'cd . && git commit -m x'", "export HUSKY=0; git commit -m x",
				"env -S 'SKIP=x' git commit -m x", "export HUSKY=1; HUSKY=0; git commit -m x",
				"HUSKY=0; export HUSKY; git commit -m x", "unset HUSKY; export HUSKY; HUSKY=0; git commit -m x",
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
			assert.equal(await h.call("write", { path: join(tmpdir(), "dynaflows-guards-scratch.txt") }), undefined);
		} finally { h.cleanup(); }
	});

	test("contains subagent symlink writes within the lexical repo, including new descendants", async () => {
		const h = harness();
		const outside = mkdtempSync(join(tmpdir(), "dynaflows-guards-outside-"));
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


test("pulling commits in (merge, rebase, cherry-pick, revert, am, pull) needs the Git reads but no harness check beyond hook-bypass flags", async () => {
	const h = harness();
	try {
		h.gate({ schemaVersion: 2, checks: { commit: ["false"] } });
		h.mode("structured");
		forbidGh(h);
		assert.equal(await h.call("bash", { command: "git merge-base HEAD HEAD" }), undefined);
		const writes = ["git cherry-pick -n HEAD", "git merge origin/main", "git rebase origin/main", "git revert HEAD", "git am patch", "git pull"];
		for (const command of writes) assertBlocked(await h.call("bash", { command }), "skill://git-tool", command);
		await h.read("skill://git-tool", mergeRecover);
		for (const command of writes) assert.equal(await h.call("bash", { command }), undefined, `no checks.commit run: ${command}`);
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

test("shell indirection fails closed: stdin shells, variable command words, unknown launchers, scripts and inline code", async () => {
	const h = harness();
	try {
		await h.read("skill://git-tool");
		const refused: [string, string][] = [
			["echo 'gh pr merge 5 --rebase' | bash", "reading commands from stdin"],
			["bash <<< 'gh pr merge 5'", "reading commands from stdin"],
			["cat merge.txt | sh -s # gh", "reading commands from stdin"],
			["X=gh; $X pr merge 5", "variable or substitution"],
			["g=git; $g push origin main", "variable or substitution"],
			["$(echo gh) pr merge 5", "variable or substitution"],
			["`echo gh` pr merge 5", "variable or substitution"],
			["printf 'gh pr merge 5' > m.sh; bash m.sh", "does not exist before this command"],
			["printf 'gh pr merge 5' > m.sh && ./m.sh", "does not exist before this command"],
			["python3 -c 'import subprocess; subprocess.run([\"gh\", \"pr\", \"merge\", \"5\"])'", "inline python3 code"],
			["node -e 'require(\"child_process\").execSync(\"gh pr merge 5\")'", "inline node code"],
			["perl -e '`gh pr merge 5`'", "perl runs git, gh, hub or omp"],
			["python3 -c 'print(\"git\")'", "inline python3 code"],
			["curl -X PUT https://api.github.com/repos/owner/repo/pulls/5/merge", "writes to the GitHub API"],
			["curl -d '{}' https://api.github.com/repos/owner/repo/merges", "writes to the GitHub API"],
			["wget --method=PUT https://api.github.com/repos/owner/repo/pulls/5/merge", "writes to the GitHub API"],
			["http PUT https://api.github.com/repos/owner/repo/pulls/5/merge", "writes to the GitHub API"],
			["gh alias set mm 'pr merge'", "gh alias set defines a command"],
			["gh alias import aliases.yml", "gh alias import defines a command"],
		];
		for (const [command, fragment] of refused) assertBlocked(await h.call("bash", { command }), fragment, command);
		for (const command of ["dash -c 'gh pr merge 5'", "ksh -c 'gh pr merge 5'", "fish -c 'gh pr merge 5'", "mksh -c 'gh pr merge 5'"]) {
			assertBlocked(await h.call("bash", { command }), "minimal mode", command);
		}
		for (const command of [
			"find . -exec gh pr merge 5 \\;", "stdbuf -oL gh pr merge 5", "script -q /dev/null gh pr merge 5", "caffeinate -i gh pr merge 5",
			"arch -arm64 gh pr merge 5", "doas gh pr merge 5", "chronic gh pr merge 5", "ionice -c3 gh pr merge 5",
		]) assertBlocked(await h.call("bash", { command }), "where the rails cannot check it", command);
		writeFileSync(join(h.root, "merge.sh"), "#!/usr/bin/env bash\nset -e\nmerge() {\n  gh pr merge \"$1\" --rebase\n}\nmerge 5\n");
		execFileSync("chmod", ["+x", join(h.root, "merge.sh")]);
		for (const command of ["./merge.sh", "bash merge.sh", "sh ./merge.sh", `${h.root}/merge.sh`, "source merge.sh", "timeout 60 bash merge.sh"]) {
			assert.equal(isBlocked(await h.call("bash", { command })), true, command);
		}
		writeFileSync(join(h.root, "commit.sh"), "#!/bin/sh\ngit commit --no-verify -m x\n");
		assertBlocked(await h.call("bash", { command: "sh commit.sh" }), "pre-commit hook");
		writeFileSync(join(h.root, "tidy.sh"), "#!/bin/sh\necho tidy\n");
		for (const command of [
			"echo 'gh pr merge 5'", "echo gh pr merge 5 | cat", "bash tidy.sh", "./tidy.sh", "bash -n tidy.sh", "printf x | bash",
			"$EDITOR notes.md", "python3 -c 'print(1)'", "node -e 'console.log(1)'",
			"curl https://api.github.com/repos/owner/repo/pulls/5", "curl -X PUT https://example.com/pulls/5/merge",
			"gh alias list", "rg git src", "which gh", "brew upgrade gh", "find . -name '*.ts'", "/usr/bin/true",
		]) assert.equal(await h.call("bash", { command }), undefined, command);
		const commitTier = join(import.meta.dir, "../scripts/run-ci-quality-gates.sh");
		assert.equal(isBlocked(await h.call("bash", { command: `bash ${commitTier} --tier commit` })), false, "the repository's own gate script parses");
	} finally { h.cleanup(); }
});

test("gh aliases and extensions are refused: they run commands the rails cannot check", async () => {
	const h = harness();
	const previous = process.env["GH_CONFIG_DIR"];
	const config = mkdtempSync(join(tmpdir(), "dynaflows-gh-config-"));
	try {
		process.env["GH_CONFIG_DIR"] = config;
		execFileSync("gh", ["alias", "set", "mm", "pr merge"], { env: process.env });
		execFileSync("gh", ["alias", "set", "--shell", "sm", "gh pr merge \"$1\" --rebase"], { env: process.env });
		await h.read("skill://git-tool");
		for (const command of ["gh mm 5 --rebase", "gh sm 5", "gh unknown-extension"]) assertBlocked(await h.call("bash", { command }), "not a built-in gh command", command);
		assert.equal(await h.call("bash", { command: "gh pr view 5" }), undefined);
	} finally {
		restoreEnvironment("GH_CONFIG_DIR", previous);
		rmSync(config, { recursive: true, force: true });
		h.cleanup();
	}
});

test("Git writes through the eval and github tools are refused", async () => {
	const h = harness();
	try {
		for (const code of [
			"import subprocess\nsubprocess.run(['gh', 'pr', 'merge', '5'])", "import os\nos.system('git push origin main')",
			"const { execSync } = require('node:child_process'); execSync('gh pr merge 5');", "await Bun.$`git push`", "!git commit --no-verify -m x",
		]) assertBlocked(await h.call("eval", { code }), "eval code that runs git or gh", code);
		for (const code of ["print('git is a tool')", "subprocess.run(['ls'])", "prove()"]) assert.equal(await h.call("eval", { code }), undefined, code);
		for (const op of ["pr_create", "pr_push", "pr_checkout"]) assertBlocked(await h.call("github", { op }), `github tool's ${op}`, op);
		for (const op of ["repo_view", "search_prs", "run_watch"]) assert.equal(await h.call("github", { op }), undefined, op);
	} finally { h.cleanup(); }
});

test("git config hook-path and alias writes are refused; an in-repo hook directory and reads pass", async () => {
	const h = harness();
	try {
		mkdirSync(join(h.root, ".githooks"));
		writeFileSync(join(h.root, ".githooks/pre-commit"), "#!/bin/sh\n");
		for (const command of [
			"git config core.hooksPath /dev/null", "git config core.hooksPath ''", "git config --global core.hooksPath .githooks",
			"git config --unset core.hooksPath", "git config core.hooksPath missing", "git config core.hooksPath ../elsewhere",
			"git config set core.hooksPath /tmp/hooks", "git config CORE.HOOKSPATH /tmp", "git config --local core.hooksPath ~/hooks",
			"git config --global alias.x 'commit --no-verify'", "git config alias.ci commit",
		]) assertBlocked(await h.call("bash", { command }), "git config", command);
		for (const command of [
			"git config core.hooksPath .githooks", "git config --get core.hooksPath", "git config --list", "git config --get alias.ci",
			"git config user.name x", `bash ${join(import.meta.dir, "../scripts/install-quality-gates.sh")}`,
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
	const dir = mkdtempSync(join(tmpdir(), "dynaflows-guards-config-"));
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

test("minimal mode runs only the fixed guards: no reference reads, path checks or commit checks", async () => {
	const h = harness();
	try {
		h.gate({ schemaVersion: 2, checks: { commit: ["false"] } });
		h.pathFailure("must not run");
		await h.emit("session_start");
		assert.ok(h.notes.every(note => note.includes("dynaflows minimal mode for owner/repo")), h.notes.join("; "));
		assert.equal(await h.call("write", { path: "tests/new.test.ts" }), undefined);
		assert.equal(await h.emit("session_stop"), undefined);
		const unread = reasonOf(await h.call("bash", { command: "git commit -m x" }));
		assert.ok(unread.includes("(minimal mode): read skill://git-tool,"), unread);
		assert.equal(unread.includes(mergeRecover), false, unread);
		await h.read("skill://git-tool");
		assert.equal(await h.call("bash", { command: "git commit -m x" }), undefined);
	} finally { h.cleanup(); }
});

test("structured mode adds the merge-and-recover read before Git integration commands", async () => {
	const h = harness();
	try {
		h.mode("structured");
		await h.read("skill://git-tool");
		const unread = reasonOf(await h.call("bash", { command: "git switch -c agent/x" }));
		assert.ok(unread.includes(`(structured mode): read ${mergeRecover}, then retry`), unread);
		await h.read(mergeRecover);
		assert.equal(await h.call("bash", { command: "git switch -c agent/x" }), undefined);
	} finally { h.cleanup(); }
});

async function assertBypassesBlocked(h: Harness): Promise<void> {
	await execute("git", ["config", "alias.pp", "push"], { cwd: h.root });
	await execute("git", ["config", "alias.shellpush", "!git push origin main"], { cwd: h.root });
	for (const command of [
		"git push origin heads/main", "git push origin 'refs/heads/*:refs/heads/*'", "git push origin '+refs/heads/agent/*:refs/heads/*'",
		"git pp origin main", "git shellpush",
	]) {
		assertBlocked(await h.call("bash", { command }), "default branch main is blocked in minimal and structured mode", command);
	}
	const routed: [string, string][] = [
		["git -c push.default=upstream push", "git -c push.default changes where git push sends commits"],
		["git -c remote.origin.push=HEAD:main push", "git -c remote.origin.push changes where git push sends commits"],
		["git -c alias.zz=push zz origin main", "git -c alias.zz defines a command the rails cannot check"],
		["git send-pack origin main", "git send-pack bypasses the push checks"],
		["gh api -X PUT repos/owner/repo/pulls/7/merge", "gh api PUT repos/owner/repo/pulls/7/merge is refused"],
		["gh api --method=put /repos/owner/repo/pulls/7/merge", "gh api PUT repos/owner/repo/pulls/7/merge is refused"],
		["gh api repos/owner/repo/merges -f base=main -f head=agent/test", "gh api POST repos/owner/repo/merges is refused"],
		["gh api -X PATCH repos/owner/repo/git/refs/heads/main -f sha=abc", "gh api PATCH repos/owner/repo/git/refs/heads/main is refused"],
		["gh api graphql -f query='mutation { mergePullRequest(input: {}) { clientMutationId } }'", "gh api graphql mutation mergePullRequest is outside"],
	];
	for (const [command, fragment] of routed) {
		assertBlocked(await h.call("bash", { command }), fragment, command);
	}
	for (const command of ["git pp origin agent/test", "gh api repos/owner/repo/pulls/7", "gh api graphql -f query='{ viewer { login } }'"]) {
		assert.equal(await h.call("bash", { command }), undefined, command);
	}
	await execute("git", ["config", "branch.agent/test.remote", "origin"], { cwd: h.root });
	await execute("git", ["config", "branch.agent/test.merge", "refs/heads/main"], { cwd: h.root });
	assertBlocked(await h.call("bash", { command: "git push" }), "default branch main", "a feature branch whose upstream is the default branch");
	await execute("git", ["config", "--unset", "branch.agent/test.merge"], { cwd: h.root });
	await execute("git", ["config", "push.default", "matching"], { cwd: h.root });
	assertBlocked(await h.call("bash", { command: "git push" }), "default branch main", "push.default matching pushes every branch");
	await execute("git", ["config", "--unset", "push.default"], { cwd: h.root });
	await execute("git", ["config", "remote.origin.push", "refs/heads/agent/test:refs/heads/main"], { cwd: h.root });
	assertBlocked(await h.call("bash", { command: "git push origin" }), "default branch main", "a configured push refspec");
	await execute("git", ["config", "--unset", "remote.origin.push"], { cwd: h.root });
	await execute("git", ["symbolic-ref", "HEAD", "refs/heads/main"], { cwd: h.root });
	assertBlocked(await h.call("bash", { command: "git push origin @" }), "default branch main", "@ names the current branch");
	await execute("git", ["symbolic-ref", "HEAD", "refs/heads/agent/test"], { cwd: h.root });
}

test("minimal mode blocks gh pr merge and pushes to the default branch while feature pushes and PR creation pass", async () => {
	const h = harness();
	try {
		await h.read("skill://git-tool");
		forbidGh(h);
		const merge = reasonOf(await h.call("bash", { command: "gh pr merge 7 --squash" }));
		assert.ok(merge.includes("gh pr merge is blocked in dynaflows minimal mode"), merge);
		h.gate({ schemaVersion: 2 });
		assert.ok(reasonOf(await h.call("bash", { command: "gh pr merge 7" })).includes("minimal mode"));
		for (const command of [
			"git push origin main", "git push origin HEAD:main", "git push -f origin +agent/test:refs/heads/main",
			"git push --all origin", "git push origin :", "git push origin --delete main", "git push --repo=origin main",
			"git push -o ci.skip origin main", "sudo git push origin main",
		]) {
			assertBlocked(await h.call("bash", { command }), "default branch main is blocked in minimal and structured mode", command);
		}
		await assertBypassesBlocked(h);
		for (const command of [
			"git push", "git push -u origin agent/test", "git push origin HEAD", "git push --tags origin",
			"git push -o ci.skip origin agent/test", "git push origin agent/test:agent/review", "gh pr create --fill",
		]) {
			assert.equal(await h.call("bash", { command }), undefined, command);
		}
		await execute("git", ["symbolic-ref", "HEAD", "refs/heads/main"], { cwd: h.root });
		for (const command of ["git push", "git push origin HEAD"]) {
			assertBlocked(await h.call("bash", { command }), "default branch main", command);
		}
		await execute("git", ["symbolic-ref", "--delete", "refs/remotes/origin/HEAD"], { cwd: h.root });
		assertBlocked(await h.call("bash", { command: "git push origin agent/test" }), "run git remote set-head origin --auto");
	} finally { h.cleanup(); }
});

test("structured mode keeps the default-branch push block and routes gh pr merge to the merge checks", async () => {
	const h = harness();
	try {
		h.mode("structured");
		await h.read("skill://git-tool", mergeRecover);
		assertBlocked(await h.call("bash", { command: "git push origin main" }), "default branch main");
		assert.equal(await h.call("bash", { command: "git push origin agent/test" }), undefined);
		await assertBypassesBlocked(h);
		const merge = reasonOf(await h.call("bash", { command: "gh pr merge 7" }));
		assert.ok(merge.includes("merge requires --match-head-commit"), merge);
	} finally { h.cleanup(); }
});

test("an invalid mode file names its fault, runs minimal mode and refuses commits until fixed", async () => {
	const h = harness();
	try {
		h.mode("structured");
		h.modesText(JSON.stringify({ schemaVersion: 1, repos: { "owner/repo": "on" } }));
		await h.emit("session_start");
		await h.read("skill://git-tool", mergeRecover);
		assert.ok(h.notes.some(note => note.includes("is invalid: it must be")), h.notes.join("; "));
		assert.equal(await h.call("write", { path: "tests/new.test.ts" }), undefined, "minimal mode asks for no reference reads");
		assertBlocked(await h.call("bash", { command: "git commit -m x" }), "is invalid: it must be");
		h.modesText("{broken");
		assertBlocked(await h.call("bash", { command: "git commit -m x" }), "changed during this session", "the session trusts the mode file it started with");
		for (const text of ["{broken", JSON.stringify({ schemaVersion: 2, repos: {} }), JSON.stringify({ schemaVersion: 1, repos: {}, extra: 1 }),
			JSON.stringify({ schemaVersion: 1, repos: { "Owner/Repo": "structured" } })]) {
			h.modesText(text);
			await h.emit("session_start");
			await h.read("skill://git-tool", mergeRecover);
			assertBlocked(await h.call("bash", { command: "git commit -m x" }), "the mode file", text);
		}
		rmSync(h.modesPath);
		await h.emit("session_start");
		await h.read("skill://git-tool", mergeRecover);
		assertBlocked(await h.call("bash", { command: "git commit -m x" }), "cannot be read", "a missing mode file is refused, never read as minimal");
		h.modesText(JSON.stringify({ schemaVersion: 1, repos: {} }));
		await h.emit("session_start");
		await h.read("skill://git-tool", mergeRecover);
		h.notes.length = 0;
		assert.equal(await h.call("bash", { command: "git commit -m x" }), undefined);
		assert.ok(h.notes.some(note => note.includes("does not set structured; commit checks do not run")), h.notes.join("; "));
		h.mode("structured");
		await h.emit("session_start");
		await h.read("skill://git-tool", mergeRecover);
		assert.equal(await h.call("bash", { command: "git commit -m x" }), undefined);
	} finally { h.cleanup(); }
});
