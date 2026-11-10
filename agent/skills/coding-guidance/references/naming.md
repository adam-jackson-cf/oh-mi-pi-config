# Naming

Name for what it holds or does; one name per concept.
The word lists, casing per ecosystem, `v<N>` patterns and plural allowlist are data in
[house-style.json](../../setup-repository-guardrails/assets/python-reference/omp_guardrails/data/house-style.json); `omp-guardrails naming` enforces them.

- Folder/module: singular lowercase noun for its concept or tier (`capability`, `harness`, `engine`).
  Plural only for a catalogue of independent same-kind items addressed by name (house-style `catalogue_plurals`).
- A name covers everything the folder holds; when contents outgrow it, rename the folder.
- A child never repeats its parent's name; name it by role (`engine/workflow/stages`, not `engine/workflow/workflow`).
- Name by behaviour, never by version, date or ticket (`test_base_comparison`, not `test_policy4`).
  Dates only for time-ordered records (decision record, review).
  Versions only as `<name>/v<N>` folders or files ([Structure and grouping](structure-and-grouping.md)).
- One name per concept across code, flags, record fields, docs and tests; use the project glossary when one exists.
  A rename changes every use in one change, with no alias for the old name.
- Siblings share one naming pattern (`one-shot-verify` beside `define-build-verify`).
  Keep a name that describes a mechanism when a product name changes.
- Use plain domain words, never a vague grab-bag or status word (house-style `banned_words`);
  never add a `v<N>` suffix outside a `<name>/v<N>` folder or file.
- Use the ecosystem's casing for code files and the house-style `asset_id` casing for ids of non-code assets.
