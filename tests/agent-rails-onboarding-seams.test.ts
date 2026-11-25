import assert from "node:assert/strict";
import { execFile, execFileSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { afterEach, describe, test } from "bun:test";
import { z } from "@oh-my-pi/pi-coding-agent";
import { QUESTIONS, onboardingInstruction, registerOwnerSurface, skillVersion } from "../agent/extensions/agent-rails/lib/onboarding.ts";
import { configPathFor, resolveConfig, type AgentRailsConfig, type SkillScope } from "../agent/extensions/agent-rails/lib/repo-config.ts";

const ENGINE = resolve(import.meta.dir, "../agent/extensions/agent-rails/engine");
const CHECK = ["uv", "run", "--project", ENGINE, "guardrails", "config-check"];
const MINIMAL: AgentRailsConfig = JSON.parse(readFileSync(resolve(import.meta.dir, "fixtures/agentrails/valid/minimal.json"), "utf8"));
const SLUG = "example-owner/ledger";
const TIMEOUT_MS = 120_000;

const execFailure = z.object({ code: z.number().optional(), killed: z.boolean().optional() });
const labelled = z.object({ label: z.string() });
const git = (cwd: string, ...args: string[]) => execFileSync("git", ["-C", cwd, ...args], { stdio: "pipe", encoding: "utf8" });
type ExecResult = { code: number; killed: boolean; stdout: string; stderr: string };

function realExec(command: string, args: string[], options?: { cwd?: string; timeout?: number }): Promise<ExecResult> {
	return new Promise(done => {
		execFile(command, args, { cwd: options?.cwd, timeout: options?.timeout, encoding: "utf8" }, (error, stdout, stderr) => {
			const failure = error === null ? { code: 0 } : execFailure.parse(error);
			done({ code: failure.code ?? 1, killed: failure.killed ?? false, stdout, stderr });
		});
	});
}

type Asked = { kind: "select" | "confirm" | "input"; title: string; options: string[] };
/** The owner's answers by dialog title; a select with no rule takes the first option, a confirm says no, an input is blank. */
type Script = {
	select?: (title: string, options: string[]) => string | undefined;
	confirm?: (title: string) => boolean;
	input?: (title: string) => string | undefined;
};
type Tool = { name: string; execute: Function };
type Command = { handler: Function; getArgumentCompletions?: Function | undefined };
type SectionValue = AgentRailsConfig["commands" | "checkers" | "structure" | "houseStyle" | "tests"];
type Event = { text?: string; source?: string; reason?: string; prompt?: string };

/** A real repository with a GitHub origin and origin/HEAD, the owner surface registered on a stub omp, and a recording UI. */
function surface(script: Script = {}) {
	const temp = realpathSync(mkdtempSync(join(tmpdir(), "agentrails-onboarding-")));
	const root = join(temp, "ledger");
	git(temp, "init", "-q", "-b", "main", root);
	git(root, "remote", "add", "origin", `https://github.com/${SLUG}.git`);
	git(root, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "core.hooksPath=/dev/null", "commit", "-q", "--allow-empty", "-m", "init");
	git(root, "update-ref", "refs/remotes/origin/main", "HEAD");
	git(root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main");
	const skills = join(temp, "skills");
	for (const skill of ["setup-repo-proofs", "test-guidance"]) {
		mkdirSync(join(skills, skill, "references"), { recursive: true });
		writeFileSync(join(skills, skill, "SKILL.md"), `# ${skill}\n`);
		writeFileSync(join(skills, skill, "references", "a.md"), "reference\n");
	}
	const asked: Asked[] = [];
	const notes: string[] = [];
	const sent: string[] = [];
	const ghCalls: string[][] = [];
	let ghFails = false;
	const commands = new Map<string, Command>();
	const tools = new Map<string, Tool>();
	const handlers = new Map<string, Function[]>();
	const session = { reads: new Set<string>(), onboarding: new Set<string>() };
	const pi = {
		async exec(command: string, args: string[], options?: { cwd?: string; timeout?: number }): Promise<ExecResult> {
			if (command !== "gh") return realExec(command, args, options);
			ghCalls.push(args);
			if (ghFails) return { code: 1, killed: false, stdout: "", stderr: "gh: not logged in" };
			const endpoint = args[1] ?? "";
			if (endpoint === "user") return { code: 0, killed: false, stdout: "example-owner\n", stderr: "" };
			return { code: 0, killed: false, stdout: endpoint.endsWith("check-runs") ? "gate\nlint\n" : "gate\n", stderr: "" };
		},
		on(name: string, handler: Function) { handlers.set(name, [...(handlers.get(name) ?? []), handler]); },
		registerCommand(name: string, options: Command) { commands.set(name, options); },
		registerTool(tool: Tool) { tools.set(tool.name, tool); },
		async sendUserMessage(text: string) { sent.push(text); },
	};
	registerOwnerSurface(pi, { check: { argv: CHECK }, engineDir: ENGINE, skillsDir: skills, session: () => session });
	const ui = {
		async select(title: string, options: (string | { label: string })[]) {
			const labels = options.map(option => labelled.safeParse(option).success ? labelled.parse(option).label : String(option));
			asked.push({ kind: "select", title, options: labels });
			return script.select ? script.select(title, labels) : labels[0];
		},
		async confirm(title: string) { asked.push({ kind: "confirm", title, options: [] }); return script.confirm?.(title) ?? false; },
		async input(title: string) { asked.push({ kind: "input", title, options: [] }); return script.input?.(title) ?? ""; },
		notify(message: string) { notes.push(message); },
	};
	const ctx = { cwd: root, hasUI: true, agent: { kind: "main", id: "main", name: "main", depth: 0 }, ui, sessionManager: { getSessionId: () => "s1" } };
	const configFile = configPathFor(root);
	return {
		temp, root, skills, asked, notes, sent, ghCalls, session, ctx, tools, handlers, config: configFile,
		failGh: () => { ghFails = true; },
		run: async (args: string) => { await commands.get("agentrails")?.handler(args, ctx); return notes.at(-1) ?? ""; },
		completions: (prefix: string) => commands.get("agentrails")?.getArgumentCompletions?.(prefix),
		record: async (section: string, value: SectionValue, scope: SkillScope = { kind: "whole" }, at = ctx): Promise<{ text: string; code?: string }> => {
			const result = await tools.get("agentrails_config")?.execute("call", { section, value, scope }, undefined, undefined, at);
			return { text: result.content[0].text, code: result.details.code };
		},
		emit: async (name: string, event: Event, at = ctx) => { for (const handler of handlers.get(name) ?? []) await handler({ type: name, ...event }, at); },
		place: (config: AgentRailsConfig) => { mkdirSync(join(root, ".agentrails"), { recursive: true }); writeFileSync(join(root, ".agentrails", "config.json"), `${JSON.stringify(config, null, 2)}\n`); },
		read: (): AgentRailsConfig => JSON.parse(readFileSync(configFile, "utf8")),
		exclude: () => readFileSync(join(root, ".git", "info", "exclude"), "utf8").split("\n"),
		draft: () => join(root, ".git", "agentrails", "draft.json"),
	};
}
type Surface = ReturnType<typeof surface>;

const surfaces: Surface[] = [];
function open(script: Script = {}): Surface {
	const opened = surface(script);
	surfaces.push(opened);
	return opened;
}
afterEach(() => { for (const opened of surfaces.splice(0)) rmSync(opened.temp, { recursive: true, force: true }); });

const { checkers, structure, houseStyle, tests } = MINIMAL;
const budget = "$approve approval budget a1b2c3d4e5f60718 -- fast-tier budget 20 s approved";

// The owner's answers for the onboarding Q&A this suite pins.
function ownerAnswers(): Script {
	let argvTries = 0;
	let policies = 0;
	let rulePicked = false;
	const selects: [RegExp, string][] = [
		[/^May agents merge/, "ask"], [/^Which runner/, "github-actions"], [/^judgments: structure-naming\/same_thing$/, "shadow"],
		[/^judgments: extension-impact\/impact$/, "off"], [/^mergePolicies: merge method/, "rebase"],
	];
	return {
		select: (title, options) => {
			if (title.startsWith("gates.rules")) {
				const pick = rulePicked ? "done" : "CODE.no-comments: on";
				rulePicked = true;
				return pick;
			}
			return selects.find(([pattern]) => pattern.test(title))?.[1] ?? options[0];
		},
		confirm: title => {
			if (title.startsWith("mergePolicies: add")) return policies++ === 0;
			return !title.startsWith("Run the path check");
		},
		input: title => {
			if (title.startsWith("mergePolicies: base")) return "main";
			if (title.startsWith("mergePolicies: head")) return "agent/";
			if (title.startsWith("The after-merge command")) return argvTries++ === 0 ? "make sync" : '["make", "sync"]';
			return title.startsWith("Only merges touching") ? "none" : "";
		},
	};
}

describe("the onboarding Q&A", () => {
	test("asks the fixed questions in order; neither ask_first, dependency impact nor gates.audit is offered", () => {
		assert.deepEqual(QUESTIONS.map(question => `${question.id}:${question.kind}`), [
			"autonomy.branch:select", "autonomy.pr:select", "autonomy.merge:select", "ci.enabled:confirm", "ci.runner:select",
			"ci.requiredContext:input", "gates.commitChecks:confirm", "gates.pathCheck:confirm", "gates.prove:confirm",
			"gates.structureReview:confirm", "gates.rules:loop", "judgments:loop", "mergePolicies:loop", "afterMerge:confirm",
			"afterMerge.argv:argv-json", "afterMerge.paths:input",
		]);
		assert.ok(QUESTIONS.every(question => !/audit|ask_first|dependency-severity\/impact/.test(question.section)));
	});

	test("the onboarding instruction maps each skill's outputs to its sections", () => {
		const text = onboardingInstruction(SLUG, "/repo", ["commands", "tests"]);
		for (const fragment of [
			"still missing: commands, tests", "skill://setup-repo-proofs", "commands: the repository's commit, ci and audit entry points",
			"commit is the check entrypoint its hook runs", "checkers, structure and houseStyle", "--list-rules", "skill://test-guidance", "translate its tier map, adoption choice and signed plan into the tests section schema", "CONFIG.md, Tests", "guardrails init --repo /repo --out", "/.proofs/", "/agentrails onboard again",
		]) assert.ok(text.includes(fragment), fragment);
	});
});

describe("/agentrails onboard", () => {
	test("runs the setup skills into a draft, then the Q&A, and writes a valid excluded config", async () => {
		const s = open(ownerAnswers());
		mkdirSync(join(s.root, ".githooks"));
		assert.deepEqual(s.completions("co").map((item: { value: string }) => item.value), ["configure"]);
		assert.match(await s.run("onboard"), /onboarding example-owner\/ledger started/);
		assert.equal(s.sent.length, 1);
		assert.ok(s.sent[0]?.includes("still missing: commands, checkers, structure, houseStyle, tests"), s.sent.join("\n"));
		assert.ok(s.session.onboarding.has(s.root));
		assert.ok(s.exclude().includes("/.agentrails/") && s.exclude().includes("/.proofs/"), s.exclude().join("|"));

		const unread = await s.record("commands", { commit: ["make", "gate"] });
		assert.equal(unread.code, "agentrails-config-write");
		assert.match(unread.text, /read skill:\/\/setup-repo-proofs in this session before recording commands/);
		s.session.reads.add("skill://setup-repo-proofs/references/run-state.md");
		const bad = await s.record("commands", { commit: "make gate" });
		assert.match(bad.text, /commands.commit: must be a non-empty argv array/);
		for (const [section, value] of Object.entries({ commands: { commit: ["make", "gate"] }, checkers, structure, houseStyle })) {
			const recorded = await s.record(section, value);
			assert.equal(recorded.code, undefined, recorded.text);
		}
		assert.match((await s.record("tests", tests)).text, /read skill:\/\/test-guidance/);
		s.session.reads.add("skill://test-guidance");
		assert.match((await s.record("tests", tests, { kind: "paths", paths: ["tests"] })).text, /every setup section is recorded/);
		assert.equal(existsSync(s.config), false, "the draft never writes the config");

		await s.emit("input", { text: budget, source: "interactive" });
		await s.emit("before_agent_start", { reason: "prompt", prompt: budget });
		assert.match(s.notes.at(-1) ?? "", /approved approval budget a1b2c3d4e5f60718/);

		await s.run("onboard");
		assert.match(s.notes.at(-1) ?? "", /example-owner\/ledger is onboarded .* core.hooksPath is .githooks\./);
		const config = s.read();
		assert.deepEqual({ autonomy: config.autonomy, ci: config.ci, gates: config.gates, judgments: config.judgments, mergePolicies: config.mergePolicies, afterMerge: config.afterMerge }, {
			autonomy: { branch: "allowed", pr: "allowed", merge: "ask" },
			ci: { enabled: true, runner: "github-actions", requiredContext: "gate" },
			gates: { commitChecks: true, pathCheck: false, prove: true, structureReview: true, rules: { "CODE.no-comments": false }, audit: false },
			judgments: { "structure-naming/same_thing": { enabled: true, mode: "shadow" }, "extension-impact/impact": { enabled: false } },
			mergePolicies: [{ base: "main", headPrefix: "agent/", method: "rebase", proof: true }],
			afterMerge: { argv: ["make", "sync"] },
		});
		assert.deepEqual(config.repo, { slug: SLUG, defaultBranch: "main" });
		assert.equal(config.onboarding.origin, "onboarding");
		assert.equal(config.onboarding.skills["setup-repo-proofs"]?.version, await skillVersion(join(s.skills, "setup-repo-proofs")));
		assert.deepEqual(config.onboarding.skills["test-guidance"]?.scope, { kind: "paths", paths: ["tests"] });
		assert.deepEqual(config.approvals.map(entry => [entry.kind, entry.target, entry.approvedBy]), [["budget", "a1b2c3d4e5f60718", "example-owner"]]);
		assert.deepEqual(config.commands, { commit: ["make", "gate"] });
		assert.equal((await resolveConfig({ exec: realExec }, ENGINE, s.root)).kind, "ok");
		assert.equal(git(s.root, "status", "--porcelain", "--untracked-files=all"), "", "the config and draft stay out of Git");
		assert.equal(existsSync(s.draft()), false, "onboarding deletes the draft");
		assert.equal(s.session.onboarding.has(s.root), false);
		assert.equal(git(s.root, "config", "--get", "core.hooksPath").trim(), ".githooks");
		const titles = s.asked.map(entry => entry.title);
		assert.ok(titles.some(title => title.includes("blank keeps gate")), "the required context is prefilled from the default branch's checks");
		assert.ok(titles.some(title => title.includes("afterMerge.argv must be a JSON array")), "a malformed argv is asked again");
		assert.ok(!titles.some(title => /ask_first|dependency-severity\/impact|audit/.test(title)), titles.join("\n"));
		assert.deepEqual(s.ghCalls.filter(args => args[1] !== "user").map(args => args[1]), [
			`repos/${SLUG}/commits/main/check-runs`, `repos/${SLUG}/commits/main/status`,
		]);
		assert.match(await s.run("onboard"), /already onboarded .* run \/agentrails configure/);
	}, TIMEOUT_MS);

	test("refuses outside an interactive session and outside a GitHub repository", async () => {
		const s = open();
		s.ctx.hasUI = false;
		for (const sub of ["onboard", "configure"]) {
			assert.equal(await s.run(sub), `Agent rails: run /agentrails ${sub} in an interactive omp session.`);
		}
		assert.equal(s.sent.length, 0);
		assert.equal(s.session.onboarding.size, 0);
		s.ctx.hasUI = true;
		s.ctx.cwd = s.temp;
		assert.match(await s.run("onboard"), /no configurable repository \(outside\)/);
		assert.match(await s.run("bogus"), /usage: \/agentrails <onboard\|configure\|export\|import\|status>/);
	}, TIMEOUT_MS);

	test("a cancelled Q&A writes nothing and keeps the draft", async () => {
		const s = open({ select: () => undefined });
		await s.run("onboard");
		s.session.reads.add("skill://setup-repo-proofs").add("skill://test-guidance");
		for (const [section, value] of Object.entries({ commands: MINIMAL.commands, checkers, structure, houseStyle, tests })) await s.record(section, value);
		assert.equal(await s.run("onboard"), "Agent rails: onboarding Q&A cancelled; nothing was written.");
		assert.equal(existsSync(s.config), false);
		assert.ok(existsSync(s.draft()));
		assert.match(await s.run("status"), /config missing at .*\nonboarding in this session: in progress\ndraft: .*sections: commands, checkers, structure, houseStyle, tests; missing: nothing/);
	}, TIMEOUT_MS);
});

describe("/agentrails configure", () => {
	test("rewrites only the Q&A sections, prefilled from the config", async () => {
		const s = open({
			select: (title, options) => title.startsWith("May agents merge") ? "allowed" : title.startsWith("gates.rules") ? "done" : options[0],
			confirm: title => /^(?:Run|Do merges that need proof|mergePolicies: keep)/.test(title),
		});
		const before: AgentRailsConfig = { ...MINIMAL, mergePolicies: [{ base: "main", headPrefix: "", method: "merge", proof: false }], judgments: { "extension-impact/impact": { enabled: false } } };
		s.place(before);
		await s.run("configure");
		assert.match(s.notes.at(-1) ?? "", /example-owner\/ledger config updated/);
		const after = s.read();
		const { autonomy, ci, gates, judgments, mergePolicies, afterMerge, ...rest } = after;
		const { autonomy: _a, ci: _c, gates: _g, judgments: _j, mergePolicies: _m, ...kept } = before;
		assert.deepEqual(rest, kept, "every section outside the Q&A is untouched");
		assert.deepEqual(autonomy, { branch: "allowed", pr: "allowed", merge: "allowed" });
		assert.deepEqual(ci, { enabled: false });
		assert.deepEqual(gates, { ...MINIMAL.gates, rules: {} });
		assert.deepEqual(judgments, {}, "the judgment selects start on the config's override and the owner chose follow global");
		assert.deepEqual(mergePolicies, before.mergePolicies);
		assert.equal(afterMerge, undefined);
		const prefilled = s.asked.find(entry => entry.title === "judgments: extension-impact/impact");
		assert.deepEqual(prefilled?.options, ["follow global (enforced)", "shadow", "off"]);
		assert.ok(s.asked.some(entry => entry.title === "gates.commitChecks" || entry.title.startsWith("Run commands.commit")));
	}, TIMEOUT_MS);

	test("brings a config refused only for an older questionnaire version up to date; refuses other invalid configs", async () => {
		const s = open({ select: (title, options) => title.startsWith("gates.rules") ? "done" : options[0], confirm: title => !title.startsWith("mergePolicies: add"), input: title => title.startsWith("The after") ? '["true"]' : "" });
		s.place({ ...MINIMAL, onboarding: { ...MINIMAL.onboarding, questionnaireVersion: 0 + 2 } });
		assert.equal((await resolveConfig({ exec: realExec }, ENGINE, s.root)).kind, "invalid");
		await s.run("configure");
		assert.equal(s.read().onboarding.questionnaireVersion, 1);
		assert.equal((await resolveConfig({ exec: realExec }, ENGINE, s.root)).kind, "ok");
		s.place({ ...MINIMAL, commands: { commit: [] } });
		assert.match(await s.run("configure"), /has an invalid config: commands.commit/);
		rmSync(s.config);
		assert.match(await s.run("configure"), /has no config; run \/agentrails onboard/);
	}, TIMEOUT_MS);
});

describe("/agentrails export and import", () => {
	test("export writes the config outside every work tree only", async () => {
		const s = open();
		s.place(MINIMAL);
		assert.match(await s.run(`export ${join(s.root, "out.json")}`), /export refused: .*inside the Git work tree/);
		assert.match(await s.run(`export ${join(s.root, ".git", "out.json")}`), /export refused: .*inside a Git repository/);
		assert.match(await s.run("export"), /usage: \/agentrails export/);
		const target = join(s.temp, "ledger-config.json");
		assert.match(await s.run(`export ${target}`), /exported example-owner\/ledger config/);
		assert.deepEqual(JSON.parse(readFileSync(target, "utf8")), MINIMAL);
		assert.match(await s.run(`export ${target}`), /export refused: EEXIST/);
		rmSync(s.config);
		assert.match(await s.run(`export ${join(s.temp, "other.json")}`), /only a valid config is exported; this repository's is missing/);
	}, TIMEOUT_MS);

	test("import validates against the origin, and asks before it replaces a config", async () => {
		const s = open();
		const source = join(s.temp, "import.json");
		writeFileSync(source, JSON.stringify({ ...MINIMAL, commands: {} }));
		assert.match(await s.run(`import ${source}`), /import refused, .* is not a valid config for example-owner\/ledger:\ncommands.commit: is missing/);
		writeFileSync(source, JSON.stringify({ ...MINIMAL, repo: { ...MINIMAL.repo, slug: "example-owner/other" } }));
		assert.match(await s.run(`import ${source}`), /repo.slug: is 'example-owner\/other' but the origin remote names 'example-owner\/ledger'/);
		assert.equal(existsSync(s.config), false);
		writeFileSync(source, JSON.stringify(MINIMAL));
		assert.match(await s.run(`import ${source}`), /imported .* as example-owner\/ledger's config/);
		assert.deepEqual(s.read(), MINIMAL);
		assert.ok(s.exclude().includes("/.agentrails/"));
		writeFileSync(source, JSON.stringify({ ...MINIMAL, autonomy: { ...MINIMAL.autonomy, merge: "allowed" } }));
		assert.equal(await s.run(`import ${source}`), "Agent rails: import cancelled; the existing config was kept.");
		assert.equal(s.read().autonomy.merge, "ask");
		s.ctx.hasUI = false;
		assert.equal(s.notes.at(-1), "Agent rails: import cancelled; the existing config was kept.");
		await s.run(`import ${source}`);
		assert.ok(s.notes.includes("Agent rails: run /agentrails import in an interactive omp session."));
	}, TIMEOUT_MS);
});

describe("the agentrails_config tool", () => {
	test("refuses subagents, repositories not being onboarded, and on an onboarded repository writes only what the owner confirms", async () => {
		let allow = false;
		const s = open({ confirm: () => allow });
		s.session.reads.add("skill://setup-repo-proofs");
		const sub = await s.record("commands", MINIMAL.commands, { kind: "whole" }, { ...s.ctx, agent: { kind: "sub", id: "t", name: "task", depth: 1 } });
		assert.match(sub.text, /only the main agent records config sections/);
		assert.match((await s.record("commands", MINIMAL.commands)).text, /no onboarding is in progress for this repository in this session; ask the owner to run \/agentrails onboard/);
		assert.equal(existsSync(s.draft()), false);
		s.place(MINIMAL);
		const audited = { commit: ["make", "gate-commit"], audit: ["make", "audit"] };
		assert.match((await s.record("commands", audited)).text, /the owner declined the commands rewrite; the config is unchanged/);
		assert.deepEqual(s.read(), MINIMAL);
		allow = true;
		assert.match((await s.record("commands", audited)).text, /commands written to/);
		assert.deepEqual([s.read().commands, s.read().gates.audit], [audited, true], "gates.audit follows commands.audit");
		assert.deepEqual(s.read().onboarding, MINIMAL.onboarding, "the tool never stamps an onboarded config");
		s.ctx.hasUI = false;
		assert.match((await s.record("houseStyle", { version: 1 })).text, /needs the owner's confirmation in an interactive omp session/);
	}, TIMEOUT_MS);
});
