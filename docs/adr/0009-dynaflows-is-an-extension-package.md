# ADR 0009: Dynaflows is an extension package

- Status: accepted
- Date: 2026-11-18
- Builds on: [ADR 0008](0008-generic-setup-skill-and-record-docs.md)

## Context

Dynaflows combines runtime enforcement, progressively disclosed workflow guidance and native judged
rules. A skill is only its guidance surface; keeping the implementation in generic Git rails made
those boundaries unclear and prevented independent package discovery.

## Decision

Package dynaflows at `agent/extensions/dynaflows/`, with `package.json` declaring the single
`omp.extensions` entry `./index.ts`. Explicitly list the package directory in `agent/config.yml`
`extensions:` so omp discovers sibling `skills/` and `rules/` roots. Native extension discovery also
scans one-level directories with manifests or index modules. Both discovery routes resolve to the
same absolute entry file; omp de-duplicates that entry rather than executing its factory twice.
Do not disable `extension-module:dynaflows`: that would also filter entries expanded from the
configured package directory.

The package owns the dynaflows switch/directive/tool, workflow pointer, targeted edit and handoff
reads, path checks, harness commit checks, owner-only commit refusal, PR proof/structure/CI guards,
and session suppression of its judged rules. Generic `agent-rails.ts` retains Git safety and the
Git skill read gate, including working-directory, publishing, hook-bypass and worktree-location
checks. Shared parsing and policy primitives live under `agent/lib`; the generic rails do not load
the dynaflows package to provide ordinary Git safety. Record resolution remains harness config.

Bundle the existing `omp-dyna-workflows` skill unchanged in identity. A package skill has its bare
name unless a differing skill collides; package namespacing is not unconditional. Keep the old
skill folder as a disabled deprecated entry (`enabled: false`) until the owner's final cleanup.
It is not a callable shim and cannot displace the packaged skill's bare URL.

Move the four judged rules into package `rules/`, rename `agent-rails-` to `dynaflows-`, and keep
`agent/agent-rails.yml`'s suppression names in agreement. Preserve user `ttsr.disabledRules` entries.
Off still means plain omp plus git-tool and generic Git safety, as required by ADR 0004.

`prove()` and `structure_review()` remain eval-loaded helpers in the relocated `workflows.py`.
This change registers neither helper as a tool and changes no proof or judgment contracts.

## Consequences

Generic setup-repo-proofs and test-guidance remain useful without dynaflows. Harness configuration
is documented beside the record resolvers. Deploy the extension package together with config,
shared helpers and its guidance/rules. Existing device rollout copies the tracked configuration
checkout and personal records; no target repository acquires a dependency on this package.

## Capability traceability

In the following tables, `W` means the former `agent/skills/omp-dyna-workflows/` root and `P` means
`agent/extensions/dynaflows/skills/omp-dyna-workflows/`. Whole-file ranges map every paragraph,
including unchanged policy, examples and frozen judgment evidence. Changed text only adjusts paths,
relative-root calculation, rule identity and the package boundary; it does not remove capabilities.

| Former file under W | New file under P | Lines | Status |
| --- | --- | --- | --- |
| `AGENTS.md` | `AGENTS.md` | 1–112 | Moved |
| `SKILL.md` | `SKILL.md` | 1–247 | Moved; eval path updated |
| `references/adapter-authoring.md` | same relative path | 1–99 | Moved |
| `references/delivery-principles.md` | same relative path | 1–35 | Moved; rule tags renamed |
| `references/harness-setup.md` | same relative path | 1–546 | Moved |
| `references/patterns.md` | same relative path | 1–339 | Moved |
| `references/playbooks/bug-fix.md` | same relative path | 1–47 | Moved |
| `references/playbooks/feature.md` | same relative path | 1–58 | Moved |
| `references/playbooks/investigation.md` | same relative path | 1–40 | Moved |
| `references/playbooks/multi-phase.md` | same relative path | 1–54 | Moved |
| `references/playbooks/project-setup.md` | same relative path | 1–107 | Moved |
| `references/playbooks/refactor.md` | same relative path | 1–46 | Moved |
| `references/proofs.md` | same relative path | 1–73 | Moved |
| `references/structure-and-naming.md` | same relative path | 1–52 | Moved |
| `references/test-enforcement.md` | same relative path | 1–32 | Moved |
| `references/verify-loop.md` | same relative path | 1–65 | Moved |
| `show-me-omp-flows.html` | same relative path | 1–138 | Moved; links updated |
| `templates/workflows.py` | same relative path | 1–1494 | Moved; root and judgment paths updated |

The judgment subdirectories under W map to identical relative paths under P, without changing
frozen inputs, labels, questions, scores or thresholds:

| Directory | File | Lines | Status |
| --- | --- | --- | --- |
| `judgments/dependency-severity` | `cases.jsonl` | 1–56 | Moved |
| same | `label-guide.md` | 1–100 | Moved |
| same | `labels.jsonl` | 1–710 | Moved |
| same | `questions.json` | 1–37 | Moved |
| same | `scores.csv` | 1–225 | Moved |
| same | `thresholds.json` | 1–98 | Moved |
| `judgments/structure-naming` | `cases.jsonl` | 1–61 | Moved |
| same | `label-guide.md` | 1–106 | Moved |
| same | `labels.jsonl` | 1–61 | Moved |
| same | `questions.json` | 1–94 | Moved |
| same | `scores.csv` | 1–57 | Moved |
| same | `thresholds.json` | 1–387 | Moved |

Each rule maps from `agent/rules/agent-rails-<suffix>.md` to
`agent/extensions/dynaflows/rules/dynaflows-<suffix>.md`:

| Suffix | Lines | Status |
| --- | --- | --- |
| `clean-cutovers` | 1–13 | Moved |
| `flakes` | 1–13 | Moved |
| `measured-claims` | 1–17 | Moved |
| `regression-coverage` | 1–14 | Moved |

The disabled old skill retains only deprecation metadata and a pointer to P. Its original capability
paragraphs all live in the mapped package entry; deletion of that skill folder is deferred to owner
sign-off, not performed by this change. The Python engine stays in the final location chosen in
ADR 0008 and is not moved a second time.

## Runtime preservation map

Coordinates below refer to `agent/extensions/agent-rails.ts` before this split. `I` means
`agent/extensions/dynaflows/index.ts`; `L` means `agent/lib/rails-runtime.ts`.

| Former capability | Source | New home | Status |
| --- | --- | --- | --- |
| Workflow extension entry | 537 | `I:90` | Split from generic entry |
| Proof, structure and exact-head CI | 637–724 | `I:149–236` | Moved |
| Path checks, owner-only and commit checks | 780–875 | `I:292–387` | Moved |
| Directive/tool, targeted reads and handoff | workflow handlers | `I:90` onward | Moved |
| Disabled native judged rules | workflow lifecycle | `I:90` onward | Moved |
| Shell action parsing | shared policy helpers | `L:213` onward | Shared |
| Worktree safety | generic Git branches | `agent-rails.ts:39` onward | Retained |

The existing rails tests are partitioned between `tests/agent-rails.test.ts` and
`tests/dynaflows-extension.test.ts`; shared test fixtures remain exported by the former. Generic
cases register only generic rails, while workflow cases cover package-only and co-registered use.

## Installed-runtime evidence

The installed omp 18.22.2 scratch proof is saved as `local://dynaflows-extension-proof.md`.
It observes one package registration with native plus explicit discovery, all seven configured
read-gate URLs resolving, all four native package rules loading, a positive clean-cutovers native
injection when on and none when off, and an actual write blocked by both targeted reads when on
but permitted when off. Scratch credentials and fixtures were removed after saving that evidence.
