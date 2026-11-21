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

### Rails

| Rail | Modes | Decision |
| --- | --- | --- |
| Git read gate | Both | `gitCommand` covers every Git and GitHub write |
| Merge policies | Structured | `mergePolicies` entries; default rebase with proof |
| `--admin` | Structured | Always refused |
| `afterMerge` | Structured | Required after a matching merge until a run exits 0 |
| Obligations | Structured | Checked at `session_stop`; 3 follow-ups, then the owner is told |
| Obligation hook | Structured | `obligationSources` option for later judgments |
| Device rollout | Structured | Opt-in only: `$rollout` line or tool `rollout` |
| Restart review | Both | Per-repo revision in `~/.omp/agent/dynaflows-reviews.json` |
| Review gate | Structured | Git writes refused until acknowledged; minimal notifies |
| Impact seam | Both | `decide("extension-impact", …)`; `escalate` until the judge wires it |
| Handoff | Both | `handoff` tool; never committed; deleted after the next first turn |

Obligations:

| Id | Unmet when |
| --- | --- |
| `pr:<branch>` | The agent's PR is open, CI green on its head, `agentMerge` not disabled |
| `afterMerge:<root>` | No whole-command run of the step exited 0 since the merge |
| `rollout` | Rollout requested and no `device_rollout.py` run exited 0 |

A revision is `git:<last commit touching the extension>` in a Git config root, else a content
hash. `modes.json` is excluded, so a mode switch is not a change. A repo's first session
stores the revision without review.

### Removed instruction text

| File | Removed | Now |
| --- | --- | --- |
| `agent/RULES.md` | Never bypass git hooks | Hook-bypass rails, both modes |
| `agent/RULES.md` | Merge on green CI, ADR and council lines | Merge rail and PR obligation |
| `agent/RULES.md` | Handoff to `docs/HANDOFF.md` | `handoff` tool |
| `agent/AGENTS.md` | Git paragraph | Mode rails and `skill://git-tool` gate |
| `agent/AGENTS.md` | Device rollout paragraph | Opt-in rollout obligation |
| `.omp/AGENTS.md` | Whole file | `ci`, `mergePolicies`; other facts in the record README |

## Consequences

Live records must run the migration before rails accept them. The mode is a tracked change in
oh-mi-pi-config. Rollout no longer follows a merge unless asked. A structured session after an
extension change cannot write Git until it acknowledges the review.
