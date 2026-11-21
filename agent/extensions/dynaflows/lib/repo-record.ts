// Resolve a clone to its personal per-repo record under the oh-mi-pi-config root.
//
// A record lives at <config root>/repos/<owner>/<name>/ (lowercase) and holds gate.json and
// profile.md. /repos/ is gitignored in oh-mi-pi-config: records are personal and never committed.
// The config root is the checkout this file belongs to, so no home-directory path is in code.
// The clone's origin remote names the record; a worktree shares its main checkout's remotes.
//
// The Python twin is repo_record.py in this directory; keep both in step.
import { readFile, stat } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { z } from "@oh-my-pi/pi-coding-agent";

export const GATE_FILE = "gate.json";
const RECORDS_DIR = "repos";

// The three GitHub origin forms. "." and ".." are refused so a slug never leaves repos/.
const ORIGIN = /^(?:https:\/\/github\.com\/|git@github\.com:|ssh:\/\/git@github\.com\/)([A-Za-z0-9_.-]+)\/([A-Za-z0-9_.-]+?)(?:\.git)?\/?$/;

export class RecordError extends Error {}
/**
 * The origin remote could not be read: git was killed, timed out, could not start, or failed for
 * a reason other than a missing remote or a path outside a Git repository. The repository's mode
 * is then unknown; callers refuse writes and never read it as "no origin".
 */
export class OriginError extends RecordError {}

const ORIGIN_TIMEOUT_MS = 20_000;

export function configRoot(): string {
	return fileURLToPath(new URL("../../../..", import.meta.url));
}

/** owner/name in lowercase for a GitHub origin URL, else null. */
export function parseOrigin(url: string): string | null {
	const match = ORIGIN.exec(url.trim());
	if (!match || [match[1], match[2]].some(part => part === "." || part === "..")) return null;
	return `${match[1]}/${match[2]}`.toLowerCase();
}

/**
 * The origin remote URL of the clone or worktree at repo, or null when it has no origin remote or
 * repo is not in a Git repository. Any other failure throws OriginError.
 */
export async function originUrl(repo: string): Promise<string | null> {
	const command = `git remote get-url origin in ${repo}`;
	let child;
	try {
		child = Bun.spawn(["git", "-C", repo, "remote", "get-url", "origin"], {
			env: { ...process.env, LC_ALL: "C", LANGUAGE: "C" }, stdin: "ignore", stdout: "pipe", stderr: "pipe", timeout: ORIGIN_TIMEOUT_MS,
		});
	} catch (error) {
		throw new OriginError(`${command} could not run: ${error instanceof Error ? error.message : String(error)}`);
	}
	const [stdout, stderr] = await Promise.all([new Response(child.stdout).text(), new Response(child.stderr).text(), child.exited]);
	if (child.exitCode === 0) return stdout;
	if (noOrigin(child.exitCode, stderr)) return null;
	const how = child.signalCode ? `was killed (${child.signalCode})` : `exited ${child.exitCode}`;
	throw new OriginError(`${command} ${how}${stderr.trim() ? `: ${stderr.trim()}` : ""}`);
}

// git's own words for the two cases that mean "no origin": no such remote (exit 2) and a path outside a repository (exit 128).
function noOrigin(code: number | null, stderr: string): boolean {
	return (code === 2 && /No such remote/.test(stderr)) || (code === 128 && /not a git repository/.test(stderr));
}

/** owner/name of the clone or worktree at repo from its origin remote, else null; throws OriginError when origin cannot be read. */
export async function repoSlug(repo: string): Promise<string | null> {
	const url = await originUrl(repo);
	return url === null ? null : parseOrigin(url);
}

/** Directory of the record for owner/name; it may not exist. */
export function recordDir(slug: string, root: string = configRoot()): string {
	const [owner = "", name = ""] = slug.toLowerCase().split("/");
	return join(root, RECORDS_DIR, owner, name);
}

export function minimalText(slug: string | null, reason: string): string {
	return `dynaflows minimal mode for ${slug ?? "none"}: ${reason}`;
}

/** The rails' words when the repository's mode cannot be resolved: never minimal mode. */
export function unresolvedText(error: OriginError): string {
	return `Agent rails: could not resolve this repository's dynaflows mode: ${error.message}; retry.`;
}

/**
 * The clone's slug and record directory, or the reason it has no record: no origin remote, an
 * origin that is not a GitHub owner/name URL, or no gate.json at the record directory. An origin
 * remote that cannot be read throws OriginError: that is not "no origin remote".
 */
export async function resolveRecord(repo: string, root: string = configRoot()): Promise<{ slug: string | null; dir: string | null; reason?: string }> {
	const url = await originUrl(repo);
	if (url === null) return { slug: null, dir: null, reason: "no origin remote" };
	const slug = parseOrigin(url);
	if (slug === null) return { slug: null, dir: null, reason: `origin ${url.trim()} is not a GitHub owner/name URL` };
	const dir = recordDir(slug, root);
	const gate = join(dir, GATE_FILE);
	const found = await stat(gate).then(info => info.isFile(), () => false);
	return found ? { slug, dir } : { slug, dir: null, reason: `no per-repo record at ${gate}` };
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
export const MERGE_METHODS = ["rebase", "merge"] as const;
export type MergeMethod = typeof MERGE_METHODS[number];
const afterMergeSchema = z.object({ argv: argvSchema, paths: z.array(nonEmpty).min(1).optional() }).strict();
const mergePolicySchema = z.object({ base: nonEmpty, headPrefix: z.string(), method: z.enum(MERGE_METHODS), proof: z.boolean() }).strict();
export type MergePolicy = z.infer<typeof mergePolicySchema>;
/** The policy for a PR with no matching mergePolicies entry. */
export const DEFAULT_MERGE_POLICY: Omit<MergePolicy, "base" | "headPrefix"> = { method: "rebase", proof: true };
// The per-repo record's gate.json; repo_record.py's gate_reason is its Python twin. Every field
// is current: an unknown field, at the top level or in `checks`, is rejected by name.
const gateSchema = z.object({
	schemaVersion: z.literal(2),
	checks: z.object({
		commit: argvSchema, ci: argvSchema.optional(), audit: argvSchema.optional(), format: argvSchema.optional(),
		profile: argvSchema.optional(),
	}).strict(),
	agentMerge: z.enum(["allowed", "disabled"]).optional(),
	ci: z.object({ requiredContext: nonEmpty, runner: nonEmpty }).optional(),
	afterMerge: afterMergeSchema.optional(),
	mergePolicies: z.array(mergePolicySchema).min(1).optional(),
}).strict().transform(gate => ({
	commit: gate.checks.commit,
	ciCheck: gate.checks.ci,
	agentMerge: gate.agentMerge,
	requiredCi: gate.ci,
	afterMerge: gate.afterMerge,
	mergePolicies: gate.mergePolicies ?? [],
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
