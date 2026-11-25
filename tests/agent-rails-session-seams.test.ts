import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import { describe, test } from "bun:test";
import { z } from "@oh-my-pi/pi-coding-agent";
import { harness, execute, reasonOf, isBlocked, askAutonomy, ciGate, harnessConfig, ciHead, greenCi, mockPrCi } from "./agent-rails-guards-seams.test";
import { extensionDiff, extensionRevision } from "../agent/extensions/agent-rails/lib/restart-review.ts";
import { MAX_FOLLOW_UPS, ranCommand, shellJoin } from "../agent/extensions/agent-rails/lib/obligations.ts";

type Harness = ReturnType<typeof harness>;
const reviewsSchema = z.object({ schemaVersion: z.literal(1), repos: z.record(z.string(), z.string()) });

const bashResult = (h: Harness, command: string, isError = false) =>
	h.emit("tool_result", { toolName: "bash", toolCallId: `result-${command}`, input: { command }, isError });
const prompt = (h: Harness, text: string) => h.emit("before_agent_start", { reason: "prompt", prompt: text });
type Reply = Awaited<ReturnType<Harness["emit"]>>;
const messageText = (result: Reply) => JSON.stringify(result ?? "");
const reviews = (h: Harness) => reviewsSchema.parse(JSON.parse(readFileSync(join(h.machine, "reviews.json"), "utf8"))).repos;

type ToolArguments = { command: string };
const toolCallEntry = (id: string, name: string, args: ToolArguments) =>
	({ type: "message", message: { role: "assistant", content: [{ type: "toolCall", id, name, arguments: args }] } });
const toolResultEntry = (id: string, isError = false) => ({ type: "message", message: { role: "toolResult", toolCallId: id, isError, content: [] } });

async function exhaust(h: Harness, id: string): Promise<void> {
	for (let turn = 1; turn <= MAX_FOLLOW_UPS; turn++) {
		const stop = await h.emit("session_stop");
		assert.equal(stop?.decision, "block", `follow-up ${turn}`);
		assert.ok(reasonOf(stop).includes(`[${id}]`), reasonOf(stop));
	}
	assert.equal(await h.emit("session_stop"), undefined, "the rails stop asking after the cap");
	assert.ok(h.errors.some(error => error.includes(`obligation [${id}] is still unmet after ${MAX_FOLLOW_UPS} follow-ups`)), h.errors.join("; "));
	assert.equal(await h.emit("session_stop"), undefined);
	assert.equal(h.errors.filter(error => error.includes(`[${id}]`)).length, 1, "the owner is told once");
}

describe("end-of-turn obligations", () => {
	test("an open PR with green CI must be merged before the turn ends, at most three follow-ups", async () => {
		const h = harness();
		try {
			h.govern();
			await bashResult(h, "gh pr create --fill");
			mockPrCi(h, greenCi, { selector: "agent/test" });
			await exhaust(h, "pr:agent/test");
		} finally { h.cleanup(); }
	});

	test("the PR obligation waits on pending CI, ends at merge and never applies when agents may not merge", async () => {
		const h = harness();
		try {
			h.config({ ci: { enabled: true, runner: "github-actions", requiredContext: "test" } });
			h.govern();
			await bashResult(h, "gh pr create --head agent/other --fill");
			mockPrCi(h, [{ __typename: "CheckRun", name: "test", status: "IN_PROGRESS", conclusion: null }], { selector: "agent/other" });
			const pending = reasonOf(await h.emit("session_stop"));
			assert.ok(pending.includes("[pr:agent/other]") && pending.includes('required CI context "test" is IN_PROGRESS/') && pending.includes("gh pr checks agent/other --watch"),
				`running CI holds the turn with the wait command: ${pending}`);
			mockPrCi(h, greenCi, { selector: "agent/other" });
			assert.ok(reasonOf(await h.emit("session_stop")).includes("has green CI on its head and is unmerged"), "green CI asks for the merge");
			h.config(askAutonomy);
			assert.equal(await h.emit("session_stop"), undefined, "autonomy.merge ask leaves the merge to the owner");
			h.config();
			await bashResult(h, "gh pr create --head agent/other --fill");
			mockPrCi(h, greenCi, { selector: "agent/other", state: "MERGED" });
			assert.equal(await h.emit("session_stop"), undefined, "a merged PR meets the obligation");
			await bashResult(h, "gh pr create --head agent/failed --fill", true);
			assert.equal(await h.emit("session_stop"), undefined, "a failed gh pr create adds nothing");
		} finally { h.cleanup(); }
	});

	test("pending required CI holds the turn end with the ci_runner submit and wait commands", async () => {
		const h = harness();
		try {
			h.govern();
			h.config(ciGate);
			await bashResult(h, "gh pr create --fill");
			mockPrCi(h, [{ __typename: "StatusContext", context: "gate", state: "PENDING" }], { selector: "agent/test" });
			const reason = reasonOf(await h.emit("session_stop"));
			assert.ok(reason.includes("[pr:agent/test]") && reason.includes("ci_runner.py") && reason.includes(" submit ci-gate owner/repo ") && reason.includes(" wait ci-gate owner/repo "), reason);
		} finally { h.cleanup(); }
	});

	test("an open PR this session did not create is found at the turn end once its branch is pushed", async () => {
		const h = harness();
		try {
			h.govern();
			mockPrCi(h, greenCi, { selector: "agent/test" });
			let views = 0;
			const exec = h.api.exec.bind(h.api);
			h.api.exec = async (command, args, options) => { if (command === "gh") views++; return exec(command, args, options); };
			assert.equal(await h.emit("session_stop"), undefined, "an unpushed branch has no PR to look for");
			assert.equal(views, 0, "no gh call before the branch is pushed");
			await execute("git", ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "--allow-empty", "-qm", "x"], { cwd: h.root });
			await execute("git", ["update-ref", "refs/remotes/origin/agent/test", "HEAD"], { cwd: h.root });
			assert.ok(reasonOf(await h.emit("session_stop")).includes("[pr:agent/test]"), "a pushed branch's open PR is tracked");
			mockPrCi(h, greenCi, { selector: "agent/test", state: "MERGED" });
			assert.equal(await h.emit("session_stop"), undefined);
		} finally { h.cleanup(); }
	});

	test("a resumed session rebuilds PR, afterMerge and rollout obligations from its branch", async () => {
		const h = harness();
		try {
			h.govern();
			h.config({ afterMerge: { argv: ["make", "sync"] } });
			h.branch.push(
				{ type: "message", message: { role: "user", content: "fix it\n$rollout" } },
				toolCallEntry("c1", "bash", { command: "gh pr create --head agent/resumed --fill" }), toolResultEntry("c1"),
				toolCallEntry("c2", "bash", { command: `gh pr merge 4 --merge --match-head-commit ${ciHead}` }), toolResultEntry("c2"),
				toolCallEntry("c3", "bash", { command: "gh pr create --head agent/failed --fill" }), toolResultEntry("c3", true),
			);
			mockPrCi(h, greenCi, { selector: "agent/resumed" });
			h.setSession("resumed");
			await h.emit("session_start");
			const due = reasonOf(await h.emit("session_stop"));
			for (const id of ["[rollout]", "[pr:agent/resumed]", `[afterMerge:${h.root}]`]) assert.ok(due.includes(id), `${id}: ${due}`);
			assert.ok(!due.includes("agent/failed"), due);
			h.branch.push(toolCallEntry("c4", "bash", { command: "make sync" }), toolResultEntry("c4"));
			await h.emit("session_switch");
			assert.ok(!reasonOf(await h.emit("session_stop")).includes("afterMerge"), "a later exit-0 run in the branch meets it");
		} finally { h.cleanup(); }
	});

	test("a repository that is onboarding adds no obligations", async () => {
		const h = harness();
		try {
			await h.onboard();
			await bashResult(h, "gh pr create --fill");
			mockPrCi(h, greenCi, { selector: "agent/test" });
			assert.equal(await h.emit("session_stop"), undefined);
			assert.ok(messageText(await prompt(h, "$rollout")).includes("need an onboarded repository (a valid .agentrails/config.json)"));
			assert.equal(await h.emit("session_stop"), undefined);
		} finally { h.cleanup(); }
	});

	test("a merge whose files match afterMerge.paths needs the step to exit 0", async () => {
		const h = harness();
		try {
			h.govern();
			h.config({ afterMerge: { argv: ["make", "sync"], paths: ["agent/**"] } });
			let files = ["readme.md"];
			const exec = h.api.exec.bind(h.api);
			h.api.exec = async (command, args, options) => {
				if (command !== "gh") return exec(command, args, options);
				assert.deepEqual(args, ["pr", "view", "3", "--json", "files"]);
				return { code: 0, killed: false, stdout: JSON.stringify({ files: files.map(path => ({ path })) }), stderr: "" };
			};
			const merge = `gh pr merge 3 --rebase --match-head-commit ${ciHead}`;
			await bashResult(h, merge);
			assert.equal(await h.emit("session_stop"), undefined, "no file under agent/**");
			files = ["agent/config.yml"];
			await bashResult(h, merge, true);
			assert.equal(await h.emit("session_stop"), undefined, "a failed merge needs no step");
			await bashResult(h, merge);
			const due = await h.emit("session_stop");
			assert.ok(reasonOf(due).includes(`[afterMerge:${h.root}]`) && reasonOf(due).includes("make sync"), reasonOf(due));
			await bashResult(h, "make sync", true);
			assert.equal((await h.emit("session_stop"))?.decision, "block", "a failing run does not count");
			await bashResult(h, "make sync | tail -1");
			assert.equal((await h.emit("session_stop"))?.decision, "block", "a piped run hides the exit status");
			await bashResult(h, "make sync");
			assert.equal(await h.emit("session_stop"), undefined);
		} finally { h.cleanup(); }
	});

	test("afterMerge without paths follows every merge", async () => {
		const h = harness();
		try {
			h.govern();
			h.config({ afterMerge: { argv: ["./sync", "--all"] } });
			await bashResult(h, "gh pr merge 3 --merge --match-head-commit x");
			assert.ok(reasonOf(await h.emit("session_stop")).includes("./sync --all"));
			await bashResult(h, "cd . && ./sync --all");
			assert.equal(await h.emit("session_stop"), undefined);
		} finally { h.cleanup(); }
	});

	test("device rollout is opt-in by directive or tool option and met only by an exit 0 run", async () => {
		const h = harness();
		const rollout = `python3 ${h.records}/scripts/device_rollout.py`;
		try {
			h.govern();
			await prompt(h, "ship it");
			assert.equal(await h.emit("session_stop"), undefined, "rollout is never automatic");
			assert.ok(messageText(await prompt(h, "fix the rails\n$rollout")).includes(rollout));
			assert.ok(reasonOf(await h.emit("session_stop")).includes("[rollout]"));
			await bashResult(h, `${rollout} --dry-run`);
			await bashResult(h, rollout, true);
			assert.ok(reasonOf(await h.emit("session_stop")).includes("[rollout]"));
			await bashResult(h, rollout);
			assert.equal(await h.emit("session_stop"), undefined);
			assert.ok((await h.tool("agentrails", { rollout: true })).includes(rollout));
			await bashResult(h, `cd ${h.records} && python3 scripts/device_rollout.py 2>&1`);
			assert.equal(await h.emit("session_stop"), undefined, "any spelling that resolves to the config root's script counts");
			await h.tool("agentrails", { rollout: true });
			await bashResult(h, "python3 ~/.omp/scripts/device_rollout.py");
			assert.ok(reasonOf(await h.emit("session_stop")).includes("[rollout]"), "the rails hold no ~/.omp literal: another root's script does not count");
		} finally { h.cleanup(); }
	});

	test("an obligation source added by later judgments is checked at each turn end", async () => {
		let status: "met" | "unmet" | "waiting" = "unmet";
		let root = "/";
		const source = async () => [{ id: "evidence", root, text: "attach completion evidence", status: async () => status }];
		const h = harness({ extra: { obligationSources: [source] } });
		try {
			h.govern();
			assert.equal(await h.emit("session_stop"), undefined, "an obligation outside a governed repo does not apply");
			root = h.root;
			assert.ok(reasonOf(await h.emit("session_stop")).includes("[evidence] attach completion evidence"));
			status = "waiting";
			assert.equal(await h.emit("session_stop"), undefined);
			status = "unmet";
			assert.equal((await h.emit("session_stop"))?.decision, "block");
			status = "met";
			assert.equal(await h.emit("session_stop"), undefined);
		} finally { h.cleanup(); }
	});

	test("commands count only as whole shell commands joined by && with the step's own exit status", () => {
		assert.equal(shellJoin(["make", "it's here"]), "make 'it'\\''s here'");
		const make = { argv: ["make", "sync"], root: "/r" };
		assert.equal(ranCommand("cd x && make sync", make, "/r"), true);
		for (const masked of ["cd x && make sync; echo ok", "make sync || true", "make sync; echo ok", "true || make sync", "echo x # ; make sync",
			"make sync | tail -5", "make sync &", "echo make sync", "make sync --dry-run", "$(make sync)", "make sync\necho ok"]) {
			assert.equal(ranCommand(masked, make, "/r"), false, masked);
		}
		const home = homedir();
		const rollout = { argv: ["python3", join(home, ".omp/scripts/device_rollout.py")], root: join(home, ".omp") };
		for (const run of ["python3 ~/.omp/scripts/device_rollout.py", "python3 ~/.omp/scripts/device_rollout.py 2>&1", "python3 ~/.omp/scripts/device_rollout.py > out.log",
			"cd ~/.omp && python3 scripts/device_rollout.py", "python3 $HOME/.omp/scripts/device_rollout.py", `python3 '${home}/.omp/scripts/device_rollout.py'`]) {
			assert.equal(ranCommand(run, rollout, "/tmp"), true, run);
		}
		assert.equal(ranCommand("python3 scripts/device_rollout.py", rollout, "/tmp"), false, "a relative path resolves from the command's directory");
	});
});

const commit = (h: Harness) => h.call("bash", { command: "git commit --allow-empty -m x" });
async function changedExtension(h: Harness): Promise<void> {
	await h.emit("session_start");
	assert.match(reviews(h)["owner/repo"] ?? "", /^sha256:[0-9a-f]{64}$/, "the first session stores the revision silently");
	assert.ok(!messageText(await prompt(h, "work")).includes("First task"));
	writeFileSync(join(h.machine, "extension", "index.ts"), "export const changed = true;\n");
	h.setSession("restarted");
}

describe("review on restart", () => {
	test("a governed repository gets a review task and refuses Git writes until acknowledged", async () => {
		const h = harness();
		try {
			h.govern();
			await changedExtension(h);
			const before = reviews(h)["owner/repo"];
			await h.emit("session_start");
			const task = messageText(await prompt(h, "work"));
			assert.ok(task.includes("First task: the agent-rails extension changed") && task.includes("Config checks") &&
				task.includes("guardrails config-check: exit 0") && task.includes("guardrails check: exit 0"), task);
			assert.ok(task.includes("unavailable (not a Git revision pair)"), task);
			assert.ok(reasonOf(await commit(h)).includes("acknowledge: true"));
			assert.equal(await h.call("bash", { command: "git status" }), undefined, "reads stay open");
			assert.equal(reviews(h)["owner/repo"], before, "nothing is stored before acknowledgement");
			assert.ok((await h.tool("agentrails", { acknowledge: true })).includes("acknowledged agent-rails extension revision sha256:"));
			assert.notEqual(reviews(h)["owner/repo"], before);
			assert.equal(await commit(h), undefined);
			assert.ok((await h.tool("agentrails", { acknowledge: true })).includes("no extension change is waiting"));
			h.setSession("third");
			await h.emit("session_start");
			assert.ok(!messageText(await prompt(h, "work")).includes("First task"), "an acknowledged revision is not reviewed again");
		} finally { h.cleanup(); }
	});

	test("Git writes in another worktree of the reviewed repository are refused until acknowledged", async () => {
		const h = harness();
		const worktree = join(realpathSync(mkdtempSync(join(tmpdir(), "agent-rails-review-wt-"))), "wt");
		try {
			h.govern();
			await execute("git", ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "--allow-empty", "-qm", "base"], { cwd: h.root });
			await execute("git", ["worktree", "add", "-q", "-b", "agent/wt", worktree], { cwd: h.root });
			await changedExtension(h);
			await h.emit("session_start");
			await prompt(h, "work");
			for (const command of [`git -C '${worktree}' commit --allow-empty -m x`, `cd '${worktree}' && git commit --allow-empty -m x`]) {
				assert.ok(reasonOf(await h.call("bash", { command })).includes("acknowledge: true"), command);
			}
			const other = harness();
			try {
				assert.equal(await other.call("bash", { command: "git status" }), undefined);
				assert.equal(reasonOf(await h.call("bash", { command: `git -C '${other.root}' commit --allow-empty -m x` })).includes("acknowledge"), false,
					"another repository is not held by this review");
			} finally { other.cleanup(); }
			await h.tool("agentrails", { acknowledge: true });
			assert.equal(await h.call("bash", { command: `git -C '${worktree}' commit --allow-empty -m x` }), undefined);
		} finally { h.cleanup(); rmSync(join(worktree, ".."), { recursive: true, force: true }); }
	});

	test("failing config checks are in the task; an acknowledged revision asks for no second review", async () => {
		const h = harness({ guardrailsCommand: ["sh", "-c", "echo 'PROFILE.complete missing'; exit 1", "check"] });
		try {
			h.govern();
			await changedExtension(h);
			await h.emit("session_start");
			const task = messageText(await prompt(h, "work"));
			assert.ok(task.includes("guardrails check: exit 1") && task.includes("PROFILE.complete missing"), task);
			await h.tool("agentrails", { acknowledge: true });
			h.setSession("acknowledged");
			await h.emit("session_start");
			assert.ok(!messageText(await prompt(h, "work")).includes("First task"));
		} finally { h.cleanup(); }
	});

	test("extension-impact judges a Git revision pair: no stores it silently, yes and escalate keep the review task", async () => {
		const records = realpathSync(mkdtempSync(join(tmpdir(), "agent-rails-impact-")));
		const extension = join(records, "agent/extensions/agent-rails");
		const h = harness({ configRoot: records, registry: "banded", extra: { extensionDir: extension } });
		const git = (...args: string[]) => execFileSync("git", ["-c", "user.name=t", "-c", "user.email=t@t", ...args], { cwd: records });
		const change = (subject: string) => {
			writeFileSync(join(extension, "index.ts"), `export const subject = ${JSON.stringify(subject)};\n`);
			git("add", "agent"); git("commit", "-qm", subject);
		};
		try {
			mkdirSync(extension, { recursive: true });
			git("init", "-q");
			writeFileSync(join(records, ".gitignore"), "node_modules/\n");
			git("add", ".gitignore");
			change("feat: first rails");
			h.config({ afterMerge: { argv: ["make", "sync"] } });
			h.govern();
			await h.emit("session_start");
			assert.match(reviews(h)["owner/repo"] ?? "", /^git:[0-9a-f]{40}$/);
			change("refactor: rename an internal helper");
			h.setSession("no-impact");
			await h.emit("session_start");
			assert.ok(!messageText(await prompt(h, "work")).includes("First task"), "a no outcome needs no review");
			assert.ok(h.notes.some(note => note.includes("extension-impact/impact no") && note.includes("no review task; revision stored")),
				`a no outcome is shown to the owner: ${h.notes.join("; ")}`);
			assert.equal(reviews(h)["owner/repo"], `git:${git("rev-parse", "HEAD").toString().trim()}`);
			assert.equal(await commit(h), undefined);
			const judged = z.object({ config: z.record(z.string(), z.unknown()),
				change: z.object({ commit_subjects: z.array(z.string()), files: z.array(z.object({ path: z.string(), status: z.string() })), patch_excerpts: z.array(z.object({ path: z.string(), patch: z.string() })) }).passthrough() })
				.strict().parse(h.judged.calls.at(-1)?.state);
			const config = harnessConfig("owner/repo", { afterMerge: { argv: ["make", "sync"] } });
			assert.deepEqual(judged.config, {
				autonomy: config.autonomy, ci: config.ci, gates: config.gates, judgments: config.judgments, mergePolicies: config.mergePolicies,
				afterMerge: config.afterMerge, commands: config.commands,
			}, "extension-impact judges the config's rail sections, never its setup-owned sections");
			assert.deepEqual(judged.change.commit_subjects, ["refactor: rename an internal helper"]);
			assert.deepEqual(judged.change.files.map(file => `${file.status} ${file.path}`), ["M agent/extensions/agent-rails/index.ts"]);
			assert.ok(judged.change.patch_excerpts[0]?.patch.startsWith("--- a/agent/extensions/agent-rails/index.ts"));
			for (const [score, outcome] of [[0.9, "yes, P=0.90"], [0.4, "escalate, P=0.40"]] as const) {
				h.judged.scores["impact"] = score;
				change(`feat: change ${score}`);
				h.setSession(`impact-${score}`);
				await h.emit("session_start");
				const task = messageText(await prompt(h, "work"));
				assert.ok(task.includes("First task") && task.includes(`Extension impact: extension-impact/impact ${outcome}`), task);
				await h.tool("agentrails", { acknowledge: true });
			}
			const { readJudgmentCalls } = await import("../agent/extensions/agent-rails/lib/judgment-calls.ts");
			assert.deepEqual(readJudgmentCalls(join(h.machine, "agent-rails", "agent-rails.db")).map(call => call.outcomes["impact"]), ["no", "yes", "escalate"]);
			h.judged.model = "openrouter/typesafe/jev-1.14";
			change("feat: judged by another model");
			h.setSession("other-model");
			await h.emit("session_start");
			const refused = messageText(await prompt(h, "work"));
			assert.ok(refused.includes("Extension impact: not judged") && refused.includes("thresholds are tuned on typesafe/jev-1.14.0"), refused);
		} finally { h.cleanup(); rmSync(records, { recursive: true, force: true }); }
	});

	test("an extension-impact the repo config turns off is never judged and keeps the review task", async () => {
		const records = realpathSync(mkdtempSync(join(tmpdir(), "agent-rails-impact-off-")));
		const extension = join(records, "agent/extensions/agent-rails");
		const h = harness({ configRoot: records, extra: { extensionDir: extension } });
		const git = (...args: string[]) => execFileSync("git", ["-c", "user.name=t", "-c", "user.email=t@t", ...args], { cwd: records });
		const change = (subject: string) => {
			writeFileSync(join(extension, "index.ts"), `export const subject = ${JSON.stringify(subject)};\n`);
			git("add", "agent"); git("commit", "-qm", subject);
		};
		try {
			mkdirSync(extension, { recursive: true });
			git("init", "-q");
			writeFileSync(join(records, ".gitignore"), "node_modules/\n");
			git("add", ".gitignore");
			change("feat: first rails");
			h.govern();
			h.config({ judgments: { "extension-impact/impact": { enabled: false } } });
			await h.emit("session_start");
			change("refactor: rename an internal helper");
			h.setSession("impact-off");
			await h.emit("session_start");
			const task = messageText(await prompt(h, "work"));
			assert.ok(task.includes("First task") && task.includes("Extension impact: extension-impact/impact is off in this repository's config (not judged)"),
				`an off extension-impact keeps the review task: ${task}`);
			assert.equal(h.judged.calls.length, 0, "an off question makes no judge call");
			const { readJudgmentCalls } = await import("../agent/extensions/agent-rails/lib/judgment-calls.ts");
			assert.deepEqual(readJudgmentCalls(join(h.machine, "agent-rails", "agent-rails.db")), [], "an off question writes no decision store row");
		} finally { h.cleanup(); rmSync(records, { recursive: true, force: true }); }
	});

	test("a repository that is not governed only notifies and stores the new revision", async () => {
		const h = harness();
		try {
			await changedExtension(h);
			await h.emit("session_start");
			assert.ok(h.notes.some(note => note.includes("the agent-rails extension changed")), h.notes.join("; "));
			assert.equal(await prompt(h, "work"), undefined);
			assert.equal(await h.call("bash", { command: "git switch -c agent/next" }), undefined, "no review holds Git writes");
			h.setSession("again");
			h.notes.length = 0;
			await h.emit("session_start");
			assert.ok(!h.notes.some(note => note.includes("the agent-rails extension changed")));
		} finally { h.cleanup(); }
	});

	test("a Git config root uses the last commit touching the extension and diffs the extension path", async () => {
		const root = realpathSync(mkdtempSync(join(tmpdir(), "agent-rails-review-")));
		const exec = { exec: async (command: string, args: string[], options: { cwd: string }) => {
			try { return { code: 0, killed: false, stdout: (await execute(command, args, options)).stdout, stderr: "" }; }
			catch { return { code: 1, killed: false, stdout: "", stderr: "" }; }
		} };
		try {
			const extension = join(root, "agent/extensions/agent-rails");
			mkdirSync(extension, { recursive: true });
			const git = (...args: string[]) => execFileSync("git", ["-c", "user.name=t", "-c", "user.email=t@t", ...args], { cwd: root });
			git("init", "-q");
			writeFileSync(join(extension, "index.ts"), "export {};\n");
			git("add", "."); git("commit", "-qm", "one");
			const from = await extensionRevision(exec, root, extension);
			writeFileSync(join(root, "other.md"), "x\n");
			git("add", "."); git("commit", "-qm", "other");
			assert.equal(await extensionRevision(exec, root, extension), from, "paths outside the extension do not move the revision");
			writeFileSync(join(extension, "index.ts"), "export const x = 1;\n");
			git("add", "."); git("commit", "-qm", "two");
			const to = await extensionRevision(exec, root, extension);
			assert.match(to, /^git:[0-9a-f]{40}$/);
			assert.notEqual(to, from);
			const diff = await extensionDiff(exec, root, extension, { from, to });
			assert.ok(diff?.includes("agent/extensions/agent-rails/index.ts") && !diff.includes("other.md"), String(diff));
			assert.equal(await extensionDiff(exec, root, extension, { from: "sha256:x", to }), undefined);
		} finally { rmSync(root, { recursive: true, force: true }); }
	});
});

describe("handoffs", () => {
	test("the handoff tool writes HANDOFF.md at the worktree root and excludes it locally", async () => {
		const h = harness();
		try {
			const reply = await h.tool("handoff", { content: "next: wire the judge" });
			assert.ok(reply.includes(join(h.root, "HANDOFF.md")), reply);
			assert.equal(readFileSync(join(h.root, "HANDOFF.md"), "utf8"), "next: wire the judge\n");
			await h.tool("handoff", { content: "next: second\n" });
			const exclude = readFileSync(join(h.root, ".git/info/exclude"), "utf8");
			assert.equal(exclude.split("\n").filter(line => line === "/HANDOFF.md").length, 1, exclude);
			assert.equal((await execute("git", ["status", "--porcelain", "--", "HANDOFF.md"], { cwd: h.root })).stdout, "");
		} finally { h.cleanup(); }
	});

	test.each(["onboarding", "governed"])("a staged HANDOFF.md refuses the commit (%s)", async name => {
		const h = harness();
		try {
			if (name === "governed") h.govern(); else await h.onboard();
			await h.tool("handoff", { content: "state" });
			await execute("git", ["add", "-f", "HANDOFF.md"], { cwd: h.root });
			const refused = await h.call("bash", { command: "git commit -m x" });
			assert.equal(isBlocked(refused), true);
			assert.ok(reasonOf(refused).includes("HANDOFF.md is staged"), reasonOf(refused));
			await execute("git", ["rm", "-q", "--cached", "HANDOFF.md"], { cwd: h.root });
			assert.equal(await h.call("bash", { command: "git commit -m x" }), undefined);
		} finally { h.cleanup(); }
	});

	test("a new session receives the handoff and deletes it when its first turn ends", async () => {
		const h = harness();
		try {
			await h.tool("handoff", { content: "carry on with step 3" });
			h.setSession("next");
			await h.emit("session_start");
			assert.ok(messageText(await prompt(h, "continue")).includes("carry on with step 3"));
			assert.equal(await prompt(h, "again"), undefined, "the handoff is given once");
			await h.emit("agent_end", { willContinue: true });
			assert.equal(existsSync(join(h.root, "HANDOFF.md")), true, "a continuing turn keeps it");
			await h.emit("agent_end");
			assert.equal(existsSync(join(h.root, "HANDOFF.md")), false);
			await h.tool("handoff", { content: "written in this session" });
			await h.emit("agent_end");
			assert.equal(existsSync(join(h.root, "HANDOFF.md")), true, "a handoff written by this session stays");
		} finally { h.cleanup(); }
	});

	test("a handoff rewritten before the turn ends is kept", async () => {
		const h = harness();
		try {
			await h.tool("handoff", { content: "old" });
			h.setSession("next");
			await h.emit("session_start");
			await prompt(h, "continue");
			await h.tool("handoff", { content: "new" });
			await h.emit("agent_end");
			assert.equal(readFileSync(join(h.root, "HANDOFF.md"), "utf8"), "new\n");
		} finally { h.cleanup(); }
	});
});
