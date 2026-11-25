# Structure and naming

A path shows its owner and dependency direction; a name says what a thing holds or does, with one name per concept.
[House style](../assets/conventions/house-style.json) holds the language-neutral word lists, `v<N>` patterns and the plural allowlist.
The [language and tooling packs](../assets/conventions/) own ecosystem casing, fixed names and test layout.
`Rule:` lines name the enforcing catalogue rule; `Review judgment.` lines need a reviewer; the rest is guidance.

Automatic naming, structure and path checks judge only added or renamed paths: worktree additions and untracked files at edit,
staged additions and renames at commit, merge-base-to-head additions and renames at pull request. Only new path segments are judged;
a new file never rejudges a legacy parent name, and placement never requires existing code to move.
An explicit run without paths, or with an all-files flag, is a full scan.

## Structure and grouping

- Group by owner (capability or domain), never by technical kind; one folder is one ownership unit. Review judgment.
- Tiers are folders: contracts, shared types and parsing low; entry points high. Rule: `STRUCT.dependency-direction`.
- Import only from lower tiers, never from a sibling on the same tier. Rule: `STRUCT.dependency-direction`.
- Add a tier only once two parts read it; until then keep modules flat. Rule: `STRUCT.tier-readership`.
- In an existing project, read the current tier order from its import direction. Guidance only.
- Add an automated import check where the toolchain allows one. Guidance only.
- Code two siblings need moves to the lowest tier both read, never imported across. Rule: `STRUCT.sibling-isolation`.
- A part lives under its one owner; a second user moves it to a shared lower tier. Rule: `STRUCT.single-owner-placement`.
- No grab-bag folders (house-style `banned_words`); shared code goes in the lowest tier, named for content. Rule: `NAME.conventions`.
- An added or moved member of a sibling prefix cluster (`audit_*`) blocks until the owner extracts the concept or records an exception.
  Rule: `PREFIX.candidates`.
- Files jointly implementing one concept go in a folder named for it (`audit/`). Review judgment.
- Merge units with overlapping responsibilities into one owner; no near-duplicates. Review judgment.
- Run-time data (definitions, configuration, templates) lives in a root folder outside the code package. Review judgment.
- Tests follow the language pack's placement; fixtures sit beside tests loading them. Rule: `STRUCT.folder-placement`.
- Name each test file for the behaviour it checks. Rule: `NAME.test-files`.
- A code move updates test imports in the same step; test files stay where they are. Guidance only.
- A CLI or API entry point is the package's interface and lives in its top tier. Review judgment.
- A finished-product consumer (dashboard, report tool) gets its own root folder and is never imported. Review judgment.
- Generated output and run artifacts live outside the checkout or behind ignored links. Rule: `STRUCT.folder-placement`.
- Version only what must be reproduced or compared, as `<name>/v<N>`; name the current version in one place. Guidance only.
- Never edit a measured version; a change is a new version. Guidance only.
- Never version code modules. Rule: `NAME.conventions`.
- Reference another part by explicit path, never via search order, shadowing or a fallback location. Guidance only.
- Every instance of one kind shares one layout; older instances change only in a brownfield restructure. Guidance only.
- A new same-kind instance includes its configured instance marker. Rule: `STRUCT.folder-placement`.
- Never create an empty folder for a planned layout; it arrives with its first file. Rule: `STRUCT.folder-placement`.

## Naming

- Folder and module names are singular lowercase nouns for their concept or tier (`capability`, `engine`). Rule: `NAME.conventions`.
- Plurals name only a catalogue of same-kind items addressed by name (house-style `catalogue_plurals`). Rule: `NAME.conventions`.
- A name covers everything its folder holds; rename the folder when its contents outgrow it. Review judgment.
- A child name never repeats its parent's tokens: `audit/tokens.js`, not `audit/audit-tokens.js`. Rule: `NAME.conventions`.
- Tokens split at `_`, `-`, `.`, lower-or-digit-to-upper and acronym-to-word boundaries; drop empty ones. Rule: `NAME.conventions`.
- A repeat is the parent's tokens, contiguous, ordered and whole, anywhere in the child, ignoring case. Rule: `NAME.conventions`.
- `audit/log_audit.py` repeats its parent; `rail/railways/` and `audit-log/log-audit.py` do not. Rule: `NAME.conventions`.
- A file stem ends before its first dot, except dotfiles; language-fixed names are declared only in the packs. Rule: `NAME.conventions`.
- Owner-approved `proofs.toml` `exceptions` with `check: child-repeats-parent` keep other naming checks. Rule: `NAME.conventions`.
- Name by behaviour, never version, date or ticket (`test_base_comparison`, not `test_policy4`). Rule: `NAME.conventions`.
- Dates only name time-ordered records such as decisions and reviews; versions only `<name>/v<N>`. Rule: `NAME.conventions`.
- Use one name per concept across code, flags, config fields, docs and tests, and the project glossary if any. Review judgment.
- A rename changes every use in one change, with no old-name alias. Rule: `RENAME.residue`.
- Retire the whole renamed name (`gate_effort`), not each word; unchanged mechanism names (`gate`) stay. Rule: `RENAME.residue`.
- Siblings share one pattern (`one-shot-verify`, `define-build-verify`); keep mechanism names. Review judgment.
- Use plain domain words, never grab-bag or status words (house-style `banned_words`). Rule: `NAME.conventions`.
- Never add a `v<N>` suffix outside a `<name>/v<N>` folder or file. Rule: `NAME.conventions`.
- Code files use the language pack's casing; non-code asset ids use house-style `asset_id` casing. Rule: `NAME.conventions`.

## By project state

- Greenfield naming applies from the first file: every initial path is new. Rule: `NAME.conventions`.
- Greenfield placement applies from the first file: every initial path is new. Rule: `STRUCT.folder-placement`.
- Brownfield additions use new-path scope and never require existing code to move. Rule: `STRUCT.folder-placement`.
- Kind folders are not owners: new behaviour (retry, caching) gets a source-root capability folder. Review judgment.
- If a rule-correct home needs existing code moved, use the matching-role folder (never a grab-bag) and state the mismatch. Guidance only.
- Brownfield restructure moves one concept per step, with no behaviour change in the move. Guidance only.
- Each restructure step updates every import, config, doc, test and tool exclude using the retired name. Rule: `RENAME.residue`.
- Each restructure leaves build and tests passing, without re-export shims or aliases. Guidance only.
- Domain folders share a tier and never import each other; move shared parts down. Rule: `STRUCT.sibling-isolation`.
- Dissolve grab-bags: a file moves under its one owner, or to the shared tier. Rule: `STRUCT.single-owner-placement`.
- Apply [clean cutovers](design-principles.md#clean-cutovers) to each move; then delete migration scaffolding. Guidance only.
