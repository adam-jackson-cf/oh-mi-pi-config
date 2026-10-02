# Jev policies

Evidence ladder: a deterministic rule decides when it can; Jev classifies what rules cannot; an LLM
reviews only what Jev cannot. A policy moves to `enforce` when replayed, labelled history shows it
adds value over its baseline at an accepted burden (`skill://evaluate-jev`, development.md).
`shadow` (log only) is for event types with no replayable history, time-boxed. Modes live in
`~/.omp/agent/jev-policies.json` (`off` | `shadow` | `enforce`), read at session start, so every
change is verified in a freshly started OMP. All three guards are in `enforce` since 2026-10-02.

Decisions are `DecisionRecord` lines in `~/.omp/agent/jev-audit/<policy>/<date>.jsonl` (0600,
redacted). Labels are appended to `<policy>/labels.jsonl` from the Jev lab workbench. Rows carry
`reviewer: "human" | "agent"`; agent labels are first passes written by
`bun jev-lab/scripts/apply-agent-labels.ts`, and the latest human label always overrides them.

## Process profiles

Per-process rules, thresholds, label vocabularies and evaluation criteria:

- [Scope watchdog](jev-scope.md) (`jev-scope`)
- [Tool guards](guards.md) (`guard.bash`, `guard.write`, `guard.result`)
- [Integrity guard](guard-integrity.md) (`guard.integrity`)
- [`jev_ask` tool](ask.md) (`ask`)

Dropped on 2026-10-02 (code removed; audit data kept, still listed by `export-cases.ts --list`):

- `subagent.effort`: all 15 effort raises in 35 labels were unnecessary.
- `subagent.review-triage`: depth was right in 5 of 37 labelled spawns (14%) against the
  pre-registered 70% bar.

## Promotion to enforce

Each policy needs its own frozen experiment, run now rather than accumulated in shadow. A
threshold is never tuned on the evaluation set.

1. Replay the policy over history (audit records, session transcripts, git history) and label at
   least 30 decisions blind, with at least 5 of the minority label. Choose the threshold on that
   set with the Metrics sweep and freeze it.
2. Run the matching regression:
   - Guards: frozen destructive, secret and injection corpora (Jev lab case sets) with benign
     canaries. Require zero missed blocks on the destructive set, and a false-block rate on canaries
     low enough that confirm prompts stay rare.
3. Decide on value against the baseline; unmet targets become the next iteration, not a reason to
   wait. Flip one policy at a time in `jev-policies.json`, restart OMP, verify in a fresh process,
   and keep auditing.

The 2026-10-02 guard promotion (`.todo/artifacts/021026-jev-promotion/report.md`) narrowly missed
its recall bars on an independent held-out corpus: bash 39/40 dangerous commands caught, result
28/30 injections (28/29 in scope), write 10/10 known-format keys and 17/20 other credentials. False
positives were 0 on 90 held-out real decisions and 1 on 414 canaries. The owner chose to enforce
all three regardless; the known misses are `launchctl unload` of system daemons, `requirepass` and
`--auth-password` credentials (pre-filter), and an injection buried in a long page.
