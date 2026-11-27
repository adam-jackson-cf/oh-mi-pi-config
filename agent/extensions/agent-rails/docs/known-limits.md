# Agent-rails known limits

What the rails do not catch or measure today.

- `RENAME.residue` sees a rename only at Git's 50% similarity (`RENAME_THRESHOLD` in
  `rename_residue.py`). A rewrite recorded as delete plus add gives no rename evidence, and a
  retired term reused outside the renamed folder or file is not flagged.
- `STRUCT.runtime-data-placement` judges each language on its own: in a folder holding Python and
  TypeScript, Python still flags JSON the TypeScript code imports. Data whose nearest code sits in a
  sibling folder, and `.md` templates, are not flagged. TypeScript JSON is exempt only when a source
  file in the same folder imports it.
- `STRUCT.overlapping-responsibilities` runs for Python only: TypeScript and Go have no measured
  cutoff yet (`engine/data/structure-languages.json`). Its similarity counts shared 3-grams held by
  at most 40 definitions, so copies built from common idioms score low, and code repeated in another
  language or inside one folder is not flagged.
- `STRUCT.prefix-folder` runs for Python only: TypeScript has no measured weights or cutoff yet, and
  the rule does not apply to Go, where a folder is a package.
- The structure rules read TypeScript and Go through tree-sitter grammars (`ast-grep-py`), which can
  lag the languages: tree-sitter-go rejects Go 1.26 `new(expression)`, so a file using it is reported
  as unreadable. tree-sitter also accepts a few Python errors CPython rejects (a repeated keyword
  argument); the parser adds CPython's argument-order check, which keeps the measured Python results
  identical on 57,016 definitions in 8 repositories.
- tree-sitter-python in the pinned ast-grep-py 0.51.0 rejects Python 3.13 type-parameter defaults
  (`class Box[T = int]:`, `def f[T = int]()`, `type X[T = int] = list[T]`). The engine reads Python
  through tree-sitter, so such a file is unparseable: `CODE.no-comments`, `PYS000` from the
  naming and layout checks, and `FANOUT.general` report it, and the prefix-folder, overlap and
  candidate proposers skip it. The PYQ, PYS and `TEST.subprocess-timeouts` checks read CPython's
  own tree (they compare `ast.unparse` text with configured annotations) and accept it.
- The tree-sitter TSX and JavaScript grammars reject a bare `&` in JSX text
  (`<span>Findings & annotations</span>`), which React accepts. Such a file gets a blocking
  "cannot parse source" finding from the bun_test and vitest adapters and from
  `CODE.no-comments` (which reads TypeScript, JavaScript and Go comments through the grammars),
  and is unreadable to the structure rules. ast-grep-py 0.51.3, the newest release on 2026-11-26,
  still rejects it; the engine pins 0.51.0. Writing `&amp;` or `{"&"}` avoids it. A Go file using
  Go 1.26 `new(expression)` likewise gets a `CODE.no-comments` "cannot parse" finding.
- `TEST.no-test-hooks` tallies top-level functions and classes (Python), package-level
  functions and types (Go) and top-level declarations (TypeScript): a method only tests call is
  not reported. A reference resolves through imports and module attributes, so a call through an
  object (`cache.reset()`) is no use of a module's `reset`, and a local name that shadows a
  top-level one in the same file counts as a use of it. Python console scripts and modules a file
  names by a literal dotted string (importlib dispatch) count as production uses; a module named
  by a computed string, a function fetched from a dictionary of helpers, or code held in a string
  does not, so such a function is reported unless it is the package's public entry point.
- The structure-naming candidate proposers (`kind-candidates`, `consumer-candidates`, `name-pairs`)
  propose Python only (`proposers.languages` in structure-languages.json): their judgments were
  measured on Python cases. Their TypeScript and Go readings (`proposers.signals`) run only when a
  measurement asks for them. The TypeScript consumer reading maps a package.json `bin` path to
  TypeScript source only for the same stem (`bin/x.js` to `bin/x.ts`), not across a build's output
  folder (`dist/` to `src/`); a Go import names a package, so every file of a package has the
  package's importers. `name-pairs` keeps the Python 3-gram reading `same_thing` was measured
  on, which drops triple-quoted strings and cuts from a `#` to the line end even inside a string;
  reading comments and docstrings from the tree instead drops labelled pair N17-0083 from the clone
  rule, so the change waits for a new measurement.
- `structure-naming/same_thing` judges only the name pairs `name-pairs` proposes (alias, head noun,
  synonym, constant value, clone); two names for one concept that none of those rules links are
  never asked. Its held-out precision is 0.72 (13/18), below the 0.75 floor, by owner acceptance.
- `STRUCT.prefix-folder` scores only clusters with a member added or moved in the change, and its
  cutoff 0.15 flags most clusters (in-sample 62 of 63 labelled clusters, 47 correct).
- `structure-naming/technical_kind_grouping` runs in shadow mode: no real violation is labelled yet,
  so its flags are logged for review and never block until 10 labelled flags measure it.
- `structure-naming/consumer_placement` asks only files `consumer-candidates` proposes (a run marker,
  no importer, no path, module-string or console-script reference) and runs in shadow mode: 30
  labelled real files hold 2 violations, too few flags to measure precision.
- `STRUCT.entry-point-placement` judges regular packages (`__init__.py` chains) and entry modules by
  name (`__main__.py`, `cli.py`); command scripts with other names are not flagged.
- No rail stops an agent commit that edits a repo's dependency-audit exception file; the owner
  catches it at PR review.
- A command word built only from variables, with no literal `git` or `gh` text (`$X$Y pr merge`), is
  not detected.
- `completion-evidence` pre-check P3 (linked plan) is not wired: no labelled PR links a plan or
  issue. Turn-end obligations stop after 3 follow-ups (`MAX_FOLLOW_UPS`); the owner is told once.
- Rails scan shell and eval text, so obfuscated code can evade them. The owner keeps server-side
  protection: the default-branch ruleset and a required `gate` status only the gate identity can
  post.
- Two rules stay instructions in `agent/AGENTS.md`: no Git in the NUC's `~/.omp` (remote `ssh`
  shells are opaque), and restart sessions after rail changes (old sessions keep old rails).
- The decision store logs deterministic check results only for `prove()`'s rename-residue,
  path-check and structure checkers; commit-time checks are not logged.
- Only bool decisions and choice decisions with an option threshold (`consumer_placement`) run in
  shadow mode: a rubric choice, such as `dependency-severity`'s `impact`, has no outcome that leaves
  the rubric unchanged.
- Passes keep their payload only for a stable 1-in-10 sample, so a sampled review sees a tenth of
  passes. `shadow-review --machine nuc` copies labelled NUC decisions home; the NUC's own store is
  only read, so its payloads are never pruned.
- Turn-end `session_stop` blocks and the `agentrails` tool's refusals carry no rail code, so the
  rails ledger does not count them.
