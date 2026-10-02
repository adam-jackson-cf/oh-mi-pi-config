import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { promisify } from "node:util";

const run = promisify(execFile);
const SUMMARIZE = join(import.meta.dirname, "..", "agent/skills/evaluate-jev/scripts/summarize.ts");

test("summarize runs on plain Node: human labels beat agent labels, failures are not cases, scores come from answers", async () => {
  const root = await mkdtemp(join(tmpdir(), "evaluate-jev-"));
  try {
    const casesIn = join(root, "cases.jsonl");
    const labelsIn = join(root, "labels.jsonl");
    const noul = (p: number) => [{ id: "danger", type: "noul", noul: p }];
    await writeFile(casesIn, [
      { id: "a", timestamp: "2026-10-01T00:00:00Z", version: "v1", verdict: "block", answers: noul(0.95), positiveLabel: "dangerous" },
      { id: "b", timestamp: "2026-10-01T01:00:00Z", version: "v1", verdict: "allow", answers: noul(0.2), label: "safe", labelBy: "agent" },
      { id: "c", timestamp: "2026-10-01T02:00:00Z", version: "v1", verdict: "block", answers: noul(0.55) },
      { type: "failure", timestamp: "2026-10-01T03:00:00Z", reason: "timeout" },
    ].map(r => JSON.stringify(r)).join("\n") + "\nnot json\n");
    await writeFile(labelsIn, [
      { id: "a", label: "safe" },
      { id: "a", label: "dangerous", by: "agent" },
      { id: "b", label: "dangerous" },
    ].map(r => JSON.stringify(r)).join("\n") + "\n");

    const summary = JSON.parse((await run("node", [SUMMARIZE, "--cases-in", casesIn, "--labels-in", labelsIn, "--threshold", "0.9"])).stdout);
    assert.equal(summary.total, 3);
    assert.equal(summary.malformedLines, 1);
    assert.deepEqual(summary.failuresBeforeCase, { timeout: 1 });
    // a: the later agent label cannot replace the human `safe`; b: a human label replaces the case's agent label.
    assert.deepEqual(summary.verdictByLabel, { block: { safe: 1, unlabelled: 1 }, allow: { dangerous: 1 } });
    assert.deepEqual(summary.labels.byReviewer, { human: 2, unlabelled: 1 });
    // At 0.9: a (0.95, safe) is a false positive, b (0.2, dangerous) a false negative.
    assert.deepEqual([summary.confusion.tp, summary.confusion.fp, summary.confusion.fn, summary.confusion.tn], [0, 1, 1, 0]);
    assert.equal(summary.score.coinFlip0_4to0_6, 1);
  } finally { await rm(root, { recursive: true, force: true }); }
});
