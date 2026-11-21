// Rails-owned trust: the omp settings the rails pin, the paths no agent tool may write, the eval
// cells the rails refuse, the proof evidence only prove() and structure_review() runs the rails saw
// may supply, and the rail state (mode file, origin) seen at session start. Records are agent-writable
// at any time (ADR 0001); the rails re-read them on every action.
import { createHash } from "node:crypto";
import { readdir, readFile, stat } from "node:fs/promises";
import { homedir } from "node:os";
import { isAbsolute, join, relative, sep } from "node:path";
import { z } from "@oh-my-pi/pi-coding-agent";
import { lookup } from "@oh-my-pi/pi-coding-agent/config/registry";
import { absentFile, canonicalPath, mentionsGit, repoRoot } from "./rails-runtime.ts";

// checks.commit plus the harness checks get this long; omp's tool_call budget must exceed it.
const COMMIT_CHECKS_TIMEOUT_MS = 600_000;
const TOOL_CALL_TIMEOUT_MS = COMMIT_CHECKS_TIMEOUT_MS + 60_000;
// omp routes that land commits or pushes without a bash call: the eval commit prelude, the github
// tool's pr_push, and isolated tasks merged back as branches (cherry-picked without hooks).
const PINNED: [string, boolean | string][] = [["commit.enabled", false], ["github.enabled", false], ["task.isolation.merge", "patch"]];

type SettingsScope = Parameters<NonNullable<ReturnType<typeof lookup>>["get"]>[0];

function setting(id: string): NonNullable<ReturnType<typeof lookup>> {
	const found = lookup(id);
	if (!found) throw new Error(`Agent rails: omp setting ${id} is unavailable`);
	return found;
}

function pinSettings(settings: SettingsScope): void {
	for (const [id, value] of PINNED) setting(id).override(settings, value);
	const timeout = setting("extensionHandlers.toolCallTimeoutMs");
	if (z.number().catch(0).parse(timeout.get(settings)) < TOOL_CALL_TIMEOUT_MS) timeout.override(settings, TOOL_CALL_TIMEOUT_MS);
}

// modesFile: the tracked per-repo mode file; reviewsFile: the restart-review acknowledgements.
type Protection = { modesFile: string; reviewsFile: string; extensionDir: string; harnessConfig: string };

function protectedRoots(protection: Protection): string[] {
	const home = homedir();
	return [protection.modesFile, protection.reviewsFile, protection.extensionDir, protection.harnessConfig,
		join(home, ".gitconfig"), join(home, ".config/git/config"), join(home, ".config/gh/config.yml")];
}

const inside = (path: string, root: string) => {
	const rel = relative(root, path);
	return rel === "" || !(rel === ".." || rel.startsWith(`..${sep}`) || isAbsolute(rel));
};

// Why an agent tool may not write path: Git internals (hooks, config, refs, proof evidence), the
// mode file, the review state, the live extension, the harness config, or Git and gh config.
function protectedPathReason(path: string, protection: Protection): string | undefined {
	const forms = [path, canonicalPath(path)];
	if (forms.some(form => form.split(sep).includes(".git"))) {
		return `Agent rails: ${path} is inside Git's internal directory (hooks, config, refs and proof evidence); agents never write it.`;
	}
	const root = protectedRoots(protection).find(candidate => forms.some(form => inside(form, candidate) || inside(form, canonicalPath(candidate))));
	return root ? `Agent rails: ${path} is rail state (${root}); only the owner changes it.` : undefined;
}

const escape = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

// Text naming rail state: Git internals and proof evidence anywhere, Git and gh config, and the
// mode file, review state, extension and harness config by absolute or ~/ path.
function markerPattern(protection: Protection): RegExp {
	const home = homedir();
	const roots = protectedRoots(protection).flatMap(root => root.startsWith(`${home}/`) ? [root, `~${root.slice(home.length)}`] : [root]);
	return new RegExp(`(?:^|[\\s'"=:/(])\\.git(?:/|$|[\\s'";)])|omp-prove|omp-structure|dynaflows-reviews\\.json|dynaflows/modes\\.json|\\.gitconfig|git/config\\b|gh/config\\.yml|${roots.map(escape).join("|")}`, "m");
}

const SHELL_WRITES = /(?<!<)>(?!&)|\b(?:tee|cp|mv|rm|ln|install|rsync|dd|truncate|chmod|chown|touch|mkdir|unlink|shred|patch|python[\d.]*|node|bun|perl|ruby|deno)\b|-delete\b|\bsed\s+(?:-[a-zA-Z]*i|--in-place)/;

function shellStateReason(command: string, protection: Protection): string | undefined {
	return markerPattern(protection).test(command) && SHELL_WRITES.test(command)
		? "Agent rails: this command writes near rail state (Git internals, proof evidence, the mode file, the review state, the extension or Git/gh config); only the owner changes it."
		: undefined;
}

const EVAL_PROCESS = /\bsh\s*\(|\bsubprocess\b|\bos\s*\.\s*(?:system|popen|exec\w*|spawn\w*|posix_spawn\w*|fork\w*)\b|\bPopen\b|\bpty\b|\bBun\s*\.\s*(?:spawn\w*|\$)|\bchild_process\b|\$`|\bexecSync\b|\bspawnSync\b|\bexecFile\w*|^\s*!|%%?(?:sx|system|bash|sh|script|run|sc)\b|\bget_ipython\b|\bgetoutput\b/m;
const EVAL_WRITES = /\b(?:open|write_text|write_bytes|writeFile\w*|appendFile\w*|unlink\w*|rename\w*|remove\w*|rmtree|rmdir|shutil|copy\w*|move|symlink\w*|chmod|truncate|replace|mkdir|makedirs|touch|createWriteStream|write)\b/;
const PROOF_NAMES = "prove|structure_review|result_path|freeze|deterministic_checks|_write_result|_publish_result|_atomic_json|_common";
const EVAL_TAMPER = new RegExp(`\\b(?:def|class|async\\s+def)\\s+(?:${PROOF_NAMES})\\b|\\b(?:${PROOF_NAMES})\\s*=(?!=)|\\bsetattr\\s*\\(|\\bglobals\\s*\\(|__builtins__|\\bbuiltins\\b|\\bsys\\s*\\.\\s*modules\\b|\\breload\\s*\\(`);
const EVAL_COMMIT = /\bcommit\s*\.\s*draft\b|\bno_verify\b|\bnoVerify\b/;

// Why an eval cell is refused: the commit prelude, a process launch naming git/gh/hub/omp, a write
// near rail state, or a redefinition of the proof workflow the rails trust.
function evalReason(code: string, protection: Protection): string | undefined {
	if (EVAL_COMMIT.test(code)) return "Agent rails: the eval commit namespace commits outside the rails; commit with git commit in the bash tool.";
	if (EVAL_PROCESS.test(code) && mentionsGit(code)) return "Agent rails: eval may not run git, gh, hub or omp; run them with the bash tool so the rails check them.";
	if (markerPattern(protection).test(code) && (EVAL_WRITES.test(code) || EVAL_PROCESS.test(code))) {
		return "Agent rails: eval may not write rail state (Git internals, proof evidence, the mode file, the review state, the extension or Git/gh config).";
	}
	return EVAL_TAMPER.test(code) ? "Agent rails: eval may not redefine the proof workflow or reach builtins; call prove() and structure_review() as loaded." : undefined;
}

const PROOF_CALL = /\b(?:prove|structure_review)\s*\(/;
const EVIDENCE_DIRS = ["omp-prove", "omp-structure"];
const MTIME_SLACK_MS = 2000;
const digest = (text: string) => createHash("sha256").update(text).digest("hex");

// Proof evidence the rails trust: files under <git-common-dir>/omp-prove and omp-structure that an
// eval cell calling prove() or structure_review() wrote while the rails watched. Files written any
// other way, or in another omp process, are not evidence.
class Evidence {
	readonly #pending = new Map<string, { start: number; roots: string[] }>();
	readonly #trusted = new Map<string, Set<string>>();

	async begin(toolCallId: string, code: string, cwd: string): Promise<void> {
		if (!PROOF_CALL.test(code)) return;
		const literals = [...code.matchAll(/["'](\/[^"'\n]+)["']/g)].map(match => match[1] ?? "");
		const roots = new Set<string>();
		for (const path of [cwd, ...literals]) {
			const root = await repoRoot(path).catch(() => undefined);
			if (root) roots.add(root);
		}
		this.#pending.set(toolCallId, { start: Date.now() - MTIME_SLACK_MS, roots: [...roots] });
	}

	async end(toolCallId: string, commonDir: (root: string) => Promise<string>): Promise<void> {
		const pending = this.#pending.get(toolCallId);
		this.#pending.delete(toolCallId);
		if (!pending) return;
		for (const root of pending.roots) {
			const common = await commonDir(root);
			for (const name of EVIDENCE_DIRS) await this.#capture(join(common, name), pending.start);
		}
	}

	async #capture(dir: string, start: number): Promise<void> {
		const names = await readdir(dir).catch(absentFile) ?? [];
		for (const name of names) {
			const path = join(dir, name);
			if ((await stat(path)).mtimeMs < start) continue;
			const trusted = this.#trusted.get(path) ?? new Set();
			trusted.add(digest(await readFile(path, "utf8")));
			this.#trusted.set(path, trusted);
		}
	}

	trusts(path: string, text: string): boolean {
		return this.#trusted.get(path)?.has(digest(text)) === true;
	}
}

// Rail state seen at session start (the mode file and the session repo's origin): a change
// during the session, other than through the rails' own mode switch, refuses
// every git and gh action until the owner restores it or restarts omp.
class Snapshot {
	readonly #seen = new Map<string, string>();
	readonly #readers = new Map<string, () => Promise<string>>();

	async capture(key: string, read: () => Promise<string>): Promise<void> {
		this.#readers.set(key, read);
		this.#seen.set(key, await read());
	}

	accept(key: string, value: string): void {
		if (this.#seen.has(key)) this.#seen.set(key, value);
	}

	async driftReason(): Promise<string | undefined> {
		for (const [key, read] of this.#readers) {
			if (await read() !== this.#seen.get(key)) {
				return `Agent rails: ${key} changed during this session; rails trust the state seen at session start. Restore it, or the owner restarts omp after changing it.`;
			}
		}
		return undefined;
	}
}

async function fileState(path: string): Promise<string> {
	const text = await readFile(path, "utf8").catch(absentFile);
	return text === null ? "absent" : digest(text);
}


export { COMMIT_CHECKS_TIMEOUT_MS, TOOL_CALL_TIMEOUT_MS, Evidence, Snapshot, evalReason, fileState, pinSettings, protectedPathReason, setting, shellStateReason };
export type { Protection };
