import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { z } from "zod";
import type { LabPaths } from "./types";

export const PROPOSALS_FILE = "scope-proposals.jsonl";

const proposalRow = z.object({
  id: z.string().min(1),
  label: z.enum(["overreach", "no_overreach", "uncertain"]),
  agreement: z.enum(["agreed", "disputed"]),
  rationale: z.string(),
  namedChange: z.string().optional(),
  evidence: z.string().optional(),
  labellers: z.record(z.string(), z.string()),
});
export type ProposalRow = z.infer<typeof proposalRow>;

export type ProposalIndex = { byId: Map<string, ProposalRow>; malformed: number };

/** First-pass AI proposals; runtime-only and optional. Proposals are never written as labels. */
export async function loadProposals(paths: LabPaths): Promise<ProposalIndex> {
  const byId = new Map<string, ProposalRow>();
  let malformed = 0;
  let text: string;
  try { text = await readFile(join(paths.runsDir, PROPOSALS_FILE), "utf8"); } catch { return { byId, malformed }; }
  for (const raw of text.split("\n")) {
    if (!raw) continue;
    try {
      const parsed = proposalRow.safeParse(JSON.parse(raw));
      if (parsed.success) byId.set(parsed.data.id, parsed.data);
      else malformed++;
    } catch { malformed++; }
  }
  return { byId, malformed };
}
