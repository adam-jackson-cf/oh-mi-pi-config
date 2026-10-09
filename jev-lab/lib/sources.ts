import { CASESET_PREFIX, listCasesets, loadCasesetCases } from "./caseset";
import { appendPolicyLabel, listPolicies, loadPolicyCases, POLICY_PREFIX } from "./policy";
import type { LabelReviewer } from "../../agent/extensions/lib/jev";
import { LabError, type LabCase, type LabPaths, type Source } from "./types";

export async function loadCases(paths: LabPaths, source: string): Promise<LabCase[]> {
  if (source.startsWith(POLICY_PREFIX)) return loadPolicyCases(paths, source.slice(POLICY_PREFIX.length));
  if (source.startsWith(CASESET_PREFIX)) return loadCasesetCases(paths, source.slice(CASESET_PREFIX.length));
  throw new LabError("Unknown source.", 404);
}

export async function listSources(paths: LabPaths): Promise<Source[]> {
  const sources: Source[] = [];
  for (const name of await listPolicies(paths.auditDir)) {
    sources.push({ id: POLICY_PREFIX + name, kind: "policy", title: `Policy: ${name}`, count: (await loadPolicyCases(paths, name)).length });
  }
  for (const name of await listCasesets(paths)) {
    sources.push({ id: CASESET_PREFIX + name, kind: "caseset", title: `Case set: ${name}`, count: (await loadCasesetCases(paths, name)).length });
  }
  return sources;
}

export async function applyLabel(paths: LabPaths, source: string, id: string, label: string, reviewer: LabelReviewer, note?: string): Promise<void> {
  if (source.startsWith(POLICY_PREFIX)) {
    await appendPolicyLabel(paths, source.slice(POLICY_PREFIX.length), id, label, reviewer, note);
    return;
  }
  throw new LabError("Case sets carry `expected` values instead of labels; edit the .jsonl or save from the Playground.", 400);
}
