import { readFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";
import { z } from "@oh-my-pi/pi-coding-agent";

// The log engine/workflows.py decide() and structure_review() append one JSON line to per judgment call.
const JUDGMENT_CALLS = "dynaflows/judgment-calls.jsonl";
const sha256 = z.string().regex(/^[0-9a-f]{64}$/);
const probability = z.number().min(0).max(1);
export const judgmentCallSchema = z.object({
	judgment: z.string().regex(/^[a-z0-9]+(?:-[a-z0-9]+)*$/),
	state_sha256: sha256,
	model: z.string().min(1),
	question_version: sha256,
	probabilities: z.record(z.string(), z.union([probability, z.record(z.string(), probability)])),
	outcomes: z.record(z.string(), z.enum(["yes", "no", "escalate"])),
	timestamp: z.string().min(1),
}).strict();
export type JudgmentCall = z.infer<typeof judgmentCallSchema>;

/** The call log under the omp agent dir: PI_CODING_AGENT_DIR, else ~/.omp/agent, as the engine resolves it. */
export function judgmentCallLog(agentDir: string | undefined = process.env["PI_CODING_AGENT_DIR"]): string {
	return join(agentDir?.trim() || join(homedir(), ".omp", "agent"), JUDGMENT_CALLS);
}

/** Every logged call, oldest first; a missing log is no calls, and a malformed line throws naming its number. */
export async function readJudgmentCalls(path: string = judgmentCallLog()): Promise<JudgmentCall[]> {
	let text: string;
	try { text = await readFile(path, "utf8"); }
	catch (error) {
		if (error instanceof Error && "code" in error && error.code === "ENOENT") return [];
		throw error;
	}
	return text.split("\n").flatMap((line, index) => {
		if (!line.trim()) return [];
		const parsed = judgmentCallSchema.safeParse((() => { try { return JSON.parse(line); } catch { return undefined; } })());
		if (!parsed.success) throw new Error(`judgment call log ${path} line ${index + 1} is not a valid decide() record`);
		return [parsed.data];
	});
}

/** The newest logged call of `judgment` on the state with digest `stateSha256` at question version `questionVersion`. */
export async function latestJudgmentCall(
	judgment: string, stateSha256: string, questionVersion: string, path: string = judgmentCallLog(),
): Promise<JudgmentCall | undefined> {
	return (await readJudgmentCalls(path)).findLast(call =>
		call.judgment === judgment && call.state_sha256 === stateSha256 && call.question_version === questionVersion);
}
