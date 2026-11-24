// Git locations the rails share: a repository's common dir, its main checkout, its GitHub origin
// and the info/exclude file every worktree of the repository reads.
import { appendFile, mkdir, readFile, realpath } from "node:fs/promises";
import { basename, dirname, resolve } from "node:path";
import { type ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import { RailError } from "./rails-ledger.ts";
import { SUBPROCESS_TIMEOUT_MS, absentFile } from "./rails-runtime.ts";
import { parseOrigin } from "./repo-record.ts";

export type ExecApi = Pick<ExtensionAPI, "exec">;

/** The directory is in no Git repository. */
export class OutsideRepository extends Error {}

/** The repository's Git common dir, real path: the same for every worktree of one repository. */
export async function commonDir(pi: ExecApi, cwd: string): Promise<string> {
	const result = await pi.exec("git", ["rev-parse", "--path-format=absolute", "--git-common-dir"], { cwd, timeout: SUBPROCESS_TIMEOUT_MS });
	if (result.code === 128 && result.stderr.includes("not a git repository")) throw new OutsideRepository(`${cwd} is not in a git repository`);
	if (result.code !== 0 || result.killed || !result.stdout.trim()) throw new Error(`cannot resolve the Git common dir of ${cwd}: ${result.stderr.trim()}`);
	return realpath(result.stdout.trim());
}

/** The main checkout: the common dir's parent. A bare repository or a separate git dir has none. */
export async function mainCheckout(pi: ExecApi, cwd: string): Promise<string> {
	const common = await commonDir(pi, cwd);
	if (basename(common) !== ".git") {
		throw new RailError("config-unresolved",
			`the Git common dir ${common} is not a checkout's .git directory; a bare repository or a separate git dir has no main checkout`);
	}
	return dirname(common);
}

/**
 * The lowercase owner/name of the origin remote, or null when there is no origin remote or it is
 * not a GitHub URL. Any other git failure throws.
 */
export async function originSlug(pi: ExecApi, cwd: string): Promise<string | null> {
	const result = await pi.exec("git", ["remote", "get-url", "origin"], { cwd, timeout: SUBPROCESS_TIMEOUT_MS });
	if (result.code === 0 && !result.killed) return parseOrigin(result.stdout);
	if (result.code === 2 && !result.killed && result.stderr.includes("No such remote")) return null;
	throw new Error(`git remote get-url origin in ${cwd} failed: ${result.stderr.trim()}`);
}

/**
 * Appends line once to the repository's info/exclude. git resolves the file through the common
 * dir, so one write covers every worktree of the repository.
 */
export async function ensureExcluded(pi: ExecApi, cwd: string, line: string): Promise<void> {
	const result = await pi.exec("git", ["rev-parse", "--path-format=absolute", "--git-path", "info/exclude"], { cwd, timeout: SUBPROCESS_TIMEOUT_MS });
	if (result.code !== 0 || result.killed || !result.stdout.trim()) throw new Error(`cannot resolve info/exclude for ${cwd}: ${result.stderr.trim()}`);
	const path = resolve(cwd, result.stdout.trim());
	const text = (await readFile(path, "utf8").catch(absentFile)) ?? "";
	if (text.split("\n").some(entry => entry.trim() === line)) return;
	await mkdir(dirname(path), { recursive: true });
	await appendFile(path, `${text && !text.endsWith("\n") ? "\n" : ""}${line}\n`);
}
