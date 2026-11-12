# Verify loop

The implementation task returns a ready PR: no pending writer edits, complete writer-authored description,
and required CI green for its exact head. Main owns the judgement comment and the `agent-verified` commit status,
and merges. Main never patches or commits; proof tasks do not publish PRs.

## Canonical contracts

- Six-stage ordering and task briefs: [dynaflows SKILL](skill://omp-dyna-workflows/SKILL.md).
- Helper signatures, manifest schema, docs eligibility and marker contract: [patterns](skill://omp-dyna-workflows/references/patterns.md).
- Complete deterministic checks and the sole verifier blocker text, `BLOCKING_RULE`:
  [workflows.py](skill://omp-dyna-workflows/templates/workflows.py).
- PR-head/target CI waits, infrastructure incidents and no-CI eligibility:
  [merge-and-recover](merge-and-recover.md#ci-waits-errors-and-fallbacks).

Do not copy the blocker text, eligibility allowlist or marker schema here. Infrastructure failures fail closed;
they are not red proof assertions. `prove()` runs no repository suites. It reads the gate's declared required CI context
first and refuses unless it is success on the exact head; rails enforce exact-head CI again at the status write and merge.

## Verify the actual PR range

1. Main obtains the actual PR base branch and exact 40-hex head from GitHub, using the primary remote/default already
   resolved by [branch-and-integrate](branch-and-integrate.md). Fetch the actual target and record its remote-tracking
   ref as `target_ref`. PRs target the resolved default unless the repository's project instructions
   document a target exception. Confirm that GitHub's PR target matches; helper range consistency cannot authenticate that metadata.
2. Set `parent` to `git merge-base <target_ref> <head>`, never `HEAD~1`. It must be a strict ancestor of head.
   This is the whole-PR range, distinct from each proof's pinned baseline parent.
3. Record objective, acceptance criteria, frozen manifest/path and SHA-256, source worktree/branch and recorded authors.
   Missing access or unknown evidence blocks the operation.
4. Choose the helper under the [patterns risk-tier contract](skill://omp-dyna-workflows/references/patterns.md).
   Eligible non-runtime docs: main calls `exempt_docs_only(repo, parent, head, target_ref=target_ref)`.
   Runtime-loaded prose is code even when named Markdown; known runtime content never takes the docs exemption.
   Otherwise main calls from Python eval:
   `prove(repo, parent, head, manifest_path, manifest_sha256, objective, target_ref=target_ref)`.
   `repo` is the dedicated source worktree on its named branch at head, not main's possibly different checkout.
   Dependabot PRs use the [external-bot authorship exception](skill://omp-dyna-workflows/references/patterns.md#external-bot-prs) ("External bot PRs") with the same CI, `prove()` and rebase-merge path; when the record's `gate.json` declares `ci`, main submits the Dependabot head SHA with the gate's runner role ([merge-and-recover](merge-and-recover.md#declared-ci-contract)); never re-apply as an agent PR.
5. `prove()` assembles the brief and dispatches the sole `agent='verifier'` session. Do not manually author a second
   brief or spawn another verifier. It reads persisted author provenance; rails decides family independence.
   For supported verifier-model override and failure handling, follow patterns; do not substitute a task for verifier.
   Deterministic evidence includes the complete head/baseline/earlier-commit checks, journey stability, mutation detection
   and verifier criteria validation (unknown IDs) defined in workflows.py, not a partial checklist maintained here.
   The verifier takes those proof records as settled evidence and does not re-run them.
6. Inspect the full result. Failures return to the assigned writer with commands, output and expected outcomes.
   No available approved independent verifier or an infrastructure failure blocks passing, with the actual reason preserved.
   Source-checkout changes require another run; the source check is not a sandbox or attribution of who changed it.

## Changed heads and fix rounds

Every new SHA invalidates previous evidence, including after amend, fix or rebase. `agent-verified` is a commit status
bound to the SHA it was posted on, so a new head has none until it is verified again. The writer commits through the commit checks, pushes, updates the description and submits/waits for exact-head CI.
Main then repeats the full risk tier: full `prove()` or a fresh eligible docs exemption.
A wording-only change is not a no-verification exception. Old-head evidence never approves a new SHA.

A fix round is one writer repair cycle returning a new SHA after a blocker/red check following the first `prove()` result.
Main records `Fix round n/3` in a PR comment with triggering evidence and the resulting SHA; read prior comments after restart.
Pre-initial-prove CI repairs and pure rebases do not consume a round, but do invalidate evidence. Never reset the counter.
The assigned writer performs all rebases, even conflict-free ones, under [merge-and-recover](merge-and-recover.md).
After round three, unresolved blockers stop integration: escalate with PR/head, findings, failed checks, attempted fixes and decisions needed.

## Verification comment and agent-verified status

`prove()` writes its JSON result to `<git-common-dir>/omp-prove/<head>.result.json`, beside the pass marker, and posts it
as a PR comment on the PR at that head. The result carries the manifest and its SHA-256, deterministic evidence, verifier
criteria, blockers, follow-ups, authors, verifier model and head; main never copies it by hand. A result that cannot be
written or posted turns a pass into a fail. Main then posts its own short comment, outside the description's
400–650-word budget, with only the objective and its judgement of the result.
For the docs tier, main posts the exemption result and exact range/head instead, explicitly recording that no verifier was required.
If `gateChanges` discovers omitted paths, have the writer update the body's optional **Gate changes** section with paths/effects;
a changed/listed path alone is not a blocker. The canonical `BLOCKING_RULE` governs unauthorized weakening and wrong/stale docs.

`agent-verified` is a commit status with context `agent-verified` on the exact head SHA, never a label.
With dynaflows on, a passing `prove()` posts it after writing the pass marker; main does not post it by hand.
Otherwise, only after risk-tier verification and exact-head CI pass, main posts it:
`gh api repos/OWNER/REPO/statuses/SHA -f state=success -f context=agent-verified`.
Rails allow posting it only when the declared required context passes on that exact PR head (with `ci` declared, only
that context decides; without it, every reported check must be green), plus a valid exact-head pass marker with dynaflows on, and they block adding an `agent-verified` label.
Never hand-write a pass marker.
Main proceeds to [merge-and-recover](merge-and-recover.md), where CI is checked again at the merge boundary.
