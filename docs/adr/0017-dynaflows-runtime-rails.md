# 0017: Dynaflows runtime rails

Status: accepted, 2026-11-21. Supersedes in part
[ADR 0015](0015-dynaflows-minimal-and-structured-modes.md) (mode source) and
[ADR 0016](0016-skill-moves-and-legacy-removal.md) (machine-file reader).

## Context

The mode lived in each personal record's `gate.json`, with a machine file
`~/.omp/dynaflows.json` able to force minimal mode. Merge on green CI, ADR and council
resolution, handoffs, device rollout and the `nuc-config` merge method were instruction text
in `agent/RULES.md`, `agent/AGENTS.md` and `.omp/AGENTS.md`, not checks.

## Decision

### Mode file

| Topic | Decision |
| --- | --- |
| Mode source | Tracked `agent/extensions/dynaflows/modes.json`, keyed by `<owner>/<name>` |
| Unlisted repo | Minimal |
| Structured, no valid record | Minimal; setup writes the record |
| Writers | `dynaflows` tool and `$dynaflows` directive; minimal removes the entry |
| Record | `dynaflows` removed from `gate.json`; readers reject it as unknown |
| Machine file | `~/.omp/dynaflows.json` support removed |
| Migration | `lib/migrate_records_to_mode_file.py` (`--dry-run`) moves live modes |
| Missing or invalid mode file | Error: minimal with a note; commits refused |
| Mode file writes | Agent tools never write it; a change mid-session refuses Git ([ADR 0018](0018-rails-trust-only-what-they-saw.md)) |

### Rails

| Rail | Modes | Decision |
| --- | --- | --- |
| Git read gate | Both | `gitCommand` lists the gated writes; anything else is ungated |
| Shell indirection | Both | Fails closed: stdin shells, variable command words, unknown scripts |
| Launchers | Both | Launchers and unknown programs naming `git`/`gh`/`hub`/`omp` refused ([ADR 0018](0018-rails-trust-only-what-they-saw.md)) |
| Script files | Both | An existing shell script is scanned as if its commands ran |
| Inline code | Both | `python -c`, `node -e` naming `git`/`gh`/`hub`/`omp`, or eval starting them |
| GitHub API | Both | `curl`/`wget`/`http` writes to it refused; `gh alias set` refused |
| `gh` aliases | Both | Refused with extensions: they run commands the rails cannot check |
| `github` tool | Both | `pr_create`, `pr_push`, `pr_checkout` refused |
| `git config` | Both | Alias writes and hook paths outside the repo refused |
| Commit shape | Both | A commit sharing a command with an index write is refused |
| Commit scope | Structured | `-a`, `-i`, `-o`, `-p`, pathspecs refused: index only |
| Merge policies | Structured | `mergePolicies` entries; default rebase with proof |
| `--admin` | Structured | Always refused |
| `afterMerge` | Structured | Required after a matching merge until a run exits 0 |
| Obligations | Structured | Checked at `session_stop`; 3 follow-ups, then the owner is told |
| PR discovery | Structured | Turn end finds the pushed branch's open PR via `gh pr view` |
| Resume | Structured | `session_start`/`session_switch` replay the branch into obligations |
| Obligation hook | Structured | `obligationSources` option for later judgments |
| Device rollout | Structured | Opt-in only: `$rollout` line or tool `rollout` |
| Restart review | Both | Per-repo revision in `<agent dir>/dynaflows-reviews.json` |
| Review gate | Structured | Every worktree of the repo (Git common dir) refused until ack |
| Impact judgment | Structured | `extension-impact` `no`: owner notified, revision stored |
| Handoff | Both | `handoff` tool; never committed; deleted after the next first turn |

Obligations:

| Id | Unmet when |
| --- | --- |
| `pr:<branch>` | Open PR, `agentMerge` not disabled; pending CI names its wait command |
| `afterMerge:<root>` | No exit-0 run of the step, alone or in an `&&` chain, since the merge |
| `rollout` | Rollout requested; no exit-0 run of `<config root>/scripts/device_rollout.py` |

A revision is `git:<last commit touching the extension>` in a Git config root, else a content
hash. `modes.json` is excluded, so a mode switch is not a change. A repo's first session
stores the revision without review.

### Judged rails

Structured mode only; the registry is [ADR 0020](0020-judgment-registry-and-direct-judge.md).
Deterministic pre-checks run first; `decide()` runs only on what they pass.

| Judgment | Rail | Pre-check | yes | escalate |
| --- | --- | --- | --- | --- |
| `extension-impact` | Restart | Git revision pair, valid record | Review task | Review task |
| `adr-needed` | Merge | Method, head, proof, CI; ADR file in diff skips | Refused | Owner |
| `implementation-integrity` | Commit | Path check, `checks.commit`, harness | Refused | Owner |
| `completion-evidence` | Turn end | P2, P4-P8 on the agent's PR | Unmet | Owner |
| `completion-evidence` | Merge | P2, P4, P8; CI by merge policy | Refused | Owner |
| `in-scope` | `ask`, `askFirstCommand` | Every parsed command position | Ask first | Ask first |

| Topic | Decision |
| --- | --- |
| Transport | omp's judge role from the extension host; `lib/decide.py` asks and records |
| Model | The answering model must equal `thresholds.model`, else refused |
| Bands | `no_at_most` and `yes_at_least` come from each judgment's measured band |
| Status | All five stay `provisional`, so all five run in shadow |
| Shadow | `decide()` runs and logs; a notice tells what the rail would do; never refuses |
| Enforcement | Per judgment: `thresholds.json` `ready`, decisions `ready`, minimums confirmed |
| Owner approval | Prompt line `$approve <judgment>:<id>`; one session, one subject |
| Failure | Shadow: a notice. Enforced: refuses commit, merge, command; evidence unmet |
| In-scope `no` on `ask` | Refused: the agent decides details of the agreed work itself |
| Subagent privileged command | Refused without a judgment; the main agent asks |
| Not wired | Completion P3 (linked plan): no PR in the cases links one |

### Removed instruction text

| File | Removed | Now |
| --- | --- | --- |
| `agent/RULES.md` | Never bypass git hooks | Hook-bypass rails, both modes |
| `agent/RULES.md` | Merge on green CI, in-scope ADR lines | PR obligation, in-scope, `adr-needed` |
| `agent/RULES.md` | Resolve councils after 2 rounds | Dropped (owner, 2026-11-21): not kept |
| `agent/RULES.md` | Handoff to `docs/HANDOFF.md` | `handoff` tool |
| `agent/AGENTS.md` | Git paragraph | Mode rails and `skill://git-tool` gate |
| `agent/AGENTS.md` | Device rollout paragraph | Opt-in rollout obligation |
| `agent/AGENTS.md` | NUC `~/.omp` and session restart lines | Kept as instruction: not enforced |
| `.omp/AGENTS.md` | Whole file | `ci`, `mergePolicies`; other facts in the record README |

### Not enforced

| Fact | Why |
| --- | --- |
| No Git in the NUC's `~/.omp` | Remote shells over `ssh` are opaque; `ssh` asks first |
| Restart sessions after rail changes | Old sessions run old rails |
| Missing `ci-gate-repos.json` entry needs a setup PR | Record README fact |
| An Actions run never replaces a missing `gate` | Record README fact |
| Obligations after 3 follow-ups | The owner is told once; the rails stop asking |
| `$X$Y` command words without a `git`/`gh` word | No word to detect |

### Cutover steps

1. Run `python3 agent/extensions/dynaflows/lib/migrate_records_to_mode_file.py --dry-run`, then without.
2. It strips `dynaflows` and adds the `nuc-config` sync policy to the oh-mi-pi-config record.
3. Roll the records out with `device_rollout.py`.
4. Delete the migration script and its test.

## Consequences

Live records must run the migration before rails accept them. Structured commits, merges, asks
and turn ends now call the judge, in shadow while the thresholds are provisional
(`skill://jev-actions`: uncalibrated thresholds are not enforced): the call log and notices build
the evidence for calibration, and nothing waits on the judge. A judgment marked `ready` refuses on
`yes` and asks the owner on `escalate`; an outage of an enforced judgment refuses until it answers.
The mode is a tracked change in oh-mi-pi-config. Rollout no longer follows a merge unless asked. A structured
session after an extension change cannot write Git until it acknowledges the review.
