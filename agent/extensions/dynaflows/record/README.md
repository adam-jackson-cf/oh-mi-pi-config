# Per-repository record

A record is personal harness configuration for one repository, not a repository prerequisite
for ordinary Git work. [gate.json](gate.json) is an example; [profile.md](profile.md) is the
harness profile template. Neither is a live record or an approval.

## Location and resolution

Live records belong at `<oh-mi-pi-config root>/repos/<owner>/<name>/`, lowercase. The clone's
`origin` determines the owner and name from `https://github.com/<owner>/<name>(.git)`,
`git@github.com:<owner>/<name>(.git)` or `ssh://git@github.com/<owner>/<name>(.git)`.
Worktrees share their main checkout's record. [repo_record.py](../lib/repo_record.py) and
[repo-record.ts](../lib/repo-record.ts) implement resolution and gate validation.
No supported GitHub origin means no record; record setup reports that prerequisite and stops.

Agents write and repair records at any time, through any tool, in any session
([ADR 0001](../../../../docs/adr/0001-agents-write-records.md)). Rails never refuse a record write
and never snapshot the record: they re-read it on every action, so a changed record takes effect
on the next commit, merge or check ([ADR 0018](../../../../docs/adr/0018-rails-trust-only-what-they-saw.md)).

`/repos/` is gitignored. Never stage, commit, include a record in a PR, or copy it into the
target repository. Device rollout copies records to every device. The target repository
contains no harness package, configuration, pin or mention; its own controls remain independent.
Paths naming repo files are repo-relative; paths naming record files are record-relative.
No setting names a harness directory inside the repo.

## Gate schema

The record's `gate.json` uses schema version 2. Required fields are `schemaVersion` and
`checks.commit`; the mode lives in [`modes.json`](../modes.json), and `dynaflows` is refused.
Unknown top-level or `checks` fields fail the load with the field name; the obsolete path
command field is rejected, never ignored.
A documentation-only repo still declares `checks.commit`.

| Field | Contract |
| --- | --- |
| `schemaVersion` | Integer `2`. |
| `checks.commit` | [^n1] |
| `checks.ci` | [^n2] |
| `checks.audit` | [^n6] |
| `checks.format` | [^n3] |
| `checks.profile` | [^n4] |
| `agentMerge` | [^n5] |
| `ci` | Optional object with nonblank `requiredContext` and `runner`; see below. |
| `afterMerge` | Optional `{argv, paths?}`; see [After merge](#after-merge). |
| `mergePolicies` | Optional nonempty list; see [Merge policies](#merge-policies). |

Commands are argv arrays, never shell strings. Repo gate and formatter commands name the
repo's own controls, not omp paths. Test globs, tier selectors and layout belong in
`profile.md`, not extra gate fields. Substitute real discovered commands and paths in
[the example gate](gate.json), which retains the `gate`
context, `github-actions` runner and `agentMerge: allowed`.

### Merge and CI policy

Git-tool reads `agentMerge` and `ci` in both dynaflows modes.
`agentMerge: disabled` requires the owner's approval to merge; `allowed` never waives CI.
Follow [git-tool](../skills/git-tool/references/authority-and-safety.md), not setup, for Git
authorization.

`ci.requiredContext` is the commit-status context or check-run name that must report
`success` on the exact PR head, normally `gate`. `ci.runner` is an owner-chosen role,
not a host or account. Each device resolves the role to submit, wait, status and logs
commands in machine-level `ci-runners.json`, written by device setup.
Agents use `ci_runner.py <submit|wait> <runner> <OWNER/REPO> <SHA>`; see
[the Git CI runbook](../skills/git-tool/references/merge-and-recover.md#declared-ci-contract).

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

### Merge policies

Each entry is `{base, headPrefix, method, proof}`. Structured rails use the first entry whose
`base` equals the PR's base branch and whose `headPrefix` starts its head branch.

| Field | Values |
| --- | --- |
| `base` | Base branch name |
| `headPrefix` | Head branch prefix; `""` matches every head |
| `method` | `rebase` or `merge`: the one `gh pr merge` flag accepted |
| `proof` | `true` needs the `prove()` and `structure_review()` results; `false` skips them |

No match means `method: rebase`, `proof: true`. Rails always refuse `--admin`; no policy waives CI.

### After merge

`afterMerge.argv` is a repo command structured mode requires after a merge. With `paths` (globs),
only a merge whose PR files match one glob requires it. The agent runs it in that repository as a
whole shell command; until a run exits 0, the session's turn-end obligations name it.

### oh-mi-pi-config example

The record for `adam-jackson-cf/oh-mi-pi-config` (personal, not tracked) holds:

```json
{
  "ci": { "requiredContext": "gate", "runner": "ci-gate" },
  "mergePolicies": [
    { "base": "nuc-config", "headPrefix": "agent/nuc-sync-", "method": "merge", "proof": false }
  ]
}
```

The cutover migration `lib/migrate_records_to_mode_file.py` adds the `mergePolicies` entry
([ADR 0017 cutover steps](../../../../docs/adr/0017-dynaflows-runtime-rails.md#cutover-steps)).

| Fact | Enforced by |
| --- | --- |
| `nuc-config` sync: PR from `agent/nuc-sync-<date>`, `--merge`, no proof | `mergePolicies` |
| Every other PR rebases with proof | Default policy |
| `gate` must be green on the exact head | `ci` |
| `ci-gate` is `scripts/ci_gate.py` on the NUC | `~/.omp/ci-runners.json` |
| Missing `scripts/ci-gate-repos.json` entry: a setup PR, never a CI waiver | Not enforced |
| A passing `ci-quality-gates.yml` run never replaces a missing `gate`; escalate | Not enforced |

[ADR 0017](../../../../docs/adr/0017-dynaflows-runtime-rails.md#not-enforced) records both as not enforced.

The fallback dispatch is in the [readme](../../../../readme.md#ci-runners); this repo never takes
the no-CI exception because it declares `ci`.

### Process mode and events

The record holds no mode. [`modes.json`](../modes.json) in the extension is the one source:

| Entry | Mode |
| --- | --- |
| `"<owner>/<name>": "structured"` | Structured, once the record loads |
| Unlisted | Minimal |
| No origin, non-GitHub origin | Minimal |

The `dynaflows` tool and the `$dynaflows` directive write it; minimal removes the entry.
[ADR 0015](../../../../docs/adr/0015-dynaflows-minimal-and-structured-modes.md) records the modes;
[ADR 0017](../../../../docs/adr/0017-dynaflows-runtime-rails.md) the mode file.

Minimal mode keeps the fixed guards: the `skill://git-tool` read before every Git or GitHub write
(`rails.yml` `gitCommand`),
hook-bypass refusal, worktree placement, subagent path safety, the `agentMerge` policy, no
`gh pr merge` and no `git push` to the remote's default branch. Pushes to other branches and
`gh pr create` pass.

Structured mode keeps those guards except the merge refusal. Rails also require reading
`skill://git-tool/references/merge-and-recover.md` before Git integration commands, require a
`prove()` pass, a `structure_review()` result and green exact-head CI before `gh pr merge`, and run
`guardrails path-check --repo <worktree> --record <record dir>` after edits and before commit.
Required `guardrails.structure`, `guardrails.naming` and `guardrails.paths` configure this
harness-owned per-edit contract, even where repo checks overlap; see [ADR 0002](../../../../docs/adr/0002-harness-owns-path-checks.md).
At commit, rails then run `checks.commit` in the worktree followed by
`guardrails check --repo <worktree> --record <record dir>`.

Judged rails follow the deterministic checks
([ADR 0017](../../../../docs/adr/0017-dynaflows-runtime-rails.md#judged-rails)):

| Event | Judgment | Reads from the record |
| --- | --- | --- |
| Commit | `implementation-integrity` | Nothing; the staged diff and message |
| Merge | `adr-needed` | Nothing; the PR, unless it adds an ADR file |
| Turn end | `completion-evidence` | `ci.requiredContext` for pre-check P6 |
| Restart | `extension-impact` | `gate.json` in full and a `profile.md` summary |
| `ask`, privileged command | `in-scope` | Nothing; owner messages in the session |

An escalated outcome waits for the owner's prompt line `$approve <judgment>:<id>`.
Naming and structure checks cover only added or renamed paths: worktree changes at edit,
staged changes at commit, and merge-base/head changes at PR. Only new path segments are
checked, never unchanged legacy parent directories. Content edits do not trigger NAME/STRUCT.
Explicit CLI paths select a diagnostic scope; no paths select a full scan.
Other whole-repository checks remain blocking without baselines.
Run the engine from `agent/extensions/dynaflows/engine` using `uv run --project`.
CI retains the repo's own required context and gate. There is no pre-push hook or
separate handoff check.

## Profile schema and checker ownership

Start the live `profile.md` from [this harness template](profile.md), not the generic
test-guidance template. It contains one JSON object between `---` lines, then prose.
JSON is valid YAML front matter; the engine reads it without a YAML parser.
The front matter is authoritative; any prose a live profile keeps must agree with it.

The template preserves tiers, public entry points, run events, per-machine budgets,
coverage, guards, environments, verification surface, ownership areas, identity artefacts,
layout and command notes. Harness additions are `adapters` (one entry per test framework, each
with its per-tier selector and location; a tier keeps only its `command`), `structure` (owners,
tiers and import direction), `house_style` and `guardrails`. Fill target roots, exclusions,
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
  `guardrails.structure` and `guardrails.naming`. Apart from `guardrails.exceptions`,
  unknown section names outside `GUARDRAILS_SECTIONS` in `guardrails/context/config/profile.py`
  fail `profile-check`.
- `guardrails.check.run` selects whole-repo steps; absent selection means all
  `DEFAULT_STEPS` in `guardrails/commands/check/whole_repo.py`.
- `guardrails.check.exclude` lists generated, vendored and fixture paths to exclude.
- `guardrails.check.repo` maps each repo-owned step to checker files that jointly enforce
  every rule it reports, for example
  `{"python-quality": ["scripts/checks/check-python-quality.py"]}`. `checks.commit` runs
  those checkers, so the harness leaves that step out, never running a duplicate.
  Remove settings only delegated steps read; `guardrails.python` is rejected when every
  configured Python step belongs to the repo.
- `house_style.version` records the shipped `guardrails/data/house-style.json` version.
- `guardrails.exceptions` is an optional list of `{rule, path, reason}` objects with an
  optional `check`. Every entry requires a nonempty owner-approved reason; no decision
  record is required. Unknown fields are rejected.
  `path` uses shared profile matching: glob patterns use `fnmatchcase`; plain paths
  match themselves and descendants. Optional `check` limits suppression to the finding's
  sub-check, for example `child-repeats-parent` within `NAME.conventions`; omitted `check`
  suppresses the whole rule at matching paths. Other sub-checks remain enforced.
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

`guardrails init --repo <clone> --record <record dir>` drafts `gate.json`, `profile.md` and
native-tool proposals under `proposed/` in a live record or a draft directory, without writing
into the repo, never inventing an approval. Harness setup's
[candidate step](../skills/structured-workflow/references/setup-candidates.md)
defines its options and use. The setup's draft `guardrail-proposal.json` has
`{"schemaVersion":1,"minimal":[...],"full":[...]}` with repo-relative candidate paths; it travels
with the confirmed record, never into the target repository.
[Setup approval](../skills/structured-workflow/references/setup-approval.md) owns
the confirmation, the Minimal/Full or Approve/Revise decision and its STOP. After confirmation the
agent writes the live record directly; never ask the owner to save it.

All approvals and exceptions live only in the record's profile, never a PR. They include
`approved_skips`, any `approval`, `approved_*` or `*_approval`, `user_approval`,
`guardrails.exceptions`, fast-tier machine approvals and coverage-ratchet approvals.
Write each only as the owner stated it in chat. Record-write authority does not approve
repository fixes; the setup approval remains the boundary
before applying them.

## Field details

[^n1]: Required nonempty argv array for the repo's static checks, every T1 and affected T2 boundary
  tests.
[^n2]: Optional nonempty argv array for the repo's full T1 and T2 gate. CI runs this as one gate.
[^n3]: Optional nonempty argv array for the formatter `freeze()` runs before hashing proofs.
[^n4]: Optional nonempty argv array for profile completeness, normally `guardrails
  profile-check`; `prove()` does not run it.
[^n5]: `allowed` or `disabled`; absent field or absent record defaults to `allowed`, subject to
  governing Git authorization and CI.
[^n6]: Nonempty argv array for the repo's dependency audit, required by the `ci-gate` runner. It
  runs with network after `postCreateCommand`; `checks.ci` then runs with no network.
