# Python reference checkers

These standalone scripts implement the catalogue's Python bespoke rules with AST checks, import normalisation and portable
thresholds: `check-python-quality.py` (`PYQ100`–`PYQ299`, except `PYQ201`), `check-python-scalability.py` (`PYS000`–`PYS253`)
and `check-python-fanout.py` (`FANOUT.general`, `FANOUT.large-module`), sharing `python_quality_scope.py`.
The contract is `../guardrail-rules.json`. They need Python 3.11+ and the standard library only.
Ruff and mypy settings come from the catalogue's tool rows through `scripts/propose_tool_configs.py`.

They are reference implementations, not drop-in files. The module-level constants are the per-target settings. Before application,
move every one the target needs into `proofs.toml` `[python_quality]` (design principle 3: no value lives in a checker's source),
make the adapted copies read that table with `tomllib`, and own them as ordinary repo code with their own tests:

- `python_quality_scope.py`: maintained roots and, where relevant, skill roots. Empty maintained roots leave
  discovered Python files unmapped (`PYQ100`), not approved. Add exclusions only for documented non-maintained paths;
  generic build, dependency and tool-cache exclusions are bundled. Files come from `git ls-files`.
- `check-python-quality.py`: test and script roots, canonical test groups (match the native test layout, for example pytest's
  `tests/`, so `PYQ102` accepts it), script names, non-package directory names and justified path exceptions. Map analyzer roots
  and approved bases, project-analyzer bases to entrypoint method names, dynamic-import scope and adapters, and contract files.
  Empty allowlists grant no exceptions. Subprocess calls require an approved file/function wrapper. `GENERIC_NAME_PARTS` equals
  the `banned_words` of `../conventions/house-style.json`.
- `check-python-scalability.py`: set `ANALYZER_PREFIX` to the target's analyzer-like source prefixes and `OWNERSHIP_MARKERS` to its
  approved ownership files. An empty prefix tuple means no analyzer-like scope is configured, not proof that those conditional
  rules are inapplicable. Loop-depth checks remain independent.
- `check-python-fanout.py`: pass every maintained import root with `--source-root` and `--root-package`; review any additional
  non-maintained exclusions.

`PYQ201` (public methods per class) reads the owner-approved limit from `proofs.toml` `[python_quality]`; implement it in the adapted
quality checker or with Ruff `PLR0904` at the approved limit.

Usage after adaptation, from the target root:

```text
python3 <path>/check-python-quality.py --repo-root . [paths ...]
python3 <path>/check-python-scalability.py --repo-root .
python3 <path>/check-python-fanout.py --repo-root . --source-root <root> --root-package <package>
```

Each exits 0 when clean and nonzero with one finding per line naming the file, line, rule and problem;
add the concrete fix text when adapting, as the catalogue diagnostics require.
All empty collections and per-target comments are configuration obligations, not completed enforcement.
Copying these files without adaptation does not satisfy the coverage validator. Prove every applicable rule with positive and
negative canaries. A target without Python maps the portable semantics to its own language; Python-only exclusions still need
owner-approved evidence.
