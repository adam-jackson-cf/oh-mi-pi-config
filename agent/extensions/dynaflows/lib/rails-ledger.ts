// The rails outcome ledger: one rail_events row per blocking rail decision, post-edit path-check
// failure and later resolution, in the decision store (<agent dir>/dynaflows/dynaflows.db,
// decision-store.ts), outside every repository. The ledger is observability, not a check: a write
// failure never changes a decision; the extension surfaces it as a note. ledger-report.ts
// summarises the rows.
import { machineName, withStore } from "./decision-store.ts";

export { storePath } from "./decision-store.ts";

// Every code a rail decision can carry, one per rule. A refusal's text is for the agent; the code is for counting.
export const RAIL_CODES = [
	// The rails themselves: rails.yml, an evaluation error, the origin remote and rail state.
	"rails-config", "policy-error", "post-edit-error", "handoff-error", "mode-unresolved", "rail-state-drift", "rail-state-write",
	"protected-path", "extension-review", "github-token", "config-unresolved",
	// The agent-rails config: a setup-section write the rails refused, and a repository that needs onboarding.
	"agentrails-config-write", "onboarding-required",
	// Reads, subagents and tools other than bash.
	"read-gate", "subagent-path", "eval-git", "eval-refused", "github-tool-write", "judged-scope",
	// The shell scan.
	"shell-unparseable", "git-unseen", "script-unchecked", "http-github-write", "omp-in-bash", "git-retarget", "git-external-command",
	"alias-depth", "cwd-unresolved", "unexpanded-shell",
	// Hooks, Git config and Git state.
	"hook-env", "hook-bypass", "hooks-path-override", "hooks-path-write", "config-alias", "config-alias-write", "config-oneshot",
	"config-write", "push-config-override", "push-bypass", "git-plumbing", "remote-write", "symbolic-ref-write", "nested-git",
	// gh.
	"gh-repo-override", "gh-alias-write", "gh-external-command", "gh-settings", "gh-api-dynamic-endpoint", "gh-api-write",
	"gh-graphql-body", "gh-graphql-mutation",
	// Worktrees.
	"worktree-base-unset", "worktree-origin", "worktree-placement",
	// Commits.
	"commit-alone", "commit-after-staging", "commit-scope", "commit-unstaged", "handoff-staged", "staged-path-check",
	"commit-checks", "harness-checks", "judged-integrity",
	// Publishing: push, PR and merge.
	"autonomy-ask", "minimal-merge", "push-remote-head", "push-default-branch", "merge-alone", "merge-admin",
	"merge-flag-unknown", "merge-args-ambiguous", "merge-head-format", "merge-method", "merge-head-stale", "merge-prove",
	"merge-structure", "merge-evidence-untrusted", "merge-pr-unreadable", "merge-foreign-repo", "merge-ci", "judged-evidence",
	// Edits and handoff.
	"path-check", "handoff-path-check", "handoff-read",
] as const;
export type RailCode = typeof RAIL_CODES[number];

/** A rail decision: the agent-facing text and, where that text carries command output or values, a summary without them. */
export type Refusal = { code: RailCode; reason: string; summary?: string };

/** A refusal the shell scan raises mid-parse; the extension reports it as policy text under its code. */
export class RailError extends Error {
	readonly code: RailCode;
	constructor(code: RailCode, message: string) {
		super(message);
		this.code = code;
	}
}

export function refuse(code: RailCode, reason: string, summary?: string): Refusal {
	return summary === undefined ? { code, reason } : { code, reason, summary };
}

/** A refusal for reason text a module outside the rails' refusal wiring returns, under code. */
export function coded(code: RailCode, reason: string | undefined): Refusal | undefined {
	return reason === undefined ? undefined : refuse(code, reason);
}

export const REASON_LIMIT = 200;

/** The ledger text for a refusal: its summary or first line, at most REASON_LIMIT characters. */
export function ledgerReason(refusal: Refusal): string {
	const line = (refusal.summary ?? refusal.reason).split("\n")[0]?.trim() ?? "";
	return line.length > REASON_LIMIT ? `${line.slice(0, REASON_LIMIT - 1)}…` : line;
}

/** A variable assignment or config entry reduced to its name, so no value reaches the ledger. */
export function assignmentName(word: string): string {
	return word.split("=")[0] ?? "";
}

export const LEDGER_KINDS = ["block", "context", "resolved"] as const;
export type LedgerKind = typeof LEDGER_KINDS[number];
// One ledger row. Resolved rows only carry `calls` (tool calls from the episode's first refusal
// to the call that passed) and `refusals` (how many refusals the episode held); others carry `reason`.
export type LedgerLine = {
	tool: string; kind: LedgerKind; code: RailCode; reason: string | null; calls: number | null; refusals: number | null;
};
export type LedgerEntry = LedgerLine & { ts: string; session: string; repo: string | null };

export function blockLine(kind: Exclude<LedgerKind, "resolved">, tool: string, refusal: Refusal): LedgerLine {
	return { tool, kind, code: refusal.code, reason: ledgerReason(refusal), calls: null, refusals: null };
}

export function resolvedLine(resolution: Resolution): LedgerLine {
	return { tool: resolution.tool, kind: "resolved", code: resolution.code, reason: null, calls: resolution.calls, refusals: resolution.refusals };
}

const INSERT = "INSERT INTO rail_events (ts, session, repo, tool, kind, code, reason, calls, refusals, machine) " +
	"VALUES ($ts, $session, $repo, $tool, $kind, $code, $reason, $calls, $refusals, $machine)";

/** Writes entries to the rail_events table of the decision store at path, in one transaction, with this machine's name. */
export function insertEntries(path: string, entries: LedgerEntry[]): void {
	if (!entries.length) return;
	const machine = machineName();
	withStore(db => {
		const insert = db.query(INSERT);
		db.transaction(() => { for (const entry of entries) insert.run({ ...entry, machine }); })();
	}, path);
}

// An open refusal episode: the same code refused for overlapping subjects (Git verbs such as
// `git commit`, edit target paths, `path-check:<root>` or `session_stop`) until a later call on one
// of those subjects passes. A command the rails cannot parse has no subject and never resolves.
export type Episode = { code: RailCode; tool: string; subjects: string[]; call: number; refusals: number };
export type Resolution = { code: RailCode; tool: string; calls: number; refusals: number };

const overlaps = (left: string[], right: string[]) => left.some(subject => right.includes(subject));

/** Opens an episode for a refusal, or counts it into the open episode with the same code and an overlapping subject. */
export function openEpisode(episodes: Episode[], refusal: Omit<Episode, "refusals">): void {
	const open = episodes.find(episode => episode.code === refusal.code && overlaps(episode.subjects, refusal.subjects));
	if (open) { open.refusals++; open.subjects = [...new Set([...open.subjects, ...refusal.subjects])]; return; }
	episodes.push({ ...refusal, refusals: 1 });
}

/** Closes every episode whose subjects overlap a passing call's subjects; returns their resolutions. */
export function closeEpisodes(episodes: Episode[], subjects: string[], call: number): Resolution[] {
	const closed = episodes.filter(episode => overlaps(episode.subjects, subjects));
	episodes.splice(0, episodes.length, ...episodes.filter(episode => !closed.includes(episode)));
	return closed.map(episode => ({ code: episode.code, tool: episode.tool, calls: call - episode.call, refusals: episode.refusals }));
}
