// The per-repo dynaflows mode: the one source of truth is modes.json in this extension, keyed by
// the lowercase GitHub owner/name of the clone's origin. A repository it does not list runs minimal.
import { randomUUID } from "node:crypto";
import { readFile, rename, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { z } from "@oh-my-pi/pi-coding-agent";

export const MODES = ["minimal", "structured"] as const;
export type Mode = typeof MODES[number];
const SLUG = /^[a-z0-9_.-]+\/[a-z0-9_.-]+$/;
const modesSchema = z.object({
	schemaVersion: z.literal(1),
	repos: z.record(z.string().regex(SLUG), z.enum(MODES)),
}).strict();
export const MODES_REQUIREMENT = '{"schemaVersion": 1, "repos": {"<owner>/<name>": "minimal"|"structured"}}';

export function defaultModesPath(): string {
	return fileURLToPath(new URL("../modes.json", import.meta.url));
}

async function readModes(path: string): Promise<Map<string, Mode>> {
	let parsed;
	try { parsed = modesSchema.safeParse(JSON.parse(await readFile(path, "utf8"))); }
	catch (error) { throw new Error(`the mode file ${path} cannot be read: ${error instanceof Error ? error.message : String(error)}`, { cause: error }); }
	if (!parsed.success) throw new Error(`the mode file ${path} is invalid: it must be ${MODES_REQUIREMENT}`);
	return new Map(Object.entries(parsed.data.repos));
}

/** The mode modes.json gives owner/name; an unlisted repository is minimal. */
export async function repoMode(path: string, slug: string): Promise<Mode> {
	return (await readModes(path)).get(slug.toLowerCase()) ?? "minimal";
}

/** Sets owner/name's mode; minimal removes the entry, since an unlisted repository is minimal. */
export async function writeMode(path: string, slug: string, mode: Mode): Promise<void> {
	const repos = await readModes(path);
	if (mode === "minimal") repos.delete(slug.toLowerCase());
	else repos.set(slug.toLowerCase(), mode);
	const sorted = Object.fromEntries([...repos].toSorted(([left], [right]) => left.localeCompare(right)));
	const temp = `${path}.${randomUUID()}.tmp`;
	await writeFile(temp, `${JSON.stringify({ schemaVersion: 1, repos: sorted }, null, 2)}\n`);
	await rename(temp, path);
}
