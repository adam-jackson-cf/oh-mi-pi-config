# Run state

A setup spans several sessions: `grow` changes must merge before setup completes. The run keeps its working state in the target
under `.proofs/`, excluded from Git through `.git/info/exclude`. Nothing in a gate, checker or CI job reads `.proofs/`;
anything enforcement needs lives in a native config or in `proofs.toml`.

## Layout

| Path | Holds |
| --- | --- |
| `.proofs/AGENTS.md` | The guard below; also `.proofs/CLAUDE.md` as a symlink when the target pairs those files |
| `.proofs/state.json` | The one run-state file |

Temporary clones, candidate code, violating fixtures, tool caches and command output stay in the candidate root outside the checkout.
`.proofs/` holds JSON and Markdown only, so repo tools that ignore `.gitignore` still find no code there.

Guard text:

```md
# Setup run state

This folder holds working state for the setup-repo-proofs skill. Do not read or act on its contents unless you are running
that skill or the user asks you to. It records past setup decisions, not current repository rules; the enforced rules are in
`proofs.toml` and the native tool, hook and CI configuration.
```

## `state.json`

One JSON object, `"schema": 1`, with these keys. Each step writes only its own keys, except `decisions`, which any step appends to.

| Key | Step | Content |
| --- | --- | --- |
| `target` | 0 | Git root, base ref or `null`, stack evidence, OS, CI provider, protected paths, candidate root |
| `scope` | 0 | Offered scopes and the owner's choice: `{"mode", "base_ref", "paths"}`, plus exclusion candidates with origin evidence |
| `modes` | 0 | `greenfield`, `adopt` and `extend` as they apply |
| `inventory` | 1 | `inventory_controls.py` output plus each control's status (`active`, `manual`, `proposed`, `stale`, `unverified`), rule-ID evidence and gaps |
| `map` | 2 | Per family: outcome (`keep`, `grow`, `add`), owning control, rule-ID evidence, native home of each value, blockers |
| `dry_run` | 4 | Per scope: findings by rule and path, file and line counts, measured costs, estimates, unknowns |
| `proposal` | 5 | `{"scope": {...}, "minimal": {"root", "paths"}, "full": {"root", "paths"}}`: each tier's candidate root and repo-relative paths |
| `decisions` | 0–6 | Each decision: `subject`, `status` (`pending`, `approved` or `blocked`), the owner's words or the blocker, date. Questions raised before Step 5 are recorded `pending` with the recommended default and answered at Step 5; `blocked` names an item the chosen tier cannot land and why |
| `grow_changes` | 6 | Each `grow` change: family, PR URL, merge state |

A value is either evidenced or marked `unknown`; placeholders (`TBD`, `n/a`, `<...>`) are never written.

## Lifecycle

1. Step 0 creates `.proofs/`, appends `/.proofs/` to `.git/info/exclude`, writes the guard and starts `state.json`.
2. A rerun reads `state.json` as prior decisions to re-confirm, inventories the repo fresh, and never treats old entries as current truth.
3. Step 6 moves each lasting decision to a tracked home (`proofs.toml`, a native config or `CODEOWNERS`); no gate reads it from `.proofs/`.
4. Step 7 checks that `decisions` holds no `pending` entry and no placeholder, reports every `blocked` entry, deletes `inventory`, `dry_run` and `proposal`,
   keeps `target`, `scope`, `modes`, `map`, `decisions` and `grow_changes` for the next run, and reports what it kept.
