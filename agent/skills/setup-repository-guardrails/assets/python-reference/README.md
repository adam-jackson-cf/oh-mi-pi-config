# Python reference implementations

These files implement the bundled guardrail rules with Python AST checks, import
normalization, portable thresholds, and Ruff and mypy settings. The authoritative
contract is `../guardrail-rules.json`; no external source checkout is required.

They are reference implementations, not drop-in target files. Before application,
the target adapts every per-target configuration below from its maintained-source
inventory and approved ownership maps:

- `python_quality_scope.py`: populate `MAINTAINED_ROOTS` and, where relevant,
  `SKILL_ROOTS`. Empty maintained roots leave discovered Python files unmapped
  (`PYQ100`), not approved. Add exclusions only for documented non-maintained
  paths; generic build, dependency, and tool-cache exclusions are bundled.
- `check-python-quality.py`: populate test/script roots, canonical test groups,
  script names, non-package directory names, and justified path exceptions. Map
  analyzer roots and approved bases, project-analyzer bases to entrypoint method
  names, dynamic-import scope and adapters, and contract files. Empty allowlists
  grant no exceptions. Subprocess calls require an approved file/function wrapper.
- `check-python-scalability.py`: set `ANALYZER_PREFIX` to the target's analyzer-like
  source prefixes and `OWNERSHIP_MARKERS` to its approved ownership files. An empty
  prefix tuple means no analyzer-like scope has been configured, not proof that
  those conditional rules are inapplicable. Loop-depth checks remain independent.
- `check-python-fanout.py`: pass every maintained import root with `--source-root`
  and review any additional non-maintained exclusions.
- `mypy.ini`: set the supported Python version, every maintained import root and
  package, and justified exclusions. Add named module overrides only with evidence
  for missing stubs or decorator typing; no project-specific exceptions ship.
- `ruff.toml`: set the supported Python version and maintained first-party package
  names. Review exclusions and lint exceptions against the target inventory.

All empty collections and per-target comments are configuration obligations, not
completed target enforcement. Copying these files without adaptation does not
satisfy the rule-coverage gate. Prove every applicable rule with positive and
negative canaries and reject missing coverage. A target without Python maps the
portable semantics to its chosen language; Python-only exclusions still need
user-approved evidence.
