// Handoffs between sessions: <worktree root>/HANDOFF.md, excluded locally, never committed.
import { appendFile, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { type ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import { SUBPROCESS_TIMEOUT_MS, absentFile } from "./rails-runtime.ts";

type ExecApi = Pick<ExtensionAPI, "exec">;
export const HANDOFF_FILE = "HANDOFF.md";

async function gitOutput(pi: ExecApi, cwd: string, args: string[]): Promise<string> {
	const result = await pi.exec("git", args, { cwd, timeout: SUBPROCESS_TIMEOUT_MS });
	if (result.code !== 0 || result.killed) throw new Error(`git ${args.join(" ")} failed: ${result.stderr.trim()}`);
	return result.stdout;
}

async function ensureExcluded(pi: ExecApi, root: string): Promise<void> {
	const path = resolve(root, (await gitOutput(pi, root, ["rev-parse", "--git-path", "info/exclude"])).trim());
	const lines = ((await readFile(path, "utf8").catch(absentFile)) ?? "").split("\n").map(line => line.trim());
	if (lines.includes(HANDOFF_FILE) || lines.includes(`/${HANDOFF_FILE}`)) return;
	await mkdir(dirname(path), { recursive: true });
	const text = lines.join("\n");
	await appendFile(path, `${text && !text.endsWith("\n") ? "\n" : ""}/${HANDOFF_FILE}\n`);
}

/** Writes the handoff at the worktree root and keeps it out of Git through info/exclude. */
export async function writeHandoff(pi: ExecApi, root: string, content: string): Promise<string> {
	await ensureExcluded(pi, root);
	const path = join(root, HANDOFF_FILE);
	await writeFile(path, content.endsWith("\n") ? content : `${content}\n`);
	return path;
}

export async function readHandoff(root: string): Promise<string | null> {
	return readFile(join(root, HANDOFF_FILE), "utf8").catch(absentFile);
}

/** Deletes the handoff only while it still holds what the session was given. */
export async function consumeHandoff(root: string, content: string): Promise<void> {
	if (await readHandoff(root) === content) await rm(join(root, HANDOFF_FILE));
}

export async function stagedHandoffReason(pi: ExecApi, cwd: string): Promise<string | undefined> {
	const staged = (await gitOutput(pi, cwd, ["diff", "--cached", "--name-only", "-z"])).split("\0");
	return staged.includes(HANDOFF_FILE)
		? `Agent rails: ${HANDOFF_FILE} is staged; it is a session handoff, never committed. Run git rm --cached ${HANDOFF_FILE}, then commit.`
		: undefined;
}
