// End-of-turn obligations: work a structured-mode session must finish before it settles. At each
// turn end the rails re-check every obligation; an unmet one sends a follow-up turn naming it, at most
// MAX_FOLLOW_UPS times, after which the owner is told and the rails stop asking.
export const MAX_FOLLOW_UPS = 3;
/** met: done, dropped; unmet: ask for it now; waiting: keep, but nothing to ask yet (e.g. CI running). */
export type ObligationStatus = "met" | "unmet" | "waiting";
export type Obligation = {
	id: string;
	/** The repository the obligation belongs to; obligations apply only while it is in structured mode. */
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

/** Whether a successful shell command ran `expected` as one whole command of its own. */
export function ranCommand(command: string, expected: string[]): boolean {
	return command.split(/\s*(?:&&|\|\||;|\n)\s*/).some(part => expected.includes(part.trim()));
}
