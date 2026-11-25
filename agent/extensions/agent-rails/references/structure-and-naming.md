# Structure and naming

A path shows its owner and dependency direction; a name says what a thing holds or does, with one name per concept.
[house-style.json](../engine/guardrails/data/house-style.json) holds the language-neutral word lists,
`v<N>` patterns and the plural allowlist. Extension-selected [language and tooling packs](../engine/guardrails/foundation/conventions.py)
own ecosystem casing, fixed names and test layout. Judgment decisions live in `judgments/structure-naming/thresholds.json`;
every `structure_review()` finding blocks merge.

Automatic NAME.*, STRUCT.* and path checks judge only added or renamed paths: worktree additions and untracked files after
edits, staged additions and renames at commit, and merge-base-to-head additions and renames at PR/prove. Only new path
segments are judged; a new file never rejudges a legacy parent name, and placement never requires existing code to move.
Explicit `guardrails naming`, `structure` or `path-check` without paths, or with `--all`, is a full scan.

## Structure and grouping

- Group by owner (capability or domain), never by technical kind; one folder is one ownership unit. Judged by structure-naming/technical_kind_grouping.
- Tiers are folders: contracts, shared types and parsing low; entry points high. Enforced by STRUCT.dependency-direction (guardrails structure, path-check).
- Import only from lower tiers, never from a sibling on the same tier. Enforced by STRUCT.dependency-direction (guardrails structure, path-check).
- Add a tier only once two parts read it; until then keep modules flat. Enforced by STRUCT.tier-readership (guardrails structure, path-check).
- In an existing project, read the current tier order from its import direction. Guidance only.
- Add an automated import check where the toolchain allows one. Guidance only.
- Code two siblings need moves to the lowest tier both read, never imported across. Enforced by STRUCT.sibling-isolation (guardrails structure, path-check).
- A part lives under its one owner; a second user moves it to a shared lower tier. Enforced by STRUCT.single-owner-placement (guardrails structure, path-check).
- No grab-bag folders (house-style `banned_words`); shared code goes in the lowest tier, named for content. Enforced by NAME.conventions (guardrails naming).
- Extract candidates when sibling files share a name prefix for one concept (`audit_*`). Enforced by PREFIX.candidates (guardrails prefix-candidates).
- Files jointly implementing one concept go in a folder named for it (`audit/`). Enforced by STRUCT.prefix-folder (guardrails prefix-folder).
- Code repeated across folders moves into one shared mechanism. Enforced by STRUCT.overlapping-responsibilities (guardrails responsibility-overlap).
- Run-time data (definitions, config, templates) stays out of Python code folders. Enforced by STRUCT.runtime-data-placement (guardrails structure, path-check).
- Tests follow the language pack's placement; fixtures sit beside tests loading them. Enforced by STRUCT.folder-placement (guardrails structure, path-check).
- Name each test file for the behaviour it checks. Enforced by NAME.test-files (guardrails naming).
- A code move updates test imports in the same step; test files stay where they are. Guidance only.
- `__main__.py` and `cli.py` sit in the package root or a folder directly under it. Enforced by STRUCT.entry-point-placement (guardrails structure, path-check).
- A deeper internal-tool entry module needs an owner `guardrails.exceptions` entry. Enforced by STRUCT.entry-point-placement (guardrails structure, path-check).
- A finished-product consumer (dashboard, report tool) gets its own root folder and is never imported. Judged by structure-naming/consumer_placement.
- Generated output and run artifacts live outside the checkout or behind ignored links. Enforced by STRUCT.folder-placement (guardrails structure, path-check).
- Version only what must be reproduced or compared, as `<name>/v<N>`; name the current version in one place. Guidance only.
- Never edit a measured version; a change is a new version. Guidance only.
- Never version code modules. Enforced by NAME.conventions (guardrails naming).
- Reference another part by explicit path, never via search order, shadowing or a fallback location. Guidance only.
- Every instance of one kind shares one layout; older instances change only in a brownfield restructure. Guidance only.
- A new same-kind instance includes its configured instance marker. Enforced by STRUCT.folder-placement (guardrails structure, path-check).
- Never create an empty folder for a planned layout; it arrives with its first file. Enforced by STRUCT.folder-placement (guardrails structure, path-check).

## Naming

- Folder and module names are singular lowercase nouns for their concept or tier (`capability`, `engine`). Enforced by NAME.conventions (guardrails naming).
- Plurals name only a catalogue of same-kind items addressed by name (house-style `catalogue_plurals`). Enforced by NAME.conventions (guardrails naming).
- A name covers everything its folder holds, including the parts of its concept. Guidance only.
- Rename a folder when its contents outgrow its name. Guidance only.
- A child name never repeats its parent's tokens: `harness/tokens.js`, not `harness/harness-tokens.js`. Enforced by NAME.conventions (guardrails naming).
- Tokens split at `_`, `-`, `.`, lower-or-digit-to-upper and acronym-to-word boundaries; drop empty ones. Enforced by NAME.conventions (guardrails naming).
- A repeat is the parent's tokens, contiguous, ordered and whole, anywhere in the child, ignoring case. Enforced by NAME.conventions (guardrails naming).
- `guardrails/omp_guardrails/` repeats its parent; `rail/guardrails/` and `audit-log/log-audit.py` do not. Enforced by NAME.conventions (guardrails naming).
- A file stem ends before its first dot, except dotfiles; language-fixed names are declared only in the packs. Enforced by NAME.conventions (guardrails naming).
- Owner-approved `guardrails.exceptions` with `check: child-repeats-parent` keep other naming checks. Enforced by NAME.conventions (guardrails naming).
- Name by behaviour, never version, date or ticket (`test_base_comparison`, not `test_policy4`). Enforced by NAME.conventions (guardrails naming).
- Dates only name time-ordered records such as decisions and reviews; versions only `<name>/v<N>`. Enforced by NAME.conventions (guardrails naming).
- Use one name per concept across code, flags, record fields, docs and tests, and the project glossary if any. Judged by structure-naming/same_thing.
- A rename changes every use in one change, with no old-name alias. Enforced by RENAME.residue (guardrails rename-residue).
- Retire the whole renamed name (`gate_effort`), not each word; unchanged mechanism names (`gate`) stay. Enforced by RENAME.residue (guardrails rename-residue).
- A pinned or released file moved under a renamed folder is renamed too, with locks redone and experiments rerun, or stays put. Guidance only.
- A new folder follows the pattern its same-role peers under one parent share; a mechanism name may stay. Guidance only.
- Use plain domain words, never grab-bag or status words (house-style `banned_words`). Enforced by NAME.conventions (guardrails naming).
- Never add a `v<N>` suffix outside a `<name>/v<N>` folder or file. Enforced by NAME.conventions (guardrails naming).
- Code files use the language pack's casing; non-code asset ids use house-style `asset_id` casing. Enforced by NAME.conventions (guardrails naming).

## By project state

- Greenfield naming applies from the first file: every initial path is new. Enforced by NAME.conventions (guardrails naming).
- Greenfield placement applies from the first file: every initial path is new. Enforced by STRUCT.folder-placement (guardrails structure, path-check).
- Brownfield additions use new-path scope and never require existing code to move. Enforced by STRUCT.folder-placement (guardrails structure, path-check).
- Kind folders are not owners: new behaviour (retry, caching) gets a source-root capability folder. Judged by structure-naming/technical_kind_grouping.
- If a rule-correct home needs existing code moved, use the matching-role folder (never a grab-bag) and state the mismatch. Guidance only.
- Brownfield restructure moves one concept per step, with no behaviour change in the move. Guidance only.
- Each restructure step updates every import, config, doc, test and tool exclude using the retired name. Enforced by RENAME.residue (guardrails rename-residue).
- Each restructure leaves build and tests passing, without re-export shims or aliases. Guidance only.
- Domain folders share a tier and never import each other; move shared parts down. Enforced by STRUCT.sibling-isolation (guardrails structure, path-check).
- Dissolve grab-bags: a file moves under its one owner, or to the shared tier. Enforced by STRUCT.single-owner-placement (guardrails structure, path-check).
- Apply [clean cutovers](../rules/clean-cutovers.md) to each move; then delete migration scaffolding. Guidance only.
