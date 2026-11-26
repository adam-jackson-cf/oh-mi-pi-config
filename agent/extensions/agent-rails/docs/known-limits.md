# Agent-rails known limits

What the rails do not catch or measure today.

- `RENAME.residue` sees a rename only at Git's 50% similarity (`RENAME_THRESHOLD` in
  `rename_residue.py`). A rewrite recorded as delete plus add gives no rename evidence, and a
  retired term reused outside the renamed folder or file is not flagged.
- `STRUCT.runtime-data-placement` treats only folders holding Python source as code: data in
  non-Python code (a Go package), data whose nearest Python sits in a sibling folder, and `.md`
  templates are not flagged, while config beside a Python script folder is.
- `STRUCT.overlapping-responsibilities` reads Python only, and its similarity counts shared 3-grams
  held by at most 40 definitions: copies built from common idioms score low, and code repeated in
  another language or inside one folder is not flagged.
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
