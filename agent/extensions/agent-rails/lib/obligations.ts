import { homedir } from "node:os";
import { resolve } from "node:path";
// End-of-turn obligations: work a session in a governed repository must finish before it settles. At each
// turn end the rails re-check every obligation; an unmet one sends a follow-up turn naming it, at most
// MAX_FOLLOW_UPS times, after which the owner is told and the rails stop asking.
export const MAX_FOLLOW_UPS = 3;
/** met: done, dropped; unmet: ask for it now; waiting: keep, but nothing to ask yet (e.g. CI running). */
export type ObligationStatus = "met" | "unmet" | "waiting";
export type Obligation = {
	id: string;
	/** The repository the obligation belongs to; obligations apply only while it is governed (a valid config). */
	root: string;
	text: string;
	status(): Promise<ObligationStatus>;
};
/** The hook later judgments (completion evidence) use: a source returns its obligations at each turn end. */
export type ObligationSource = (sessionId: string) => Promise<Obligation[]>;
export type Notify = (text: string) => void;

export class ObligationBook {
	#items = new Map<string, Obligation>();
	#asked = new Map<string, number>();

	add(obligation: Obligation): void {
		this.#items.set(obligation.id, obligation);
		this.#asked.delete(obligation.id);
	}

	satisfy(id: string): void {
		this.#items.delete(id);
	}

	ids(): string[] {
		return [...this.#items.keys()];
	}

	/** The follow-up reason for this turn end, or undefined when nothing is due. */
	async turnEnd(extra: Obligation[], applies: (root: string) => Promise<boolean>, notify: Notify): Promise<string | undefined> {
		const pending = new Map([...extra.map(item => [item.id, item] as const), ...this.#items]);
		const due: Obligation[] = [];
		for (const obligation of pending.values()) {
			if (!await applies(obligation.root)) continue;
			const status = await obligation.status();
			if (status === "met") this.satisfy(obligation.id);
			else if (status === "unmet" && this.#ask(obligation, notify)) due.push(obligation);
		}
		if (!due.length) return undefined;
		return `Agent rails: unmet obligations before this work ends: ${due.map(item => `[${item.id}] ${item.text}`).join("; ")}.`;
	}

	#ask(obligation: Obligation, notify: Notify): boolean {
		const asked = (this.#asked.get(obligation.id) ?? 0) + 1;
		this.#asked.set(obligation.id, asked);
		if (asked <= MAX_FOLLOW_UPS) return true;
		if (asked === MAX_FOLLOW_UPS + 1) {
			notify(`Agent rails: obligation [${obligation.id}] is still unmet after ${MAX_FOLLOW_UPS} follow-ups; the rails stop asking. ${obligation.text}`);
		}
		this.satisfy(obligation.id);
		return false;
	}
}

export function shellJoin(argv: string[]): string {
	return argv.map(word => /^[\w@%+=:,./~-]+$/.test(word) ? word : `'${word.replaceAll("'", "'\\''")}'`).join(" ");
}

/** A step the rails ask for: its argv, run from `root`. */
export type Step = { argv: string[]; root: string };
const TOKENS = /(?:[^\s;&|()"'\\#`$]+|\\.|"(?:\\[\\"]|[^"\\$`])*"|'[^']*')+|&&|\|\||[;&|()\n#`$]/g;
const UNQUOTE = /"((?:\\[\\"]|[^"\\])*)"|'([^']*)'|\\(.)/g;
const REDIRECT = /^\d*(?:>>?|&>>?|<)(.*)$/;
const unquoteWord = (word: string) => word.replace(UNQUOTE, (_match, double: string | undefined, single: string | undefined, escaped: string | undefined) =>
	double?.replace(/\\(["\\])/g, "$1") ?? single ?? escaped ?? "");
function expandHome(word: string): string {
	return word.replace(/^(?:~|\$HOME|\$\{HOME\})(?=\/|$)/, homedir());
}
const pathLike = (word: string) => word.includes("/") || word.startsWith("~");
function sameWord(actual: string, expected: string, dir: string, root: string): boolean {
	if (actual === expected) return true;
	return pathLike(actual) && pathLike(expected) && resolve(dir, expandHome(actual)) === resolve(root, expandHome(expected));
}
/** The command's words, or undefined when it is anything but simple commands joined by && (no ;, ||, |, &, comments or substitutions). */
function andParts(command: string): string[][] | undefined {
	const normalized = command.trim().replace(/\s+\d*>&\d+(?=\s|$)/g, "").replace(/\$\{HOME\}|\$HOME(?!\w)/g, homedir());
	const tokens = normalized.match(TOKENS) ?? [];
	if (tokens.join("").replace(/\s/g, "") !== normalized.replace(/\s/g, "")) return undefined;
	const parts: string[][] = [[]];
	for (const token of tokens) {
		if (token === "&&") parts.push([]);
		else if (/^(?:\|\||[;&|()\n#`$])$/.test(token)) return undefined;
		else parts.at(-1)?.push(token);
	}
	return parts.every(part => part.length) ? parts : undefined;
}
function withoutRedirections(words: string[]): string[] {
	const kept: string[] = [];
	for (let index = 0; index < words.length; index++) {
		const redirect = REDIRECT.exec(words[index] ?? "");
		if (!redirect) kept.push(words[index] ?? "");
		else if (!redirect[1]) index++;
	}
	return kept;
}
/**
 * Whether a shell command that exited 0 ran `step` as a whole command: the command is simple commands
 * joined by && (so exit 0 means each ran and exited 0), one of them is the step, optionally with
 * output redirections, after any `cd` parts. Words with a path compare as resolved paths.
 */
export function ranCommand(command: string, step: Step, cwd: string): boolean {
	const parts = andParts(command);
	if (!parts) return false;
	let dir = cwd;
	for (const part of parts) {
		const words = withoutRedirections(part.map(unquoteWord));
		if (words[0] === "cd" && words.length === 2) { dir = resolve(dir, expandHome(words[1] ?? "")); continue; }
		if (words.length === step.argv.length && words.every((word, index) => sameWord(word, step.argv[index] ?? "", dir, step.root))) return true;
	}
	return false;
}
