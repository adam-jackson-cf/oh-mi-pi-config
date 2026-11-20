# Dynaflows

Keep [workflow.html](docs/workflow.html) aligned with the canonical skill and extension implementation;
the page explains them and does not define another contract.

## Design directive

Every change to dynaflows, its skills, `guardrails`, the agent rails and the record schema follows
these principles. A change that conflicts with one needs the owner's explicit decision first;
the [exceptions](#exceptions) below are the only standing ones.

1. **The repo stays independent.** A repository holds no omp or dynaflows files, pins, mentions or
   workarounds. Anyone can clone, build, test and run its checks without the harness.
2. **Grow the repo's controls; the harness covers only the rest.** Where a repository can hold a
   control (its own checkers, linters, test runner or CI gate), the missing rules are added to it
   by a repo PR. The harness enforces a rule only when the repo could not hold it without depending
   on omp or dynaflows, such as checks on the record itself.
   - Never run a harness copy of a rule beside a repo control, even while the repo PR is pending,
     and never as the route when the owner declines that PR.
   - Every claim that a repo control covers or lacks a rule cites source evidence, rule ID by rule ID.
   - For a rule the repo owns, the repo control is the authority: never reshape repo code to satisfy
     a harness copy. A disagreement means a repo PR or a stale harness setting to remove.
3. **Per-repo configuration lives in the harness.** Each repository's settings are a gitignored
   record in oh-mi-pi-config, keyed by the clone's `origin` owner and name, never committed or part
   of a PR. oh-mi-pi-config is the bespoke harness: setup for one machine belongs in its deploy
   branches, and workflow code holds no environment values except configuration a skill sets on purpose.
4. **Required means required.** No optional checks, skipping defaults, fallbacks, compatibility shims
   or tolerance of old formats. Cutovers migrate every caller and delete superseded fields and code.
   Many failures in a repository are cleaned up, never answered by softening the check.
5. **Quality gates block without baselines.** Repository quality gates and harness commit checks
   cover the whole repository. Placement never requires moving existing code.
6. **Events trigger checks, with no ceremony.** Edits and commits run the harness checks; CI reruns
   the gates offline on the events the repository chose, with no time budget. Pulling or merging
   commits into a branch needs no proof; a resulting commit runs the normal checks. Behaviour is the
   same on any CI host with the harness deployed. Harness process additions run only in dynaflows
   structured mode for the path's repo; minimal mode keeps fixed guards only
   ([ADR 0015](../../../docs/adr/0015-dynaflows-minimal-and-structured-modes.md)).
   Approvals work for a single owner.
7. **Proof means outcome tests.** A proof is an outcome test or lever journey linked from the feature
   map, frozen red at base and passing at head, checked by `prove()` at the PR. There are no agent
   code reviews. Quality checkers run at commit, never inside `prove()`, with no separate proof
   command; the repo's tests prove its checkers and `guardrails`' tests prove the harness checkers.
8. **Deterministic first.** Use deterministic checks wherever possible, a calibrated jev-actions
   judgment where they cannot work, and LLM instructions only as a last resort.
9. **Never version code to keep results comparable.** A code change that invalidates an experiment
   means the experiment is rerun.
10. **Verify and design only the code the repository owns.** Third-party code (installed packages,
    vendored copies, generated clients, toolchains, third-party programs used as fixtures) is excluded
    at the boundary; the repo never bends its own code or rules to make it pass.
    - **Scope.** Checkers list owned files only, from `git ls-files`. Each exclusion names its
      path or package with origin evidence. A vendored copy the repo edits is owned code with the
      full rules. Anything a checker's derived scope cannot cover is a failure, not a skip.
    - **No in-repo copies or shims.** Never keep copies, generated stubs or shims of third-party code
      to satisfy a rule, or exempt such files from checkers. A dependency upgrade never requires
      regenerating files in the repo.
    - **Types from upstream.** Use the package's own annotations (mypy `follow_untyped_imports`
      for a named package without `py.typed`) or a published stub package; ignore missing imports
      only for a package with neither.
    - **Design at the boundary.** Owned code reaches a third party through an owned adapter; the rest
      depends on repo-defined interfaces such as Protocols. Structure, naming and complexity rules judge
      those interfaces, never the third party's API.
    - **Tests.** Tests replace the owned interface, not the third party. Adapter tests prove how the
      repo uses the third party, never its own behaviour. Coverage and test integrity measure owned
      code.
    - **Upstream faults.** Fix a wrong call in owned code. Keep a workaround for the third party's own
      fault inside its adapter, citing the upstream issue so it is removed when the fix lands.

### Exceptions

- Principle 1: history keeps its wording: ADRs, and frozen study or evaluation records pinned by digest.
- Principles 1–2: harness `guardrails path-check` gives one per-edit contract from record
  settings, even where repo checks overlap; repo build, tests and CI stay independent
  ([ADR 0002](../../../docs/adr/0002-harness-owns-path-checks.md)).
- Principle 4: deprecated skills stay on disk untouched until a final owner-signed-off cleanup PR;
  this is not a compatibility path.
- Principles 5 and 7: automatic naming, structure and path checks judge only new paths and segments,
  and `prove()` runs those new-path pre-checks at the PR; full scans are explicit requests, and
  `structure_review()` findings are shadow-only unless a decision is `ready`
  ([ADR 0012](../../../docs/adr/0012-file-parent-repeats-and-scoped-exceptions.md), extending
  [ADR 0003](../../../docs/adr/0003-structure-checks-at-pr-diff-scope.md)).
