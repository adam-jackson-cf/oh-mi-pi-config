import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, test } from "bun:test";
import { z } from "@oh-my-pi/pi-coding-agent";
import { harness, execute, reasonOf, isBlocked, ciHead, greenCi, mockPrCi, mergeRecover } from "./dynaflows-guards-seams.test";
import { extensionDiff, extensionRevision } from "../agent/extensions/dynaflows/lib/restart-review.ts";
import { MAX_FOLLOW_UPS, ranCommand, shellJoin } from "../agent/extensions/dynaflows/lib/obligations.ts";

type Harness = ReturnType<typeof harness>;
const reviewsSchema = z.object({ schemaVersion: z.literal(1), repos: z.record(z.string(), z.string()) });
const rollout = "python3 ~/.omp/scripts/device_rollout.py";
const bashResult = (h: Harness, command: string, isError = false) =>
	h.emit("tool_result", { toolName: "bash", toolCallId: `result-${command}`, input: { command }, isError });
const prompt = (h: Harness, text: string) => h.emit("before_agent_start", { reason: "prompt", prompt: text });
type Reply = Awaited<ReturnType<Harness["emit"]>>;
const messageText = (result: Reply) => JSON.stringify(result ?? "");
const reviews = (h: Harness) => reviewsSchema.parse(JSON.parse(readFileSync(join(h.machine, "reviews.json"), "utf8"))).repos;

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
			h.mode("structured");
			await bashResult(h, "gh pr create --fill");
			mockPrCi(h, greenCi, { selector: "agent/test" });
			await exhaust(h, "pr:agent/test");
		} finally { h.cleanup(); }
	});

	test("the PR obligation waits on pending CI, ends at merge and never applies when agents may not merge", async () => {
		const h = harness();
		try {
			h.mode("structured");
			await bashResult(h, "gh pr create --head agent/other --fill");
			mockPrCi(h, [{ __typename: "CheckRun", name: "test", status: "IN_PROGRESS", conclusion: null }], { selector: "agent/other" });
			assert.equal(await h.emit("session_stop"), undefined, "running CI is nothing to ask for yet");
			mockPrCi(h, greenCi, { selector: "agent/other" });
			assert.ok(reasonOf(await h.emit("session_stop")).includes("[pr:agent/other]"), "green CI later makes it due");
			h.gate({ schemaVersion: 2, agentMerge: "disabled" });
			assert.equal(await h.emit("session_stop"), undefined, "agentMerge disabled leaves the merge to the owner");
			h.gate({ schemaVersion: 2 });
			await bashResult(h, "gh pr create --head agent/other --fill");
			mockPrCi(h, greenCi, { selector: "agent/other", state: "MERGED" });
			assert.equal(await h.emit("session_stop"), undefined, "a merged PR meets the obligation");
			await bashResult(h, "gh pr create --head agent/failed --fill", true);
			assert.equal(await h.emit("session_stop"), undefined, "a failed gh pr create adds nothing");
		} finally { h.cleanup(); }
	});

	test("minimal mode adds no obligations", async () => {
		const h = harness();
		try {
			h.mode("minimal");
			await bashResult(h, "gh pr create --fill");
			mockPrCi(h, greenCi, { selector: "agent/test" });
			assert.equal(await h.emit("session_stop"), undefined);
			assert.ok(messageText(await prompt(h, "$rollout")).includes("need dynaflows structured mode"));
			assert.equal(await h.emit("session_stop"), undefined);
		} finally { h.cleanup(); }
	});

	test("a merge whose files match afterMerge.paths needs the step to exit 0", async () => {
		const h = harness();
		try {
			h.mode("structured");
			h.gate({ schemaVersion: 2, afterMerge: { argv: ["make", "sync"], paths: ["agent/**"] } });
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
			h.mode("structured");
			h.gate({ schemaVersion: 2, afterMerge: { argv: ["./sync", "--all"] } });
			await bashResult(h, "gh pr merge 3 --merge --match-head-commit x");
			assert.ok(reasonOf(await h.emit("session_stop")).includes("./sync --all"));
			await bashResult(h, "cd . && ./sync --all");
			assert.equal(await h.emit("session_stop"), undefined);
		} finally { h.cleanup(); }
	});

	test("device rollout is opt-in by directive or tool option and met only by an exit 0 run", async () => {
		const h = harness();
		try {
			h.mode("structured");
			await prompt(h, "ship it");
			assert.equal(await h.emit("session_stop"), undefined, "rollout is never automatic");
			assert.ok(messageText(await prompt(h, "fix the rails\n$rollout")).includes(rollout));
			assert.ok(reasonOf(await h.emit("session_stop")).includes("[rollout]"));
			await bashResult(h, `${rollout} --dry-run`);
			await bashResult(h, rollout, true);
			assert.ok(reasonOf(await h.emit("session_stop")).includes("[rollout]"));
			await bashResult(h, rollout);
			assert.equal(await h.emit("session_stop"), undefined);
			assert.ok((await h.tool("dynaflows", { rollout: true })).includes(rollout));
			await bashResult(h, `python3 ${h.records}/scripts/device_rollout.py`);
			assert.equal(await h.emit("session_stop"), undefined, "the config root's absolute path counts too");
		} finally { h.cleanup(); }
	});

	test("an obligation source added by later judgments is checked at each turn end", async () => {
		let status: "met" | "unmet" | "waiting" = "unmet";
		let root = "/";
		const source = async () => [{ id: "evidence", root, text: "attach completion evidence", status: async () => status }];
		const h = harness({ extra: { obligationSources: [source] } });
		try {
			h.mode("structured");
			assert.equal(await h.emit("session_stop"), undefined, "an obligation outside a structured repo does not apply");
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

	test("commands count only as whole shell commands", () => {
		assert.equal(shellJoin(["make", "it's here"]), "make 'it'\\''s here'");
		assert.equal(ranCommand("cd x && make sync; echo ok", ["make sync"]), true);
		assert.equal(ranCommand("echo make sync", ["make sync"]), false);
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
	test("structured mode injects a review task and refuses Git writes until acknowledged", async () => {
		const h = harness();
		try {
			h.mode("structured");
			await h.read("skill://git-tool", mergeRecover);
			await changedExtension(h);
			const before = reviews(h)["owner/repo"];
			await h.emit("session_start");
			await h.read("skill://git-tool", mergeRecover);
			const task = messageText(await prompt(h, "work"));
			assert.ok(task.includes("First task: the dynaflows extension changed") && task.includes("Record checks") &&
				task.includes("guardrails profile-check: exit 0") && task.includes("guardrails check: exit 0"), task);
			assert.ok(task.includes("unavailable (not a Git revision pair)"), task);
			assert.ok(reasonOf(await commit(h)).includes("acknowledge: true"));
			assert.equal(await h.call("bash", { command: "git status" }), undefined, "reads stay open");
			assert.equal(reviews(h)["owner/repo"], before, "nothing is stored before acknowledgement");
			assert.ok((await h.tool("dynaflows", { acknowledge: true })).includes("acknowledged dynaflows extension revision sha256:"));
			assert.notEqual(reviews(h)["owner/repo"], before);
			assert.equal(await commit(h), undefined);
			assert.ok((await h.tool("dynaflows", { acknowledge: true })).includes("no extension change is waiting"));
			h.setSession("third");
			await h.emit("session_start");
			assert.ok(!messageText(await prompt(h, "work")).includes("First task"), "an acknowledged revision is not reviewed again");
		} finally { h.cleanup(); }
	});

	test("failing record checks are in the task, and modes.json changes are not extension changes", async () => {
		const h = harness({ guardrailsCommand: ["sh", "-c", "echo 'PROFILE.complete missing'; exit 1", "check"] });
		try {
			h.mode("structured");
			await changedExtension(h);
			writeFileSync(join(h.machine, "extension", "modes.json"), "{}\n");
			await h.emit("session_start");
			const task = messageText(await prompt(h, "work"));
			assert.ok(task.includes("guardrails check: exit 1") && task.includes("PROFILE.complete missing"), task);
			await h.tool("dynaflows", { acknowledge: true });
			writeFileSync(join(h.machine, "extension", "modes.json"), JSON.stringify({ schemaVersion: 1, repos: {} }));
			h.setSession("mode-switch");
			await h.emit("session_start");
			assert.ok(!messageText(await prompt(h, "work")).includes("First task"));
		} finally { h.cleanup(); }
	});

	test("an extension-impact decision of proceed stores the revision without a task", async () => {
		const seen: string[] = [];
		const h = harness({ extra: { decide: async (judgment, input) => { seen.push(`${judgment} ${input.slug}`); return "proceed"; } } });
		try {
			h.mode("structured");
			await changedExtension(h);
			const before = reviews(h)["owner/repo"];
			await h.emit("session_start");
			assert.ok(!messageText(await prompt(h, "work")).includes("First task"));
			assert.deepEqual(seen, ["extension-impact owner/repo"]);
			assert.notEqual(reviews(h)["owner/repo"], before);
			await h.read("skill://git-tool", mergeRecover);
			assert.equal(await commit(h), undefined);
		} finally { h.cleanup(); }
	});

	test("minimal mode only notifies and stores the new revision", async () => {
		const h = harness();
		try {
			h.mode("minimal");
			await changedExtension(h);
			await h.emit("session_start");
			assert.ok(h.notes.some(note => note.includes("the dynaflows extension changed")), h.notes.join("; "));
			assert.equal(await prompt(h, "work"), undefined);
			await h.read("skill://git-tool");
			assert.equal(await commit(h), undefined);
			h.setSession("again");
			h.notes.length = 0;
			await h.emit("session_start");
			assert.ok(!h.notes.some(note => note.includes("the dynaflows extension changed")));
		} finally { h.cleanup(); }
	});

	test("a Git config root uses the last commit touching the extension and diffs the extension path", async () => {
		const root = realpathSync(mkdtempSync(join(tmpdir(), "dynaflows-review-")));
		const exec = { exec: async (command: string, args: string[], options: { cwd: string }) => {
			try { return { code: 0, killed: false, stdout: (await execute(command, args, options)).stdout, stderr: "" }; }
			catch { return { code: 1, killed: false, stdout: "", stderr: "" }; }
		} };
		try {
			const extension = join(root, "agent/extensions/dynaflows");
			mkdirSync(extension, { recursive: true });
			const git = (...args: string[]) => execFileSync("git", ["-c", "user.name=t", "-c", "user.email=t@t", ...args], { cwd: root });
			git("init", "-q");
			writeFileSync(join(extension, "index.ts"), "export {};\n");
			git("add", "."); git("commit", "-qm", "one");
			const from = await extensionRevision(exec, root, extension);
			writeFileSync(join(extension, "modes.json"), "{}\n");
			writeFileSync(join(root, "other.md"), "x\n");
			git("add", "."); git("commit", "-qm", "modes and other");
			assert.equal(await extensionRevision(exec, root, extension), from, "modes.json and other paths do not move the revision");
			writeFileSync(join(extension, "index.ts"), "export const x = 1;\n");
			git("add", "."); git("commit", "-qm", "two");
			const to = await extensionRevision(exec, root, extension);
			assert.match(to, /^git:[0-9a-f]{40}$/);
			assert.notEqual(to, from);
			const diff = await extensionDiff(exec, root, extension, { from, to });
			assert.ok(diff?.includes("agent/extensions/dynaflows/index.ts") && !diff.includes("other.md") && !diff.includes("modes.json"), String(diff));
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

	test.each(["minimal", "structured"])("a staged HANDOFF.md refuses the commit (%s mode)", async name => {
		const h = harness();
		try {
			h.mode(name === "structured" ? "structured" : "minimal");
			await h.read("skill://git-tool", mergeRecover);
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
