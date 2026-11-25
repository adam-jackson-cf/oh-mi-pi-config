# Dynaflows

Dynaflows is the omp extension that runs the agent rails, their judgments and checks.

## Design directive

Every change to dynaflows, its skills, `guardrails`, the agent rails and the record schema follows
these principles. A change that conflicts with one needs the owner's explicit decision first; the
[exceptions](#exceptions) below are the only standing ones.

### Mechanism order

**1. Deterministic process in extension code or omp mechanisms first.** Next, a Jev decision that
selects one action from a fixed set. Agent instructions last, only for what neither can decide.
Skills hold only that last tier.

### What dynaflows enforces

**2. Structure and naming.** The naming, structure and grouping of code objects across a repo:
deterministic path checks, then Jev judgments.

**3. Verification layers.** LLM analysis designs the layers; enforced deterministic checks run them.
Nothing weakens a layer: no suppressed, skipped, loosened or bypassed checks, stub bodies, hardcoded
results or fallback modes (implementation integrity).

**4. Tests prove user journeys.** Every test drives a reachable user path from its feature-map entry
to a caller-visible outcome, and together they prove the journey's acceptance criteria are met. A
case no caller or real data can reach, a single bug-value pin, an internal or wiring assertion, or a
duplicate of an outcome class already asserted is dropped, not added. LLM analysis creates the
patterns tests are generated from; checks on the suite itself are deterministic where possible.

**5. Proofs.** Deterministic gates prove that principles 2-4 and 6-9 were respected. A proof is an
outcome test frozen red at base and passing at head, checked by `prove()` at the PR. There are no
agent code reviews. Quality checkers run at commit, never inside `prove()`, with no separate proof
command; the repo's tests prove its checkers and `guardrails`' tests prove the harness checkers.

**6. Clean cutovers.** A change replaces the old design outright: no backward compatibility, shims,
aliases or dual paths, and legacy references and version-suffixed names are removed.

**7. Regression repair.** A bug fix names the broken rule, repairs its root cause rather than the
reported input, and protects the rule with a journey or property case shown failing first.

**8. Measured claims.** A performance or quality conclusion states its sample size and variance, or
is labelled provisional.

**9. Flaky tests.** An intermittent failure is reproduced and its cause removed; a green re-run,
retry or quarantine repairs nothing.

### Workflows

Plan, build, PR and merge are the normal flow; the directive defines only these:

**10. Onboarding initiates a repository.** It sets up the repo's verification (repo-native controls,
principles 13-14), the repo's agent permissions (the config's `autonomy`), and the rest of its
harness configuration. Until onboarding completes, the rails refuse ordinary work in the repo and
route it to onboarding.

**11. A spike is a bounded experiment that never ships.** It starts from a plan (user input and
context), runs in scratch space, and ends in one decision: promote, with the final build plan and
its learnings, or discard and stop. Spike code is discarded either way.

**12. Merge-through follows the agent permission.** Agents and subagents respect the principles
throughout. They decide the details of the agreed work themselves and surface to the owner any
change to the agreed direction. Where the repo is onboarded and its `autonomy` allows each step, they
commit on a work branch under the commit checks, open the PR, and merge it once every verification
and proof is green on the exact head. A step its `autonomy` sets to `ask` waits for the owner's
approval.

### Repository boundary

**13. No part of dynaflows enters a repository.** A repository holds no omp or dynaflows files,
pins, mentions, dependencies or workarounds; anyone can clone, build, test and run its checks
without the harness. Dynaflows may create repo-native controls alongside the repo's norms, such as
verification, hooks, tests and CI, which run without the harness.

**14. Grow the repo's controls; the harness covers only the rest.** Where a repository can hold a
control (its own checkers, linters, test runner or CI gate), the missing rules are added to it by a
repo PR. The harness enforces a rule only when the repo could not hold it without depending on omp
or dynaflows, such as checks on the record itself.

- Never run a harness copy of a rule beside a repo control, even while the repo PR is pending, and
  never as the route when the owner declines that PR.
- Every claim that a repo control covers or lacks a rule cites source evidence, rule ID by rule ID.
- For a rule the repo owns, the repo control is the authority: never reshape repo code to satisfy a
  harness copy. A disagreement means a repo PR or a stale harness setting to remove.

**15. Per-repo configuration lives in the harness.** Each repository's settings are a gitignored
record in oh-mi-pi-config, keyed by the clone's `origin` owner and name, never committed or part of
a PR. oh-mi-pi-config is the bespoke harness: setup for one machine belongs in its deploy branches,
and workflow code holds no environment values except configuration a skill sets on purpose.

**16. Required means required.** No optional checks, skipping defaults, fallbacks, compatibility
shims or tolerance of old formats. Cutovers migrate every caller and delete superseded fields and
code. Many failures in a repository are cleaned up, never answered by softening the check.

**17. Quality gates block without baselines.** Repository quality gates and harness commit checks
cover the whole repository. Placement never requires moving existing code.

**18. Events trigger checks, with no ceremony.** Edits and commits run the harness checks; CI reruns
the gates offline on the events the repository chose, with no time budget. Pulling or merging
commits into a branch needs no proof; a resulting commit runs the normal checks. Behaviour is the
same on any CI host with the harness deployed. Harness process additions run only in an onboarded
repo for the path; any other repo keeps the fixed guards only. Approvals work for a single owner.

**19. Never version code to keep results comparable.** A code change that invalidates an experiment
means the experiment is rerun.

**20. Verify and design only the code the repository owns.** Third-party code (installed packages,
vendored copies, generated clients, toolchains, third-party programs used as fixtures) is excluded
at the boundary; the repo never bends its own code or rules to make it pass.

- **Scope.** Checkers list owned files only, from `git ls-files`. Each exclusion names its path or
  package with origin evidence. A vendored copy the repo edits is owned code with the full rules.
  Anything a checker's derived scope cannot cover is a failure, not a skip.
- **No in-repo copies or shims.** Never keep copies, generated stubs or shims of third-party code to
  satisfy a rule, or exempt such files from checkers. A dependency upgrade never requires
  regenerating files in the repo.
- **Types from upstream.** Use the package's own annotations (mypy `follow_untyped_imports` for a
  named package without `py.typed`) or a published stub package; ignore missing imports only for a
  package with neither.
- **Design at the boundary.** Owned code reaches a third party through an owned adapter; the rest
  depends on repo-defined interfaces such as Protocols. Structure, naming and complexity rules judge
  those interfaces, never the third party's API.
- **Tests.** Tests replace the owned interface, not the third party. Adapter tests prove how the
  repo uses the third party, never its own behaviour. Coverage and test integrity measure owned
  code.
- **Upstream faults.** Fix a wrong call in owned code. Keep a workaround for the third party's own
  fault inside its adapter, citing the upstream issue so it is removed when the fix lands.

### Exceptions

- Principle 13: frozen study or evaluation records pinned by digest keep their wording.
- Principles 13-14: harness `guardrails path-check` gives one per-edit contract from record
  settings, even where repo checks overlap; repo build, tests and CI stay independent.
- Principles 5 and 17: automatic naming, structure and path checks judge only new paths and
  segments, and `prove()` runs those new-path pre-checks at the PR; full scans are explicit
  requests, and enforced `structure_review()` findings block merge.

## Known limits

- `RENAME.residue` sees a rename only at Git's 50% similarity (`RENAME_THRESHOLD` in
  `rename_residue.py`). A rewrite recorded as delete plus add gives no rename evidence, and a
  retired term reused outside the renamed folder or file is not flagged.
- `STRUCT.runtime-data-placement` treats only folders holding Python source as code: data in
  non-Python code (a Go package), data whose nearest Python sits in a sibling folder, and `.md`
  templates are not flagged, while config beside a Python script folder is.
- `STRUCT.overlapping-responsibilities` reads Python only, and its similarity counts shared 3-grams
  held by at most 40 definitions: copies built from common idioms score low, and code repeated in
  another language or inside one folder is not flagged.
- `structure-naming/same_thing` judges only the name pairs `name-pairs` proposes (alias, head noun,
  synonym, constant value, clone); two names for one concept that none of those rules links are
  never asked. Its held-out precision is 0.72 (13/18), below the 0.75 floor, by owner acceptance.
- `STRUCT.prefix-folder` scores only clusters with a member added or moved in the change, and its
  cutoff 0.15 flags most clusters (in-sample 62 of 63 labelled clusters, 47 correct).
- `structure-naming/technical_kind_grouping` runs in shadow mode: no real violation is labelled yet,
  so its flags are logged for review and never block until 10 labelled flags measure it.
- `structure-naming/consumer_placement` asks only files `consumer-candidates` proposes (a run marker,
  no importer, no path, module-string or console-script reference) and runs in shadow mode: 30
  labelled real files hold 2 violations, too few flags to measure precision.
- `STRUCT.entry-point-placement` judges regular packages (`__init__.py` chains) and entry modules by
  name (`__main__.py`, `cli.py`); command scripts with other names are not flagged.
- A mode switch changes `ttsr.disabledRules` for later matches only; a `dynaflows-*` reminder
  already delivered in the session is not retracted.
- No rail stops an agent commit that edits a repo's dependency-audit exception file; the owner
  catches it at PR review.
- A command word built only from variables, with no literal `git` or `gh` text (`$X$Y pr merge`), is
  not detected.
- `completion-evidence` pre-check P3 (linked plan) is not wired: no labelled PR links a plan or
  issue. Turn-end obligations stop after 3 follow-ups (`MAX_FOLLOW_UPS`); the owner is told once.
- Rails scan shell and eval text, so obfuscated code can evade them. The owner keeps server-side
  protection: the default-branch ruleset and a required `gate` status only the gate identity can
  post.
- Two rules stay instructions in `agent/AGENTS.md`: no Git in the NUC's `~/.omp` (remote `ssh`
  shells are opaque), and restart sessions after rail changes (old sessions keep old rails).
- The decision store logs deterministic check results only for `prove()`'s rename-residue,
  path-check and structure checkers; commit-time checks are not logged.
- Only bool decisions and choice decisions with an option threshold (`consumer_placement`) run in
  shadow mode: a rubric choice, such as `dependency-severity`'s `impact`, has no outcome that leaves
  the rubric unchanged.
- Passes keep their payload only for a stable 1-in-10 sample, so a sampled review sees a tenth of
  passes. `shadow-review --machine nuc` copies labelled NUC decisions home; the NUC's own store is
  only read, so its payloads are never pruned.
- Turn-end `session_stop` blocks and the `dynaflows` tool's refusals carry no rail code, so the
  rails ledger does not count them.
