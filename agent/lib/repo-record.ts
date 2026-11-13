// Resolve a clone to its personal per-repo record under the oh-mi-pi-config root.
//
// A record lives at <config root>/repos/<owner>/<name>/ (lowercase) and holds gate.json and
// profile.md. /repos/ is gitignored in oh-mi-pi-config: records are personal and never committed.
// The config root is the checkout this file belongs to, so no home-directory path is in code.
// The clone's origin remote names the record; a worktree shares its main checkout's remotes.
// No origin remote, a non-GitHub origin or no record on disk means dynaflows is off.
//
// The Python twin is repo_record.py in this directory; keep both in step.
import { execFile } from "node:child_process";
import { readFile, stat } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { z } from "@oh-my-pi/pi-coding-agent";

export const GATE_FILE = "gate.json";
export const PROFILE_FILE = "profile.md";
export const RECORDS_DIR = "repos";

// The three GitHub origin forms. "." and ".." are refused so a slug never leaves repos/.
const ORIGIN = /^(?:https:\/\/github\.com\/|git@github\.com:|ssh:\/\/git@github\.com\/)([A-Za-z0-9_.-]+)\/([A-Za-z0-9_.-]+?)(?:\.git)?\/?$/;

/** A record exists but cannot be read; callers report it instead of treating dynaflows as off. */
export class RecordError extends Error {}

/** The oh-mi-pi-config checkout this module belongs to (<root>/agent/lib/repo-record.ts). */
export function configRoot(): string {
	return fileURLToPath(new URL("../..", import.meta.url));
}

/** owner/name in lowercase for a GitHub origin URL, else null. */
export function parseOrigin(url: string): string | null {
	const match = ORIGIN.exec(url.trim());
	if (!match || [match[1], match[2]].some(part => part === "." || part === "..")) return null;
	return `${match[1]}/${match[2]}`.toLowerCase();
}

/** owner/name of the clone or worktree at repo from its origin remote, else null. */
export function repoSlug(repo: string): Promise<string | null> {
	return new Promise(done => {
		execFile("git", ["-C", repo, "remote", "get-url", "origin"], { encoding: "utf8" }, (error, stdout) => {
			done(error ? null : parseOrigin(stdout));
		});
	});
}

/** Directory of the record for owner/name; it may not exist. */
export function recordDir(slug: string, root: string = configRoot()): string {
	const [owner, name] = slug.toLowerCase().split("/");
	return join(root, RECORDS_DIR, owner, name);
}

/** Why dynaflows is off, in the words rails and workflows.py both use: names the slug or "none". */
export function offText(slug: string | null, reason: string): string {
	return `dynaflows off for ${slug ?? "none"}: ${reason}`;
}

/**
 * The clone's slug and record directory, or the reason it has no record: no origin remote, an
 * origin that is not a GitHub owner/name URL, or no gate.json at the record directory.
 */
export function resolveRecord(repo: string, root: string = configRoot()): Promise<{ slug: string | null; dir: string | null; reason?: string }> {
	return new Promise(done => {
		execFile("git", ["-C", repo, "remote", "get-url", "origin"], { encoding: "utf8" }, (error, stdout) => {
			if (error) { done({ slug: null, dir: null, reason: "no origin remote" }); return; }
			const slug = parseOrigin(stdout);
			if (slug === null) { done({ slug: null, dir: null, reason: `origin ${stdout.trim()} is not a GitHub owner/name URL` }); return; }
			const dir = recordDir(slug, root);
			stat(join(dir, GATE_FILE)).then(
				found => done(found.isFile() ? { slug, dir } : { slug, dir: null, reason: `no per-repo record at ${join(dir, GATE_FILE)}` }),
				() => done({ slug, dir: null, reason: `no per-repo record at ${join(dir, GATE_FILE)}` }),
			);
		});
	});
}

/** Record directory for the clone at repo when its gate.json exists, else null. */
export async function findRecord(repo: string, root: string = configRoot()): Promise<string | null> {
	const slug = await repoSlug(repo);
	if (slug === null) return null;
	const dir = recordDir(slug, root);
	try {
		return (await stat(join(dir, GATE_FILE))).isFile() ? dir : null;
	} catch {
		return null;
	}
}

const argvSchema = z.array(z.string().refine(value => value.trim().length > 0)).min(1);
const nonEmpty = z.string().refine(value => value.trim().length > 0);
const dynaflowsSwitch = z.object({ enabled: z.boolean() });
// The per-repo record's gate.json; repo_record.py's gate_reason is its Python twin. Every field
// is current: an unknown field, at the top level or in `checks`, is rejected by name.
// `pathCheck` and `checks.commit` are required: rails run them at every agent commit, pathCheck
// first. `checks.ci` runs in CI; `checks.format` runs in freeze() before hashing; `checks.profile`
// is validated for the harness's other consumers. `ci.requiredContext` is the commit status or
// check run that must be SUCCESS on the exact PR head, whoever posts it; `ci.runner` is a role
// resolved by the machine's ci-runners.json.
export const gateSchema = z.object({
	schemaVersion: z.literal(2),
	checks: z.object({
		commit: argvSchema, ci: argvSchema.optional(), format: argvSchema.optional(), profile: argvSchema.optional(),
	}).strict(),
	pathCheck: argvSchema,
	testGlobs: z.array(z.string()).optional(),
	agentMerge: z.enum(["allowed", "disabled"]).optional(),
	ci: z.object({ requiredContext: nonEmpty, runner: nonEmpty }).optional(),
	// The repo's dynaflows switch (required); checked below for one message.
	dynaflows: z.unknown().optional(),
}).strict().superRefine((gate, ctx) => {
	if (!dynaflowsSwitch.safeParse(gate.dynaflows).success) {
		ctx.addIssue({ code: "custom", path: ["dynaflows"], message: 'dynaflows must be {"enabled": true|false}' });
	}
}).transform(gate => ({
	commit: gate.checks.commit,
	ciCheck: gate.checks.ci,
	pathCheck: gate.pathCheck,
	testGlobs: gate.testGlobs,
	agentMerge: gate.agentMerge,
	requiredCi: gate.ci,
	dynaflows: dynaflowsSwitch.parse(gate.dynaflows),
}));
export type Gate = z.infer<typeof gateSchema>;

/** The record's gate.json text. */
export async function readGateText(record: string): Promise<string> {
	const path = join(record, GATE_FILE);
	try {
		return await readFile(path, "utf8");
	} catch (error) {
		throw new RecordError(`${path}: ${error instanceof Error ? error.message : String(error)}`);
	}
}

/** The record's gate, parsed with gateSchema; an unreadable or invalid gate throws RecordError. */
export async function loadGate(record: string): Promise<Gate> {
	const path = join(record, GATE_FILE);
	const text = await readGateText(record);
	let parsed;
	try { parsed = gateSchema.safeParse(JSON.parse(text)); }
	catch (error) { throw new RecordError(`${path}: ${error instanceof Error ? error.message : String(error)}`); }
	if (!parsed.success) throw new RecordError(`${path}: invalid gate: ${parsed.error.message}`);
	return parsed.data;
}

/** True only when the clone has a record whose dynaflows.enabled is true. */
export async function dynaflowsEnabled(repo: string, root: string = configRoot()): Promise<boolean> {
	const record = await findRecord(repo, root);
	if (record === null) return false;
	return (await loadGate(record)).dynaflows.enabled;
}
