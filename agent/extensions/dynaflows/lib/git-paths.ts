// Git locations the rails share: a repository's common dir, its main checkout, its GitHub origin
// and the info/exclude file every worktree of the repository reads.
import { appendFile, mkdir, readFile, realpath } from "node:fs/promises";
import { basename, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { type ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import { RailError } from "./rails-ledger.ts";
import { SUBPROCESS_TIMEOUT_MS, absentFile } from "./rails-runtime.ts";

export type ExecApi = Pick<ExtensionAPI, "exec">;

// The three GitHub origin forms. "." and ".." are refused so a slug is always one owner/name pair.
const ORIGIN = /^(?:https:\/\/github\.com\/|git@github\.com:|ssh:\/\/git@github\.com\/)([A-Za-z0-9_.-]+)\/([A-Za-z0-9_.-]+?)(?:\.git)?\/?$/;
const ORIGIN_TIMEOUT_MS = 20_000;

/**
 * The origin remote could not be read: git was killed, timed out, could not start, or failed for
 * a reason other than a missing remote or a path outside a Git repository, or the repository's
 * agent-rails config could not be resolved. The rails' scope is then unknown; callers refuse writes
 * and never read it as "no origin".
 */
export class OriginError extends Error {}

/** The oh-mi-pi-config checkout this extension belongs to, so no home-directory path is in code. */
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

/** The rails' words when the repository's origin or agent-rails config cannot be resolved: git and gh writes refuse. */
export function unresolvedText(reason: string): string {
	return `Agent rails: could not resolve this repository's agent-rails config: ${reason}; retry.`;
}

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
