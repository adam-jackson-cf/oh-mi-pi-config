import { constants } from "node:fs";
import { mkdir, open, readdir, readFile } from "node:fs/promises";
import { join } from "node:path";
import { z } from "zod";
import type { JevAnswers, JevQuestions } from "../../agent/extensions/lib/jev";
import { jsonValue, LabError, questionsSchema, type LabAnswer, type LabCase, type LabPaths } from "./types";

export const CASESET_PREFIX = "caseset:";
const NAME = /^[a-z0-9][a-z0-9_-]{0,63}$/;

const expectedValue = z.union([z.string(), z.boolean(), z.number()]);
export const caseRow = z.object({
  id: z.string().min(1).max(128),
  state: jsonValue,
  questions: questionsSchema,
  expected: z.record(z.string(), expectedValue).optional(),
  note: z.string().optional(),
});
export type CaseRow = z.infer<typeof caseRow>;

function casesetFile(paths: LabPaths, name: string): string {
  if (!NAME.test(name)) throw new LabError("Case set names use lowercase letters, digits, - and _.", 400);
  return join(paths.casesetDir, `${name}.jsonl`);
}

export async function listCasesets(paths: LabPaths): Promise<string[]> {
  try {
    return (await readdir(paths.casesetDir)).filter(f => f.endsWith(".jsonl")).map(f => f.slice(0, -6)).filter(n => NAME.test(n)).sort();
  } catch { return []; }
}

export async function readCaseRows(paths: LabPaths, name: string): Promise<CaseRow[]> {
  let text: string;
  try { text = await readFile(casesetFile(paths, name), "utf8"); } catch (error) {
    if (error instanceof LabError) throw error;
    return [];
  }
  const rows: CaseRow[] = [];
  for (const raw of text.split("\n")) {
    if (!raw) continue;
    try {
      const parsed = caseRow.safeParse(JSON.parse(raw));
      if (parsed.success) rows.push(parsed.data);
    } catch { /* malformed lines are skipped */ }
  }
  return rows;
}

export async function loadCasesetCases(paths: LabPaths, name: string): Promise<LabCase[]> {
  return (await readCaseRows(paths, name)).map(row => ({
    source: CASESET_PREFIX + name,
    id: row.id,
    subject: row.note ?? row.id,
    verdict: "unrun",
    uncertainty: 1,
    state: row.state,
    // SAFETY: questionsSchema mirrors JevQuestions; zod's inferred type is structurally identical.
    questions: row.questions as JevQuestions,
    answers: [],
    labelOptions: [],
    label: null,
    expected: row.expected,
    note: row.note,
  }));
}

/** Append one case (0600); refuses duplicate ids so saved cases stay stable. */
export async function saveCase(paths: LabPaths, name: string, row: CaseRow): Promise<void> {
  const file = casesetFile(paths, name);
  if ((await readCaseRows(paths, name)).some(r => r.id === row.id)) throw new LabError("A case with that id already exists.", 409);
  await mkdir(paths.casesetDir, { recursive: true, mode: 0o700 });
  const handle = await open(file, constants.O_WRONLY | constants.O_CREAT | constants.O_APPEND | constants.O_NOFOLLOW, 0o600);
  try {
    await handle.chmod(0o600);
    await handle.writeFile(JSON.stringify(row) + "\n");
  } finally {
    await handle.close();
  }
}

export type ExpectedCheck = { question: string; expected: string | number | boolean; actual: string | number | boolean | null; pass: boolean };

/** Compare answers to `expected`: choice equals label, noul>=0.5 equals boolean, score within 0.5 of number. */
export function scoreExpected(expected: CaseRow["expected"], answers: JevAnswers): ExpectedCheck[] {
  return Object.entries(expected ?? {}).map(([question, want]) => {
    const got = answers[question];
    let actual: string | number | boolean | null = null;
    let pass = false;
    if (got?.type === "choice") { actual = got.choice; pass = want === got.choice; }
    if (got?.type === "noul") { actual = got.noul >= 0.5; pass = want === actual; }
    if (got?.type === "score") { actual = got.score; pass = z.number().safeParse(want).success && Math.abs(got.score - Number(want)) <= 0.5; }
    return { question, expected: want, actual, pass };
  });
}

export function toLabAnswers(answers: JevAnswers): LabAnswer[] {
  return Object.entries(answers).map(([id, answer]) => ({ id, ...answer }));
}
