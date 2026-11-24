// Owner approval lines that write the config's exceptions and approvals sections. They count only
// when the owner typed them in the main session: the input event says whether text was typed
// (source interactive), and before_agent_start applies only the lines typed so. Prompts an
// extension injects (pi.sendUserMessage) and subagent prompts, which agents write, never pass.
// judge.ts's one-use `$approve <judgment>:<id>` and `$approve autonomy:<action>:<subject>` lines
// are a different form: no line matches both parsers.
import { type ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import { checkSection, emptyDraft, readDraft, writeConfig, writeDraft, type CheckOutcome, type ConfigCheck } from "./config-writer.ts";
import type { OnboardingContext, OnboardingSession } from "./onboarding.ts";
import { resolveConfig, type Approval, type ConfigException } from "./repo-config.ts";
import { SUBPROCESS_TIMEOUT_MS } from "./rails-runtime.ts";

const KINDS = ["budget", "ratchet", "skip", "exemption"] as const;
type ApprovalKind = typeof KINDS[number];
const OWNER_LINE = /^\$(?:approve|revoke) (?:exception|approval)(?: |$)/;
const APPROVE_EXCEPTION = /^\$approve exception (\S+) (\S+)(?: check=(\S+))? -- (\S.*)$/;
const REVOKE_EXCEPTION = /^\$revoke exception (\S+) (\S+)(?: check=(\S+))?$/;
const APPROVE_APPROVAL = /^\$approve approval (budget|ratchet|skip|exemption) (\S(?:.*?\S)?) -- (\S.*)$/;
const REVOKE_APPROVAL = /^\$revoke approval (budget|ratchet|skip|exemption) (\S(?:.*\S)?)$/;
const FORMS = [
	"$approve exception <RULE> <path-glob> [check=<check>] -- <reason>", "$revoke exception <RULE> <path-glob> [check=<check>]",
	"$approve approval <budget|ratchet|skip|exemption> <target> -- <reason>", "$revoke approval <kind> <target>",
];

type ExceptionKey = { rule: string; path: string; check?: string };
/** One parsed owner line. A revoke carries no reason. */
export type OwnerLine =
	| { section: "exceptions"; verb: "approve"; key: ExceptionKey; reason: string }
	| { section: "exceptions"; verb: "revoke"; key: ExceptionKey }
	| { section: "approvals"; verb: "approve"; key: { kind: ApprovalKind; target: string }; reason: string }
	| { section: "approvals"; verb: "revoke"; key: { kind: ApprovalKind; target: string } };

const kindOf = (word: string | undefined): ApprovalKind => KINDS.find(kind => kind === word) ?? "skip";
const exceptionKey = (rule = "", path = "", check: string | undefined): ExceptionKey => check === undefined ? { rule, path } : { rule, path, check };

/** Whether a prompt line is meant as an exception or approval line, well formed or not. */
export const isOwnerLine = (line: string): boolean => OWNER_LINE.test(line.trim());

/** The owner line a prompt line holds, or undefined when it is not one of the four forms. */
export function parseOwnerLine(text: string): OwnerLine | undefined {
	const line = text.trim();
	let match = APPROVE_EXCEPTION.exec(line);
	if (match) return { section: "exceptions", verb: "approve", key: exceptionKey(match[1], match[2], match[3]), reason: match[4] ?? "" };
	match = REVOKE_EXCEPTION.exec(line);
	if (match) return { section: "exceptions", verb: "revoke", key: exceptionKey(match[1], match[2], match[3]) };
	match = APPROVE_APPROVAL.exec(line);
	if (match) return { section: "approvals", verb: "approve", key: { kind: kindOf(match[1]), target: match[2] ?? "" }, reason: match[3] ?? "" };
	match = REVOKE_APPROVAL.exec(line);
	return match ? { section: "approvals", verb: "revoke", key: { kind: kindOf(match[1]), target: match[2] ?? "" } } : undefined;
}

type Signature = { approvedBy: string; approvedAt: string };
type Sections = { exceptions: ConfigException[]; approvals: Approval[] };
const sameException = (entry: ConfigException, key: ExceptionKey) => entry.rule === key.rule && entry.path === key.path && entry.check === key.check;
const sameApproval = (entry: Approval, key: { kind: ApprovalKind; target: string }) => entry.kind === key.kind && entry.target === key.target;

/** The sections with line applied: an approve adds or replaces its entry, a revoke removes it. Undefined when a revoke finds nothing. */
export function applyOwnerLine(sections: Sections, line: OwnerLine, signature: Signature): Sections | undefined {
	if (line.section === "exceptions") {
		const kept = sections.exceptions.filter(entry => !sameException(entry, line.key));
		if (line.verb === "revoke") return kept.length === sections.exceptions.length ? undefined : { ...sections, exceptions: kept };
		return { ...sections, exceptions: [...kept, { ...line.key, reason: line.reason, ...signature }] };
	}
	const kept = sections.approvals.filter(entry => !sameApproval(entry, line.key));
	if (line.verb === "revoke") return kept.length === sections.approvals.length ? undefined : { ...sections, approvals: kept };
	return { ...sections, approvals: [...kept, { ...line.key, reason: line.reason, ...signature }] };
}

export type OwnerApprovalsDeps = { check: ConfigCheck; engineDir: string; session: (ctx: OnboardingContext) => OnboardingSession };
type OwnerApprovalsApi = Pick<ExtensionAPI, "exec" | "on">;
// logins: the owner's GitHub login by session, read once per session; a failed read is not kept, so the next line retries.
type Run = { pi: OwnerApprovalsApi; deps: OwnerApprovalsDeps; ctx: OnboardingContext; logins: Map<string, string> };

async function ownerLogin(run: Run): Promise<string | Error> {
	const session = run.ctx.sessionManager.getSessionId();
	const known = run.logins.get(session);
	if (known !== undefined) return known;
	const result = await run.pi.exec("gh", ["api", "user", "--jq", ".login"], { cwd: run.ctx.cwd, timeout: SUBPROCESS_TIMEOUT_MS });
	const login = result.stdout.trim();
	if (result.code !== 0 || result.killed || !/^[A-Za-z0-9-]+$/.test(login)) {
		return new Error(`gh api user failed${result.killed ? " (killed)" : ` (exit ${result.code})`}: ${(result.stderr || result.stdout).trim()}`);
	}
	run.logins.set(session, login);
	return login;
}

const describe = (line: OwnerLine) => line.section === "exceptions"
	? `exception ${line.key.rule} ${line.key.path}${line.key.check ? ` check=${line.key.check}` : ""}`
	: `approval ${line.key.kind} ${line.key.target}`;

async function writeDraftLine(run: Run, main: string, line: OwnerLine, signature: Signature): Promise<CheckOutcome> {
	const draft = await readDraft(run.pi, main) ?? emptyDraft();
	const next = applyOwnerLine(draft, line, signature);
	if (next === undefined) return { ok: false, problems: [`the onboarding draft has no ${describe(line)}`] };
	const verdict = await checkSection(run.pi, run.deps.check, main, line.section, JSON.parse(JSON.stringify(next[line.section])));
	if (verdict.ok) await writeDraft(run.pi, main, { ...draft, ...next });
	return verdict;
}

// Writes one line to the config, or to the onboarding draft while the owner onboards a repository with no valid config.
async function writeLine(run: Run, line: OwnerLine, signature: Signature): Promise<CheckOutcome> {
	const state = await resolveConfig(run.pi, run.deps.engineDir, run.ctx.cwd);
	if (state.kind === "ok") {
		const next = applyOwnerLine(state.config, line, signature);
		if (next === undefined) return { ok: false, problems: [`the config has no ${describe(line)}`] };
		return writeConfig(run.pi, run.deps.check, state.main, { ...state.config, ...next });
	}
	if ("main" in state && run.deps.session(run.ctx).onboarding.has(state.main)) return writeDraftLine(run, state.main, line, signature);
	const why = "main" in state ? `its config is ${state.kind}` : `${state.kind}: ${state.reason}`;
	return { ok: false, problems: [`this repository needs a valid agent-rails config (${why}); run /agentrails onboard`] };
}

async function applyLines(run: Run, lines: string[]): Promise<void> {
	const login = await ownerLogin(run);
	for (const text of lines) {
		const line = parseOwnerLine(text);
		if (line === undefined) {
			run.ctx.ui.notify(`Agent rails: ${text.trim()} is not an owner line; use one of:\n${FORMS.join("\n")}`, "error");
			continue;
		}
		if (login instanceof Error) {
			run.ctx.ui.notify(`Agent rails: ${describe(line)} not written: the owner's GitHub login is needed for approvedBy and ${login.message}`, "error");
			continue;
		}
		const written = await writeLine(run, line, { approvedBy: login, approvedAt: new Date().toISOString() });
		run.ctx.ui.notify(written.ok
			? `Agent rails: ${line.verb === "approve" ? "approved" : "revoked"} ${describe(line)}.`
			: `Agent rails: ${describe(line)} not written:\n${written.problems.join("\n")}`, written.ok ? "info" : "error");
	}
}

/** Registers the owner approval lines: recorded from typed input, applied before the agent starts. */
export function registerOwnerApprovals(pi: OwnerApprovalsApi, deps: OwnerApprovalsDeps): void {
	const typed = new Map<string, string[]>();
	const logins = new Map<string, string>();
	pi.on("input", (event, ctx) => {
		if (event.source !== "interactive" || ctx.agent.kind !== "main") return;
		const session = ctx.sessionManager.getSessionId();
		const lines = event.text.split("\n").map(line => line.trim()).filter(isOwnerLine);
		if (lines.length) typed.set(session, [...(typed.get(session) ?? []), ...lines]);
	});
	pi.on("before_agent_start", async (event, ctx) => {
		if (ctx.agent.kind !== "main" || event.reason !== "prompt") return;
		const lines = event.prompt.split("\n").map(line => line.trim()).filter(isOwnerLine);
		if (!lines.length) return;
		const session = ctx.sessionManager.getSessionId();
		const pending = typed.get(session) ?? [];
		const owned: string[] = [];
		for (const line of lines) {
			const index = pending.indexOf(line);
			if (index >= 0) {
				pending.splice(index, 1);
				owned.push(line);
			} else ctx.ui.notify(`Agent rails: ignored "${line}": only lines the owner types in the main session write the config.`, "error");
		}
		if (owned.length) await applyLines({ pi, deps, ctx, logins }, owned);
	});
}
