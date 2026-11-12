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

const dynaflowsSwitch = z.object({ dynaflows: z.object({ enabled: z.boolean() }) });

/** The record's gate.json text; consumers parse it with their own gate schema. */
export async function readGateText(record: string): Promise<string> {
	const path = join(record, GATE_FILE);
	try {
		return await readFile(path, "utf8");
	} catch (error) {
		throw new RecordError(`${path}: ${error instanceof Error ? error.message : String(error)}`);
	}
}

/** True only when the clone has a record whose dynaflows.enabled is true. */
export async function dynaflowsEnabled(repo: string, root: string = configRoot()): Promise<boolean> {
	const record = await findRecord(repo, root);
	if (record === null) return false;
	const path = join(record, GATE_FILE);
	let parsed;
	try {
		parsed = dynaflowsSwitch.safeParse(JSON.parse(await readGateText(record)));
	} catch (error) {
		throw error instanceof RecordError ? error : new RecordError(`${path}: ${error instanceof Error ? error.message : String(error)}`);
	}
	if (!parsed.success) throw new RecordError(`${path}: dynaflows must be {"enabled": true|false}`);
	return parsed.data.dynaflows.enabled;
}
