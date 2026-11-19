# Structure and naming

A file's path shows its owner and dependency direction; names describe what things hold or do, with one name per concept. Language-neutral word lists, `v<N>` patterns and the plural allowlist live in [house-style.json](../../../engine/guardrails/data/house-style.json). Extension-selected [language and tooling packs](../../../engine/guardrails/config/conventions.py) own ecosystem casing, fixed names and test layout, with source URLs. Judgment decisions live in `judgments/structure-naming/thresholds.json`; provisional decisions remain shadow-only.

Automatic NAME.*, STRUCT.* and path checks judge only added or renamed paths: worktree additions/untracked files after edits, staged additions/renames at commit, and merge-base-to-head additions/renames at PR/prove. Only new path segments are judged; adding a file never rejudges a legacy parent name. Placement rules never require existing code to move. Explicit `guardrails naming`, `structure` or `path-check` without paths (or with `--all`) remains a full scan. See [ADR 0012](../../../../../../docs/adr/0012-file-parent-repeats-and-scoped-exceptions.md).

## Structure and grouping

- Group by owner (capability or domain), never by technical kind; one folder is one ownership unit whose contents change together. Judged by structure-naming/technical_kind_grouping.
- Tiers are folders: lower tiers hold contracts, shared types and parsing; higher tiers hold entry points and commands. Import only from lower tiers, never a sibling on the same tier. Enforced by STRUCT.dependency-direction (guardrails structure, path-check).
- Add a tier only once a part is read by two or more others; until then keep modules flat in one package. Enforced by STRUCT.tier-readership (guardrails structure, path-check).
- In an existing project, read the current tier order from its import direction. Guidance only. Inferring architectural tiers from an arbitrary language's dependency graph needs judgment; configured tier edges are checked by STRUCT.dependency-direction.
- Add an automated import check where the toolchain allows one. Guidance only. Choosing the repository's native checker requires toolchain-specific capability evidence, not a filename heuristic.
- Code two siblings both need moves down to the lowest tier both may read; never import one sibling from the other to reach it. Enforced by STRUCT.sibling-isolation (guardrails structure, path-check).
- A part one owner uses lives under that owner; once a second owner uses it, move it to the lowest tier both may read. Enforced by STRUCT.single-owner-placement (guardrails structure, path-check).
- Never create grab-bag folders (house-style `banned_words`); shared code goes in the lowest tier, in a folder named for what it holds. Enforced by NAME.conventions (guardrails naming).
- Extract candidates when two or more sibling files share a name prefix naming one concept they jointly implement (`audit_*`). Enforced by PREFIX.candidates (guardrails prefix-candidates).
- Put those files in a folder named for that concept (`audit/`) when they jointly implement it; candidate extraction alone does not establish concept ownership. Judged by structure-naming/prefix_should_be_folder.
- Merge units with overlapping responsibilities into one owner; no near-duplicates. Judged by structure-naming/overlapping_responsibilities.
- Data loaded at run time (definitions, configuration, templates) lives outside the code package, in a root folder named for what it holds. Judged by structure-naming/runtime_data_in_code.
- Tests follow the language pack's root or beside-code placement; fixtures live beside the tests that load them. Enforced by STRUCT.folder-placement (guardrails structure, path-check).
- Name each test file for the behaviour it checks. Enforced by NAME.test-files (guardrails naming).
- A code move updates test imports in the same step; test files stay where they are. Guidance only. Determining whether an unchanged test still exercises moved behaviour requires test execution and semantic judgment; RENAME.residue catches retired-name references.
- A CLI or API entry point is the product's interface, in the package's top tier; a finished-product consumer (dashboard, report generator, operator tool) gets its own root folder, reads the package and is never imported by it. Judged by structure-naming/entry_point_placement.
- Generated output and run artifacts stay outside source folders: outside the checkout or behind a link the VCS ignore file excludes. Enforced by STRUCT.folder-placement (guardrails structure, path-check).
- Version only what must be reproduced or compared, as `<name>/v<N>` folders or files; name the current version in one place, such as a pointer file. Guidance only. Reproducibility purpose and the authoritative current-version pointer need provenance; NAME.conventions enforces version spelling.
- Never edit a measured version; a change is a new version. Guidance only. Identifying measured, frozen data rather than ordinary editable data requires experiment provenance.
- Never version code modules. Enforced by NAME.conventions (guardrails naming).
- Reference another part by explicit path, never via search order, shadowing or a fallback location. Guidance only. Resolving runtime loaders, language import search and fallback semantics requires language-aware analysis beyond configured import edges.
- Every instance of one kind shares one layout: a new instance matches its siblings; older instances change only in a brownfield restructure. Guidance only. Semantic layout equivalence needs ownership context beyond required filenames.
- A new same-kind instance includes its configured instance marker. Enforced by STRUCT.folder-placement (guardrails structure, path-check).
- Never create an empty folder for a planned layout; Git's file inventory contains each folder only with its first file. Enforced by STRUCT.folder-placement (guardrails structure, path-check).

## Naming

- Folder/module names are singular lowercase nouns for their concept or tier (`capability`, `harness`, `engine`); plurals are only for a catalogue of independent same-kind items addressed by name (house-style `catalogue_plurals`). Enforced by NAME.conventions (guardrails naming).
- A name covers everything its folder holds; rename the folder when its contents outgrow it. Judged by structure-naming/folder_name_coverage.
- A child directory name or file stem never repeats its parent's complete token sequence anywhere in its name; name it by role (`harness/tokens.js`, not `harness/harness-tokens.js`). Split on `_`, `-`, `.`, ASCII lowercase-or-digit to uppercase boundaries and acronym-to-capitalized-word boundaries; ignore empty tokens and compare case-insensitively. Match contiguous, ordered whole tokens: `guardrails/omp_guardrails/` and `guardrails/guardrails-core.py` repeat, but `rail/guardrails/` and `audit-log/log-audit.py` do not. File stems end before the first dot, except dotfiles. Language/tool-fixed names are declared only in the packs. Owner-approved record `guardrails.exceptions` entries use `rule`, `path` or glob, `reason`, and optional `check: child-repeats-parent` to retain other naming checks. Enforced by NAME.conventions (guardrails naming).
- Name by behaviour, never version, date or ticket (`test_base_comparison`, not `test_policy4`); dates are only for time-ordered records such as decision records and reviews, and versions only as `<name>/v<N>` folders or files. Enforced by NAME.conventions (guardrails naming).
- Use one name per concept across code, flags, record fields, docs and tests; use the project glossary when one exists. Judged by structure-naming/one_name_per_concept.
- A rename changes every use in one change, with no old-name alias; retire the whole renamed name (`gate_effort`), not each word in it, preserving unchanged mechanism names (`gate`). Enforced by RENAME.residue (guardrails rename-residue).
- Siblings share one naming pattern (`one-shot-verify` beside `define-build-verify`); keep a name describing a mechanism when a product name changes. Judged by structure-naming/sibling_naming_pattern.
- Use plain domain words, never a vague grab-bag or status word (house-style `banned_words`); never add a `v<N>` suffix outside a `<name>/v<N>` folder or file. Enforced by NAME.conventions (guardrails naming).
- Use the extension-selected language pack's casing for code files and house-style `asset_id` casing for non-code asset ids. Enforced by NAME.conventions (guardrails naming).

## By project state

- Greenfield naming applies from the first file: every initial path is new. Enforced by NAME.conventions (guardrails naming).
- Greenfield placement applies from the first file: every initial path is new. Enforced by STRUCT.folder-placement (guardrails structure, path-check).
- Brownfield additions use new-path scope: create their folders/files without requiring existing code to move. Enforced by STRUCT.folder-placement (guardrails structure, path-check).
- In a kind-grouped project, kind folders are not owners: new code with behaviour of its own (retry, caching) gets a capability folder at the source root beside the kind folders, even when one file uses it. Judged by structure-naming/technical_kind_grouping.
- If a rule-correct home needs existing code moved, put new code in the existing folder whose role matches (never a grab-bag) and state the mismatch in the result. Guidance only. Choosing the closest semantic role and explaining the mismatch needs owner-context judgment.
- Brownfield restructure: move one concept (domain folder or module) per step, with no behaviour change in the move. Guidance only. Concept boundaries and behavioural equivalence cannot be established from the path diff.
- Each restructure step updates every import, configuration, doc, test and tool exclude using the retired name. Enforced by RENAME.residue (guardrails rename-residue).
- Each restructure leaves build and tests passing, without re-export shims or aliases. Guidance only. Behavioural equivalence and aliases require semantic and test evidence.
- Domain folders share one tier and never import each other; first move a part two domains share into the shared tier, in its own step. Enforced by STRUCT.sibling-isolation (guardrails structure, path-check).
- Dissolve grab-bag folders: a file one owner uses moves under that owner; a file several use moves to the shared tier. Enforced by STRUCT.single-owner-placement (guardrails structure, path-check).
- Apply [clean cutovers](delivery-principles.md#clean-cutovers) to each move; delete migration scaffolding once the moves land, subject to the explicit deprecated-skill retention decision. Guidance only. Recognizing obsolete scaffolding and the retention decision requires lifecycle context; RENAME.residue checks retired-name uses.
