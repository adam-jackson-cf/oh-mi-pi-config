# 0012: New-name scope, built-in conventions and record exceptions

Status: accepted (owner-approved).

## Context

ADR 0011 preserves the earlier directory-token/exact-file decision as history. The owner
now extends token matching to files, without making legacy naming debt a prerequisite
for unrelated changes. ADR 0003 supplies the PR merge-base/head scope.

## Decision

All automatic NAME.*, STRUCT.* and path checks judge only added or renamed paths:
untracked/added/renamed worktree paths after edits, staged additions/renames at commit,
and additions/renames between merge-base and head in `prove()`. Content-only edits
never invoke naming/structure. Only new segments are judged: a new file in an existing
folder does not rejudge the folder's name; a renamed folder is a new name. Placement
rules never demand moving existing code. Import graphs may read legacy context to
judge a new path, but legacy paths are not findings.

Judgment dispatch follows the same boundary in code: directory questions are eligible
only for new directory names; file-level judged payloads contain introductions, with
legacy material retained separately as evidence. Prefix grouping judgment is eligible
only when every cluster member is introduced, so it cannot require legacy moves.
Question text, threshold digests and the historical score data remain unchanged.

Commit no longer selects whole-repository naming, structure or path-check steps.
Explicit `guardrails naming`, `structure` and `path-check` without paths, or `--all`,
remain full-inventory scans. Other repository quality gates retain their scope.

The same event scope applies to Python's path-name/placement rules PYQ100–PYQ107,
including new directory segments inside legacy trees. PYQ200–PYQ203, PYQ210–PYQ211,
PYQ220–PYQ221, PYQ230–PYQ231 and PYQ299 inspect code inside files and remain whole-repo.

Parent-repeat matching uses contiguous, ordered, case-insensitive whole tokens for
directories and file stems. Split separators and camel/acronym boundaries; stems end
before the first dot except dotfiles. Non-exempt `harness/harness-tokens.js` and
`performance/test_performance_x.py` violate the rule. Keep the harness asset rename to
`theme-tokens.js`; the deployed legacy `agent/agent-rails.yml` needs no exception.

Language conventions are shipped engine data, one pack per language plus tooling.
Python, TypeScript/JavaScript, Go and shell are available immediately, selected by file
extension without a record setting. Each pack owns its casing, test patterns and
placement, fixed names/folders and source URLs. Tooling owns tool-fixed paths including
`.devcontainer/devcontainer.json`. Node dependencies are ignored. Core house-style data
stays language-neutral; no duplicate ecosystem lists remain there.

Explicit record tier locations remain authoritative for runner collection; pack test
placement is the default when no tier locations are configured.

Repository exceptions live only at `guardrails.exceptions` in profile front matter,
beside structure/naming/paths: `rule`, `path` (plain path or glob), `reason`, optional
`check`. No ADR reference is required. `check: child-repeats-parent` suppresses only that
sub-check; omission covers the matching rule. Exceptions apply before reporting.
The old exceptions key is rejected, not aliased. Existing records migrate in place;
the raidar entry's former approval text becomes its reason verbatim. Only approvals
actually stated by the owner may be written; this design grants no invented waivers.

## Guidance triage

| Former guidance-only rule | Outcome |
| --- | --- |
| Infer tier order | Guidance: architectural inference needs semantic evidence. |
| Add import checker | Guidance: native checker choice needs toolchain evidence. |
| Update moved-code test imports | Guidance: behaviour needs tests; RENAME.residue checks names. |
| Reproducible versions/current pointer | Guidance: purpose and pointer need provenance. |
| Freeze measurements/no versioned code | Guidance for provenance; NAME.conventions checks code. |
| Explicit paths/no shadow or fallback | Guidance: runtime resolution needs semantic analysis. |
| Same-kind layout | STRUCT.folder-placement checks markers; semantic equivalence needs judgment. |
| No empty planned folders | Git's file inventory cannot add empty folders. |
| Greenfield from first file | New-path scope: every initial path is new. |
| Brownfield additions leave legacy | New-path scope and segment filtering. |
| Closest existing home | Guidance: matching semantic roles needs context. |
| One concept, no behaviour change | Guidance: boundaries/equivalence need semantic/test evidence. |
| Update references/no aliases | RENAME.residue checks names; aliases/equivalence need judgment. |
| Delete scaffolding/honor retention | Guidance: lifecycle separates scaffolding from live code. |

The remaining guidance items are deferred as deterministic checks for the stated
reasons, not claimed to be covered by an unrelated judgment decision. No new judgment
or calibration is introduced by this change.

## Consequences

Legacy debt remains visible on explicit scans, never silently approved or baselined.
Conventions have one engine-data home and repository approvals have one record home.
No legacy repository file must be renamed merely to add a new, conforming file.

## Outcome coverage

- [Event scope](../../tests/test_naming_scope.py): worktree, staged and PR additions/
  renames, legacy content edits and legacy parent segments.
- [Language packs](../../tests/test_language_packs.py): extension-selected exemptions,
  test discovery/placement, non-exempt repeats and code-version paths.
- [Exceptions](../../tests/test_reference_structure.py): check-and-glob suppression
  retains unrelated checks; [profile schema](../../tests/test_reference_core.py)
  rejects the obsolete key.

## Read-only repository scan

Ran `path-check --event staged` and `structure --event staged` against isolated shared
clones of `/Users/adamjackson/Projects/work/{raidar,scout,enaible-analyzers}`, loading
temporary copies of each migrated record. Each clone staged exactly one documentation
addition: `docs/features/naming-scope.md` in raidar, `docs/naming-scope.md` in the others.
All three returned zero findings, including zero legacy-file findings. Source repositories
were read-only; no native repository suite or commit was run by this simulation.
