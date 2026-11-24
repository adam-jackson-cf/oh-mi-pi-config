// The fixed onboarding Q&A, as data: each question names the config section it answers, its
// kind, its options and the answer the current config prefills. /agentrails onboard and
// /agentrails configure run it; the owner answers through omp's select, confirm and input dialogs.
// The Q&A never offers in-scope/ask_first or dependency-severity/impact (the engine lists neither),
// and gates.audit is not asked: it is true exactly when commands.audit exists.
import { z, type ExtensionUIContext } from "@oh-my-pi/pi-coding-agent";
import {
	QUESTIONNAIRE_VERSION, type AfterMerge, type Autonomy, type Gates, type JudgmentOverride, type MergePolicy, type Permission,
} from "./repo-config.ts";

export { QUESTIONNAIRE_VERSION };

export type QaUi = Pick<ExtensionUIContext, "select" | "confirm" | "input">;
type CiAnswer = { enabled: false } | { enabled: true; runner: "github-actions" | "ci-gate"; requiredContext: string };
/** The sections the Q&A writes. */
export type QaSections = {
	autonomy: Autonomy; ci: CiAnswer; gates: Omit<Gates, "audit">; judgments: Record<string, JudgmentOverride>;
	mergePolicies: MergePolicy[]; afterMerge?: AfterMerge;
};
export type RuleOption = { id: string; summary: string };
export type OfferedQuestion = { key: string; mode: "enforced" | "shadow"; shadowable: boolean };
/** What the Q&A reads besides the owner: the engine's lists, the CI contexts seen on the default branch, the current config. */
export type QaEnv = { rules: RuleOption[]; questions: OfferedQuestion[]; contexts: () => Promise<string[]>; current: QaSections | undefined };

// The answers so far. Loops and inputs write their sections here; finished() checks every one was given.
type Answers = {
	autonomy: Partial<Autonomy>; ciEnabled?: boolean; runner?: "github-actions" | "ci-gate"; requiredContext?: string;
	gates: Partial<Omit<Gates, "audit" | "rules">>; rules?: Record<string, boolean>; judgments?: Record<string, JudgmentOverride>;
	mergePolicies?: MergePolicy[]; afterMerge?: boolean; afterArgv?: string[] | undefined; afterPaths?: string[] | undefined;
};
type Base = { id: string; section: string; title: string; when?: (answers: Answers) => boolean };
type SelectQuestion = Base & { kind: "select"; options: readonly string[]; prefill: (current: QaSections) => string; record: (answers: Answers, answer: string) => void };
type ConfirmQuestion = Base & { kind: "confirm"; options: readonly ["yes", "no"]; prefill: (current: QaSections) => boolean; record: (answers: Answers, answer: boolean) => void };
type InputQuestion = Base & {
	kind: "input" | "argv-json"; options: readonly []; required: boolean;
	prefill: (current: QaSections | undefined, env: QaEnv) => Promise<string | undefined>;
	record: (answers: Answers, answer: string) => string | undefined;
};
type LoopQuestion = Base & { kind: "loop"; options: readonly []; prefill: (current: QaSections) => string; run: (ui: QaUi, env: QaEnv, answers: Answers) => Promise<boolean> };
export type Question = SelectQuestion | ConfirmQuestion | InputQuestion | LoopQuestion;

const PERMISSIONS = ["allowed", "ask"] as const satisfies readonly Permission[];
const RUNNERS = ["github-actions", "ci-gate"] as const;
const METHODS = ["rebase", "merge"] as const;
const DONE = "done";
const argvList = z.array(z.string().trim().min(1)).min(1);

const permission = (answer: string): Permission => PERMISSIONS.find(option => option === answer) ?? "ask";
function autonomy(action: keyof Autonomy, title: string): SelectQuestion {
	return {
		id: `autonomy.${action}`, section: `autonomy.${action}`, kind: "select", options: PERMISSIONS, title,
		prefill: current => current.autonomy[action], record: (answers, answer) => { answers.autonomy[action] = permission(answer); },
	};
}
function gate(key: keyof Omit<Gates, "audit" | "rules">, title: string): ConfirmQuestion {
	return {
		id: `gates.${key}`, section: `gates.${key}`, kind: "confirm", options: ["yes", "no"], title,
		prefill: current => current.gates[key], record: (answers, answer) => { answers.gates[key] = answer; },
	};
}
function parsedList(text: string): string[] | undefined {
	try {
		const parsed = argvList.safeParse(JSON.parse(text));
		return parsed.success ? parsed.data : undefined;
	} catch { return undefined; }
}
const ciOn = (answers: Answers) => answers.ciEnabled === true;
const afterOn = (answers: Answers) => answers.afterMerge === true;
const listText = (list: string[] | undefined) => list === undefined ? undefined : JSON.stringify(list);
const currentCi = (current: QaSections | undefined) => current?.ci.enabled === true ? current.ci : undefined;

/** The questions in the order the owner answers them. A test pins this list. */
export const QUESTIONS: readonly Question[] = [
	autonomy("branch", "May agents push non-default branches?"),
	autonomy("pr", "May agents open pull requests (gh pr create)?"),
	autonomy("merge", "May agents merge pull requests (gh pr merge) once every gate passes?"),
	{
		id: "ci.enabled", section: "ci.enabled", kind: "confirm", options: ["yes", "no"], title: "Do merges need a green CI context?",
		prefill: current => current.ci.enabled, record: (answers, answer) => { answers.ciEnabled = answer; },
	},
	{
		id: "ci.runner", section: "ci.runner", kind: "select", options: RUNNERS, title: "Which runner role posts the CI context?", when: ciOn,
		prefill: current => currentCi(current)?.runner ?? "github-actions",
		record: (answers, answer) => { answers.runner = RUNNERS.find(option => option === answer) ?? "github-actions"; },
	},
	{
		id: "ci.requiredContext", section: "ci.requiredContext", kind: "input", options: [], required: true, when: ciOn,
		title: "Which CI context must be green on the PR head?",
		prefill: async (current, env) => currentCi(current)?.requiredContext ?? (await env.contexts())[0],
		record: (answers, answer) => { answers.requiredContext = answer; return undefined; },
	},
	gate("commitChecks", "Run commands.commit and the harness checks before every commit?"),
	gate("pathCheck", "Run the path check after edits, on staged paths and at handoff?"),
	gate("prove", "Do merges that need proof need a prove() pass?"),
	gate("structureReview", "Do merges that need proof need a structure_review()?"),
	{
		id: "gates.rules", section: "gates.rules", kind: "loop", options: [], title: "Switch code-check rules off or on; pick done to finish.",
		prefill: current => Object.keys(current.gates.rules).join(", "), run: rulesLoop,
	},
	{
		id: "judgments", section: "judgments", kind: "loop", options: [], title: "For each judgment question: follow the global mode, shadow or off.",
		prefill: current => Object.keys(current.judgments).join(", "), run: judgmentsLoop,
	},
	{
		id: "mergePolicies", section: "mergePolicies", kind: "loop", options: [], title: "Add a merge policy?",
		prefill: current => String(current.mergePolicies.length), run: policiesLoop,
	},
	{
		id: "afterMerge", section: "afterMerge", kind: "confirm", options: ["yes", "no"], title: "Require a repo command after every merge?",
		prefill: current => current.afterMerge !== undefined, record: (answers, answer) => { answers.afterMerge = answer; },
	},
	{
		id: "afterMerge.argv", section: "afterMerge.argv", kind: "argv-json", options: [], required: true, when: afterOn,
		title: 'The after-merge command as a JSON argv array, e.g. ["make", "sync"]',
		prefill: async current => listText(current?.afterMerge?.argv),
		record: (answers, answer) => {
			answers.afterArgv = parsedList(answer);
			return answers.afterArgv === undefined ? "must be a JSON array of non-blank strings" : undefined;
		},
	},
	{
		id: "afterMerge.paths", section: "afterMerge.paths", kind: "input", options: [], required: false, when: afterOn,
		title: 'Only merges touching these globs need it: a JSON array, e.g. ["src/**"]; none for every merge',
		prefill: async current => listText(current?.afterMerge?.paths),
		record: (answers, answer) => {
			answers.afterPaths = answer === "" || answer === "none" ? undefined : parsedList(answer);
			return answer !== "" && answer !== "none" && answers.afterPaths === undefined ? "must be none or a JSON array of non-blank globs" : undefined;
		},
	},
];

async function rulesLoop(ui: QaUi, env: QaEnv, answers: Answers): Promise<boolean> {
	const off = new Set(Object.entries(env.current?.gates.rules ?? {}).flatMap(([id, on]) => on ? [] : [id]));
	for (;;) {
		const options = [{ label: DONE, description: `keep ${off.size} rule(s) off` }, ...env.rules.map(rule => ({
			label: `${rule.id}: ${off.has(rule.id) ? "off" : "on"}`, description: rule.summary,
		}))];
		const picked = await ui.select("gates.rules: pick a code-check rule to switch it, or done", options);
		if (picked === undefined) return false;
		if (picked === DONE) break;
		const id = picked.replace(/: (?:on|off)$/, "");
		if (off.has(id)) off.delete(id); else off.add(id);
	}
	answers.rules = Object.fromEntries([...off].toSorted().map(id => [id, false]));
	return true;
}

function judgmentChoice(question: OfferedQuestion, current: JudgmentOverride | undefined): string {
	if (current === undefined) return `follow global (${question.mode})`;
	if (!current.enabled) return "off";
	return current.mode === "shadow" ? "shadow" : `follow global (${question.mode})`;
}

async function judgmentsLoop(ui: QaUi, env: QaEnv, answers: Answers): Promise<boolean> {
	const chosen: Record<string, JudgmentOverride> = {};
	for (const question of env.questions) {
		const follow = `follow global (${question.mode})`;
		const options = [follow, ...(question.mode === "enforced" && question.shadowable ? ["shadow"] : []), "off"];
		const initialIndex = Math.max(0, options.indexOf(judgmentChoice(question, env.current?.judgments[question.key])));
		const picked = await ui.select(`judgments: ${question.key}`, options, { initialIndex });
		if (picked === undefined) return false;
		if (picked === "shadow") chosen[question.key] = { enabled: true, mode: "shadow" };
		if (picked === "off") chosen[question.key] = { enabled: false };
	}
	answers.judgments = chosen;
	return true;
}

const policyText = (policy: MergePolicy) =>
	`base ${policy.base}, head prefix "${policy.headPrefix}", ${policy.method}, proof ${policy.proof ? "yes" : "no"}`;

async function newPolicy(ui: QaUi): Promise<MergePolicy | undefined> {
	let base = "";
	while (!base) {
		const answer = await ui.input("mergePolicies: base branch");
		if (answer === undefined) return undefined;
		base = answer.trim();
	}
	const headPrefix = await ui.input("mergePolicies: head branch prefix (blank matches every head)");
	if (headPrefix === undefined) return undefined;
	const method = await ui.select("mergePolicies: merge method", [...METHODS]);
	const chosen = METHODS.find(option => option === method);
	if (chosen === undefined) return undefined;
	const proof = await ui.confirm("mergePolicies: does this merge need proof?", "prove() and structure_review(), as gates allow");
	return { base, headPrefix: headPrefix.trim(), method: chosen, proof };
}

async function policiesLoop(ui: QaUi, env: QaEnv, answers: Answers): Promise<boolean> {
	const policies: MergePolicy[] = [];
	for (const policy of env.current?.mergePolicies ?? []) {
		if (await ui.confirm("mergePolicies: keep this merge policy?", policyText(policy))) policies.push(policy);
	}
	while (await ui.confirm("mergePolicies: add a merge policy?", `${policies.length} so far; a PR no policy matches rebases with proof`)) {
		const policy = await newPolicy(ui);
		if (policy === undefined) return false;
		policies.push(policy);
	}
	answers.mergePolicies = policies;
	return true;
}

async function askInput(ui: QaUi, env: QaEnv, answers: Answers, question: InputQuestion): Promise<boolean> {
	const prefill = await question.prefill(env.current, env);
	let problem: string | undefined;
	for (;;) {
		const title = [question.title, problem ? `(${question.section} ${problem})` : "", prefill ? `blank keeps ${prefill}` : ""].filter(Boolean).join(" ");
		const typed = await ui.input(title, prefill);
		if (typed === undefined) return false;
		const answer = typed.trim() || prefill || "";
		if (!answer && question.required) { problem = "is required"; continue; }
		problem = question.record(answers, answer);
		if (problem === undefined) return true;
	}
}

async function ask(ui: QaUi, env: QaEnv, answers: Answers, question: Question): Promise<boolean> {
	const current = env.current;
	switch (question.kind) {
		case "select": {
			const initialIndex = current === undefined ? 0 : Math.max(0, question.options.indexOf(question.prefill(current)));
			const picked = await ui.select(question.title, [...question.options], { initialIndex });
			if (picked !== undefined) question.record(answers, picked);
			return picked !== undefined;
		}
		case "confirm":
			question.record(answers, await ui.confirm(question.title, current === undefined ? question.section : `${question.section} is now ${question.prefill(current) ? "yes" : "no"}`));
			return true;
		case "loop": return question.run(ui, env, answers);
		default: return askInput(ui, env, answers, question);
	}
}

function given<T>(value: T | undefined, section: string): T {
	if (value === undefined) throw new Error(`the Q&A finished without an answer for ${section}`);
	return value;
}

function finishedCi(answers: Answers): CiAnswer {
	return given(answers.ciEnabled, "ci.enabled")
		? { enabled: true, runner: given(answers.runner, "ci.runner"), requiredContext: given(answers.requiredContext, "ci.requiredContext") }
		: { enabled: false };
}

function finished(answers: Answers): QaSections {
	const { autonomy: chosen, gates } = answers;
	const sections: QaSections = {
		autonomy: { branch: given(chosen.branch, "autonomy.branch"), pr: given(chosen.pr, "autonomy.pr"), merge: given(chosen.merge, "autonomy.merge") },
		ci: finishedCi(answers),
		gates: {
			commitChecks: given(gates.commitChecks, "gates.commitChecks"), pathCheck: given(gates.pathCheck, "gates.pathCheck"),
			prove: given(gates.prove, "gates.prove"), structureReview: given(gates.structureReview, "gates.structureReview"),
			rules: given(answers.rules, "gates.rules"),
		},
		judgments: given(answers.judgments, "judgments"),
		mergePolicies: given(answers.mergePolicies, "mergePolicies"),
	};
	if (answers.afterMerge === true) {
		const argv = given(answers.afterArgv, "afterMerge.argv");
		sections.afterMerge = answers.afterPaths === undefined ? { argv } : { argv, paths: answers.afterPaths };
	}
	return sections;
}

/** Runs the Q&A; undefined when the owner cancels a dialog, so nothing is written. */
export async function runQuestionnaire(ui: QaUi, env: QaEnv): Promise<QaSections | undefined> {
	const answers: Answers = { autonomy: {}, gates: {} };
	for (const question of QUESTIONS) {
		if (question.when && !question.when(answers)) continue;
		if (!await ask(ui, env, answers, question)) return undefined;
	}
	return finished(answers);
}
