# Implement

## Objective

Freeze proofs before implementation, then land the greening change through tasks in a dedicated worktree.

## Required actions

1. Read [proofs](proofs.md) for proof, freezing and record-binding requirements.
2. Before ordinary implementation, require a valid pathCheck-free schema-v2 record: `gate.json` has `checks.commit`,
   and `profile.md` supplies complete path-check settings. A missing required field or a field outside the schema,
   including obsolete `checks.pathCheck`, fails to load with an error naming the field.
   `agent/lib/repo-record.ts` (`gateSchema`) owns accepted fields and types; its Python twin
   `agent/lib/repo_record.py` (`gate_reason`) serves `prove()` and CI.
3. No record, missing controls or the lazy surface trigger require the owner-approved [Project setup](project-setup.md)
   prerequisite. Bootstrap may propose the record and repository fixes before controls exist, never bypass approval.
   Read [harness setup](harness-setup.md) for the record draft, Minimal/Full approval and application;
   never apply before approval.
4. Rails run harness `guardrails path-check --repo <worktree> --record <record dir>` after edits and before commit,
   configured by profile `guardrails.structure`, `guardrails.naming` and `guardrails.paths`. It judges worktree
   additions and renames (including untracked) after edits and staged additions and renames at commit; only new
   segments count, never content edits or legacy parent names.
5. At commit the record's `checks.commit` and the other whole-repo `guardrails check` steps follow; naming, structure and
   path-check are excluded from them. `guardrails check` also runs the CODE.* rules and `TEST.coverage-ratchet`,
   `TEST.growth.justified`, `TEST.duplicate-path`, `TEST.golden-change-record` and `TEST.identity-artefacts` over the
   staged changes. Any finding blocks. PR/prove judges merge-base/head additions and renames;
   full naming/structure scans need an explicit request
   ([ADR 0012](../../../../../../docs/adr/0012-file-parent-repeats-and-scoped-exceptions.md)).
6. When the commit gate reports `SECURITY.dependency-audit` findings, stop implementation and follow
   [dependency advisories](dependency-advisories.md).
7. Never add or edit dependency-audit exceptions; raise the need to the owner, who writes every entry.
8. Follow `skill://git-tool/references/branch-and-integrate.md` to fetch the resolved default and create the dedicated
   `agent/<slug>` branch and worktree, never from spike code.
9. Delegate every edit, including small fixes, to sequential non-isolated tasks in that worktree using absolute paths;
   never use isolated patch-return modes. Rails reject enclosing-root symlink escapes and refuse `git worktree add`
   outside `<worktree.base>/<repo>/<slug>`. The assigned worktree for edit-tool writes stays an instruction boundary;
   outside-repo scratch and internal URIs remain allowed.
10. Proof precedes implementation. Main never commits, patches implementation or writes the PR body. Run in order:
    1. The proof task writes proof files and the feature-map entries that link them, then stops uncommitted:
       no staging, push or PR.
    2. Main calls `freeze()` on those bytes. Freeze refuses a proof file no feature-map doc links and shows every proof
       red at base. Map docs are not frozen and may stay dirty. With no proofs, main freezes an empty manifest and skips
       the proof task.
    3. A separate implementation task makes the greening change and commits proofs and map docs with it, normally
       atomically. It never edits frozen proof bytes.
    4. Discoveries: user-facing behaviour the plan did not name gets a map entry and outcome test, then a re-freeze
       under [proofs](proofs.md#map-linked-proofs-and-discoveries) before implementation resumes.
    5. Map sweep: before the PR opens, a sweep task covers every user-facing behaviour the branch diff adds or changes,
       as in [proofs](proofs.md#map-linked-proofs-and-discoveries).
    6. The writer pushes, writes the PR description, opens the PR and waits for exact-head green CI under git-tool.
11. The writer owns the PR body and later corrections, using existing reported evidence only. Full proof evidence
    belongs in `prove()`'s separate result comment, outside the PR body's word budget.
12. Give every task the reads its brief lists in [task briefs](task-briefs.md) before it edits.

## Done when

- The PR is open at an exact head with green CI, frozen proofs unchanged and no pending writer edits.
