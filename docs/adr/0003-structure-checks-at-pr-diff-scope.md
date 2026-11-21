# 0003: Structure checks run at PR time with diff scope

## Status

Accepted, 2026-11-17. Superseded in part by
[ADR 0021](0021-judgments-enforced-from-the-start.md): every structure finding sets `halt`; no
decision is provisional or shadow-only, and infrastructure errors leave the review incomplete,
which blocks merge.

## Context

A rename can leave the retired name in paths and definitions. Sibling files can also jointly
implement a concept that belongs in a folder. Rename evidence and newly added or moved files
identify the ownership units a PR changes; judging untouched units would turn existing structure
debt into unrelated work. Dynaflows principles 5 and 7 normally require whole-repository checks
at commit time and prohibit quality checkers inside `prove()`.

## Decision

Structure judgment and its deterministic pre-checks run inside dynaflows `prove()` at PR time,
after exact-head CI is green and before outcome proofs. They are scoped to folders the PR adds,
renames or moves files into, using the PR base and head. This is an explicit exception to
principle 5 (whole repository, no diff scope) and principle 7 (quality checkers at commit).
Existing debt is fixed when a PR touches it; other commit and CI checks retain their scope.

`RENAME.residue` blocks a PR when a retired whole path-segment name remains in a path or defined
name under the renamed folder, or in a renamed file. Retiring `gate_effort` does not retire
`gate`; regrouping `coverage_offline.py` into `coverage/offline.py` retires neither word.
Imports, strings, comments and docstrings are not defined-name residue. Third-party files remain
outside owned-code scope. `PREFIX.candidates` supplies sibling clusters to concept-ownership
judgment; a shared prefix alone is not a blocking finding.

A term is not retired when the head's owned tree still contains a directory or module stem
equal to that whole term (using the same word splitting and case rules). A concept split that
keeps `process_compliance/` while moving some code to `process_checks/` therefore does not
retire `process_compliance`; this is retirement evidence, not an exemption list.

There is no exemption list for leftover names. Pinned or released files moved under a renamed
folder are renamed too, with locks redone and experiments rerun, or they are not moved. A byte
pin is not permission to retain the old name. This follows clean cutovers and the principle that
code is never versioned merely to preserve experiment comparability.

## Consequences

Even a PR with no frozen proofs must pass rename-residue checking. Rename detection uses a
pinned similarity threshold, so substantial rewrites recorded as deletion plus addition do not
provide rename evidence. Homonyms outside the renamed unit are not deterministically rejected.
Prefix extraction is deterministic; deciding whether the cluster jointly implements one concept
remains a judgment rather than a count-only rule. No glossary or exemption artifact is required
in the repository.

Structure/naming judgment runs separately after `prove()` through `structure_review()`.
With dynaflows on, rails require both result files for the PR's exact head plus green CI.
Ready-decision findings set `halt: true` and hard-block merge via rails, not an instruction
to main. Provisional findings and recorded infrastructure errors remain shadow-only.
A missing, corrupt or wrong-head structure result blocks even with all thresholds provisional:
the review must run on that head. Fix ready findings and rerun both helpers on the new head.

## Deciders

Adam Jackson (owner, in chat 2026-11-17).
