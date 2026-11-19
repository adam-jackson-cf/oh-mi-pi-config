# Dynaflows

Keep [workflow.html](docs/workflow.html) aligned with the canonical skill and extension implementation;
the page explains them and does not define another contract.

## Design directive

Every change to dynaflows follows these principles. That covers this skill, its playbooks and
templates, `skill://setup-repo-proofs`, `guardrails`, the agent rails and the
record schema. A change that conflicts with one of them needs the owner's explicit decision
first.

1. **The repo stays independent.** A repository holds no omp or dynaflows files, pins, mentions
   or workarounds. Anyone can clone it, build it, test it and run its checks without the harness.
   History is exempt: ADRs, and frozen study or evaluation records pinned by digest, keep their
   wording.
   Exception to principles 1–2: harness-owned per-edit path checks provide one consistent contract;
   repo build, tests and CI remain independent. See [ADR 0002](../../../docs/adr/0002-harness-owns-path-checks.md).
2. **Grow the repo's controls; the harness covers only the rest.** Where a repository already has
   the capability for a control (its own checkers, linters, test runner or CI gate), grow that
   control by adding the missing rules to it. The harness enforces a rule only when the repo could
   not hold it without depending on omp or dynaflows. Checks on the record itself are one example.
   - Never run a harness copy of a rule beside a repo control, even for a while. A rule a repo
     checker lacks is a repo PR that adds it, delivered by the setup, never a harness step that
     runs in the meantime. There is no route where the owner declines the PR and the harness copy
     runs instead.
   - Every claim that a repo control covers a rule, or lacks it, cites its source evidence, rule ID
     by rule ID.
   - For a rule the repo owns, the repo control is the authority. Never reshape repo code to
     satisfy a harness copy. When they disagree, either the repo control lacks the rule (repo PR)
     or the harness setting is stale (remove it).
   - A repo-owned control obeys principle 5: it carries no baseline.
   - Harness `guardrails path-check` is the explicit principles 1–2 exception in
     [ADR 0002](../../../docs/adr/0002-harness-owns-path-checks.md), even when repo checks overlap.
3. **Per-repo configuration lives in the harness.** Each repository's settings are a record in
   oh-mi-pi-config, keyed by the clone's `origin` owner and name. Records are gitignored, never
   committed or part of a PR. A record is harness config: git-tool may read its `ci`
   and `agentMerge` settings independently of dynaflows. Agents write records directly,
   with approval fields only as the owner stated them in the chat. oh-mi-pi-config is the bespoke
   harness. Setup for one
   machine or host belongs in its deploy branches, not in AGENTS.md or workflow code. Workflow code
   holds no environment values, except configuration that a skill sets on purpose.
4. **Required means required.** No optional checks, defaults that skip a check, fallbacks,
   compatibility shims or tolerance of old formats. Records are strict: `checks.commit` is
   required and unknown fields, including the obsolete path command field, fail to load.
   Cutovers migrate every caller and delete superseded fields and code. Deprecated skills stay
   on disk untouched until a final owner-signed-off cleanup PR; this is not a compatibility path.
   When the harness surfaces many failures in a repository, the
   job is to clean them up, never to soften the check.
5. **Quality gates block; new names have new-path scope.** Repository quality gates and
   other harness commit checks cover the whole repository, without baselines. Automatic
   NAME.*, STRUCT.* and path-check judge only added/renamed paths: worktree additions
   (including untracked) after edits, staged additions/renames at commit, merge-base/head
   additions/renames at PR/prove. Only new segments are judged, never a legacy parent
   name or a content-only edit. Placement never requires moving existing code.
   Full naming/structure scans are explicit requests, never automatic commit steps.
   See [ADR 0012](../../../docs/adr/0012-file-parent-repeats-and-scoped-exceptions.md),
   extending [ADR 0003](../../../docs/adr/0003-structure-checks-at-pr-diff-scope.md).
6. **Events trigger checks, with no ceremony.**
   - After edits and before commit, rails run harness `guardrails path-check` with record
     config and the event's new-path scope. At commit it is followed by `checks.commit`,
     then whole-repo harness checks excluding naming, structure and path-check.
   - CI reruns the gates offline on the events the repository chose. CI has no time budget.
   - Pulling or merging commits into a branch needs no proof. A commit that results from it runs
     the normal checks.
   - Behaviour is the same on any CI host that has the harness deployed.
   - Harness process additions run only with dynaflows on for the path's repo:
     targeted edit reads, path checks, verification handoff reads, owner-only commit
     refusal, commit checks and PR verification. Off means plain omp + git-tool +
     generic Git safety rails. At session start, off appends the configured native judged-rule
     names to `ttsr.disabledRules`, preserving user exclusions; on leaves it untouched.
     See [ADR 0004](../../../docs/adr/0004-dynaflows-flag-gates-the-harness-process.md).
   - Approvals work for a single owner.
7. **Proof means outcome tests.**
   - A proof is an outcome test or lever journey linked from the feature map. It is frozen red at
     base and must pass at head (TDD), and `prove()` checks it at the PR. There are no agent code
     reviews.
   - Behaviour found during implementation, and the pre-PR map sweep, add map entries and tests
     through a re-freeze.
   - Quality checkers run at commit, never inside `prove()`, and there is no separate proof
     command for them. The repo's own tests prove the repo's checkers, and `guardrails`' tests
     prove the harness checkers.
   - Exception: new-path deterministic naming/structure pre-checks run inside `prove()`
     at PR time under [ADR 0012](../../../docs/adr/0012-file-parent-repeats-and-scoped-exceptions.md).
     Main then calls `structure_review()` on that PR range. Provisional structure/naming
     findings and infrastructure errors are shadow-only, never proof or merge blockers.
     A finding from an explicitly `ready` decision sets `halt`; callers must include
     `not structure_result["halt"]` alongside prove pass in their merge guard.
8. **Deterministic first.** Use deterministic checks wherever possible, a calibrated jev-actions
   judgment where they cannot work, and LLM instructions only as a last resort.
9. **Never version code to keep results comparable.** A code change that invalidates an
   experiment means the experiment is rerun.
10. **Verify and design only the code the repository owns.** Verification rules and design rules
    apply to code the repository creates and controls. Third-party code is everything else:
    installed packages, vendored copies, generated clients, toolchains, and third-party programs used
    as fixtures. It is excluded at the boundary, and the repo never bends its own code or rules to
    make third-party code pass them.
    - **Scope.** Every checker lists owned files only, from `git ls-files`. Each exclusion of
      third-party code names its path or package, with evidence of where it came from. A vendored
      copy that the repo edits becomes owned code and gets the full rules.
      A checker's scope is derived from the repository; anything it cannot cover is a failure, not
      a skip.
    - **No in-repo copies or shims.** Never keep copies, generated stubs or shims of third-party
      code in the repo to satisfy a rule, and never exempt such files from the repo's checkers.
      They bend the rules around code the repo cannot change, and every upgrade has to redo them.
      A dependency upgrade never requires regenerating files in the repo.
    - **Types from upstream.** Take third-party types from the package's own annotations (mypy
      `follow_untyped_imports` for a named package without `py.typed`) or a published stub
      package. Ignore missing imports only for a package that has neither.
    - **Design at the boundary.** Owned code reaches a third party through an owned adapter. The
      rest of the code depends on interfaces the repo defines, such as Protocols. Structure,
      naming and complexity rules judge those owned interfaces, never the third party's API.
    - **Tests.** Tests replace the owned interface, not the third party. Tests of the adapter prove
      how the repo uses the third party, never the third party's own behaviour. Coverage and test
      integrity measure owned code only.
    - **Upstream faults.** Where owned code calls a third party wrongly, fix the call. Where the
      third party's own types or behaviour are wrong, keep the workaround inside its adapter and
      cite the upstream issue, so the workaround is removed when the fix lands. Never reshape the
      rest of the code around it.
