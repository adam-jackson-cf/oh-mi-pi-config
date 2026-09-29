import { constants } from "node:fs";
import { mkdir, open, readFile } from "node:fs/promises";
import { join } from "node:path";
import { z } from "zod";
import { CASESET_PREFIX, listCasesets, loadCasesetCases } from "./caseset";
import { appendPolicyLabel, listPolicies, loadPolicyCases, POLICY_PREFIX } from "./policy";
import { loadProposals } from "./proposals";
import { appendScopeLabel, loadScopeCases, SCOPE_SOURCE } from "./scope";
import { LabError, type LabCase, type LabPaths, type Source } from "./types";

const noteLine = z.object({ requestId: z.string(), note: z.string() });

async function readScopeNotes(paths: LabPaths): Promise<Map<string, string>> {
  const notes = new Map<string, string>();
  let text = "";
  try { text = await readFile(join(paths.runsDir, "scope-notes.jsonl"), "utf8"); } catch { return notes; }
  for (const raw of text.split("\n")) {
    if (!raw) continue;
    try {
      const parsed = noteLine.safeParse(JSON.parse(raw));
      if (parsed.success) notes.set(parsed.data.requestId, parsed.data.note);
    } catch { /* skip malformed */ }
  }
  return notes;
}

/** The scope audit record format is fixed, so reviewer notes live beside it in the runtime-only runs directory. */
async function appendScopeNote(paths: LabPaths, requestId: string, note: string): Promise<void> {
  await mkdir(paths.runsDir, { recursive: true, mode: 0o700 });
  const handle = await open(join(paths.runsDir, "scope-notes.jsonl"),
    constants.O_WRONLY | constants.O_CREAT | constants.O_APPEND | constants.O_NOFOLLOW, 0o600);
  try {
    await handle.chmod(0o600);
    await handle.writeFile(JSON.stringify({ requestId, note, timestamp: new Date().toISOString() }) + "\n");
  } finally {
    await handle.close();
  }
}

export async function loadCases(paths: LabPaths, source: string): Promise<LabCase[]> {
  if (source === SCOPE_SOURCE) {
    const notes = await readScopeNotes(paths);
    const { byId } = await loadProposals(paths);
    return (await loadScopeCases(paths)).cases.map(c => ({ ...c, labelNote: notes.get(c.id), proposal: byId.get(c.id) }));
  }
  if (source.startsWith(POLICY_PREFIX)) return loadPolicyCases(paths, source.slice(POLICY_PREFIX.length));
  if (source.startsWith(CASESET_PREFIX)) return loadCasesetCases(paths, source.slice(CASESET_PREFIX.length));
  throw new LabError("Unknown source.", 404);
}

export async function listSources(paths: LabPaths): Promise<Source[]> {
  const sources: Source[] = [
    { id: SCOPE_SOURCE, kind: "jev-scope", title: "Scope watchdog (jev-scope)", count: (await loadScopeCases(paths)).cases.length },
  ];
  for (const name of await listPolicies(paths.auditDir)) {
    sources.push({ id: POLICY_PREFIX + name, kind: "policy", title: `Policy: ${name}`, count: (await loadPolicyCases(paths, name)).length });
  }
  for (const name of await listCasesets(paths)) {
    sources.push({ id: CASESET_PREFIX + name, kind: "caseset", title: `Case set: ${name}`, count: (await loadCasesetCases(paths, name)).length });
  }
  return sources;
}

export async function applyLabel(paths: LabPaths, source: string, id: string, label: string, note?: string): Promise<void> {
  if (source === SCOPE_SOURCE) {
    await appendScopeLabel(paths, id, label);
    if (note) await appendScopeNote(paths, id, note);
    return;
  }
  if (source.startsWith(POLICY_PREFIX)) {
    await appendPolicyLabel(paths, source.slice(POLICY_PREFIX.length), id, label, note);
    return;
  }
  throw new LabError("Case sets carry `expected` values instead of labels; edit the .jsonl or save from the Playground.", 400);
}
