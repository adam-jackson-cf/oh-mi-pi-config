import assert from "node:assert/strict";
import { execFile, execFileSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { afterEach, describe, test } from "bun:test";
import { z } from "@oh-my-pi/pi-coding-agent";
import { Approvals } from "../agent/extensions/dynaflows/lib/judge.ts";
import { registerOwnerSurface } from "../agent/extensions/dynaflows/lib/onboarding.ts";
import { applyOwnerLine, isOwnerLine, parseOwnerLine } from "../agent/extensions/dynaflows/lib/owner-approvals.ts";

const ENGINE = resolve(import.meta.dir, "../agent/extensions/dynaflows/engine");
const CHECK = ["uv", "run", "--project", ENGINE, "guardrails", "config-check"];
const FIXTURE = resolve(import.meta.dir, "fixtures/agentrails/valid/minimal.json");
const TIMEOUT_MS = 120_000;
const ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/;

const execFailure = z.object({ code: z.number().optional(), killed: z.boolean().optional() });
const git = (cwd: string, ...args: string[]) => execFileSync("git", ["-C", cwd, ...args], { stdio: "pipe", encoding: "utf8" });
type ExecResult = { code: number; killed: boolean; stdout: string; stderr: string };
type Exception = { rule: string; path: string; check?: string; reason: string; approvedBy: string; approvedAt: string };
type Approval = { kind: string; target: string; reason: string; approvedBy: string; approvedAt: string };
type Config = { exceptions: Exception[]; approvals: Approval[] };
type Event = { text?: string; source?: string; reason?: string; prompt?: string };
type Agent = { kind: "main" | "sub"; id: string; name: string; depth: number };

function realExec(command: string, args: string[], options?: { cwd?: string; timeout?: number }): Promise<ExecResult> {
	return new Promise(done => {
		execFile(command, args, { cwd: options?.cwd, timeout: options?.timeout, encoding: "utf8" }, (error, stdout, stderr) => {
			const failure = error === null ? { code: 0 } : execFailure.parse(error);
			done({ code: failure.code ?? 1, killed: failure.killed ?? false, stdout, stderr });
		});
	});
}

const opened: string[] = [];
afterEach(() => { for (const temp of opened.splice(0)) rmSync(temp, { recursive: true, force: true }); });

/** A real repository holding the minimal fixture config, with the owner surface on a stub omp whose gh login is stubbed. */
function owner(withConfig = true) {
	const temp = realpathSync(mkdtempSync(join(tmpdir(), "agentrails-approvals-")));
	opened.push(temp);
	const root = join(temp, "ledger");
	git(temp, "init", "-q", "-b", "main", root);
	git(root, "remote", "add", "origin", "git@github.com:example-owner/ledger.git");
	const path = join(root, ".agentrails", "config.json");
	if (withConfig) {
		mkdirSync(dirname(path), { recursive: true });
		writeFileSync(path, readFileSync(FIXTURE, "utf8"));
	}
	const handlers = new Map<string, Function[]>();
	const notes: string[] = [];
	const gh = { calls: 0, fails: false, login: "example-owner" };
	const session = { reads: new Set<string>(), onboarding: new Set<string>() };
	const pi = {
		async exec(command: string, args: string[], options?: { cwd?: string; timeout?: number }): Promise<ExecResult> {
			if (command !== "gh") return realExec(command, args, options);
			assert.deepEqual(args, ["api", "user", "--jq", ".login"]);
			gh.calls++;
			return gh.fails ? { code: 1, killed: false, stdout: "", stderr: "gh: not logged in" } : { code: 0, killed: false, stdout: `${gh.login}\n`, stderr: "" };
		},
		on(name: string, handler: Function) { handlers.set(name, [...(handlers.get(name) ?? []), handler]); },
		registerCommand() {},
		registerTool() {},
		async sendUserMessage() {},
	};
	registerOwnerSurface(pi, { check: { argv: CHECK }, engineDir: ENGINE, skillsDir: temp, session: () => session });
	let sessionId = "s1";
	const context = (kind: Agent["kind"] = "main") => ({
		cwd: root, hasUI: true, agent: { kind, id: kind, name: kind, depth: kind === "main" ? 0 : 1 },
		ui: { notify(message: string) { notes.push(message); } }, sessionManager: { getSessionId: () => sessionId },
	});
	const emit = async (name: string, event: Event, kind: Agent["kind"] = "main") => {
		for (const handler of handlers.get(name) ?? []) await handler({ type: name, ...event }, context(kind));
	};
	return {
		root, path, notes, gh, session,
		newSession: (id: string) => { sessionId = id; },
		/** The owner types text in the main session; it reaches the agent as the prompt. */
		typed: async (text: string) => {
			await emit("input", { text, source: "interactive" });
			await emit("before_agent_start", { reason: "prompt", prompt: text });
			return notes.at(-1) ?? "";
		},
		emit,
		config: (): Config => JSON.parse(readFileSync(path, "utf8")),
		draft: (): Config => JSON.parse(readFileSync(join(root, ".git", "agentrails", "draft.json"), "utf8")),
	};
}

describe("owner line parsers", () => {
	test("parse the four forms, and never a judge.ts approval line", () => {
		assert.deepEqual(parseOwnerLine("$approve exception NAME.conventions src/legacy/** check=child-repeats-parent -- vendored names"), {
			section: "exceptions", verb: "approve", key: { rule: "NAME.conventions", path: "src/legacy/**", check: "child-repeats-parent" }, reason: "vendored names",
		});
		assert.deepEqual(parseOwnerLine("  $revoke exception CODE.no-comments docs/x.py  "), {
			section: "exceptions", verb: "revoke", key: { rule: "CODE.no-comments", path: "docs/x.py" },
		});
		assert.deepEqual(parseOwnerLine("$approve approval skip tests/test_slow.py::test_a[b c] -- needs a container"), {
			section: "approvals", verb: "approve", key: { kind: "skip", target: "tests/test_slow.py::test_a[b c]" }, reason: "needs a container",
		});
		assert.deepEqual(parseOwnerLine("$revoke approval budget m-1"), { section: "approvals", verb: "revoke", key: { kind: "budget", target: "m-1" } });
		const judged = ["$approve in-scope:0123456789ab", "$approve autonomy:merge:7", "$approve completion-evidence:abcdefabcdef12"];
		for (const line of judged) {
			assert.equal(parseOwnerLine(line), undefined, line);
			assert.equal(isOwnerLine(line), false, line);
		}
		const approvals = new Approvals();
		assert.deepEqual(approvals.fromPrompt("s", judged.join("\n")), ["in-scope:0123456789ab", "autonomy:merge:7", "completion-evidence:abcdefabcdef12"]);
		assert.deepEqual(approvals.fromPrompt("s", "$approve exception CODE.no-comments x -- r\n$approve approval skip t -- r\n$revoke approval skip t"), []);
		for (const line of ["$approve exception CODE.no-comments x", "$approve approval forever t -- r", "$revoke exception", "$approve approval skip -- r"]) {
			assert.equal(isOwnerLine(line), true, line);
			assert.equal(parseOwnerLine(line), undefined, line);
		}
	});

	test("an approve adds or replaces its entry; a revoke removes it or finds nothing", () => {
		const signature = { approvedBy: "o", approvedAt: "2026-11-24T09:00:00Z" };
		const line = parseOwnerLine("$approve approval ratchet pyproject.toml -- first");
		assert.ok(line);
		const once = applyOwnerLine({ exceptions: [], approvals: [] }, line, signature);
		const second = parseOwnerLine("$approve approval ratchet pyproject.toml -- second");
		assert.ok(second);
		const again = once && applyOwnerLine(once, second, signature);
		assert.deepEqual(again?.approvals, [{ kind: "ratchet", target: "pyproject.toml", reason: "second", ...signature }]);
		const revoke = parseOwnerLine("$revoke approval ratchet pyproject.toml");
		assert.ok(revoke && again);
		assert.deepEqual(applyOwnerLine(again, revoke, signature)?.approvals, []);
		assert.equal(applyOwnerLine({ exceptions: [], approvals: [] }, revoke, signature), undefined);
	});
});

describe("owner lines write the config", () => {
	test("each form writes or removes its entry, signed with the gh login, and the engine refuses what is invalid", async () => {
		const o = owner();
		assert.match(await o.typed("$approve exception NAME.conventions src/legacy/** check=child-repeats-parent -- vendored names"), /approved exception NAME.conventions src\/legacy\/\*\* check=child-repeats-parent/);
		const [entry] = o.config().exceptions;
		assert.deepEqual({ ...entry, approvedAt: "" }, {
			rule: "NAME.conventions", path: "src/legacy/**", check: "child-repeats-parent", reason: "vendored names", approvedBy: "example-owner", approvedAt: "",
		});
		assert.match(entry?.approvedAt ?? "", ISO);
		assert.match(await o.typed("$approve approval skip tests/test_x.py::test_slow -- needs a container"), /approved approval skip/);
		assert.deepEqual(o.config().approvals.map(item => [item.kind, item.target, item.approvedBy]), [
			["budget", "a1b2c3d4e5f60718", "example-owner"], ["skip", "tests/test_x.py::test_slow", "example-owner"],
		]);
		assert.match(await o.typed("$revoke exception NAME.conventions src/legacy/** check=child-repeats-parent"), /revoked exception/);
		assert.match(await o.typed("$revoke approval skip tests/test_x.py::test_slow"), /revoked approval skip/);
		assert.deepEqual({ exceptions: o.config().exceptions, approvals: o.config().approvals }, {
			exceptions: [], approvals: JSON.parse(readFileSync(FIXTURE, "utf8")).approvals,
		});
		const before = readFileSync(o.path, "utf8");
		assert.match(await o.typed("$approve exception NOT.a-rule src -- r"), /not written:\nexceptions\[0\].rule: "NOT.a-rule" is not a rule id/);
		assert.match(await o.typed("$approve approval budget m-unknown -- r"), /approvals\[1\].target: 'm-unknown' is not a tests.fast_tier_budget.machines\[\].id/);
		assert.match(await o.typed("$revoke approval skip never-approved"), /not written:\nthe config has no approval skip never-approved/);
		assert.match(await o.typed("$approve approval forever x -- r"), /is not an owner line; use one of:/);
		assert.equal(readFileSync(o.path, "utf8"), before, "refused lines leave the config as it was");
		assert.equal(o.gh.calls, 1, "the login is read once per session");
		o.newSession("s2");
		await o.typed("$revoke approval skip never-approved");
		assert.equal(o.gh.calls, 2);
	}, TIMEOUT_MS);

	test("injected and subagent prompts never write; only lines the owner typed in the main session count", async () => {
		const o = owner();
		const before = readFileSync(o.path, "utf8");
		const line = "$approve approval skip tests/x -- r";
		await o.emit("before_agent_start", { reason: "prompt", prompt: line });
		assert.match(o.notes.at(-1) ?? "", /ignored "\$approve approval skip tests\/x -- r": only lines the owner types in the main session/);
		await o.emit("input", { text: line, source: "extension" });
		await o.emit("before_agent_start", { reason: "prompt", prompt: line });
		await o.emit("input", { text: line, source: "rpc" });
		await o.emit("before_agent_start", { reason: "prompt", prompt: line });
		await o.emit("input", { text: line, source: "interactive" }, "sub");
		await o.emit("before_agent_start", { reason: "prompt", prompt: line }, "sub");
		await o.emit("before_agent_start", { reason: "revive", prompt: line });
		assert.equal(readFileSync(o.path, "utf8"), before);
		assert.equal(o.gh.calls, 0);
		const ignored = () => o.notes.filter(note => note.includes("ignored")).length;
		assert.equal(ignored(), 3, "the injected, extension and rpc prompts; subagent and revive prompts are not read");
		await o.emit("input", { text: line, source: "interactive" });
		await o.emit("before_agent_start", { reason: "prompt", prompt: `${line}\n${line}` });
		assert.equal(o.config().approvals.length, 2, "the typed line wrote once");
		assert.equal(ignored(), 4, "the copy the owner did not type is ignored");
	}, TIMEOUT_MS);

	test("a failed gh login read refuses the write", async () => {
		const o = owner();
		o.gh.fails = true;
		assert.match(await o.typed("$approve approval skip tests/x -- r"), /approval skip tests\/x not written: the owner's GitHub login is needed for approvedBy and gh api user failed \(exit 1\): gh: not logged in/);
		assert.equal(o.config().approvals.length, 1);
		o.gh.fails = false;
		assert.match(await o.typed("$approve approval skip tests/x -- r"), /approved/, "a failed read is not kept");
	}, TIMEOUT_MS);

	test("with no valid config, lines need an onboarding in progress, and then go to the draft", async () => {
		const o = owner(false);
		assert.match(await o.typed("$approve approval budget m-1 -- r"), /needs a valid agent-rails config \(its config is missing\); run \/agentrails onboard/);
		o.session.onboarding.add(o.root);
		assert.match(await o.typed("$approve approval budget m-1 -- r"), /approved approval budget m-1/);
		assert.match(await o.typed("$approve exception NOT.a-rule src -- r"), /exceptions\[0\].rule: "NOT.a-rule" is not a rule id/);
		assert.match(await o.typed("$revoke exception CODE.no-comments src"), /the onboarding draft has no exception CODE.no-comments src/);
		assert.deepEqual(o.draft().approvals.map(item => [item.kind, item.target]), [["budget", "m-1"]]);
		assert.deepEqual(o.draft().exceptions, []);
		assert.equal(existsSync(o.path), false, "the draft never becomes a config here");
	}, TIMEOUT_MS);
});
