// Review on restart: each repository stores the last dynaflows extension revision a session
// acknowledged, under the omp agent dir. A newer revision asks for a review of the change first.
import { createHash, randomUUID } from "node:crypto";
import { readdir, readFile, rename, writeFile } from "node:fs/promises";
import { join, relative } from "node:path";
import { z, type ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import { SUBPROCESS_TIMEOUT_MS, absentFile } from "./rails-runtime.ts";

type ExecApi = Pick<ExtensionAPI, "exec">;
export type ImpactInput = { slug: string; from: string; to: string; diff: string | undefined; checks: string };
/** The decision seam the judge package fills; until it does, every change is surfaced. */
export type Decide = (judgment: "extension-impact", input: ImpactInput) => Promise<"proceed" | "escalate">;
export const escalate: Decide = async () => "escalate";
// Not extension behaviour: the mode file changes on every mode switch.
const IGNORED = new Set(["node_modules", "__pycache__", ".venv", "build", "modes.json"]);
const reviewsSchema = z.object({ schemaVersion: z.literal(1), repos: z.record(z.string(), z.string().min(1)) }).strict();

async function gitText(pi: ExecApi, cwd: string, args: string[]): Promise<string | undefined> {
	const result = await pi.exec("git", args, { cwd, timeout: SUBPROCESS_TIMEOUT_MS });
	return result.code === 0 && !result.killed ? result.stdout.trim() : undefined;
}

async function contentHash(dir: string): Promise<string> {
	const hash = createHash("sha256");
	const walk = async (at: string): Promise<void> => {
		const entries = (await readdir(at, { withFileTypes: true })).toSorted((left, right) => left.name.localeCompare(right.name));
		for (const entry of entries) {
			if (IGNORED.has(entry.name)) continue;
			const path = join(at, entry.name);
			if (entry.isDirectory()) await walk(path);
			else if (entry.isFile()) hash.update(`${relative(dir, path)}\0`).update(await readFile(path)).update("\0");
		}
	};
	await walk(dir);
	return `sha256:${hash.digest("hex")}`;
}

/** git:<last commit touching the extension> in a Git config root, else sha256:<content hash>. */
export async function extensionRevision(pi: ExecApi, configRoot: string, extensionDir: string): Promise<string> {
	const top = await gitText(pi, configRoot, ["rev-parse", "--show-toplevel"]);
	if (top !== undefined) {
		const path = relative(top, extensionDir);
		const sha = await gitText(pi, top, ["log", "-1", "--format=%H", "--", path, `:(exclude)${path}/modes.json`]);
		if (sha) return `git:${sha}`;
	}
	return contentHash(extensionDir);
}

/** The extension-path diff between two git revisions, when both are git revisions. */
export async function extensionDiff(pi: ExecApi, configRoot: string, extensionDir: string, range: { from: string; to: string }): Promise<string | undefined> {
	const [from, to] = [range.from, range.to].map(revision => revision.startsWith("git:") ? revision.slice(4) : undefined);
	if (!from || !to) return undefined;
	const top = await gitText(pi, configRoot, ["rev-parse", "--show-toplevel"]);
	if (top === undefined) return undefined;
	const path = relative(top, extensionDir);
	return gitText(pi, top, ["diff", "--stat", from, to, "--", path, `:(exclude)${path}/modes.json`]);
}

export async function storedRevision(path: string, slug: string): Promise<string | undefined> {
	const text = await readFile(path, "utf8").catch(absentFile);
	if (text === null) return undefined;
	const parsed = reviewsSchema.safeParse(JSON.parse(text));
	if (!parsed.success) throw new Error(`the review state ${path} is invalid: ${parsed.error.message}`);
	return parsed.data.repos[slug];
}

export async function storeRevision(path: string, slug: string, revision: string): Promise<void> {
	const text = await readFile(path, "utf8").catch(absentFile);
	const repos = text === null ? {} : reviewsSchema.parse(JSON.parse(text)).repos;
	const temp = `${path}.${randomUUID()}.tmp`;
	await writeFile(temp, `${JSON.stringify({ schemaVersion: 1, repos: { ...repos, [slug]: revision } }, null, 2)}\n`);
	await rename(temp, path);
}
