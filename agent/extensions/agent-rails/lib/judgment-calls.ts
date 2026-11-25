// Judgment calls as the decision store holds them: engine/workflows.py decide(), structure_review()
// and the rail bridge write one decisions row per question answered; a call is the rows sharing a call_id.
import { existsSync } from "node:fs";
import { z } from "@oh-my-pi/pi-coding-agent";
import { storePath, withStore } from "./decision-store.ts";

const sha256 = z.string().regex(/^[0-9a-f]{64}$/);
const probability = z.number().min(0).max(1);
const OUTCOMES = { flag: "yes", pass: "no", escalate: "escalate" } as const;
/** One registry call as the bridge returns it and the store reconstructs it. */
export const judgmentCallSchema = z.object({
	call_id: z.string().regex(/^[0-9a-f]{32}$/),
	judgment: z.string().regex(/^[a-z0-9]+(?:-[a-z0-9]+)*$/),
	state_sha256: sha256,
	model: z.string().min(1),
	question_version: sha256,
	probabilities: z.record(z.string(), z.union([probability, z.record(z.string(), probability)])),
	outcomes: z.record(z.string(), z.enum(["yes", "no", "escalate"])),
	modes: z.record(z.string(), z.enum(["enforced", "shadow"])),
	timestamp: z.string().min(1),
}).strict();
export type JudgmentCall = z.infer<typeof judgmentCallSchema>;

type Row = {
	seq: number; call_id: string; judgment: string; state_sha256: string; model: string; judgment_version: string; question: string;
	probability: number | null; choice_probabilities: string | null; outcome: keyof typeof OUTCOMES | null; mode: string; ts: string;
};
// Complete calls only: a call with an error or incomplete row never produced an outcome.
const CALLS = `SELECT rowid AS seq, call_id, judgment, state_sha256, model, judgment_version, question, probability, choice_probabilities, outcome, mode, ts
FROM decisions WHERE source = 'jev' AND status = 'ok'
AND call_id NOT IN (SELECT call_id FROM decisions WHERE source = 'jev' AND status != 'ok')`;

function calls(rows: Row[]): JudgmentCall[] {
	const grouped = new Map<string, Row[]>();
	for (const row of rows) grouped.set(row.call_id, [...grouped.get(row.call_id) ?? [], row]);
	return [...grouped.values()].map(group => {
		const [first] = group;
		if (!first) throw new Error("the decision store returned an empty call");
		const parsed = judgmentCallSchema.safeParse({
			call_id: first.call_id, judgment: first.judgment, state_sha256: first.state_sha256, model: first.model,
			question_version: first.judgment_version, timestamp: first.ts,
			probabilities: Object.fromEntries(group.map(row => [row.question, row.choice_probabilities === null ? row.probability : JSON.parse(row.choice_probabilities)])),
			outcomes: Object.fromEntries(group.flatMap(row => row.outcome === null ? [] : [[row.question, OUTCOMES[row.outcome]]])),
			modes: Object.fromEntries(group.map(row => [row.question, row.mode])),
		});
		if (!parsed.success) throw new Error(`decision store call ${first.call_id} is not a valid decide() call: ${parsed.error.message}`);
		return parsed.data;
	});
}

/** Every complete logged call, oldest first; a store not yet created holds no calls. */
export function readJudgmentCalls(path: string = storePath()): JudgmentCall[] {
	if (!existsSync(path)) return [];
	return calls(withStore(db => db.query<Row, []>(`${CALLS} ORDER BY ts, rowid`).all(), path));
}

/** The newest logged call of `judgment` on the state with digest `stateSha256` at question version `questionVersion`. */
export function latestJudgmentCall(
	judgment: string, stateSha256: string, questionVersion: string, path: string = storePath(),
): JudgmentCall | undefined {
	if (!existsSync(path)) return undefined;
	const rows = withStore(db => {
		const latest = db.query<{ call_id: string }, [string, string, string]>(
			`SELECT call_id FROM (${CALLS}) WHERE judgment = ? AND state_sha256 = ? AND judgment_version = ? ORDER BY ts DESC, seq DESC LIMIT 1`,
		).get(judgment, stateSha256, questionVersion);
		return latest ? db.query<Row, [string]>(`${CALLS} AND call_id = ? ORDER BY rowid`).all(latest.call_id) : [];
	}, path);
	return calls(rows)[0];
}
