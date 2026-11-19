# Structure and naming

A file's path shows its owner and dependency direction; names describe what things hold or do, with one name per concept. Word lists, ecosystem casing, `v<N>` patterns and the plural allowlist are canonical data in [house-style.json](../../../engine/guardrails/data/house-style.json). Judgment decisions live in `judgments/structure-naming/thresholds.json`; their provisional status remains shadow-only.

## Structure and grouping

- Group by owner (capability or domain), never by technical kind; one folder is one ownership unit whose contents change together. Judged by structure-naming/technical_kind_grouping.
- Tiers are folders: lower tiers hold contracts, shared types and parsing; higher tiers hold entry points and commands. Import only from lower tiers, never a sibling on the same tier. Enforced by STRUCT.dependency-direction (guardrails structure, path-check).
- Add a tier only once a part is read by two or more others; until then keep modules flat in one package. Enforced by STRUCT.tier-readership (guardrails structure, path-check).
- In an existing project, read the current tier order from its import direction. Guidance only.
- Add an automated import check where the toolchain allows one. Guidance only.
- Code two siblings both need moves down to the lowest tier both may read; never import one sibling from the other to reach it. Enforced by STRUCT.sibling-isolation (guardrails structure, path-check).
- A part one owner uses lives under that owner; once a second owner uses it, move it to the lowest tier both may read. Enforced by STRUCT.single-owner-placement (guardrails structure, path-check).
- Never create grab-bag folders (house-style `banned_words`); shared code goes in the lowest tier, in a folder named for what it holds. Enforced by NAME.conventions (guardrails naming).
- Extract candidates when two or more sibling files share a name prefix naming one concept they jointly implement (`audit_*`). Enforced by PREFIX.candidates (guardrails prefix-candidates).
- Put those files in a folder named for that concept (`audit/`) when they jointly implement it; candidate extraction alone does not establish concept ownership. Judged by structure-naming/prefix_should_be_folder.
- Merge units with overlapping responsibilities into one owner; no near-duplicates. Judged by structure-naming/overlapping_responsibilities.
- Data loaded at run time (definitions, configuration, templates) lives outside the code package, in a root folder named for what it holds. Judged by structure-naming/runtime_data_in_code.
- Tests use one root test folder unless the ecosystem places tests beside code; fixtures live beside the tests that load them, and root, fixture and support folder names are house-style `test_roots`. Enforced by STRUCT.folder-placement (guardrails structure, path-check).
- Name each test file for the behaviour it checks. Enforced by NAME.test-files (guardrails naming).
- A code move updates test imports in the same step; test files stay where they are. Guidance only.
- A CLI or API entry point is the product's interface, in the package's top tier; a finished-product consumer (dashboard, report generator, operator tool) gets its own root folder, reads the package and is never imported by it. Judged by structure-naming/entry_point_placement.
- Generated output and run artifacts stay outside source folders: outside the checkout or behind a link the VCS ignore file excludes. Enforced by STRUCT.folder-placement (guardrails structure, path-check).
- Version only what must be reproduced or compared, as `<name>/v<N>` folders or files; name the current version in one place, such as a pointer file. Guidance only.
- Never edit a measured version; a change is a new version. Never version code modules. Guidance only.
- Reference another part by explicit path, never via search order, shadowing or a fallback location. Guidance only.
- Every instance of one kind shares one layout: a new instance matches its siblings; older instances change only in a brownfield restructure. Guidance only.
- Never create an empty folder for a planned layout; create each folder with its first file. Guidance only.

## Naming

- Folder/module names are singular lowercase nouns for their concept or tier (`capability`, `harness`, `engine`); plurals are only for a catalogue of independent same-kind items addressed by name (house-style `catalogue_plurals`). Enforced by NAME.conventions (guardrails naming).
- A name covers everything its folder holds; rename the folder when its contents outgrow it. Judged by structure-naming/folder_name_coverage.
- A child never repeats its parent's name; name it by role (`engine/workflow/stages`, not `engine/workflow/workflow`). Enforced by NAME.conventions (guardrails naming).
- Name by behaviour, never version, date or ticket (`test_base_comparison`, not `test_policy4`); dates are only for time-ordered records such as decision records and reviews, and versions only as `<name>/v<N>` folders or files. Enforced by NAME.conventions (guardrails naming).
- Use one name per concept across code, flags, record fields, docs and tests; use the project glossary when one exists. Judged by structure-naming/one_name_per_concept.
- A rename changes every use in one change, with no old-name alias; retire the whole renamed name (`gate_effort`), not each word in it, preserving unchanged mechanism names (`gate`). Enforced by RENAME.residue (guardrails rename-residue).
- Siblings share one naming pattern (`one-shot-verify` beside `define-build-verify`); keep a name describing a mechanism when a product name changes. Judged by structure-naming/sibling_naming_pattern.
- Use plain domain words, never a vague grab-bag or status word (house-style `banned_words`); never add a `v<N>` suffix outside a `<name>/v<N>` folder or file. Enforced by NAME.conventions (guardrails naming).
- Use the ecosystem's casing for code files and house-style `asset_id` casing for non-code asset ids. Enforced by NAME.conventions (guardrails naming).

## By project state

- Greenfield: apply every rule from the first file. Guidance only.
- Brownfield addition: place new code where the rules put it; creating the folder or file it needs is in scope, but moving or renaming existing code is not. Guidance only.
- In a kind-grouped project, kind folders are not owners: new code with behaviour of its own (retry, caching) gets a capability folder at the source root beside the kind folders, even when one file uses it. Judged by structure-naming/technical_kind_grouping.
- If a rule-correct home needs existing code moved, put new code in the existing folder whose role matches (never a grab-bag) and state the mismatch in the result. Guidance only.
- Brownfield restructure: move one concept (domain folder or module) per step, with no behaviour change in the move. Guidance only.
- Each restructure step updates every import, configuration, doc, test and tool exclude, leaving build and tests passing; no re-export shim or alias. Guidance only.
- Domain folders share one tier and never import each other; first move a part two domains share into the shared tier, in its own step. Enforced by STRUCT.sibling-isolation (guardrails structure, path-check).
- Dissolve grab-bag folders: a file one owner uses moves under that owner; a file several use moves to the shared tier. Enforced by STRUCT.single-owner-placement (guardrails structure, path-check).
- Apply [clean cutovers](delivery-principles.md#clean-cutovers) to each move; delete migration scaffolding once the moves land, subject to the explicit deprecated-skill retention decision. Guidance only.
