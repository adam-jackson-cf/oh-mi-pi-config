// Judged rails run in governed repositories (a config the engine accepted), except in-scope, which runs
// in every Git repository. Each runs its deterministic pre-checks first and calls decide()
// only on what they pass. extension-impact gates the restart review,
// implementation-integrity the commit, in-scope asks and privileged commands, completion-evidence
// the merge and the turn end. An enforced verdict acts and a judge failure refuses; a shadow verdict is
// logged and shown as "shadow, not blocking" but never acts, so the rail behaves as if it had not run.
// A question the repo config turns off is never judged or logged and never acts.
// An escalated enforced outcome goes to the owner, who approves with a $approve prompt line.
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { z, type ExtensionAPI, type ExtensionContext } from "@oh-my-pi/pi-coding-agent";
import { SUBPROCESS_TIMEOUT_MS, shellCommands, type GitAction, type ToolEvent } from "./rails-runtime.ts";
import { approvalLine, clip, digest, enforced, jsonText, verdictText, type Decide, type DecisionSubject, type JudgeState, type RailJudgment, type Approvals, type Verdict } from "./judge.ts";
import type { Obligation, ObligationStatus } from "./obligations.ts";
import type { AgentRailsConfig } from "./repo-config.ts";
import { parseOrigin } from "./git-paths.ts";

type ExecApi = Pick<ExtensionAPI, "exec">;
type Run = { code: number; killed: boolean; stdout: string; stderr: string };
type Failure = { failure: string };

const HEADING = /^(#{1,6})\s+(.+?)\s*#*\s*$/;
const REQUEST_HEADINGS = ["in short", "summary", "why", "changes"];
const EVIDENCE_HEADINGS = ["evidence", "verification", "testing", "test plan"];
const NOT_TESTED = /^\s*(?:[-*]\s*)?\*\*Not tested:?\*\*:?\s*(.*)$/i;
const PENDING_CHECK = /^(?:QUEUED|IN_PROGRESS|PENDING|EXPECTED|WAITING|REQUESTED)$/;
const FAILED_CHECK = new Set(["FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "STARTUP_FAILURE", "ACTION_REQUIRED"]);
// git commit message arguments: an inline value, or a value or file path in the next argument.
const MESSAGE_FORMS = [
	{ pattern: /^--message=([\s\S]*)$/, kind: "text", skip: 0 },
	{ pattern: /^-m([\s\S]+)$/, kind: "text", skip: 0 },
	{ pattern: /^(?:--message|-[a-zA-Z]*m)$/, kind: "text", skip: 1 },
	{ pattern: /^(?:-F|--file)$/, kind: "file", skip: 1 },
	{ pattern: /^--file=(.*)$/, kind: "file", skip: 0 },
] as const;
const TEXT_LIMIT = 3000;
const SUBAGENT_PRIVILEGED = "Agent rails: subagents cannot run privileged commands (privileges, credentials, installs, other machines); " +
	"return the command to the main agent, which asks the owner when the agreed work does not grant it.";
const DIFF_LIMIT = 12000;
const LARGEST_FILES = 20;
const IMPACT_FILES = 80;
const PATCH_LIMIT = 2500;
const PATCHES_LIMIT = 12000;
const SCOPE_MESSAGES = 4;
const SCOPE_MESSAGE_LIMIT = 800;
const SCOPE_CONTEXT_LIMIT = 700;
const SCOPE_ACTION_LIMIT = 600;
// The decision store's item text for an ask or a command: enough to find it, never the whole input.
const SUBJECT_LIMIT = 200;

const checkSchema = z.union([
	z.object({ __typename: z.literal("CheckRun"), name: z.string(), status: z.string(), conclusion: z.union([z.string(), z.literal(null)]) })
		.transform(check => ({ check: check.name, result: check.status === "COMPLETED" ? check.conclusion ?? "" : check.status })),
	z.object({ __typename: z.literal("StatusContext"), context: z.string(), state: z.string() })
		.transform(check => ({ check: check.context, result: check.state })),
]);
type Check = z.infer<typeof checkSchema>;
const fileSchema = z.object({ path: z.string(), additions: z.number(), deletions: z.number() });
type PrFile = z.infer<typeof fileSchema>;
const prViewSchema = z.object({
	number: z.number(), title: z.string(), body: z.string(), state: z.string(),
	headRefOid: z.string().regex(/^[0-9a-f]{40}$/),
	url: z.string().regex(/^https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+\/pull\/\d+$/),
	files: z.array(fileSchema),
	statusCheckRollup: z.array(checkSchema).optional(),
});
type PrView = z.infer<typeof prViewSchema>;
const branchMessageSchema = z.object({ type: z.literal("message"), message: z.object({
	role: z.string(), content: z.union([z.string(), z.array(z.object({ type: z.string(), text: z.string().optional() }))]).optional(),
}) });
type BranchMessage = z.infer<typeof branchMessageSchema>["message"];
/** The raw tool input fields in-scope reads: the ask tool's questions or html, and the call's intent. */
export const scopeInputSchema = z.object({
	questions: z.array(z.object({ question: z.string() })).optional(), html: z.string().optional(), intent: z.string().optional(),
}).catch({});
export type ScopeInput = z.infer<typeof scopeInputSchema>;

async function run(pi: ExecApi, command: string, args: string[], cwd: string): Promise<Run> {
	return pi.exec(command, args, { cwd, timeout: SUBPROCESS_TIMEOUT_MS });
}
async function prView(pi: ExecApi, cwd: string, selector: string | undefined, fields: string): Promise<PrView> {
	const result = await run(pi, "gh", ["pr", "view", ...(selector ? [selector] : []), "--json", fields], cwd);
	if (result.code !== 0 || result.killed) throw new Error(`cannot read the PR via gh: ${result.stderr.trim()}`);
	return prViewSchema.parse(jsonText.parse(result.stdout));
}
const repositoryOf = (view: PrView) => new URL(view.url).pathname.split("/").slice(1, 3).join("/");
const prSubject = (view: PrView): DecisionSubject => ({
	trigger: "pr", repo: repositoryOf(view), commit: view.headRefOid, pr: view.number, item_kind: "pr", item: `${repositoryOf(view)}#${view.number}`,
});
const sessionOf = (ctx: ExtensionContext) => ctx.sessionManager.getSessionId();
const ownerReason = (verdict: Verdict, key: string, question: string) =>
	`${verdictText(verdict)} is uncertain; ask the owner ${question} If they approve, they send ${approvalLine(key)} as a line of their own.`;

async function attempt(decide: Decide, name: RailJudgment, state: JudgeState, ctx: ExtensionContext, subject: DecisionSubject): Promise<Verdict | Failure> {
	try { return await decide(name, state, ctx, subject); }
	catch (error) { return { failure: `the ${name} judgment could not run: ${error instanceof Error ? error.message : String(error)}` }; }
}

function headingAt(line: string): { level: number; title: string } | undefined {
	const match = HEADING.exec(line);
	if (!match?.[1] || !match[2]) return undefined;
	return { level: match[1].length, title: match[2].replace(/[*_:`]/g, "").trim().toLowerCase() };
}
function sectionEnd(lines: string[], start: number, level: number): number {
	const next = lines.findIndex((line, index) => index >= start && (headingAt(line)?.level ?? 7) <= level);
	return next < 0 ? lines.length : next;
}
/** Markdown sections whose heading names one of `names`: each section's text up to the next heading at its level or above. */
export function sections(body: string, names: string[]): string[] {
	const lines = body.split(/\r?\n/);
	return lines.flatMap((line, index) => {
		const heading = headingAt(line);
		if (!heading || !names.includes(heading.title)) return [];
		const text = lines.slice(index + 1, sectionEnd(lines, index + 1, heading.level)).join("\n").trim();
		return text ? [text] : [];
	});
}

/** Evidence split as the completion-evidence cases were: Not tested paragraphs and sections leave `tested`. */
export function evidenceText(body: string) {
	const notTested = sections(body, ["not tested"]);
	const tested = sections(body, EVIDENCE_HEADINGS).map(section => section.split(/\n\s*\n/).filter(paragraph => {
		const [first = "", ...rest] = paragraph.split("\n");
		const label = NOT_TESTED.exec(first);
		if (label) notTested.push([label[1] ?? "", ...rest].join("\n").trim());
		return !label;
	}).join("\n\n").trim()).filter(Boolean);
	return { tested: tested.join("\n\n"), notTested: notTested.filter(Boolean).join("\n\n") };
}

const bySize = (left: PrFile, right: PrFile) => right.additions + right.deletions - (left.additions + left.deletions) || left.path.localeCompare(right.path);
const sum = (files: PrFile[], field: "additions" | "deletions") => files.reduce((total, file) => total + file[field], 0);
function messageArg(args: string[], index: number) {
	const arg = args[index] ?? "";
	const form = MESSAGE_FORMS.find(entry => entry.pattern.test(arg));
	if (!form) return undefined;
	return { kind: form.kind, skip: form.skip, value: (form.skip ? args[index + 1] : form.pattern.exec(arg)?.[1]) ?? "" };
}
/** The commit message a `git commit` sets: -m/--message parts, a -F/--file file, or the amended message. */
export async function commitMessage(pi: ExecApi, cwd: string, args: string[]): Promise<string> {
	const parts: string[] = [];
	for (let index = 1; index < args.length; index++) {
		const part = messageArg(args, index);
		if (!part) continue;
		index += part.skip;
		if (part.kind === "text") parts.push(part.value);
		else if (part.value !== "-") parts.push(await readFile(resolve(cwd, part.value), "utf8").catch(() => ""));
	}
	if (!parts.length && args.includes("--amend")) return (await run(pi, "git", ["log", "-1", "--format=%B"], cwd)).stdout.trim();
	return parts.join("\n\n").trim();
}
const lineCount = (value: string) => value === "-" ? null : Number(value);
/** The implementation-integrity `commit` state from the index; undefined when nothing is staged. */
export async function commitState(pi: ExecApi, cwd: string, args: string[]) {
	const numstat = await run(pi, "git", ["diff", "--cached", "--numstat"], cwd);
	if (numstat.code !== 0 || numstat.killed) throw new Error(`git diff --cached failed: ${numstat.stderr.trim()}`);
	const files = numstat.stdout.split("\n").filter(Boolean).map(line => {
		const [added = "-", deleted = "-", ...path] = line.split("\t");
		return { path: path.join("\t"), added: lineCount(added), deleted: lineCount(deleted) };
	});
	if (!files.length) return undefined;
	const diff = (await run(pi, "git", ["diff", "--cached", "-U3"], cwd)).stdout;
	return { commit: { message: await commitMessage(pi, cwd, args), files, diff: diff.slice(0, DIFF_LIMIT), diff_truncated: diff.length > DIFF_LIMIT } };
}

function messageText(message: BranchMessage): string {
	const { content } = message;
	if (content === undefined) return "";
	return Array.isArray(content) ? content.flatMap(block => block.type === "text" && block.text !== undefined ? [block.text] : []).join("\n") : content;
}
/** The in-scope `work` state: owner messages from the session branch, the agent's last text, the item. */
export function scopeState(branch: readonly object[], proposedAction: string) {
	const messages = branch.flatMap(entry => { const parsed = branchMessageSchema.safeParse(entry); return parsed.success ? [parsed.data.message] : []; });
	const texts = (role: string) => messages.filter(message => message.role === role).map(messageText).filter(text => text.trim());
	const [first, ...later] = texts("user");
	const recent = later.slice(-SCOPE_MESSAGES);
	const omitted = later.length - recent.length;
	const agreed = [
		...(first === undefined ? [] : [`[initial request] ${clip(first.trim(), SCOPE_MESSAGE_LIMIT)}`]),
		...(omitted ? [`[${omitted} further owner messages omitted]`] : []),
		...recent.map(text => `[later owner message] ${clip(text.trim(), SCOPE_MESSAGE_LIMIT)}`),
	].join("\n\n");
	const assistant = texts("assistant").at(-1) ?? "";
	return { work: {
		agreed_scope: agreed,
		agent_context: assistant.slice(-SCOPE_CONTEXT_LIMIT),
		proposed_action: clip(proposedAction, SCOPE_ACTION_LIMIT),
	} };
}
/** The command when any parsed command position in it (wrappers, subshells, -c scripts, substitutions) is ask-first. */
async function privilegedCommand(command: string | undefined, cwd: string, askFirst: (part: string) => boolean): Promise<string | undefined> {
	if (command === undefined) return undefined;
	const positions = await shellCommands(command, cwd).catch(() => [command]);
	return positions.some(position => askFirst(position.trim())) ? command : undefined;
}
const questionText = (input: ScopeInput): string | undefined => input.questions?.length
	? input.questions.map(item => item.question).join("\n")
	: input.html?.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();

const patchRank = (path: string) => /(^|\/)tests?\/|\.test\.|_test\.|test_/.test(path) ? 2 : /\.(md|html)$|(^|\/)docs\//.test(path) ? 1 : 0;
function patchesOf(diff: string): { path: string; patch: string }[] {
	return diff.split(/^diff --git .*$/m).slice(1).map(chunk => {
		const start = chunk.indexOf("\n--- ");
		const patch = start >= 0 ? chunk.slice(start + 1).trimEnd() : chunk.trim();
		return { path: /^\+\+\+ b\/(.+)$/m.exec(patch)?.[1] ?? /^--- a\/(.+)$/m.exec(patch)?.[1] ?? "", patch };
	});
}
function excerptsOf(patches: { path: string; patch: string }[]) {
	const excerpts: { patch: string; path: string }[] = [];
	const clipped: string[] = [];
	let budget = PATCHES_LIMIT;
	for (const item of patches.toSorted((left, right) => patchRank(left.path) - patchRank(right.path) || left.path.localeCompare(right.path))) {
		const patch = item.patch.slice(0, Math.min(PATCH_LIMIT, budget));
		if (patch.length < item.patch.length) clipped.push(item.path);
		if (!patch) continue;
		excerpts.push({ patch, path: item.path });
		budget -= patch.length;
	}
	return { excerpts, clipped };
}
/** The extension-impact `change` state for one config-root revision range. */
export async function impactChange(pi: ExecApi, top: string, range: { from: string; to: string }) {
	const git = async (args: string[]) => {
		const result = await run(pi, "git", args, top);
		if (result.code !== 0 || result.killed) throw new Error(`git ${args[0]} failed: ${result.stderr.trim()}`);
		return result.stdout;
	};
	const scope = [range.from, range.to, "--", "."];
	const rows = async (args: string[]) => (await git(["diff", "--no-renames", ...args, ...scope])).split("\n").filter(Boolean).map(line => line.split("\t"));
	const statuses = new Map((await rows(["--name-status"])).map(([status = "M", path = ""]) => [path, status]));
	const files = (await rows(["--numstat"])).map(([added = "-", deleted = "-", path = ""]) =>
		({ added: lineCount(added), deleted: lineCount(deleted), path, status: statuses.get(path) ?? "M" }));
	const { excerpts, clipped } = excerptsOf(patchesOf(await git(["diff", "--no-renames", "-U3", ...scope])));
	return {
		base: range.from, head: range.to,
		commit_subjects: (await git(["log", "--format=%s", `${range.from}..${range.to}`])).split("\n").filter(Boolean),
		files: files.slice(0, IMPACT_FILES), omitted_files: Math.max(0, files.length - IMPACT_FILES),
		patch_excerpts: excerpts, patch_clipped_or_omitted: clipped,
	};
}

function pendingReason(checks: Check[], requiredContext: string | undefined): string | undefined {
	if (!checks.length) return "P5 no checks reported for the head yet";
	if (checks.some(check => PENDING_CHECK.test(check.result))) return "P5 checks are still running on the head";
	if (requiredContext && !checks.some(check => check.check === requiredContext)) return `P6 required context ${requiredContext} has not reported yet`;
	return undefined;
}
function sectionFailures(view: PrView, requiredContext: string | undefined): string[] {
	const checks = view.statusCheckRollup ?? [];
	const failed = checks.filter(check => FAILED_CHECK.has(check.result)).map(check => `${check.check}: ${check.result}`);
	return [
		...(sections(view.body, REQUEST_HEADINGS).length ? [] : ["P2 no In short, Summary, Why or Changes section"]),
		...(evidenceText(view.body).tested ? [] : ["P4 no Evidence, Verification, Testing or Test plan section"]),
		...(failed.length ? [`P7 failed checks: ${failed.join(", ")}`] : []),
		...(requiredContext && !checks.some(check => check.check === requiredContext && check.result === "SUCCESS")
			? [`P6 required context ${requiredContext} is not green`] : []),
	];
}
async function shaFailures(pi: ExecApi, cwd: string, view: PrView): Promise<string[]> {
	const { tested, notTested } = evidenceText(view.body);
	const failures: string[] = [];
	for (const sha of new Set(`${tested}\n${notTested}`.match(/\b[0-9a-f]{40}\b/g) ?? [])) {
		if (sha === view.headRefOid) continue;
		const compare = await run(pi, "gh", ["api", `repos/${repositoryOf(view)}/compare/${sha}...${view.headRefOid}`, "--jq", ".status"], cwd);
		if (compare.code !== 0 || !["ahead", "identical"].includes(compare.stdout.trim())) failures.push(`P8 evidence quotes ${sha}, which is not the head or its ancestor`);
	}
	return failures;
}
/** The completion-evidence state for a PR that passed P2 and P4-P8. */
export function evidenceState(view: PrView) {
	const { tested, notTested } = evidenceText(view.body);
	return {
		work_request: { repository: repositoryOf(view), title: view.title, request: clip(sections(view.body, REQUEST_HEADINGS).join("\n\n"), TEXT_LIMIT) },
		evidence: {
			tested: clip(tested, TEXT_LIMIT), not_tested: clip(notTested, TEXT_LIMIT), ci_on_head: view.statusCheckRollup ?? [],
			diff_summary: {
				files_changed: view.files.length, additions: sum(view.files, "additions"), deletions: sum(view.files, "deletions"),
				largest_files: view.files.toSorted(bySize).slice(0, LARGEST_FILES).map(file => `${file.path} +${file.additions} -${file.deletions}`),
			},
		},
	};
}

export type JudgedDeps = {
	decide: Decide;
	/** The owner's approvals for this extension instance: escalated judgments and autonomy asks share them. */
	approvals: Approvals;
	/** Whether cwd is inside a Git repository: in-scope runs in every one, whatever its config state. */
	inGitRepo(cwd: string): Promise<boolean>;
	/** Whether one command part needs in-scope before it runs (rails.yml askFirstCommand). */
	askFirst(part: string): boolean;
};
export type ImpactInput = { slug: string; config: AgentRailsConfig | undefined; configRoot: string; from: string; to: string };
// The config sections extension-impact judges a change against: what the rails run and enforce in the repository.
const IMPACT_SECTIONS = ["autonomy", "ci", "gates", "judgments", "mergePolicies", "afterMerge", "commands"] as const;
/** The extension-impact `config` state: the repo config's rail-facing sections. */
export function impactConfig(config: AgentRailsConfig) {
	return Object.fromEntries(IMPACT_SECTIONS.flatMap(section => config[section] === undefined ? [] : [[section, config[section]]]));
}
export type EvidenceTarget = { cwd: string; root: string; branch: string; requiredContext(): Promise<string | undefined> };

export function judgedRails(pi: ExecApi & Pick<ExtensionAPI, "on">, deps: JudgedDeps) {
	const { approvals } = deps;
	const verdicts = new Map<string, Verdict | Failure>();
	async function decided(name: RailJudgment, state: JudgeState, ctx: ExtensionContext, subject: DecisionSubject): Promise<Verdict | Failure> {
		const key = `${name}:${digest(state)}`;
		const cached = verdicts.get(key);
		if (cached) return cached;
		const verdict = await attempt(deps.decide, name, state, ctx, subject);
		if ("failure" in verdict) return verdict;
		verdicts.set(key, verdict);
		if (verdict.mode === "shadow" && verdict.outcome !== "no") ctx.ui.notify(`Agent rails: ${verdictText(verdict)}; logged for review.`, "info");
		return verdict;
	}
	/** A verdict the rail acts on: an enforced one that did not fail; shadow and off verdicts never act. */
	const acting = (verdict: Verdict | Failure): verdict is Verdict & { mode: "enforced" } => !("failure" in verdict) && enforced(verdict);
	const approved = (ctx: ExtensionContext, key: string) => approvals.has(sessionOf(ctx), key);

	pi.on("before_agent_start", async (event, ctx) => {
		if (ctx.agent.kind !== "main" || event.reason !== "prompt") return;
		const keys = approvals.fromPrompt(sessionOf(ctx), event.prompt ?? "");
		if (!keys.length) return;
		const content = `Agent rails: the owner approved ${keys.join(", ")}; retry the refused step.`;
		return { message: { customType: "agent-rails", content, display: false, attribution: "agent" as const } };
	});

	async function askReason(question: string, ctx: ExtensionContext): Promise<string | undefined> {
		const verdict = await decided("in-scope", scopeState(ctx.sessionManager.getBranch(), question), ctx, { trigger: "manual", item_kind: "ask", item: clip(question, SUBJECT_LIMIT) });
		if (!acting(verdict) || verdict.outcome !== "no") return undefined;
		return `Agent rails: ${verdictText(verdict)}: this question decides a detail of the agreed work. Decide it yourself and continue; ask only for what the agreed work does not grant.`;
	}

	async function commandReason(command: string, input: ScopeInput, ctx: ExtensionContext): Promise<string | undefined> {
		const action = `${input.intent ? `[${input.intent}] ` : ""}bash ${command}`;
		const key = `in-scope:${digest({ action }).slice(0, 16)}`;
		if (approved(ctx, key)) return undefined;
		const verdict = await decided("in-scope", scopeState(ctx.sessionManager.getBranch(), action), ctx, { trigger: "manual", item_kind: "command", item: clip(command, SUBJECT_LIMIT) });
		if ("failure" in verdict) return `Agent rails: ${verdict.failure}; ask the owner before running it.`;
		if (!enforced(verdict) || verdict.outcome === "no") return undefined;
		return `Agent rails: ${verdictText(verdict)}: this command may go beyond the agreed work (privileges, credentials, cost or other machines). ` +
			`Ask the owner first. If they approve, they send ${approvalLine(key)} as a line of their own.`;
	}

	async function evidenceStatus(ctx: ExtensionContext, at: EvidenceTarget, obligation: Obligation): Promise<ObligationStatus> {
		const view = await prView(pi, at.cwd, at.branch, "number,title,body,state,headRefOid,url,files,statusCheckRollup").catch(() => undefined);
		if (!view) return "waiting";
		if (view.state !== "OPEN") return "met";
		const requiredContext = await at.requiredContext();
		if (pendingReason(view.statusCheckRollup ?? [], requiredContext)) return "waiting";
		const failures = [...sectionFailures(view, requiredContext), ...await shaFailures(pi, at.cwd, view)];
		if (failures.length) { obligation.text = `the PR for ${at.branch} fails the completion pre-checks: ${failures.join("; ")}`; return "unmet"; }
		return evidenceVerdictStatus(ctx, at, obligation, view);
	}
	async function evidenceVerdictStatus(ctx: ExtensionContext, at: EvidenceTarget, obligation: Obligation, view: PrView): Promise<ObligationStatus> {
		const key = `completion-evidence:${view.headRefOid}`;
		if (approved(ctx, key)) return "met";
		const verdict = await decided("completion-evidence", evidenceState(view), ctx, prSubject(view));
		if ("failure" in verdict) { obligation.text = verdict.failure; return "unmet"; }
		if (!enforced(verdict) || verdict.outcome === "no") return "met";
		obligation.text = verdict.outcome === "yes"
			? `${verdictText(verdict)}: an outcome the PR for ${at.branch} claims has no evidence; add the test, run or observation that shows it to the PR's Evidence section`
			: ownerReason(verdict, key, `whether the PR for ${at.branch} shows evidence for every outcome it claims.`);
		return "unmet";
	}

	async function scopeSubject(event: ToolEvent, input: ScopeInput, ctx: ExtensionContext, cwd: string) {
		const question = event.toolName === "ask" && ctx.agent.kind === "main" ? questionText(input) : undefined;
		const command = event.toolName === "bash" ? await privilegedCommand(event.input.command, cwd, deps.askFirst) : undefined;
		return { question, command };
	}

	// completion-evidence holds the merge: the pre-checks, then the judgment for the head; escalate needs the owner.
	async function evidenceMergeReason(cwd: string, view: PrView, head: string, ctx: ExtensionContext): Promise<string | undefined> {
		const key = `completion-evidence:${head}`;
		if (approved(ctx, key)) return undefined;
		// CI on the head is the merge policy's check; the evidence pre-checks here are the description's (P2, P4, P8).
		const failures = [...sectionFailures(view, undefined).filter(failure => !/^P[67] /.test(failure)), ...await shaFailures(pi, cwd, view)];
		if (failures.length) return `Agent rails: the PR fails the completion pre-checks: ${failures.join("; ")}; fix the PR description, then merge.`;
		return evidenceVerdictReason(view, key, ctx);
	}
	async function evidenceVerdictReason(view: PrView, key: string, ctx: ExtensionContext): Promise<string | undefined> {
		const verdict = await decided("completion-evidence", evidenceState(view), ctx, prSubject(view));
		if ("failure" in verdict) return `Agent rails: ${verdict.failure}; retry the merge.`;
		if (!enforced(verdict) || verdict.outcome === "no") return undefined;
		if (verdict.outcome === "escalate") return `Agent rails: ${ownerReason(verdict, key, "whether this PR shows evidence for every outcome it claims.")}`;
		return `Agent rails: ${verdictText(verdict)}: an outcome this PR claims has no evidence; add the test, run or observation that shows it to the PR's Evidence section, then merge.`;
	}

	async function integrityReason(root: string, action: GitAction, ctx: ExtensionContext): Promise<string | undefined> {
		const state = await commitState(pi, root, action.args);
		if (!state) return undefined;
		const tree = (await run(pi, "git", ["write-tree"], root)).stdout.trim();
		const key = `implementation-integrity:${tree}`;
		if (approved(ctx, key)) return undefined;
		const subject: DecisionSubject = { trigger: "commit", item_kind: "staged-tree", item: tree };
		const origin = await run(pi, "git", ["remote", "get-url", "origin"], root);
		const repo = origin.code === 0 ? parseOrigin(origin.stdout.trim()) : null;
		if (repo) subject.repo = repo;
		const verdict = await decided("implementation-integrity", state, ctx, subject);
		if ("failure" in verdict) return `Agent rails: ${verdict.failure}; retry the commit.`;
		if (!enforced(verdict) || verdict.outcome === "no") return undefined;
		if (verdict.outcome === "escalate") return `Agent rails: ${ownerReason(verdict, key, "whether this staged change may weaken how the repository verifies itself.")}`;
		return `Agent rails: ${verdictText(verdict)}: the staged change weakens how the repository verifies its behaviour.\n` +
			"Implementation integrity means:\n" +
			"- no stub bodies, hardcoded values or stub implementations outside tests, and no mocks in product code;\n" +
			"- no altering, disabling, suppressing, loosening or conditionally bypassing gates or tests (for example --no-verify);\n" +
			"- no suppressing, defaulting or working around gate and test failures, errors or deprecations;\n" +
			"- no fallback modes or temporary strategies to meet the task, and no changing or abandoning the task after a failure without the owner's permission.\n" +
			"Restore the skipped, loosened, suppressed or bypassed checks, or cite the owner's recorded approval in the commit message, then commit again.";
	}

	async function impactDecided(ctx: ExtensionContext, input: ImpactInput): Promise<Verdict | Failure> {
		const [from, to] = [input.from, input.to].map(revision => revision.startsWith("git:") ? revision.slice(4) : undefined);
		if (!from || !to) return { failure: "no Git revision pair to judge" };
		if (!input.config) return { failure: "no valid .agentrails/config.json to judge against" };
		const { config } = input;
		const state = await (async () => ({
			config: impactConfig(config),
			change: await impactChange(pi, (await run(pi, "git", ["rev-parse", "--show-toplevel"], input.configRoot)).stdout.trim(), { from, to }),
		}))().catch((error: Error) => ({ failure: `the extension-impact judgment could not run: ${error.message}` }));
		return "failure" in state ? state : decided("extension-impact", state, ctx, { trigger: "manual", repo: input.slug, commit: to, item_kind: "extension-range", item: `${from}..${to}` });
	}

	return {
		/** implementation-integrity at a governed commit, after commands.commit and the harness checks passed. */
		async commitReason(root: string, action: GitAction, ctx: ExtensionContext): Promise<string | undefined> {
			return integrityReason(root, action, ctx);
		},
		/** completion-evidence at a governed merge, after method, head, proof and CI passed. */
		async mergeReason(cwd: string, selector: string | undefined, head: string, ctx: ExtensionContext): Promise<string | undefined> {
			const view = await prView(pi, cwd, selector, "number,title,body,state,headRefOid,url,files,statusCheckRollup")
				.catch((error: Error) => ({ failure: `the merge judgments could not read the PR: ${error.message}` }));
			if ("failure" in view) return `Agent rails: ${view.failure}; retry the merge.`;
			return evidenceMergeReason(cwd, view, head, ctx);
		},
		/** The askFirst command a bash call runs, when it runs one (rails.yml askFirstCommand). */
		async askFirstCommand(event: ToolEvent, cwd: string): Promise<string | undefined> {
			return event.toolName === "bash" ? privilegedCommand(event.input.command, cwd, deps.askFirst) : undefined;
		},
		/** in-scope before an ask or a privileged command the rails can see, in any Git repository. */
		async scopeReason(event: ToolEvent, input: ScopeInput, ctx: ExtensionContext): Promise<string | undefined> {
			const cwd = event.input.cwd ?? ctx.cwd;
			const { question, command } = await scopeSubject(event, input, ctx, cwd);
			if ((!question && !command) || !await deps.inGitRepo(cwd)) return undefined;
			if (ctx.agent.kind !== "main") return SUBAGENT_PRIVILEGED;
			return question ? askReason(question, ctx) : commandReason(command ?? "", input, ctx);
		},
		/** completion-evidence for the agent's PR at each governed turn end. */
		evidenceObligation(ctx: ExtensionContext, at: EvidenceTarget): Obligation {
			const obligation: Obligation = {
				id: `evidence:${at.branch}`, root: at.root, text: `the PR for ${at.branch} needs completion evidence`,
				status: () => evidenceStatus(ctx, at, obligation),
			};
			return obligation;
		},
		/** extension-impact for a governed repo's restart review: "no" skips the review task; yes or a failure keeps it. */
		impactVerdict(ctx: ExtensionContext, input: ImpactInput): Promise<Verdict | Failure> {
			return impactDecided(ctx, input);
		},
	};

}
export type JudgedRails = ReturnType<typeof judgedRails>;
export type { Failure };
