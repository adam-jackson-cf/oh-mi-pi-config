// The agent-rails repo config: <main checkout>/.agentrails/config.json, one per repository,
// kept out of Git through the shared info/exclude. The engine's repo_config.py is the only
// validator; `guardrails config-check` exposes it. This module finds the file, asks the engine
// for a verdict and caches it by (path, sha256). It holds hand-written types only: a TypeScript
// validator would be a second authority.
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { z } from "@oh-my-pi/pi-coding-agent";
import { SUBPROCESS_TIMEOUT_MS, absentFile } from "./rails-runtime.ts";
import { mainCheckout, originSlug, OutsideRepository, type ExecApi } from "./git-paths.ts";

// The Git locations the config is found through (common dir, main checkout, origin); the rails that
// read the config use them too.
export { commonDir, configRoot, minimalText, OriginError, originUrl, parseOrigin, repoSlug, unresolvedText } from "./git-paths.ts";

export const CONFIG_DIR = ".agentrails";
export const CONFIG_FILE = "config.json";
/** The info/exclude line that keeps the config directory out of Git. */
export const CONFIG_EXCLUDE = `/${CONFIG_DIR}/`;
/** The onboarding questionnaire version; it must equal the engine's QUESTIONNAIRE_VERSION. */
export const QUESTIONNAIRE_VERSION = 1;

export const configPathFor = (main: string): string => join(main, CONFIG_DIR, CONFIG_FILE);

// The config's shape, mirroring the engine schema. Sections the engine owns stay plain JSON here.
export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };
/** An engine-owned section (checkers, structure, tests): its inner schemas live in the engine only. */
export type EngineSection = { [key: string]: JsonValue };
export type Argv = string[];
export type Permission = "allowed" | "ask";
export type SkillName = "setup-repo-proofs" | "test-guidance";
export type SkillScope = { kind: "whole" } | { kind: "changed" } | { kind: "paths"; paths: string[] };
export type SkillRun = { version: string; ranAt: string; scope: SkillScope };
export type Onboarding =
	| { origin: "onboarding"; completedAt: string; questionnaireVersion: number; skills: Record<SkillName, SkillRun> }
	| { origin: "migration"; completedAt: string; questionnaireVersion: number; migratedFrom: string; skills: Partial<Record<SkillName, SkillRun>> };
export type Autonomy = { branch: Permission; pr: Permission; merge: Permission };
export type Ci = { enabled: false } | { enabled: true; runner: "github-actions" | "ci-gate"; requiredContext: string };
export type Gates = {
	commitChecks: boolean; pathCheck: boolean; prove: boolean; structureReview: boolean; audit: boolean;
	/** Code-check rule id to on/off; a missing rule is on. */
	rules: Record<string, boolean>;
};
/** An override for one "<judgment>/<question>"; a question with no override follows its global mode. */
export type JudgmentOverride = { enabled: false } | { enabled: true; mode: "enforced" | "shadow" };
export type MergePolicy = { base: string; headPrefix: string; method: "rebase" | "merge"; proof: boolean };
export type AfterMerge = { argv: Argv; paths?: string[] };
export type Commands = { commit: Argv; ci?: Argv; audit?: Argv; format?: Argv };
export type ConfigException = { rule: string; path: string; check?: string; reason: string; approvedBy: string; approvedAt: string };
export type Approval = {
	kind: "budget" | "ratchet" | "skip" | "exemption"; target: string; reason: string; approvedBy: string; approvedAt: string;
};
export type AgentRailsConfig = {
	schemaVersion: 1;
	repo: { slug: string; defaultBranch: string };
	onboarding: Onboarding;
	autonomy: Autonomy;
	ci: Ci;
	gates: Gates;
	judgments: Record<string, JudgmentOverride>;
	mergePolicies: MergePolicy[];
	afterMerge?: AfterMerge;
	commands: Commands;
	checkers: EngineSection;
	structure: EngineSection;
	houseStyle: { version: number };
	tests: EngineSection;
	exceptions: ConfigException[];
	approvals: Approval[];
};

/**
 * A repository's config as the rails see it. outside: a directory in no Git repository, or a
 * repository with no GitHub origin. unresolved: no main checkout (a bare repository or a separate
 * git dir), or Git or the engine could not answer.
 */
export type ConfigState =
	| { kind: "ok"; main: string; path: string; config: AgentRailsConfig; sha: string }
	| { kind: "missing"; main: string; path: string }
	| { kind: "invalid"; main: string; path: string; problems: string[] }
	| { kind: "unresolved"; reason: string }
	| { kind: "outside"; reason: string };

type ConfigFile = { main: string; path: string; text: string; sha: string };
type Verdict = { sha: string; slug: string; state: ConfigState };

const outcomeSchema = z.object({
	ok: z.boolean(),
	kind: z.enum(["ok", "missing", "invalid", "unresolved", "outside"]),
	path: z.string().nullable(),
	problems: z.array(z.string()),
});
type Outcome = z.infer<typeof outcomeSchema>;

// Engine verdicts on file content, by config path. A missing file drops its entry.
const verdicts = new Map<string, Verdict>();

const digest = (text: string): string => createHash("sha256").update(text).digest("hex");

function parsedOutcome(stdout: string): Outcome | undefined {
	const line = stdout.trim().split("\n").at(-1) ?? "";
	try {
		const parsed = outcomeSchema.safeParse(JSON.parse(line));
		return parsed.success ? parsed.data : undefined;
	} catch {
		return undefined;
	}
}

// Only an ok verdict gives the text its type: the engine accepted exactly these bytes.
function accepted(file: ConfigFile): ConfigState {
	const config: AgentRailsConfig = JSON.parse(file.text);
	return { kind: "ok", main: file.main, path: file.path, config, sha: file.sha };
}

function stateFor(outcome: Outcome, file: ConfigFile): ConfigState {
	const reason = outcome.problems.join("; ");
	switch (outcome.kind) {
		case "ok": return accepted(file);
		case "invalid": return { kind: "invalid", main: file.main, path: file.path, problems: outcome.problems };
		case "missing": return { kind: "missing", main: file.main, path: file.path };
		case "outside": return { kind: "outside", reason };
		case "unresolved": return { kind: "unresolved", reason };
	}
}

function failure(argv: string[], result: { code: number; killed?: boolean; stdout: string; stderr: string }): string {
	const detail = (result.stderr.trim() || result.stdout.trim()).split("\n").at(-1) ?? "";
	const how = result.killed ? "was killed" : `exited ${result.code}`;
	return `uv ${argv.join(" ")} ${how}${detail ? `: ${detail}` : ""}`;
}

async function engineVerdict(pi: ExecApi, engineDir: string, cwd: string, file: ConfigFile): Promise<ConfigState> {
	const argv = ["run", "--project", engineDir, "guardrails", "config-check", "--repo", cwd];
	const result = await pi.exec("uv", argv, { cwd, timeout: SUBPROCESS_TIMEOUT_MS });
	const outcome = parsedOutcome(result.stdout);
	if (result.killed || outcome === undefined || result.code !== (outcome.ok ? 0 : 1)) return { kind: "unresolved", reason: failure(argv, result) };
	const after = await readFile(file.path, "utf8").catch(() => undefined);
	if (after === undefined || digest(after) !== file.sha) return { kind: "unresolved", reason: `${file.path} changed while it was checked; retry` };
	return stateFor(outcome, file);
}

async function located(pi: ExecApi, cwd: string): Promise<{ main: string; slug: string } | ConfigState> {
	try {
		const main = await mainCheckout(pi, cwd);
		const slug = await originSlug(pi, cwd);
		return slug === null
			? { kind: "outside", reason: `${cwd} has no GitHub origin remote; agent rails configure GitHub repositories only` }
			: { main, slug };
	} catch (error) {
		const reason = error instanceof Error ? error.message : String(error);
		return { kind: error instanceof OutsideRepository ? "outside" : "unresolved", reason };
	}
}

// The config text, null when the file does not exist, or the error that stopped the read.
async function readConfig(path: string): Promise<string | null | Error> {
	try {
		return await readFile(path, "utf8").catch(absentFile);
	} catch (error) {
		return error instanceof Error ? error : new Error(String(error));
	}
}

/**
 * The config state for the repository holding cwd. A linked worktree reads its main checkout's
 * file. The engine checks a file once per (path, sha256, origin); a missing file drops the cached
 * verdict, so a deleted config is never served from the cache.
 */
export async function resolveConfig(pi: ExecApi, engineDir: string, cwd: string): Promise<ConfigState> {
	const where = await located(pi, cwd);
	if ("kind" in where) return where;
	const path = configPathFor(where.main);
	const text = await readConfig(path);
	if (text === null) {
		verdicts.delete(path);
		return { kind: "missing", main: where.main, path };
	}
	if (text instanceof Error) return { kind: "invalid", main: where.main, path, problems: [`${path} cannot be read: ${text.message}`] };
	const sha = digest(text);
	const cached = verdicts.get(path);
	if (cached?.sha === sha && cached.slug === where.slug) return cached.state;
	const state = await engineVerdict(pi, engineDir, cwd, { main: where.main, path, text, sha });
	if (state.kind === "ok" || state.kind === "invalid") verdicts.set(path, { sha, slug: where.slug, state });
	else verdicts.delete(path);
	return state;
}
