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

Map every acceptance criterion to a named proof. Build missing drivers, fixtures or formal
models first, then commit proof files through the pre-commit hook. Main calls
`freeze(worktree, manifest_path, proofs, slug)` before spawning the separate implementation
task. `proofs` are the dicts below without `sha256`; the helper requires each file tracked
and unmodified at HEAD, runs each command once from `worktree`, computes hashes, writes and
validates the manifest, and returns `(Path, manifest_sha256)`. Failures name the proof id.
Use a manifest outside the proof files (recommended `$TMPDIR/omp-proofs/<slug>.json`);
post its content on the PR. Correct and commit a rejected proof before calling `freeze()` again.

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
      "parent": "<optional pre-fix commit-ish override>",
      "sha256": {"src/auth/redirect.test.ts": "<sha256 written by freeze()>"}
    }
  ]
}
```

- `defect`: overlay proof files onto parent; require failure containing `expectRed`.
- `preserve`: require parent success with the proof overlay.
- `new-surface`: parent red by definition (API absent); no parent run.
- All kinds require two passing head runs. `expectRed` is required for defects, null otherwise.
- Optional `parent` overrides `prove()`'s parent only for that proof's overlay. Use it for
  review-added defects needing a pre-fix commit newer than the original baseline. Commit-ish
  aliases resolve to commits; distinct parents share one detached worktree each.
- Missing files, edited hashes, unknown/missing or duplicate ids, invalid schema and a changed
  manifest block. Never weaken a proof to make it pass; correct, commit and call `freeze()` again.

## prove

- **Inputs**: `repo`, `parent`, `head`, `manifest_path`, `manifest_sha256`, `objective`;
  optional `max_checks` (default 16).
- **Guardrails**: require head `.omp/gate.json` with `checks.commit` and `pathCheck`; use
  `skill://setup-repository-guardrails` if missing. `prove()` never runs repository checks:
  the pre-commit hook runs `checks.commit`; CI runs `checks.ci` and merge requires it green.
- **Runs**: validate manifest, create detached parent/head worktrees under `$TMPDIR` plus
  one per distinct overridden parent, run proofs twice at head, validate proof hashes, overlay
  proofs on their selected parent and test their kind. Always remove every detached tree.
  Plain functions expose deterministic checks without eval globals.
- **Gate changes**: changed `.omp/gate.json`, `.github/workflows/**`, `.githooks/**`,
  `lefthook.yml`, `.pre-commit-config.yaml` or lint/test configs named by gate files are listed
  in `gateChanges`. They never block the merge; the PR description lists them under
  **Gate changes** with what each change does to the checks.
- **Verifier**: one `agent="verifier"` in a family different from every author. Brief contains
  objective, manifest, deterministic results, diff stat and blocking rule. Explicitly read
  `skill://codegraph/references/readiness-and-bootstrap.md`,
  `skill://codegraph/references/query-selection.md`, `skill://git-tool` before discovery/Git.
  Work only in supplied trees and TMPDIR scratch; no repository edits or agent spawns.
  Drive each criterion live, check doc lines changed or made wrong, audit diff scope/coverage
  report-only. Findings need exact command, actual output, required output; omit unrun claims.
- **Mutation**: compare source repo porcelain-v2 status and HEAD before/after verifier;
  any difference fails with `verifier mutated the repo`.
- **Output**: `{status, deterministic, verifier, blocking, followUps}`; status is `pass` or
  `fail`. Remove detached worktrees on every exit; no automatic fix loop.
- **Merge marker**: only final `status: pass` (including successful worktree cleanup) writes
  `<git-common-dir>/omp-prove/<head-sha-40>.json` with
  `{"schemaVersion":1,"head":"<40-hex>","kind":"prove","status":"pass"}`.
  The common directory is resolved for the source repo, so linked worktrees share markers.
  With dynaflows on, merge requires this marker and `--match-head-commit <same-sha>`.
- **Blocking rule**: failing head run, failed gate, doc line changed or made wrong, base defect
  copied or newly reached, missing/weakened proof, check loosening the objective does not call
  for. Everything else is a parked follow-up, including uncovered naming/structure on
  added/renamed paths.
- **Sizing**: full verification once at code-ready head; after a minor fix or rebase, verify
  the new head once (skip proof reruns only with unchanged `git patch-id` evidence).
  Non-runtime wording-only docs need no verifier; skills, rules, agents, AGENTS.md and prompts
  are runtime code. Non-runtime docs merge on green; other changes need one cross-family
  verification. Agents merge every verified PR.

## Documentation-only exemption

`exempt_docs_only(repo, parent, head)` resolves commits and writes the same marker with
`"kind":"docs-only"`, returning its `Path`, only for a nonempty diff where every changed
path is non-runtime documentation. Allowed extensions: `.md`, `.txt`, `.png`, `.jpg`,
`.jpeg`, `.svg`, `.gif`. Basenames `AGENTS.md`, `CLAUDE.md`, `SKILL.md` are excluded;
so is any path containing a segment `skills`, `agents`, `rules`, `prompts`, `.agents`,
`.claude` or `.omp`. Additions, deletions and both sides of renames are checked.
Otherwise raise `WorkflowError` naming the first runtime path; do not write a marker.
This exempts cross-family `prove()`, not required CI.

## blind_label

- **Inputs**: frozen JSON batch files of `{id, ...}` cases, rubric path, labels;
  default labellers `("scout", "task")` (Haiku vs Sol).
- **Gate**: distinct agents, separate blind read-only sessions; two jobs per batch, at most
  `MAX_AGENTS` per wave. Read only the batch and rubric, quote deciding evidence.
- **Output**: `agreed` and `disputed`; failed/off-schema agent results never count as votes.
  Orchestrator settles disputes; human confirms before writing labels.

## Failure handling

- Deterministic failures prevent verifier dispatch; agent failures never become a pass.
- `prove()` returns `fail` with error text for missing/invalid `.omp/gate.json` or exceptions
  from proofs or the verifier, and still cleans up its detached worktrees. Missing
  `checks.commit` or `pathCheck` routes to `skill://setup-repository-guardrails`.
- `WorkflowError` means a broken contract or cap; fix the call, never catch and continue.
- The quality gate runs the stdlib Python tests after Bun with Git hook variables unset.
  To run them alone: `python3 -m unittest discover -s tests -p 'test_*.py'`.
- Add templates only for dependency-coupled owner contracts that native keywords cannot express;
  reuse `run_check`, define a cap and stop rule, and document them here and in SKILL.md.
