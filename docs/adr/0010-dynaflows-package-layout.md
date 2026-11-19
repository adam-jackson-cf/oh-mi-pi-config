# 0010: Give dynaflows one package layout

Status: accepted (owner-approved).

## Context

The extension bundled its Python project, orchestration module, judgment assets and human page
beneath a skill-oriented layout. Those resources belong to the extension, not the skill. The
package entry remains `agent/extensions/dynaflows/index.ts`.

## Decision

Use these ownership boundaries beneath `agent/extensions/dynaflows/`:

| Previous path | Canonical path |
| --- | --- |
| `skills/omp-dyna-workflows/AGENTS.md` | `AGENTS.md` |
| `skills/omp-dyna-workflows/` skill | `skills/dynaflows/` skill |
| `skills/omp-dyna-workflows/judgments/` | `judgments/` |
| `skills/omp-dyna-workflows/workflow.html` | `docs/workflow.html` |
| `guardrails/` Python project | `engine/` |
| `guardrails/omp_guardrails/` import package | `engine/guardrails/` |
| `skills/omp-dyna-workflows/templates/workflows.py` | `engine/workflows.py` |

The owner chose **guardrails** as the single Python project, import-package and console-command
name, replacing `omp-guardrails` and `omp_guardrails`. The skill's frontmatter and every skill URL
use **dynaflows**, replacing `omp-dyna-workflows`. No aliases, compatibility paths or shims
remain. Historical ADR wording is retained.

`engine/workflows.py` remains directly loadable with `%load` and retains its orchestration
behavior. Judgments are runtime assets outside code folders; moving them removes the skill's
misleading ownership of those assets. Rules and the extension entry do not move.

## Consequences

Extension command construction, orchestration paths/imports, references, tests, packaging,
lockfiles, container setup and checker configuration migrate together. Markdown links follow their
resource's new owner. Deployment scripts are inspected independently because a deployed branch may
own its own synchronization mechanism; this change does not edit `nuc-config`.
