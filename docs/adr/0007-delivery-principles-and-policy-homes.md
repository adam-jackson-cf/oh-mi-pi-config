# ADR 0007: Delivery principles and policy homes

- Status: Superseded in part by [ADR 0015](0015-dynaflows-minimal-and-structured-modes.md) (tool-trigger
  reads and judged-rule suppression live in the dynaflows `rails.yml` and follow its modes) and
  [ADR 0016](0016-skill-moves-and-legacy-removal.md) (the retention of deprecated skill files)
- Date: 2026-11-18
- Deciders: Adam Jackson; delivery-policy writer for delegated placement details

## Decision

Dynaflows owns delivery integrity, clean cutovers, regression repair, structure and naming.
Generic test practice stays in test-guidance, useful without dynaflows. Regression test design
belongs in case-selection; fixing the broken rule belongs in delivery principles.
Verification scaling is a short delivery pointer to before-commit, not a second procedure.

Tool-trigger reads stay in agent-rails.yml and fire only with dynaflows on for the edited repo.
Native rules are reply reminders, cover main and task, and repeat once. Flakes and measured
claims remain test topics. Rails' dynaflowsRules list remains the suppression authority.

Every policy bullet has one status. Enforced tags name catalogue IDs and their subcommands;
structure judgment tags name existing threshold decisions. Provisional judgments remain
shadow-only. `main/implementation-integrity` means ordinary agent judgment, not a calibrated
judge or blocking checker. `agent-rails/*` tags name native reply-reminder questions, not
semantic proof that an implementation obeys the rule. The policy test checks these distinctions.

Deterministic integrity uses the separately delivered CODE rules and Ruff selections.
CODE rules inspect added diff lines (a touched comment line counts as added); the comment ban
covers every comment syntax, including docstrings, with tool-directive exemptions. No existing
comments are removed merely to meet the new policy.

Deprecated skill files remain untouched until a final owner-signed-off cleanup PR. All callers
move now. Removing their .gitignore allowlist entries does not untrack or delete the retained
files. The clean-cutover design directive records this explicit retention exception.
The existing long-line exception for guidance skills uses a family glob so retained files keep
their existing lint treatment without an active caller naming the deprecated skill.

The rollout paragraph in agent/AGENTS.md is outside OMP_GLOBAL_RULES_END. Repository discovery
found no generator/template for that paragraph; its generated-header warning applies to the
preceding marked block. Only the repository-owned paragraph is changed.

## Capability traceability

Source paths below are relative to `agent/skills/coding-guidance/`. Destinations use:

- D = `agent/skills/omp-dyna-workflows/references/delivery-principles.md`
- S = `agent/skills/omp-dyna-workflows/references/structure-and-naming.md`
- C = `agent/skills/test-guidance/references/case-selection.md:21-25`
- B = `agent/skills/test-guidance/references/before-commit.md:9-14`
- R = `agent/agent-rails.yml`

Source aliases: I = `references/implementation-integrity.md`; K = `references/clean-cutovers.md`;
G = `references/regression-coverage.md`; V = `references/verification.md`;
N = `references/naming.md`; T = `references/structure-and-grouping.md`.

Each source sentence is covered, including continuations. Headings, blank lines and frontmatter
are accounted for separately below; no policy sentence is silently dropped. Enforced entries
retain their catalogue IDs; the destination carries the full ID and subcommand.

| Source | Capability | New home | Status |
| --- | --- | --- | --- |
| SKILL.md:3 | All-language task scope | D:3, S:3, R:7-16 | Guidance only |
| SKILL.md:12-13,16 | Integrity purpose, trigger, runbook | D:3,7-18, R:8-9 | Guidance only |
| SKILL.md:14-15 | Silent-policy judgment | D:18 | Guidance only |
| SKILL.md:20-22 | Ownership paths, creation/move trigger | S:3-28, R:10-11 | Guidance only |
| SKILL.md:26-28 | Naming purpose, rename trigger | S:30-40,37 | Guidance only |
| SKILL.md:32-34 | Canonical refactor, trigger, runbook | D:3,20-26 | Judged |
| SKILL.md:38-40 | Failure class, bug trigger, runbook | D:3,28-31, C | Judged / Guidance only |
| SKILL.md:44-46 | Risk-scaled handoff | D:33-35, B | Guidance only |
| I:3 | Integrity objective | D:5-17 | Enforced / Judged |
| I:5 | Placeholder bodies | D:7 | Enforced |
| I:5 | Product mocks | D:8 | Enforced |
| I:5 | Hardcodes and stubs | D:9 | Judged |
| I:6 | Never suppress failures | D:10,15-17 | Judged / Enforced |
| I:7 | Never loosen gates | D:11 | Judged |
| I:8 | No temporary/fallback strategy | D:12 | Judged |
| I:9 | No task abandonment after failure | D:13 | Judged |
| K:5,9 | No compatibility objectives | D:22 | Judged |
| K:10-11 | Remove legacy, version distinction | D:23, S:24-25 | Judged |
| K:12 | Preserve exact canonical wording | D:24 | Guidance only |
| K:13 | No equivalent canonical rewrites | D:25 | Guidance only |
| G:5,9 | Name the broken behavioral rule | D:30 | Judged |
| G:10 | Report coverage of broken rule | D:31, C | Guidance only |
| G:11-13 | Expand scenarios, flag gaps | C | Guidance only |
| G:14 | No single-value regression pin | C | Guidance only |
| G:15 | No duplicate/example-only test | C | Guidance only |
| V:3 | Verify every task by risk | D:35, B | Guidance only |
| V:5 | Focused narrow-change coverage | D:35, B | Guidance only |
| V:6 | Broader shared/workflow coverage | D:35, B | Guidance only |
| N:3 | Name holds/does, one concept | S:3,36 | Judged |
| N:4-5 | Style data and enforcement | S:3,32-40 | Enforced |
| N:7-8 | Singular nouns, catalogue plurals | S:32 | Enforced |
| N:9 | Folder name covers contents | S:33 | Judged |
| N:10 | Child never repeats parent | S:34 | Enforced |
| N:11-13 | Behavior, dates and versions | S:35 | Enforced |
| N:14 | One name across surfaces | S:36 | Judged |
| N:15-17 | Atomic whole-name rename, no alias | S:37 | Enforced |
| N:18-19 | Sibling pattern, retain mechanism | S:38 | Judged |
| N:20-21 | Plain words, no version suffix | S:39 | Enforced |
| N:22 | Ecosystem and asset casing | S:40 | Enforced |
| T:3 | Path shows owner/direction | S:3 | Guidance only |
| T:7 | Owner, not technical kind | S:7 | Judged |
| T:8-9 | Tier meaning, import direction | S:8 | Enforced |
| T:10-11 | Tier readership threshold | S:9 | Enforced |
| T:12 | Read current import order | S:10 | Guidance only |
| T:13 | Automated import check | S:11 | Guidance only |
| T:14-15 | Shared siblings move down | S:12 | Enforced |
| T:16-17 | Single-owner placement | S:13 | Enforced |
| T:18-19 | No grab-bag, shared tier | S:14 | Enforced |
| T:20-21 | Prefix extraction | S:15 | Enforced |
| T:20-21 | Prefix concept ownership | S:16 | Judged |
| T:22 | Merge overlapping owners | S:17 | Judged |
| T:23 | Runtime data outside code | S:18 | Judged |
| T:24-25 | Tests and fixture layout | S:19 | Enforced |
| T:24 | Behavioral test filenames | S:20 | Enforced |
| T:26 | Move imports, retain tests | S:21 | Guidance only |
| T:27-28 | Entry point and consumer | S:22 | Judged |
| T:29 | Outputs outside source | S:23 | Enforced |
| T:30 | Reproducible version, pointer | S:24 | Guidance only |
| T:31 | Immutable measurement, no code versions | S:25 | Guidance only |
| T:32 | Explicit references | S:26 | Guidance only |
| T:33 | One layout per kind | S:27 | Guidance only |
| T:34 | No speculative empty folders | S:28 | Guidance only |
| T:38 | Greenfield from first file | S:44 | Guidance only |
| T:39 | Addition scope, correct home | S:45 | Guidance only |
| T:40-41 | Capability beside kind folders | S:46 | Judged |
| T:42 | Brownfield mismatch reporting | S:47 | Guidance only |
| T:43 | One behavior-preserving move | S:48 | Guidance only |
| T:44 | Update all callers, no shim | S:49 | Guidance only |
| T:45 | Isolate domains, move shared first | S:50 | Enforced |
| T:46 | Dissolve grab-bags by readership | S:51 | Enforced |
| T:47 | Clean moves, remove scaffolding | S:52, D:26 | Guidance only |

The new comment ban (D:14) and deterministic Ruff controls (D:15-17) are explicit owner-approved
additions, not claims that the old skill contained them. The placeholder declaration exemptions
and product-code mock-library boundary are the approved deterministic interpretation; other
stub/failure-evasion cases remain agent judgment.

SKILL.md:1-2,4-6 is retired skill metadata: retained on disk, not copied into policy files because
these are references, not new skills. SKILL.md:8,10,18,24,30,36,42 and reference-file headings
are reorganized into D and S's sections, with regression test headings in C. Blank lines carry
no policy. No source file is deleted or edited by this migration.

## Consequences

Callers read targeted homes rather than the deprecated skill. A catalogue/threshold rename
without a matching policy edit fails tests. The new tests also reject missing or multiple
status tags and validate both deterministic IDs and structure-judgment decisions.
The migration depends on the integrity catalogue and generic-test PRs landing first.
