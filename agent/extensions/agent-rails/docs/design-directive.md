# Agent-rails design directive

Every change to agent-rails, its skills, `guardrails`, the agent rails and the repo config schema
follows these principles. A change that conflicts with one needs the owner's explicit decision
first; the [exceptions](#exceptions) below are the only standing ones.

## Purpose

**1. Opinionated gate enforcement.** Every gate runs deterministic code first, in extension code or
omp mechanisms. Next comes a Jev decision that selects one action from a fixed set. Agent
instructions come last, only for what neither can decide; skills, judged rules and refusal texts
hold only that tier.

## What the gates enforce

**2. Naming, structure and grouping.** The naming, structure and grouping of code objects across a
repo: deterministic path checks, then Jev judgments.

**3. Verification layers.** LLM analysis designs the layers; enforced deterministic checks run them.
Nothing weakens a layer: no suppressed, skipped, loosened or bypassed checks, stub bodies, hardcoded
results or fallback modes (implementation integrity).

**4. Tests prove user journeys.** Every test drives a reachable user path from its feature-map entry
to a caller-visible outcome, and together they prove the journey's acceptance criteria are met. A
case no caller or real data can reach, a single bug-value pin, an internal or wiring assertion, or a
duplicate of an outcome class already asserted is dropped, not added. LLM analysis creates the
patterns tests are generated from; checks on the suite itself are deterministic where possible.

**5. Proofs with gates.** Deterministic gates prove that principles 2-4 and 6-9 were respected. A
proof is an outcome test frozen red at base and passing at head, checked by `prove()` at the PR.
There are no agent code reviews. Quality checkers run at commit, never inside `prove()`, with no
separate proof command; the repo's tests prove its checkers and `guardrails`' tests prove the
harness checkers.

**6-9. Clean cutovers, regression repair, measured claims and flaky tests.** The judged rules in
[`rules/`](../rules/) hold these: [clean cutover](../rules/clean-cutovers.md),
[regression coverage](../rules/regression-coverage.md),
[measured claim](../rules/measured-claims.md) and [flaky test](../rules/flakes.md).

## How the rails run

**10. Workflows are not built in.** Spikes, plan, build, PR and merge, and similar flows are how
people use the system. The rails gate actions, whatever flow produced them; the extension ships no
workflow, stage sequence or workflow skill.

**11. One mode, with onboarding.** There is one governed state and no mode switch. The first time
the rails meet a repository with a GitHub origin and no valid config, they refuse ordinary work and
route it to onboarding: skill://setup-repo-proofs and skill://test-guidance, then a short, fixed
Q&A written in code. A repository with no GitHub origin gets the fixed guards only.
`in-scope/ask_first` runs in every Git repository, always enforced; the Q&A never offers it.

**12. Autonomy and CI come from the config.** In an onboarded repository, agents and subagents work
autonomously within the agreed work. They decide its details themselves and surface any change to
the agreed direction to the owner. They commit on a work branch under the commit checks, push it,
open the PR and merge once every verification and proof is green on the exact head. The config's
`autonomy` sets each of branch, PR and merge to `allowed` or `ask`; an `ask` waits for the
owner's `$approve autonomy:<action>:<subject>` line. The config's `ci` says whether a merge needs
a CI status and which runner role posts it. A push to the default branch is always refused.

**13. One writer per config section.** Each section of `.agentrails/config.json` has exactly one
writer, as the [config contract](config.md#sections-and-writers) lists. The rails refuse every other
write, including agent edits. Exceptions and approvals come only from `$approve` and `$revoke`
lines the owner types in the main session.

**14. Judgment effective modes.** The global mode, cutoff and measured basis of each question live
in `judgments/<judgment>/thresholds.json`. A repository may switch a question off or move it from
enforced to shadow, and the effective mode is the softer of the two. A repository never enforces a
question that is shadow globally: only measurement promotes it. Decision rows record the effective
mode. Code checks are on or off only; shadow does not apply to deterministic rules.

## Repository boundary

**15. No part of agent-rails enters a repository.** A repository holds no omp or agent-rails files,
pins, mentions, dependencies or workarounds; anyone can clone, build, test and run its checks
without the harness. Agent-rails may create repo-native controls alongside the repo's norms, such as
verification, hooks, tests and CI, which run without the harness.

**16. Grow the repo's controls; the harness covers only the rest.** Where a repository can hold a
control (its own checkers, linters, test runner or CI gate), the missing rules are added to it by a
repo PR. The harness enforces a rule only when the repo could not hold it without depending on omp
or agent-rails, such as checks on the repo config itself.

- Never run a harness copy of a rule beside a repo control, even while the repo PR is pending, and
  never as the route when the owner declines that PR.
- Every claim that a repo control covers or lacks a rule cites source evidence, rule ID by rule ID.
- For a rule the repo owns, the repo control is the authority: never reshape repo code to satisfy a
  harness copy. A disagreement means a repo PR or a stale harness setting to remove.

**17. Per-repo configuration stays out of the repository's history.** Each repository's settings
are `.agentrails/config.json` in its main checkout, kept out of git through
`<git-common-dir>/info/exclude` and never committed or part of a PR. Linked worktrees read the main
checkout's file; another clone imports an exported file or onboards. oh-mi-pi-config is the bespoke
harness: setup for one machine belongs in its deploy branches, and workflow code holds no
environment values except configuration a skill sets on purpose.

**18. Required means required.** No optional checks, skipping defaults, fallbacks, compatibility
shims or tolerance of old formats. Cutovers migrate every caller and delete superseded fields and
code. Many failures in a repository are cleaned up, never answered by softening the check.

**19. Quality gates block without baselines.** Repository quality gates and harness commit checks
cover the whole repository. Placement never requires moving existing code.

**20. Events trigger checks, with no ceremony.** Edits and commits run the harness checks; CI reruns
the gates offline on the events the repository chose, with no time budget. Pulling or merging
commits into a branch needs no proof; a resulting commit runs the normal checks. Behaviour is the
same on any CI host with the harness deployed. Approvals work for a single owner.

**21. Never version code to keep results comparable.** A code change that invalidates an experiment
means the experiment is rerun.

**22. Verify and design only the code the repository owns.** Third-party code (installed packages,
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

## Exceptions

- Principle 15: frozen study or evaluation records pinned by digest keep their wording.
- Principles 15-16: harness `guardrails path-check` gives one per-edit contract from repo config
  settings, even where repo checks overlap; repo build, tests and CI stay independent.
- Principles 5 and 19: automatic naming, structure and path checks judge only new paths and
  segments, and `prove()` runs those new-path pre-checks at the PR; full scans are explicit
  requests, and enforced `structure_review()` findings block merge.
