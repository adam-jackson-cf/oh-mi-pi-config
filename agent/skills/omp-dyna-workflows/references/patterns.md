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

Map every criterion to a named proof. Proof `task` writes files uncommitted in the dedicated
`agent/<slug>` worktree; main calls
`freeze(worktree, manifest_path, proofs, slug, *, base="HEAD", setup=None, previous=None, revise=None)`.
Then the separate implementation `task` commits proofs with the change that greens them,
through the hook (default: one atomic commit). A proof never enters history earlier.
Tasks run non-isolated, sequentially, with absolute paths; main never commits.
Split only if every earlier commit passes the hook with all proof files present.
`proofs` omit `sha256`. Freeze hashes working-tree bytes, pins base and parents as 40-hex,
and runs each proof alone in a detached baseline tree. Only paths listed in `files` may be dirty;
command file tokens must be listed too. Symlinks, ignored files and clean-filter/EOL changes fail.
The helper returns `(Path, manifest_sha256)`; use a manifest outside the repo
(recommended `$TMPDIR/omp-proofs/<slug>.json`) and post its content on the PR.

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
      "parent": "<40-hex baseline pinned by freeze()>",
      "sha256": {"src/auth/redirect.test.ts": "<sha256 written by freeze()>"}
    }
  ]
}
```

- `defect`: isolated baseline overlay must fail containing `expectRed`; freeze also truncates
  its files and requires that text absent (negative control). Use asserted actual-vs-expected
  text, never a test name or generic failure token. It must be absent in both green head outputs.
- `preserve`: isolated baseline overlay must pass.
- `new-surface`: isolated baseline overlay must not pass; this is not evidence of API absence.
- Every kind passes twice at head. Defeat result caches in `cmd` (e.g. `go test -count=1`).
  Shared fixtures must be listed in each proof's `files`; overlays never combine proof files.
- Optional `setup` argv runs once per fresh tree, before proofs; setup failures are not evidence.
- Optional per-proof `parent` selects a baseline for review-added proofs. Freeze resolves aliases
  and pins them; parents must be ancestors of head at or after base (strict ancestors in prove).
- Re-freeze with `previous=(path, digest)`: unchanged proofs are retained without rerunning;
  only new/revised proofs run at their pinned parents. Changes/removals require
  `revise={id: reason}` and record `{id, oldSha256, reason}` in `revisions` (oldSha256 hashes
  the previous proof record, including frozen file hashes). Never silently weaken proofs.
- Passing defect/new-surface baseline returns to Plan: criterion already met (report, no PR),
  or reuse existing tracked tests as preserve. Never weaken a proof into red.
- Missing/edited files, duplicate ids, invalid schema or a changed manifest block.

## prove

- **Inputs**: `repo`, `parent`, `head`, `manifest_path`, `manifest_sha256`, `objective`;
  optional `max_checks` (default 16).
- **Guardrails**: require head `.omp/gate.json` with `checks.commit` and `pathCheck`; use
  `skill://setup-repository-guardrails` if missing. `prove()` never runs repository checks:
  the pre-commit hook runs `checks.commit`; CI runs `checks.ci` and merge requires it green.
- **Runs**: validate manifest, create detached parent/head worktrees under `$TMPDIR` plus
  one per distinct overridden parent, run setup per tree and proofs twice at head, reject tracked
  mutations, then overlay each proof alone at its selected parent and test its kind. Check earlier
  commits touching proof files for green execution with their own bytes, rejecting partial proofs
  and tracked mutations; frozen byte equality is required at head only. Remove every detached tree.
  Plain functions expose deterministic checks without eval globals.
- **Gate changes**: changed `.omp/gate.json`, `.github/workflows/**`, `.githooks/**`,
  `lefthook.yml`, `.pre-commit-config.yaml` or lint/test configs named by gate files are listed
  in `gateChanges`. A changed gate path is not itself a blocker; failed gates and unauthorized
  weakening are. The PR description lists each under **Gate changes** with its effect on checks.
- **Verifier**: one `agent="verifier"` in a family different from every author. Brief contains
  objective, manifest, deterministic results, diff stat and blocking rule. Explicitly read
  `skill://codegraph/references/readiness-and-bootstrap.md`,
  `skill://codegraph/references/query-selection.md`, `skill://git-tool` before discovery/Git.
  Work only in supplied trees and TMPDIR scratch; no repository edits or agent spawns.
  Drive each criterion live, check doc lines changed or made wrong, audit diff scope/coverage
  report-only. Findings need exact command, actual output, required output; omit unrun claims.
  Required `criteria: [{proofId, command, observed, verdict}]` covers every manifest proof id;
  `verdict` must be exactly `pass` or `fail`.
  Audit harness/config tampering too. Brief outputs are capped at 4 KB each; full evidence is
  available at the supplied scratch `deterministic.json` path.
- **Mutation**: compare source repo porcelain-v2 status and HEAD before/after verifier;
  any difference fails with `verifier mutated the repo`.
- **Output**: `{status, deterministic, verifier, blocking, followUps}`; status is `pass` or
  `fail`. Remove detached worktrees on every exit; no automatic fix loop.
- **Merge marker**: only final `status: pass` (including successful worktree cleanup) writes
  `<git-common-dir>/omp-prove/<head-sha-40>.json` with
  `{"schemaVersion":1,"head":"<40-hex>","kind":"prove","status":"pass"}`.
  The common directory is resolved for the source repo, so linked worktrees share markers.
  Starting prove revokes any old marker for that head, even if this rerun fails.
  With dynaflows on, merge requires this marker and `--match-head-commit <same-sha>`.
- **Blocking rule**: failing head run, failed gate, doc line changed or made wrong, base defect
  copied or newly reached, missing/weakened proof, check loosening the objective does not call
  for; a changed gate path is not itself a blocker; failed gates and unauthorized weakening are.
  Everything else is a parked follow-up, including uncovered naming/structure on added/renamed paths.
- **Sizing**: full verification once at code-ready head; every changed head runs a full `prove()`.
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
- **Output**: `agreed` and `disputed`, keyed by `(batch index, id)`; missing/duplicate cases are
  disputed and failed/off-schema results never count as votes. Human confirms settled labels.

## Failure handling

- Deterministic failures prevent verifier dispatch; agent failures never become a pass.
- `prove()` returns `fail` with error text for missing/invalid `.omp/gate.json` or exceptions
  from proofs or the verifier, and still cleans up its detached worktrees. Missing
  `checks.commit` or `pathCheck` routes to `skill://setup-repository-guardrails`.
- `WorkflowError` means a broken contract or cap; fix the call, never catch and continue.
- In the source `~/.omp` repository (not the skill directory), the quality gate runs stdlib Python
  tests after Bun with Git hook variables unset:
  `python3 -m unittest discover -s tests -p 'test_*.py'`.
- Add templates only for dependency-coupled owner contracts that native keywords cannot express;
  reuse `run_check`, define a cap and stop rule, and document them here and in SKILL.md.
