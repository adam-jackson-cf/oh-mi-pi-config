# Per-repository record

A record is personal harness configuration for one repository, not a repository prerequisite
for ordinary Git work. [gate.json](gate.json) is an example; [profile.md](profile.md) is the
harness profile template. Neither is a live record or an approval.

## Location and resolution

Live records belong at `<oh-mi-pi-config root>/repos/<owner>/<name>/`, lowercase. The clone's
`origin` determines the owner and name from `https://github.com/<owner>/<name>(.git)`,
`git@github.com:<owner>/<name>(.git)` or `ssh://git@github.com/<owner>/<name>(.git)`.
Worktrees share their main checkout's record. [repo_record.py](../repo_record.py) and
[repo-record.ts](../repo-record.ts) implement resolution and gate validation.
No supported GitHub origin means no record; record setup reports that prerequisite and stops.

`/repos/` is gitignored. Never stage, commit, include a record in a PR, or copy it into the
target repository. Device rollout copies records to every device. The target repository
contains no harness package, configuration, pin or mention; its own controls remain independent.
Paths naming repo files are repo-relative; paths naming record files are record-relative.
No setting names a harness directory inside the repo.

## Gate schema

The record's `gate.json` uses schema version 2. Required fields are `schemaVersion`,
`checks.commit`, `ownerOnlyPaths` and `dynaflows`. Unknown top-level or `checks` fields fail
the load with the field name; the obsolete path command field is rejected, never ignored.
A documentation-only repo still declares `checks.commit`.

| Field | Contract |
| --- | --- |
| `schemaVersion` | Integer `2`. |
| `checks.commit` | [^n1] |
| `checks.ci` | [^n2] |
| `checks.format` | [^n3] |
| `checks.profile` | [^n4] |
| `ownerOnlyPaths` | [^n5] |
| `agentMerge` | [^n6] |
| `ci` | Optional object with nonblank `requiredContext` and `runner`; see below. |
| `dynaflows` | Required object with Boolean `enabled`; see below. |

Commands are argv arrays, never shell strings. Repo gate and formatter commands name the
repo's own controls, not omp paths. Test globs, tier selectors and layout belong in
`profile.md`, not extra gate fields. Substitute real discovered commands and paths in
[the example gate](gate.json), which retains the dependency-audit exception path, `gate`
context, `github-actions` runner, `agentMerge: allowed` and `dynaflows.enabled: false`.

### Owner-only paths

`ownerOnlyPaths` names files only the owner changes, such as
`scripts/checks/dependency-audit-exceptions.json`. Record-write authority does not permit
changing those files or rewriting their protection. With the harness process enabled,
rails refuse agent commits whose staged or unstaged changes touch them and direct the
agent to the owner. The switch gates that enforcement, not the owner's authority.
See [ADR 0004](../../../docs/adr/0004-dynaflows-flag-gates-the-harness-process.md).

### Merge and CI policy

Git-tool reads `agentMerge` and `ci` independently of the harness process switch.
`agentMerge: disabled` requires the owner's approval to merge; `allowed` never waives CI.
Follow [git-tool](../../skills/git-tool/SKILL.md), not setup, for Git authorization.

`ci.requiredContext` is the commit-status context or check-run name that must report
`success` on the exact PR head, normally `gate`. `ci.runner` is an owner-chosen role,
not a host or account. Each device resolves the role to submit, wait, status and logs
commands in machine-level `ci-runners.json`, written by device setup.
Agents use `ci-runner.py <submit|wait> <runner> <OWNER/REPO> <SHA>`; see
[the Git CI runbook](../../skills/git-tool/references/merge-and-recover.md#declared-ci-contract).

The `github-actions` role runs `checks.ci` on every push and pull request in a job named
for the required context; push triggers it, so no submission is needed. Another role
runs `checks.ci` on the exact SHA and posts that context. For such a runner, add the
repo's `.devcontainer/devcontainer.json` with `build.dockerfile` or `image` matching the
runner toolchain and optional `postCreateCommand` for dependency installation. Register
the repo using that runner's project or machine instructions. Missing registration,
credentials or machine runner entries block setup for owner provisioning; never switch
roles as a workaround. A non-Actions runner may retain a weekly and manual
`workflow_dispatch` GitHub workflow as a fallback rather than running it on PRs.
Git-tool's exact-head verification loop governs submission, waiting and fallback use.

### Process switch and events

`dynaflows.enabled` switches the harness process on or off. No origin or no record means
off. Write `true` only when the owner asked for it in chat; otherwise write `false`.
A later owner request can enable it. Off means plain omp, git-tool and generic Git safety,
not bypassing declared merge or CI policy.

When enabled, rails run `omp-guardrails path-check --repo <worktree> --record <record dir>`
after edits and before commit. Required `guardrails.structure`, `guardrails.naming` and
`guardrails.paths` configure this harness-owned per-edit contract, even where repo checks
overlap; see [ADR 0002](../../../docs/adr/0002-harness-owns-path-checks.md).
At commit, rails then run `checks.commit` in the worktree followed by
`omp-guardrails check --repo <worktree> --record <record dir>` over the whole repository.
Run the engine from `agent/extensions/dynaflows/guardrails` using `uv run --project`.
Any finding blocks; no baselines or diff scope apply to whole-repo checks.
CI retains the repo's own required context and gate. There is no pre-push hook or
separate handoff check.

## Profile schema and checker ownership

Start the live `profile.md` from [this harness template](profile.md), not the generic
test-guidance template. It contains one JSON object between `---` lines, then prose.
JSON is valid YAML front matter; the engine reads it without a YAML parser.
Keep the machine-readable values and human sections in step.

The template preserves tiers, public entry points, run events, per-machine budgets,
coverage, guards, environments, verification surface, ownership areas, identity artefacts,
layout and command notes. Harness additions are `adapter`, `structure` (owners, tiers and
import direction), `house_style` and `guardrails`. Fill target roots, exclusions,
allowlists, naming/ownership maps and CI wiring explicitly.

These values support dependency direction, folder ownership, naming, test placement,
fast-tier and per-test budgets, justified test growth and duplicate call-path/assertion
detection. Test evidence covers profile completeness, test-file names, tier selectors,
no self-mocking, no test hooks, no evasion, fast-tier isolation, the user-data guard,
coverage ratchets, golden change records, identity artefacts, property seeds, mutation
sensitivity and the verification surface. Per-machine budgets use a deterministic local
machine probe and at least five baseline runs. They apply only to the local commit gate;
CI never measures or reports test timing.

- `guardrails.<checker>` holds per-checker settings; examples include `guardrails.python`,
  `guardrails.structure` and `guardrails.naming`. Unknown section names outside
  `GUARDRAILS_SECTIONS` in `omp_guardrails/config/profile.py` fail `profile-check`.
- `guardrails.check.run` selects whole-repo steps; absent selection means all
  `DEFAULT_STEPS` in `omp_guardrails/whole_repo.py`.
- `guardrails.check.exclude` lists generated, vendored and fixture paths to exclude.
- `guardrails.check.repo` maps each repo-owned step to checker files that jointly enforce
  every rule it reports, for example
  `{"python-quality": ["scripts/checks/check-python-quality.py"]}`. `checks.commit` runs
  those checkers, so the harness leaves that step out, never running a duplicate.
  Remove settings only delegated steps read; `guardrails.python` is rejected when every
  configured Python step belongs to the repo.
- `house_style.version` records the shipped `omp_guardrails/data/house-style.json` version.
  Exceptions are `{rule, path, adr}` objects; an exception without an ADR is ignored.
- `structure.import_direction.native` runs the repo's import-contract command for paths
  matching `scan` or `source_root`, regardless of extension, in both `path-check` and
  `structure`. Without path arguments the repo inventory selects sources. Python AST
  direction checks remain Python-only.

Each checker module's docstring defines settings and defaults. Defaults grant nothing:
an unset allowlist approves no path; an unset rule scope covers every maintained root.
Unconfigured repositories fail with findings rather than silently passing. Numeric defaults
are catalogue contract values. Settings cannot express a new checker behaviour: change the
harness engine instead, never install a harness package in the target. Pin repo-owned
tools through the repo's own package manager.

## Drafts, approvals and application

`omp-guardrails init --repo <clone> --record <record dir>` drafts directly in a live record
or a draft directory for owner confirmation, without writing into the repo. During setup,
keep the candidate draft outside the live records directory and start extend mode with a
copy of the existing record. Init discovers make targets, hook managers, CI jobs and
required checks through `gh`. It drafts `gate.json`, `profile.md` and native-tool proposals
(Ruff, mypy, import-linter) under `proposed/`, and proves language-server readiness with a
canary `references` query. It preserves recorded values and never invents an approval.
`--enable-dynaflows` drafts the switch on only following the owner's chat request.
`--commit-command` and `--ci-command` select repo gates; `--runner` and
`--required-context` set `ci`. Missing `checks.commit` is reported as `required:`.
There is no path-command option. `--settings` takes per-checker JSON; `check` takes no
settings overrides.

Present every command and discovered source (make target, hook, CI job, required check),
CI fields, owner-only paths, scope exclusions and profile value for confirmation. Explain
replacement and exception consequences, including removed hooks, CI, configs and docs.
The setup's draft `guardrail-proposal.json` has
`{"schemaVersion":1,"minimal":[...],"full":[...]}` with repo-relative candidate paths.
It travels with the confirmed record, never into the target repository.
The setup workflow owns Minimal/Full or Approve/Revise decisions: no elapsed time or
default option is approval. Do not apply the draft or candidate paths before the owner answers.
After confirmation, the agent writes the live record directly; never ask the owner to save it.
Compare the draft and confirmed profile with the approved matrix before applying repo fixes.

All approvals and exceptions live only in the record's profile, never a PR. They include
`approved_skips`, any `approval`, `approved_*` or `*_approval`, `user_approval`,
`house_style.exceptions`, fast-tier machine approvals and coverage-ratchet approvals.
Write each only as the owner stated it in chat. Record-write authority neither changes
`ownerOnlyPaths` nor approves repository fixes; the setup approval remains the boundary
before applying them.

## Field details

[^n1]: Required nonempty argv array for the repo's static checks, every T1 and affected T2 boundary
  tests.
[^n2]: Optional nonempty argv array for the repo's full T1 and T2 gate. CI runs this as one gate.
[^n3]: Optional nonempty argv array for the formatter `freeze()` runs before hashing proofs.
[^n4]: Optional nonempty argv array for profile completeness, normally `omp-guardrails
  profile-check`; `prove()` does not run it.
[^n5]: Required list of unique repo-relative POSIX paths; `[]` when none. A trailing `/` covers a
  directory.
[^n6]: `allowed` or `disabled`; absent field or absent record defaults to `allowed`, subject to
  governing Git authorization and CI.
