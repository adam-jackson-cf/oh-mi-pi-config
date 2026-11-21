# 0017: Dynaflows runtime rails

Status: accepted, 2026-11-21. Supersedes in part
[ADR 0015](0015-dynaflows-minimal-and-structured-modes.md) (mode source) and
[ADR 0016](0016-skill-moves-and-legacy-removal.md) (machine-file reader).

## Context

The mode lived in each personal record's `gate.json`, with a machine file
`~/.omp/dynaflows.json` able to force minimal mode. Two sources answered one question.

## Decision

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

## Consequences

Live records must run the migration before rails accept them. The mode is a tracked change in
oh-mi-pi-config, reviewed like any other.
