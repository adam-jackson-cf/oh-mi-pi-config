// The one way extension code writes a repository's agent-rails config, and the onboarding draft
// the setup sections wait in. Agent tools never write either: trust.ts refuses every path with a
// .agentrails segment, and the draft lives under the Git common dir, which no agent tool writes.
// Every config write validates the whole file with the engine (guardrails config-check) and only
// then replaces it atomically.
import { createHash, randomUUID } from "node:crypto";
import { mkdir, mkdtemp, readdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, relative, sep } from "node:path";
import { z } from "@oh-my-pi/pi-coding-agent";
import { commonDir, ensureExcluded, originSlug, type ExecApi } from "./git-paths.ts";
import { SUBPROCESS_TIMEOUT_MS, absentFile } from "./rails-runtime.ts";
import {
	CONFIG_EXCLUDE, configPathFor, type AgentRailsConfig, type Approval, type ConfigException, type EngineSection, type JsonValue, type SkillName, type SkillRun,
} from "./repo-config.ts";

/** The info/exclude line for the setup-repo-proofs run state, which the proof workflow keeps in the repository. */
export const PROOFS_EXCLUDE = "/.proofs/";
/** The sections the setup skills record, each with the one skill whose run writes it. */
export const SETUP_SECTIONS = ["commands", "checkers", "structure", "houseStyle", "tests"] as const;
export type SetupSection = typeof SETUP_SECTIONS[number];
export const SECTION_SKILL = {
	commands: "setup-repo-proofs", checkers: "setup-repo-proofs", structure: "setup-repo-proofs", houseStyle: "setup-repo-proofs",
	tests: "test-guidance",
} as const satisfies Record<SetupSection, SkillName>;

/**
 * An onboarding draft: the setup sections recorded so far, each skill's stamp, and the owner's
 * exceptions and approvals typed before the config exists. Only /agentrails onboard turns it into
 * a config, and only it writes the stamps into the config's onboarding section.
 */
export type Draft = {
	sections: Partial<Record<SetupSection, EngineSection>>;
	skills: Partial<Record<SkillName, SkillRun>>;
	exceptions: ConfigException[];
	approvals: Approval[];
};
export type CheckOutcome = { ok: true } | { ok: false; problems: string[] };

/** A skill run's scope, as the setup tool's caller states it. */
export const skillScope = z.union([
	z.object({ kind: z.literal("whole") }).strict(),
	z.object({ kind: z.literal("changed") }).strict(),
	z.object({ kind: z.literal("paths"), paths: z.array(z.string().min(1)).min(1) }).strict(),
]);
// The draft's frame. Its values were engine-checked before the extension wrote them, and the
// whole config is checked again when onboarding writes it.
const draftFile = z.object({
	sections: z.record(z.enum(SETUP_SECTIONS), z.record(z.string(), z.unknown())),
	skills: z.record(z.enum(["setup-repo-proofs", "test-guidance"]), z.object({ version: z.string(), ranAt: z.string(), scope: skillScope }).strict()),
	exceptions: z.array(z.record(z.string(), z.unknown())),
	approvals: z.array(z.record(z.string(), z.unknown())),
}).strict();
const outcome = z.object({ ok: z.boolean(), problems: z.array(z.string()) });

/** The engine's config-check as the extension runs it: an argv prefix the writer appends its form to. */
export type ConfigCheck = { argv: string[] };

/** Runs config-check with args in cwd and returns its one JSON line, or the failure as text. */
export async function runConfigCheck(pi: ExecApi, check: ConfigCheck, cwd: string, args: string[]): Promise<{ line: string } | { failure: string }> {
	const [command, ...prefix] = check.argv;
	if (!command) throw new Error("the config-check command is empty");
	const result = await pi.exec(command, [...prefix, ...args], { cwd, timeout: SUBPROCESS_TIMEOUT_MS });
	const line = result.stdout.trim().split("\n").at(-1) ?? "";
	if (result.killed || !line.startsWith("{")) {
		const detail = (result.stderr.trim() || result.stdout.trim()).split("\n").at(-1) ?? "";
		return { failure: `guardrails config-check ${args.join(" ")} ${result.killed ? "was killed" : `exited ${result.code}`}${detail ? `: ${detail}` : ""}` };
	}
	return { line };
}

async function checked(pi: ExecApi, check: ConfigCheck, cwd: string, args: string[]): Promise<CheckOutcome> {
	const ran = await runConfigCheck(pi, check, cwd, args);
	if ("failure" in ran) return { ok: false, problems: [ran.failure] };
	const parsed = outcome.safeParse(JSON.parse(ran.line));
	if (!parsed.success) return { ok: false, problems: [`guardrails config-check printed an unreadable verdict: ${ran.line}`] };
	return parsed.data.ok ? { ok: true } : { ok: false, problems: parsed.data.problems };
}

/** Validates a config file as a whole against the lowercase origin slug. */
export function checkFile(pi: ExecApi, check: ConfigCheck, cwd: string, path: string, slug: string): Promise<CheckOutcome> {
	return checked(pi, check, cwd, ["--file", path, "--slug", slug]);
}

/** Validates one section value alone. */
export async function checkSection(pi: ExecApi, check: ConfigCheck, cwd: string, section: string, value: JsonValue): Promise<CheckOutcome> {
	const dir = await mkdtemp(join(tmpdir(), "agentrails-section-"));
	try {
		const path = join(dir, `${section}.json`);
		await writeFile(path, JSON.stringify(value));
		return await checked(pi, check, cwd, ["--section", section, "--file", path]);
	} finally { await rm(dir, { recursive: true, force: true }); }
}

/**
 * Writes next as main's config: the config folder is excluded from Git first, the file is written
 * beside the config, checked whole against the origin, and renamed over the config only when the
 * engine accepts it. A refusal leaves the config as it was.
 */
export async function writeConfig(pi: ExecApi, check: ConfigCheck, main: string, next: AgentRailsConfig): Promise<CheckOutcome> {
	await ensureExcluded(pi, main, CONFIG_EXCLUDE);
	const slug = await originSlug(pi, main);
	if (slug === null) return { ok: false, problems: [`${main} has no GitHub origin remote; agent rails configure GitHub repositories only`] };
	const path = configPathFor(main);
	await mkdir(dirname(path), { recursive: true });
	const staged = join(dirname(path), `.config-${randomUUID()}.json`);
	try {
		await writeFile(staged, `${JSON.stringify(next, null, 2)}\n`);
		const verdict = await checkFile(pi, check, main, staged, slug);
		if (verdict.ok) await rename(staged, path);
		return verdict;
	} finally { await rm(staged, { force: true }); }
}

/** Keeps the config folder and the setup-repo-proofs run state out of Git for every worktree. */
export async function ensureRepoExcludes(pi: ExecApi, main: string): Promise<void> {
	await ensureExcluded(pi, main, CONFIG_EXCLUDE);
	await ensureExcluded(pi, main, PROOFS_EXCLUDE);
}

/** The onboarding draft of the repository holding cwd: <git common dir>/agentrails/draft.json. */
export async function draftPath(pi: ExecApi, cwd: string): Promise<string> {
	return join(await commonDir(pi, cwd), "agentrails", "draft.json");
}

export const emptyDraft = (): Draft => ({ sections: {}, skills: {}, exceptions: [], approvals: [] });

/** An engine-checked section value as the config type that section holds. */
export function engineChecked<Section>(value: EngineSection): Section {
	return JSON.parse(JSON.stringify(value));
}

/** The draft, or null when none exists. A draft the extension cannot read throws. */
export async function readDraft(pi: ExecApi, cwd: string): Promise<Draft | null> {
	const path = await draftPath(pi, cwd);
	const text = await readFile(path, "utf8").catch(absentFile);
	if (text === null) return null;
	const parsed = draftFile.safeParse(JSON.parse(text));
	if (!parsed.success) throw new Error(`the onboarding draft ${path} is unreadable: ${parsed.error.message}`);
	const draft: Draft = JSON.parse(text);
	return draft;
}

/** Replaces the draft atomically. */
export async function writeDraft(pi: ExecApi, cwd: string, draft: Draft): Promise<void> {
	const path = await draftPath(pi, cwd);
	await mkdir(dirname(path), { recursive: true });
	const staged = `${path}.${randomUUID()}`;
	try {
		await writeFile(staged, `${JSON.stringify(draft, null, 2)}\n`);
		await rename(staged, path);
	} finally { await rm(staged, { force: true }); }
}

export async function deleteDraft(pi: ExecApi, cwd: string): Promise<void> {
	await rm(await draftPath(pi, cwd), { force: true });
}

/** The setup sections and skill stamps a draft still lacks before onboarding can finish. */
export function draftGaps(draft: Draft | null): string[] {
	const sections = SETUP_SECTIONS.filter(name => draft?.sections[name] === undefined);
	const skills = (["setup-repo-proofs", "test-guidance"] as const).filter(name => draft?.skills[name] === undefined);
	return [...sections, ...skills.map(name => `the ${name} stamp`)];
}

const IGNORED = new Set([".DS_Store", "__pycache__"]);

async function skillFiles(root: string, dir = root): Promise<string[]> {
	const entries = await readdir(dir, { withFileTypes: true });
	const found: string[] = [];
	for (const entry of entries) {
		if (IGNORED.has(entry.name)) continue;
		const path = join(dir, entry.name);
		if (entry.isDirectory()) found.push(...await skillFiles(root, path));
		else if (entry.isFile()) found.push(relative(root, path).split(sep).join("/"));
	}
	return found;
}

/** A skill folder's version: sha256 over its files' relative paths and bytes, in sorted path order. */
export async function skillVersion(folder: string): Promise<string> {
	const hash = createHash("sha256");
	for (const path of (await skillFiles(folder)).toSorted()) {
		hash.update(`${path}\0`);
		hash.update(await readFile(join(folder, path)));
		hash.update("\0");
	}
	return hash.digest("hex");
}
