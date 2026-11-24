// Summarises the rails outcome ledger (rails-ledger.ts), the rail_events table of the decision
// store: refusals by code and by repository and mode, the share of refusals a later call
// satisfied, and the median tool calls to resolve.
//
//   bun agent/extensions/dynaflows/lib/ledger-report.ts [--since YYYY-MM-DD] [--store PATH]
//
// A rule whose refusals are nearly all satisfied within a call or two is friction: the agent
// retries and the outcome does not change. A rule whose refusals stay unsatisfied changed what
// the agent did, or stopped it.
import { existsSync } from "node:fs";
import { z } from "@oh-my-pi/pi-coding-agent";
import { storePath, withStore } from "./decision-store.ts";
import { MODES } from "./modes.ts";
import { LEDGER_KINDS } from "./rails-ledger.ts";

const rowSchema = z.object({
	id: z.number().int(),
	repo: z.string().nullable(),
	mode: z.enum(MODES).nullable(),
	kind: z.enum(LEDGER_KINDS),
	code: z.string().min(1),
	calls: z.number().int().refine(value => value >= 0).nullable(),
	refusals: z.number().int().positive().nullable(),
}).strict();
export type LedgerRow = z.infer<typeof rowSchema>;

export type CodeRow = {
	code: string; refusals: number; blocks: number; postEdit: number; satisfied: number; medianCalls: number | null;
};
export type GroupRow = { repo: string; mode: string; refusals: number; satisfied: number };
export type LedgerReport = {
	since: string | null; refusals: number; blocks: number; postEdit: number; satisfied: number; medianCalls: number | null;
	codes: CodeRow[]; groups: GroupRow[];
};

/** The rail_events rows of the store at path, from since (UTC midnight) on, in write order; an invalid row throws with its position. */
export function readLedger(path: string, since: Date | null = null): LedgerRow[] {
	if (!existsSync(path)) throw new Error(`no decision store at ${path}`);
	return withStore(db => db.query<unknown, { since: string }>(
		"SELECT id, repo, mode, kind, code, calls, refusals FROM rail_events WHERE ts >= $since ORDER BY id",
	).all({ since: since ? since.toISOString() : "" }).map((row, index) => {
		const parsed = rowSchema.safeParse(row);
		if (!parsed.success) throw new Error(`rail_events row ${index + 1} is invalid: ${parsed.error.message}`);
		return parsed.data;
	}), path);
}

function median(values: number[]): number | null {
	if (!values.length) return null;
	const sorted = values.toSorted((left, right) => left - right);
	const middle = Math.floor(sorted.length / 2);
	return sorted.length % 2 ? sorted[middle] ?? null : ((sorted[middle - 1] ?? 0) + (sorted[middle] ?? 0)) / 2;
}

type Tally = { refusals: number; blocks: number; postEdit: number; satisfied: number; calls: number[] };
const emptyTally = (): Tally => ({ refusals: 0, blocks: 0, postEdit: 0, satisfied: 0, calls: [] });

function count(tally: Tally, row: LedgerRow): void {
	if (row.kind === "resolved") {
		tally.satisfied += row.refusals ?? 0;
		if (row.calls !== null) tally.calls.push(row.calls);
		return;
	}
	tally.refusals++;
	if (row.kind === "block") tally.blocks++;
	else tally.postEdit++;
}

function tallied<K>(rows: LedgerRow[], key: (row: LedgerRow) => K): Map<K, Tally> {
	const tallies = new Map<K, Tally>();
	for (const row of rows) {
		const at = key(row);
		const tally = tallies.get(at) ?? emptyTally();
		count(tally, row);
		tallies.set(at, tally);
	}
	return tallies;
}

type Counted = { refusals: number; satisfied: number };
const order = (left: Counted, right: Counted) => right.refusals - left.refusals || right.satisfied - left.satisfied;

/** The report over rows already read for since. */
export function summarise(rows: LedgerRow[], since: Date | null = null): LedgerReport {
	const total = emptyTally();
	for (const row of rows) count(total, row);
	const codes = [...tallied(rows, row => row.code)].map(([code, tally]): CodeRow => ({
		code, refusals: tally.refusals, blocks: tally.blocks, postEdit: tally.postEdit, satisfied: tally.satisfied, medianCalls: median(tally.calls),
	}));
	const groups = [...tallied(rows, row => `${row.repo ?? "(none)"}\t${row.mode ?? "(unknown)"}`)].map(([key, tally]): GroupRow => {
		const [repo = "", mode = ""] = key.split("\t");
		return { repo, mode, refusals: tally.refusals, satisfied: tally.satisfied };
	});
	return {
		since: since ? since.toISOString().slice(0, 10) : null,
		refusals: total.refusals, blocks: total.blocks, postEdit: total.postEdit, satisfied: total.satisfied, medianCalls: median(total.calls),
		codes: codes.toSorted((left, right) => order(left, right) || left.code.localeCompare(right.code)),
		groups: groups.toSorted((left, right) => order(left, right) || left.repo.localeCompare(right.repo)),
	};
}

const percent = (part: number, whole: number) => whole ? `${Math.round((part / whole) * 100)}%` : "-";
const calls = (value: number | null) => value === null ? "-" : String(value);

// A plain-text table: the first `labels` columns are left-aligned names, the rest right-aligned numbers.
function table(header: string[], labels: number, rows: string[][]): string {
	const widths = header.map((title, column) => Math.max(title.length, ...rows.map(row => (row[column] ?? "").length)));
	const line = (cells: string[]) => cells.map((cell, column) => column < labels ? cell.padEnd(widths[column] ?? 0) : cell.padStart(widths[column] ?? 0)).join("  ").trimEnd();
	return [line(header), ...rows.map(line)].join("\n");
}

export function formatReport(report: LedgerReport, path: string): string {
	const scope = report.since ? ` since ${report.since}` : "";
	const summary = `${report.refusals} refusals (${report.blocks} blocks, ${report.postEdit} post-edit), ` +
		`${report.satisfied} later satisfied (${percent(report.satisfied, report.refusals)}), median ${calls(report.medianCalls)} calls to resolve`;
	const codes = table(["code", "refusals", "blocks", "post-edit", "satisfied", "rate", "median calls"], 1, report.codes.map(row => [
		row.code, String(row.refusals), String(row.blocks), String(row.postEdit), String(row.satisfied), percent(row.satisfied, row.refusals), calls(row.medianCalls),
	]));
	const groups = table(["repo", "mode", "refusals", "satisfied", "rate"], 2, report.groups.map(row => [
		row.repo, row.mode, String(row.refusals), String(row.satisfied), percent(row.satisfied, row.refusals),
	]));
	return `Rails ledger ${path}${scope}\n${summary}\n\n${codes}\n\n${groups}\n`;
}

type ReportArgs = { since: Date | null; store: string };

export function reportArgs(argv: string[]): ReportArgs {
	const args: ReportArgs = { since: null, store: storePath() };
	for (let index = 0; index < argv.length; index += 2) {
		const [flag, value] = [argv[index], argv[index + 1]];
		if (value === undefined) throw new Error(`${flag} needs a value`);
		if (flag === "--store") args.store = value;
		else if (flag === "--since" && /^\d{4}-\d{2}-\d{2}$/.test(value) && !Number.isNaN(Date.parse(value))) args.since = new Date(value);
		else throw new Error(`unknown argument ${flag} ${value}; usage: ledger-report.ts [--since YYYY-MM-DD] [--store PATH]`);
	}
	return args;
}

if (import.meta.main) {
	try {
		const args = reportArgs(process.argv.slice(2));
		process.stdout.write(formatReport(summarise(readLedger(args.store, args.since), args.since), args.store));
	} catch (error) {
		process.stderr.write(`ledger-report: ${error instanceof Error ? error.message : String(error)}\n`);
		process.exitCode = 2;
	}
}
