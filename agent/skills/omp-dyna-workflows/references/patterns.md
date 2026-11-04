# Pattern contracts

## Spike recipe

- Read `skill://git-tool` before Git. Give every open question a pass/fail predicate,
  timebox and declared side effects (databases, ports, hooks, shared services).
  Writes to shared services need owner approval before execution.
- Create `git worktree add --detach "$TMPDIR/omp-spike-<slug>" <default>` outside the repo,
  with no branch. Give a non-isolated `task` absolute paths into that worktree.
- Do the least work that answers the predicate on the real surface. Race 2–3 prototypes
  only when built results distinguish them; use `innovation-council` only when no prototype can.
- Write `local://spike-<slug>.md`: answers, commands, outputs, and what failed.
  Remove with `git worktree remove --force "$TMPDIR/omp-spike-<slug>"` afterwards.
  Spike code never reaches a PR. Investigation is scratch-only; its report is the deliverable.

## Proof manifest

Freeze a JSON file (recommended `$TMPDIR/omp-proofs/<slug>.json`); post its content on the PR.
Map every acceptance criterion to a named proof. Build missing drivers, fixtures or formal
models first. After the proof task finishes, main records proof hashes and the manifest's
SHA-256 before spawning the separate implementation task.

```json
{
  "schemaVersion": 1,
  "slug": "fix-login-redirect",
  "proofs": [
    {
      "id": "P1",
      "criterion": "Expired session redirects to /login",
      "kind": "defect",
      "cmd": ["pnpm", "vitest", "run", "src/auth/redirect.test.ts"],
      "files": ["src/auth/redirect.test.ts"],
      "expectRed": "expected '/home' to be '/login'",
      "sha256": {"src/auth/redirect.test.ts": "<sha256 recorded at re-freeze>"}
    }
  ]
}
```

- `defect`: overlay proof files onto parent; require failure containing `expectRed`.
- `preserve`: require parent success with the proof overlay.
- `new-surface`: parent red by definition (API absent); no parent run.
- All kinds require two passing head runs. `expectRed` is required for defects, null otherwise.
- Missing files, edited hashes, unknown/missing or duplicate ids, invalid schema and a changed
  manifest block. Never weaken a proof to make it pass; correct and explicitly re-freeze the plan.

## verify

- **Inputs**: `repo`, `parent`, `head`, `manifest_path`, `manifest_sha256`, `objective`;
  optional additional `gates` (commands), `sensitive` (patterns), `max_checks` (default 16).
- **Gate**: require head `.omp/gate.json` with `fastGate` and `pathCheck`; use
  `skill://setup-repository-guardrails` if missing. `fastGate` performs dependency setup the
  repo way when needed. Additional gates never replace the repository gate.
- **Runs**: validate manifest, create detached parent/head worktrees under `$TMPDIR`, run
  head gate and proofs twice, validate proof hashes, overlay proofs on parent and test their kind.
  Plain functions expose deterministic checks without eval globals.
- **Authority**: changed `.omp/gate.json`, `.github/workflows/**`, `lefthook.yml`,
  `.pre-commit-config.yaml` or lint/test configs named by gate files set `needsCountersign`.
  Gate `sensitive` plus caller patterns set `needsOwner`; trailing `/` matches that folder
  anywhere in a path (`auth/` matches `src/auth/a.ts`, not `authentication/x.ts`).
  Other patterns match basename prefixes. Both require owner approval, never agent clearance.
- **Verifier**: one `agent="verifier"` in a family different from every author. Brief contains
  objective, manifest, deterministic results, diff stat and blocking rule. Explicitly read
  `skill://codegraph/references/readiness-and-bootstrap.md`,
  `skill://codegraph/references/query-selection.md`, `skill://git-tool` before discovery/Git.
  Work only in supplied trees and TMPDIR scratch; no repository edits or agent spawns.
  Drive each criterion live, check doc lines changed or made wrong, audit diff scope/coverage
  report-only. Findings need exact command, actual output, required output; omit unrun claims.
- **Mutation**: compare source repo porcelain-v2 status and HEAD before/after verifier;
  any difference fails with `verifier mutated the repo`.
- **Output**: `{status, deterministic, verifier, blocking, followUps}`; status is `pass`,
  `fail`, or `needs_owner`. Remove detached worktrees on every exit; no automatic fix loop.
- **Blocking rule**: failing head run, failed gate, doc line changed or made wrong, base defect
  copied or newly reached, missing/weakened proof, unapproved gate loosening. Everything else
  is a parked follow-up, including uncovered naming/structure on added/renamed paths.
- **Sizing**: full verification once at code-ready head; after a minor fix or rebase, verify
  the new head once (skip proof reruns only with unchanged `git patch-id` evidence).
  Non-runtime wording-only docs need no verifier; skills, rules, agents, AGENTS.md and prompts
  are runtime code. Non-runtime docs merge on green; sensitive paths need verifier plus owner
  merge; other changes need one cross-family verification.

## blind_label

- **Inputs**: frozen JSON batch files of `{id, ...}` cases, rubric path, labels;
  default labellers `("scout", "task")` (Haiku vs Sol).
- **Gate**: distinct agents, separate blind read-only sessions; two jobs per batch, at most
  `MAX_AGENTS` per wave. Read only the batch and rubric, quote deciding evidence.
- **Output**: `agreed` and `disputed`; failed/off-schema agent results never count as votes.
  Orchestrator settles disputes; human confirms before writing labels.

## Failure handling

- Deterministic failures prevent verifier dispatch; agent failures never become a pass.
- `verify()` returns `fail` with error text for missing/invalid gates or exceptions from checks
  or the verifier, and still cleans up its detached worktrees. Missing `fastGate` or `pathCheck`
  routes to `skill://setup-repository-guardrails`.
- `WorkflowError` means a broken contract or cap; fix the call, never catch and continue.
- The quality gate runs the stdlib Python tests after Bun with Git hook variables unset.
  To run them alone: `python3 -m unittest discover -s tests -p 'test_*.py'`.
- Add templates only for dependency-coupled owner contracts that native keywords cannot express;
  reuse `run_check`, define a cap and stop rule, and document them here and in SKILL.md.
