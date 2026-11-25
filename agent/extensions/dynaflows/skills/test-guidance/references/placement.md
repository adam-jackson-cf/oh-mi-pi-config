# Placement

## Objective

Define the adoption options, where each object goes, and how the installed skill's text is filled.

## Guidance

- **Rules.** A rule is one bullet of a runbook template in `assets/templates/installed/references/` or of the index template's
  `Every task` section. Against the repository a rule is `stated` (a repository file says the same or stricter), `contradicted`
  (a conflict: a repository rule, config, marker or layout forbids what the rule allows, requires what it forbids, or tiers
  by speed alone, whether or not a planned change touches it; adopting a layout rule never moves existing tests) or
  `missing`; a repository rule narrower than the approach rule (covering part of its scope) leaves it `missing`.
  A runbook section is a rule group.
- **Options.** Each option decides which rules the repository takes on and where they live:
  - `pointer-only`: existing files gain only pointer lines (the root pointer creates the root instruction file when none
    exists). Missing rules go into installed runbooks; stated rules are routed
    to their repository file; on a conflict the repository wins and the rule is not adopted. No command, collection or test changes.
  - `merge`: each missing rule goes into the instruction file that states the most rules of the same rule group (one
    stated rule is enough; the shallower file on a tie), under one heading per file; with no such file, into its runbook.
    Stated rules are routed to their file. The owner decides each conflict (default: keep the repository rule).
  - `full`: runbooks hold every rule. An instruction-file bullet duplicating a runbook rule is replaced by a route line
    to the runbook; a rule human documentation states stays in full in the runbook, and that document is unchanged.
    The owner decides each conflict (default: the approach rule).
  - Under every option, `Every task` rules stay in the index: a stated one becomes the bullet `- See <path> (<heading>).`,
    and a contradicted one the repository keeps is removed. A bullet holding placeholders always keeps its filled facts
    (commands, CI, schedules, hook, fixes); when its rule is stated, its rule sentences are dropped and it ends with
    ` See <path> (<heading>).`
  - A conflict that would put a shared helper, fake or substitute where a repository rule forbids it defaults, under every
    option, to keeping the repository rule: no such helper, and its exemplar is rewritten without it or dropped.
  - No option edits a repository file except to add these rules, route lines and pointers, the optional rows chosen,
    and under breadth `exemplars` the exemplars with their helpers and design changes. Rules and route lines go only into
    instruction files (`AGENTS.md`, `CLAUDE.md`, scoped ones) and installed skill files; human documentation (READMEs,
    docs folders, contributor guides, ledgers) is read and routed to, never edited. An instruction that would require
    editing human documentation for this change is a Step 2 conflict whose default, under every option, leaves it
    unchanged. No written file names a path outside the repository.
- **Template text** outside placeholders is copied verbatim; the only change an option makes is removing the bullets it
  does not adopt (and a heading left with no bullets) and adding route lines. A runbook route line is the bullet
  `- See <path> (<heading>).` at the end of the section that lost rules, or under the title when that section is gone.
  Merged rules keep their wording and take the target file's list style and wrapping.
- **Recommendation.** Recommend `merge` when an instruction file states at least one approach rule, else `full`.
- **Homes.** Each object's location comes from the first that exists:
  1. a named home: a location an instruction file names for that kind of object;
  2. an observed home: where existing objects of that kind already sit (commands in a shell script folder, package scripts,
     a task runner or a Makefile all count as the command surface);
  3. the skill default below; the plan row then cites the searches that found no named or observed home.
  Files of an earlier install are never a named or observed home.
- **Defaults.** Index: `.agents/skills/<skill_name>/SKILL.md`; runbooks: its `references/`; helpers: the shallowest support
  folder or shared fixture file covering every test that uses them; root pointer: the root instruction file, created as
  `AGENTS.md` when none exists, or a standalone root `CLAUDE.md` when that is the only one. A path, structure or naming
  check the repository enforces wins over a default (for example runbooks beside the index when only one level is
  allowed); the plan row cites the check, and the index records it under `Repository decisions`.
- **Object kinds** are closed: index skill, runbook, rule section, instruction pointer, helper, exemplar, design change.
  **Actions** are closed: `new`, `merge` (rules into an existing file), `edit`, `delete`.
- **Pointers.** The root pointer renders `assets/templates/agents/root-section.md` into the root instruction file; when
  that file has a section whose heading names tests or testing, only its bullet is added there. Every other instruction
  file with such a section gets the bullet too. A file that already points to the skill gets nothing.
- **Symlink.** When the repository has a `CLAUDE.md` or a `.claude/` folder, `.claude/skills/<skill_name>` is a relative
  symlink to the folder holding the index as Homes resolved it, unless the index already lives at that path or a link on
  its path (such as `.claude/skills` to `../.agents/skills`) already reaches that folder; then nothing is added.
- **Runbooks installed.** A runbook installs only when it holds at least one adopted rule and its condition holds:
  `writing-tests`, `flaky-tests` and `suite-maintenance` always; `boundary-tests` when T2 tests or a runnable entry point
  (CLI, server, app) exist; `property-tests` when a property library or property tests exist, else as an optional row,
  default no; `golden-files` when golden or snapshot files exist.
- **Placeholders** take values from `inventory.md`, one line each, describing the repository as the plan leaves it.
  A fact the inventory lacks is omitted, never filled with a claim that something is absent: drop its clause from a line
  holding several facts, and the whole line when it holds no other fact. A placeholder's own fallback below is an
  instruction, not an absence claim, and applies instead.
  - `skill_name`: `testing-approach`, or an earlier install's name. `index_path`: the index's path from the repository
    root as Homes resolved it. `repo_name`: from the remote URL, else the Git root folder.
  - `tier_map` and deferral bullets: as [tiers](tiers.md) defines.
  - `entry_points`: `<package>: <interface>` per package, what it exports and the commands it installs.
  - `layout`, `fixtures`, `support`: paths and file patterns; `fixtures` is shared setup tests request, never test data.
  - `inputs_pattern`: how production code receives time, randomness, IDs and processes, with a real example, then
    `; tests pass <helper>` per helper; fallback, also when code reads them directly: `parameters with production defaults, such as a clock argument`.
  - `naming_example`: a repository test name that states behaviour, condition and outcome.
  - `coverage`: `floors in <config>: <values>`; fallback `no floor is configured; add none`.
    `timeout_config`: `<config> sets <limit>`; fallback `none configured`.
  - `local_checks`: the static checks (compile, type-check, lint, vet, format-check), all T1 and the affected T2, by
    command. A commit hook reads `the commit hook configured in <config> (installed with <command>)`.
  - `merge_checks`: `CI runs <commands> on pull requests. `. `scheduled_checks`: one sentence per scheduled workflow,
    `<workflow> runs <commands> on a schedule. `. Each is omitted when none exists.
  - `fix_command`: `only through <command>`; fallback `none is documented, so run checks without fixing`.
  - `boundary_launch`, `property_library`, `golden_files`: only with their runbook. The launch call T2 tests use, with its
    environment and timeout; the property library in use; golden folders with `<verify command>; regenerate with <command>`,
    or without a regenerate command `<verify command>; no regeneration command is documented, so ask the user before adding one`;
    when a check requires a change record for changed goldens, then `; record each change in <path> as <entry form>`.
  - `route_<task>`: the repository files and headings holding that task's stated rules, as `<path> (<heading>)`, then the
    installed runbook as a Markdown link whose text is its path from the repository root and whose target is relative to
    the index (`references/<runbook>.md` at the default home), joined by `, then `. With neither, its route line is
    removed from the index.
  - `repository_decisions`: one bullet per declined rule or optional row, conflict decision, rejected plan row, deferral or owner fact from an earlier install,
    with its reason, then `- Installed by this skill: <path> (<heading>), ...` naming every installed file and merged heading.
- **Rendering checks.** No `{{` remains; the index keeps its frontmatter and its body starts at `# Task`; every route resolves.
- **Not installed.** Hooks, CI jobs, checkers, tier commands, runner collection settings, and automatic or session-wide
  guards. Never change what an existing gate, hook or CI job runs, including the scripts they call and the configuration
  that selects their tests (such as a file mapping packages to tiers).
