// The owner's command surface for a repository's agent-rails config: the /agentrails slash
// command (onboard, configure, export, import, status) and the agentrails_config tool the main
// agent records the setup sections with. A slash command is typed by the owner; agents cannot run
// it. Every subcommand that prompts needs an interactive omp session (ctx.hasUI).
import { readFile, stat, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { z, type ExtensionAPI, type ExtensionContext, type ExtensionUIContext } from "@oh-my-pi/pi-coding-agent";
import {
	SECTION_SKILL, SETUP_SECTIONS, checkFile, checkSection, deleteDraft, draftGaps, draftPath, emptyDraft, engineChecked, ensureRepoExcludes,
	readDraft, runConfigCheck, skillScope, skillVersion, writeConfig, writeDraft, type ConfigCheck, type Draft, type SetupSection,
} from "./config-writer.ts";
import { QUESTIONNAIRE_VERSION, runQuestionnaire, type QaEnv, type QaSections } from "./questionnaire.ts";
import { resolveConfig, type AgentRailsConfig, type Commands, type ConfigState, type EngineSection } from "./repo-config.ts";
import { parseOrigin } from "./git-paths.ts";
import { registerOwnerApprovals } from "./owner-approvals.ts";
import type { RailCode } from "./rails-ledger.ts";
import { SUBPROCESS_TIMEOUT_MS } from "./rails-runtime.ts";

export { QUESTIONS } from "./questionnaire.ts";
export { skillVersion } from "./config-writer.ts";

/** The session state onboarding shares with the rails: skill reads seen and repositories mid-onboarding (main checkouts). */
export type OnboardingSession = { reads: Set<string>; onboarding: Set<string> };
export type OnboardingContext = Pick<ExtensionContext, "cwd" | "hasUI" | "agent" | "sessionManager"> & {
	ui: Pick<ExtensionUIContext, "select" | "confirm" | "input" | "notify">;
};
export type AgentRailsDeps = {
	check: ConfigCheck; engineDir: string; skillsDir: string; session: (ctx: OnboardingContext) => OnboardingSession;
};
type AgentRailsApi = Pick<ExtensionAPI, "exec" | "on" | "registerCommand" | "registerTool" | "sendUserMessage">;
type Located = Extract<ConfigState, { main: string }>;
type Run = { pi: AgentRailsApi; deps: AgentRailsDeps; ctx: OnboardingContext };

const SUBCOMMANDS = {
	onboard: "run the setup skills, then the Q&A, and write .agentrails/config.json",
	configure: "re-run the Q&A for an onboarded repository",
	export: "export <file>: write the config to a file outside every Git work tree",
	import: "import <file>: validate a config file against this repository's origin and write it",
	status: "show the config state, problems, onboarding state and draft",
} as const;
type Subcommand = keyof typeof SUBCOMMANDS;
const isSubcommand = (word: string): word is Subcommand => Object.keys(SUBCOMMANDS).includes(word);

const rulesList = z.object({ rules: z.array(z.object({ id: z.string(), summary: z.string() })) });
const questionsList = z.object({ questions: z.array(z.object({ key: z.string(), mode: z.enum(["enforced", "shadow"]), shadowable: z.boolean() })) });
const toolParams = z.object({
	section: z.enum(SETUP_SECTIONS).describe("the config section: commands, checkers, structure or houseStyle after skill://setup-repo-proofs; tests after skill://test-guidance"),
	value: z.record(z.string(), z.unknown()).describe("the whole section value, as engine/data/config-template.json describes it"),
	scope: skillScope.describe("what the skill run covered: {kind: whole}, {kind: changed} or {kind: paths, paths: [...]}"),
});
type ToolParams = z.infer<typeof toolParams>;

const say = (ctx: OnboardingContext, text: string, level: "info" | "error" = "info") => ctx.ui.notify(text, level);
const reply = (text: string) => ({ content: [{ type: "text" as const, text }], details: {} });

/** The fixed instruction /agentrails onboard sends the agent while setup sections are missing. */
export function onboardingInstruction(slug: string, root: string, gaps: string[]): string {
	return [
		`Agent rails onboarding for ${slug}. The owner ran /agentrails onboard; still missing: ${gaps.join(", ")}.`,
		"1. Read skill://setup-repo-proofs and run it against this repository. Record what it settles with the agentrails_config tool:",
		"   - commands: the repository's commit, ci and audit entry points the skill installs, as argv arrays: commit is the check " +
			"entrypoint its hook runs, ci the entrypoint its CI job runs with the full T1 and T2 suite, audit its dependency audit;",
		"   - checkers, structure and houseStyle: the settings the engine needs (the extension's engine/data/config-template.json).",
		"     Rule IDs come from `guardrails config-check --list-rules`, never from the skill's assets/guardrail-rules.json.",
		"2. Read skill://test-guidance and run it against this repository. It writes no rails config: translate its tier map, adoption choice and " +
			"signed plan into the tests section schema (the extension's engine/data/config-template.json, tests) and record it with the agentrails_config tool.",
		`   A draft from \`guardrails init --repo ${root} --out <a file outside the repository>\` may seed these sections.`,
		"3. Do not edit .git/info/exclude: the extension keeps /.agentrails/ and /.proofs/ out of Git. Set core.hooksPath only to a directory inside the repository (for example .githooks); any other value is refused.",
		"4. When every section is recorded, ask the owner to run /agentrails onboard again: it runs a short Q&A and writes .agentrails/config.json.",
	].join("\n");
}

function located(state: ConfigState, ctx: OnboardingContext): state is Located {
	if ("main" in state) return true;
	say(ctx, `Agent rails: this directory has no configurable repository (${state.kind}): ${state.reason}`, "error");
	return false;
}

function interactive(ctx: OnboardingContext, sub: Subcommand): boolean {
	if (!ctx.hasUI) say(ctx, `Agent rails: run /agentrails ${sub} in an interactive omp session.`, "error");
	return ctx.hasUI;
}

async function gitText(run: Run, main: string, args: string[]): Promise<string | undefined> {
	const result = await run.pi.exec("git", args, { cwd: main, timeout: SUBPROCESS_TIMEOUT_MS });
	return result.code === 0 && !result.killed ? result.stdout.trim() : undefined;
}

async function originOf(run: Run, main: string): Promise<string | undefined> {
	const url = await gitText(run, main, ["remote", "get-url", "origin"]);
	return url === undefined ? undefined : parseOrigin(url) ?? undefined;
}

async function defaultBranch(run: Run, main: string): Promise<string | undefined> {
	const head = await gitText(run, main, ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"]);
	return head?.startsWith("origin/") ? head.slice("origin/".length) : undefined;
}

async function listed(run: Run, main: string, flag: string): Promise<string> {
	const ran = await runConfigCheck(run.pi, run.deps.check, main, [flag]);
	if ("failure" in ran) throw new Error(ran.failure);
	return ran.line;
}

async function ciContexts(run: Run, main: string, path: string): Promise<string[]> {
	const found: string[] = [];
	for (const [endpoint, query] of [["check-runs", ".check_runs[].name"], ["status", ".statuses[].context"]] as const) {
		const result = await run.pi.exec("gh", ["api", `${path}/${endpoint}`, "--jq", query], { cwd: main, timeout: SUBPROCESS_TIMEOUT_MS });
		if (result.code === 0 && !result.killed) found.push(...result.stdout.split("\n").map(line => line.trim()).filter(Boolean));
	}
	return [...new Set(found)];
}

async function qaEnv(run: Run, at: { main: string; slug: string; branch: string }, current: QaSections | undefined): Promise<QaEnv> {
	const rules = rulesList.parse(JSON.parse(await listed(run, at.main, "--list-rules"))).rules;
	const questions = questionsList.parse(JSON.parse(await listed(run, at.main, "--list-questions"))).questions;
	return { rules, questions, current, contexts: () => ciContexts(run, at.main, `repos/${at.slug}/commits/${at.branch}`) };
}

/** The Q&A sections of a config, to prefill /agentrails configure. */
export function qaSections(config: AgentRailsConfig): QaSections {
	const { audit: _audit, ...gates } = config.gates;
	const sections: QaSections = { autonomy: config.autonomy, ci: config.ci, gates, judgments: config.judgments, mergePolicies: config.mergePolicies };
	if (config.afterMerge) sections.afterMerge = config.afterMerge;
	return sections;
}

/** config with the Q&A's sections; gates.audit follows commands.audit, and afterMerge is dropped when the owner declined it. */
export function withQa(config: AgentRailsConfig, qa: QaSections): AgentRailsConfig {
	const { afterMerge: _previous, ...rest } = config;
	const next: AgentRailsConfig = {
		...rest, autonomy: qa.autonomy, ci: qa.ci, gates: { ...qa.gates, audit: config.commands.audit !== undefined },
		judgments: qa.judgments, mergePolicies: qa.mergePolicies,
	};
	if (qa.afterMerge) next.afterMerge = qa.afterMerge;
	return next;
}

function onboardedConfig(at: { slug: string; branch: string }, draft: Draft, qa: QaSections): { config: AgentRailsConfig } | { lacking: string } {
	const { commands, checkers, structure, houseStyle, tests } = draft.sections;
	const proofs = draft.skills["setup-repo-proofs"];
	const guidance = draft.skills["test-guidance"];
	if (!commands || !checkers || !structure || !houseStyle || !tests || !proofs || !guidance) return { lacking: draftGaps(draft).join(", ") };
	const typedCommands = engineChecked<Commands>(commands);
	const base: AgentRailsConfig = {
		schemaVersion: 1, repo: { slug: at.slug, defaultBranch: at.branch },
		onboarding: {
			completedAt: new Date().toISOString(), questionnaireVersion: QUESTIONNAIRE_VERSION,
			skills: { "setup-repo-proofs": proofs, "test-guidance": guidance },
		},
		autonomy: qa.autonomy, ci: qa.ci, gates: { ...qa.gates, audit: false }, judgments: {}, mergePolicies: [],
		commands: typedCommands, checkers, structure, houseStyle: engineChecked<{ version: number }>(houseStyle), tests,
		exceptions: draft.exceptions, approvals: draft.approvals,
	};
	return { config: withQa(base, qa) };
}

async function offerHooksPath(run: Run, main: string): Promise<string | undefined> {
	const hooks = await stat(join(main, ".githooks")).then(found => found.isDirectory(), () => false);
	if (!hooks || await gitText(run, main, ["config", "--get", "core.hooksPath"]) === ".githooks") return undefined;
	const asked = await run.ctx.ui.confirm("Set core.hooksPath to .githooks?", "The repository ships .githooks, where setup-repo-proofs wires its gates. Agents may set core.hooksPath only to a directory inside the repository.");
	if (!asked) return "core.hooksPath is unchanged.";
	return await gitText(run, main, ["config", "core.hooksPath", ".githooks"]) === undefined
		? "git config core.hooksPath .githooks failed; set it yourself." : "core.hooksPath is .githooks.";
}

async function startOnboarding(run: Run, state: Located, slug: string, gaps: string[]): Promise<void> {
	await ensureRepoExcludes(run.pi, state.main);
	run.deps.session(run.ctx).onboarding.add(state.main);
	say(run.ctx, `Agent rails: onboarding ${slug} started. The agent runs the setup skills and records: ${gaps.join(", ")}. Then run /agentrails onboard again.`);
	await run.pi.sendUserMessage(onboardingInstruction(slug, state.main, gaps));
}

async function finishOnboarding(run: Run, state: Located, slug: string, draft: Draft): Promise<void> {
	const branch = await defaultBranch(run, state.main);
	if (branch === undefined) return say(run.ctx, "Agent rails: cannot read origin's default branch; run git remote set-head origin --auto, then retry.", "error");
	const qa = await runQuestionnaire(run.ctx.ui, await qaEnv(run, { main: state.main, slug, branch }, undefined));
	if (qa === undefined) return say(run.ctx, "Agent rails: onboarding Q&A cancelled; nothing was written.");
	const assembled = onboardedConfig({ slug, branch }, draft, qa);
	if ("lacking" in assembled) return say(run.ctx, `Agent rails: cannot finish onboarding: the draft still lacks ${assembled.lacking}.`, "error");
	if (state.kind === "invalid" && !await run.ctx.ui.confirm("Replace the invalid config?", state.problems.join("\n"))) {
		return say(run.ctx, "Agent rails: the invalid config was kept; nothing was written.");
	}
	const written = await writeConfig(run.pi, run.deps.check, state.main, assembled.config);
	if (!written.ok) return say(run.ctx, `Agent rails: the onboarding config was refused:\n${written.problems.join("\n")}`, "error");
	await deleteDraft(run.pi, state.main);
	run.deps.session(run.ctx).onboarding.delete(state.main);
	const hooks = await offerHooksPath(run, state.main);
	say(run.ctx, `Agent rails: ${slug} is onboarded (${state.path}).${hooks ? ` ${hooks}` : ""}`);
}

async function onboard(run: Run): Promise<void> {
	if (!interactive(run.ctx, "onboard")) return;
	const state = await resolveConfig(run.pi, run.deps.engineDir, run.ctx.cwd);
	if (!located(state, run.ctx)) return;
	if (state.kind === "ok") return say(run.ctx, `Agent rails: ${state.config.repo.slug} is already onboarded (${state.path}); run /agentrails configure to change the Q&A answers.`);
	const slug = await originOf(run, state.main);
	if (slug === undefined) return say(run.ctx, "Agent rails: cannot read this repository's GitHub origin.", "error");
	const draft = await readDraft(run.pi, state.main);
	const gaps = draftGaps(draft);
	if (draft === null || gaps.length) return startOnboarding(run, state, slug, gaps);
	return finishOnboarding(run, state, slug, draft);
}

const ONLY_QUESTIONNAIRE = /^onboarding\.questionnaireVersion: /;

// The config configure rewrites: a valid one, or one refused only for an older questionnaire version.
async function configurable(state: Located, ctx: OnboardingContext): Promise<AgentRailsConfig | undefined> {
	if (state.kind === "ok") return state.config;
	if (state.kind === "invalid" && state.problems.every(problem => ONLY_QUESTIONNAIRE.test(problem))) {
		const config: AgentRailsConfig = JSON.parse(await readFile(state.path, "utf8"));
		return config;
	}
	const why = state.kind === "missing" ? "has no config" : `has an invalid config: ${state.problems.join("; ")}`;
	say(ctx, `Agent rails: this repository ${why}; run /agentrails onboard.`, "error");
	return undefined;
}

async function configure(run: Run): Promise<void> {
	if (!interactive(run.ctx, "configure")) return;
	const state = await resolveConfig(run.pi, run.deps.engineDir, run.ctx.cwd);
	if (!located(state, run.ctx)) return;
	const config = await configurable(state, run.ctx);
	if (config === undefined) return;
	const at = { main: state.main, slug: config.repo.slug, branch: config.repo.defaultBranch };
	const qa = await runQuestionnaire(run.ctx.ui, await qaEnv(run, at, qaSections(config)));
	if (qa === undefined) return say(run.ctx, "Agent rails: configure cancelled; nothing was written.");
	const next = withQa(config, qa);
	next.onboarding = { ...config.onboarding, questionnaireVersion: QUESTIONNAIRE_VERSION };
	const written = await writeConfig(run.pi, run.deps.check, state.main, next);
	say(run.ctx, written.ok ? `Agent rails: ${config.repo.slug} config updated (${state.path}).` : `Agent rails: the configured config was refused:\n${written.problems.join("\n")}`, written.ok ? "info" : "error");
}

async function outsideWorkTrees(run: Run, target: string): Promise<string | undefined> {
	const dir = dirname(target);
	if (!await stat(dir).then(found => found.isDirectory(), () => false)) return `${dir} is not a directory`;
	const result = await run.pi.exec("git", ["rev-parse", "--show-toplevel"], { cwd: dir, timeout: SUBPROCESS_TIMEOUT_MS });
	if (result.code === 128 && result.stderr.includes("not a git repository")) return undefined;
	return result.code === 0 ? `${target} is inside the Git work tree ${result.stdout.trim()}` : `${target} is inside a Git repository (${result.stderr.trim()})`;
}

async function exportConfig(run: Run, file: string): Promise<void> {
	if (!file) return say(run.ctx, "Agent rails: usage: /agentrails export <file outside every Git work tree>", "error");
	const state = await resolveConfig(run.pi, run.deps.engineDir, run.ctx.cwd);
	if (!located(state, run.ctx)) return;
	if (state.kind !== "ok") return say(run.ctx, `Agent rails: only a valid config is exported; this repository's is ${state.kind}.`, "error");
	const target = resolve(run.ctx.cwd, file);
	const inside = await outsideWorkTrees(run, target);
	if (inside) return say(run.ctx, `Agent rails: export refused: ${inside}; choose a file outside every Git work tree.`, "error");
	const done = await writeFile(target, `${JSON.stringify(state.config, null, 2)}\n`, { flag: "wx" }).then(() => undefined, (error: Error) => error.message);
	say(run.ctx, done === undefined ? `Agent rails: exported ${state.config.repo.slug} config to ${target}.` : `Agent rails: export refused: ${done}`, done === undefined ? "info" : "error");
}

async function importConfig(run: Run, file: string): Promise<void> {
	if (!file) return say(run.ctx, "Agent rails: usage: /agentrails import <file>", "error");
	const state = await resolveConfig(run.pi, run.deps.engineDir, run.ctx.cwd);
	if (!located(state, run.ctx)) return;
	const source = resolve(run.ctx.cwd, file);
	const slug = await originOf(run, state.main);
	if (slug === undefined) return say(run.ctx, "Agent rails: cannot read this repository's GitHub origin.", "error");
	const verdict = await checkFile(run.pi, run.deps.check, state.main, source, slug);
	if (!verdict.ok) return say(run.ctx, `Agent rails: import refused, ${source} is not a valid config for ${slug}:\n${verdict.problems.join("\n")}`, "error");
	if (state.kind !== "missing" && (!interactive(run.ctx, "import") || !await run.ctx.ui.confirm(`Replace ${state.path}?`, `The repository already has a ${state.kind} config.`))) {
		return say(run.ctx, "Agent rails: import cancelled; the existing config was kept.");
	}
	const config: AgentRailsConfig = JSON.parse(await readFile(source, "utf8"));
	const written = await writeConfig(run.pi, run.deps.check, state.main, config);
	if (!written.ok) return say(run.ctx, `Agent rails: import refused:\n${written.problems.join("\n")}`, "error");
	await deleteDraft(run.pi, state.main);
	run.deps.session(run.ctx).onboarding.delete(state.main);
	say(run.ctx, `Agent rails: imported ${source} as ${slug}'s config (${state.path}).`);
}

async function draftStatus(run: Run, main: string): Promise<string> {
	const path = await draftPath(run.pi, main);
	const draft = await readDraft(run.pi, main).catch((error: Error) => error);
	if (draft instanceof Error) return `draft: ${draft.message}`;
	if (draft === null) return "draft: none";
	const sections = SETUP_SECTIONS.filter(name => draft.sections[name] !== undefined);
	return `draft: ${path}; sections: ${sections.join(", ") || "none"}; missing: ${draftGaps(draft).join(", ") || "nothing"}`;
}

async function status(run: Run): Promise<void> {
	const state = await resolveConfig(run.pi, run.deps.engineDir, run.ctx.cwd);
	if (!("main" in state)) return say(run.ctx, `Agent rails status: ${state.kind}: ${state.reason}`);
	const lines = [
		`Agent rails status: config ${state.kind} at ${state.path}`,
		...(state.kind === "invalid" ? state.problems.map(problem => `  ${problem}`) : []),
		`onboarding in this session: ${run.deps.session(run.ctx).onboarding.has(state.main) ? "in progress" : "not started"}`,
		await draftStatus(run, state.main),
	];
	say(run.ctx, lines.join("\n"));
}

async function subcommand(run: Run, args: string): Promise<void> {
	const [word = "", ...rest] = args.trim().split(/\s+/);
	const file = rest.join(" ");
	if (!isSubcommand(word)) {
		return say(run.ctx, `Agent rails: usage: /agentrails <${Object.keys(SUBCOMMANDS).join("|")}>`, "error");
	}
	const handlers = {
		onboard: () => onboard(run), configure: () => configure(run), export: () => exportConfig(run, file),
		import: () => importConfig(run, file), status: () => status(run),
	} satisfies Record<Subcommand, () => Promise<void>>;
	try { await handlers[word](); }
	catch (error) { say(run.ctx, `Agent rails: /agentrails ${word} failed: ${error instanceof Error ? error.message : String(error)}`, "error"); }
}

const hasRead = (reads: Set<string>, skill: string) => [...reads].some(url => url === `skill://${skill}` || url.startsWith(`skill://${skill}/`));
const WRITE_REFUSED: RailCode = "agentrails-config-write";
const refused = (text: string) => `Agent rails (${WRITE_REFUSED}): ${text}`;

/** config with one setup section replaced; writing commands keeps gates.audit equal to whether commands.audit exists. */
export function withSection(config: AgentRailsConfig, section: SetupSection, value: EngineSection): AgentRailsConfig {
	switch (section) {
		case "commands": {
			const commands = engineChecked<Commands>(value);
			return { ...config, commands, gates: { ...config.gates, audit: commands.audit !== undefined } };
		}
		case "houseStyle": return { ...config, houseStyle: engineChecked<{ version: number }>(value) };
		default: return { ...config, [section]: value };
	}
}

async function liveWrite(run: Run, state: Extract<ConfigState, { kind: "ok" }>, params: ToolParams, value: EngineSection): Promise<string> {
	if (!run.ctx.hasUI) return refused(`${state.config.repo.slug} is onboarded; rewriting ${params.section} needs the owner's confirmation in an interactive omp session.`);
	const preview = JSON.stringify(value, null, 2);
	const allowed = await run.ctx.ui.confirm(`Agent rails: let the agent rewrite ${params.section} in ${state.config.repo.slug}'s config?`,
		preview.length > 2000 ? `${preview.slice(0, 2000)}…` : preview);
	if (!allowed) return refused(`the owner declined the ${params.section} rewrite; the config is unchanged.`);
	const written = await writeConfig(run.pi, run.deps.check, state.main, withSection(state.config, params.section, value));
	return written.ok ? `Agent rails: ${params.section} written to ${state.path}.` : refused(`the config with this ${params.section} is refused:\n${written.problems.join("\n")}`);
}

async function draftWrite(run: Run, state: Located, params: ToolParams, value: EngineSection): Promise<string> {
	if (!run.deps.session(run.ctx).onboarding.has(state.main)) {
		return refused("no onboarding is in progress for this repository in this session; ask the owner to run /agentrails onboard.");
	}
	const skill = SECTION_SKILL[params.section];
	const draft = await readDraft(run.pi, state.main) ?? emptyDraft();
	draft.sections[params.section] = value;
	draft.skills[skill] = { version: await skillVersion(join(run.deps.skillsDir, skill)), ranAt: new Date().toISOString(), scope: params.scope };
	await writeDraft(run.pi, state.main, draft);
	const gaps = draftGaps(draft);
	return gaps.length
		? `Agent rails: ${params.section} recorded in the onboarding draft. Still missing: ${gaps.join(", ")}.`
		: "Agent rails: every setup section is recorded. Ask the owner to run /agentrails onboard again to answer the Q&A.";
}

async function recordSection(run: Run, params: ToolParams): Promise<string> {
	if (run.ctx.agent.kind !== "main") return refused("only the main agent records config sections.");
	const skill = SECTION_SKILL[params.section];
	if (!hasRead(run.deps.session(run.ctx).reads, skill)) return refused(`read skill://${skill} in this session before recording ${params.section}.`);
	const state = await resolveConfig(run.pi, run.deps.engineDir, run.ctx.cwd);
	if (!("main" in state)) return refused(`this directory has no configurable repository (${state.kind}): ${state.reason}`);
	const value: EngineSection = JSON.parse(JSON.stringify(params.value));
	const verdict = await checkSection(run.pi, run.deps.check, state.main, params.section, value);
	if (!verdict.ok) return refused(`the ${params.section} value is refused:\n${verdict.problems.join("\n")}`);
	return state.kind === "ok" ? liveWrite(run, state, params, value) : draftWrite(run, state, params, value);
}

/** Registers /agentrails, the agentrails_config tool and the owner's $approve/$revoke config lines. */
export function registerOwnerSurface(pi: AgentRailsApi, deps: AgentRailsDeps): void {
	registerAgentRails(pi, deps);
	registerOwnerApprovals(pi, deps);
}

/** Registers /agentrails and the agentrails_config tool. */
export function registerAgentRails(pi: AgentRailsApi, deps: AgentRailsDeps): void {
	pi.registerCommand("agentrails", {
		description: "Agent rails: onboard, configure, export, import or status for this repository's .agentrails/config.json",
		getArgumentCompletions: prefix => Object.entries(SUBCOMMANDS)
			.filter(([name]) => name.startsWith(prefix.trim()))
			.map(([name, description]) => ({ value: name, label: name, description })),
		handler: (args, ctx) => subcommand({ pi, deps, ctx }, args),
	});
	pi.registerTool({
		name: "agentrails_config",
		label: "Agent rails config",
		description: "Record one setup section of this repository's agent-rails config while the owner onboards it: commands, " +
			"checkers, structure and houseStyle after reading and running skill://setup-repo-proofs; tests after skill://test-guidance. " +
			"Pass the whole section value and the scope the skill run covered. On an onboarded repository the owner must confirm the rewrite.",
		parameters: toolParams,
		approval: "write",
		async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
			const text = await recordSection({ pi, deps, ctx }, toolParams.parse(params));
			return { ...reply(text), details: text.startsWith(refused("")) ? { code: WRITE_REFUSED } : {} };
		},
	});
}
