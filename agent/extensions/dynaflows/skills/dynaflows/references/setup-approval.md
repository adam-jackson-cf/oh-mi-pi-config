# Harness setup: Steps 4–5 dry-run and approval deltas

## Objective

Dry-run the harness checks with the draft record, then gate every record and repo write on the owner's tier choice.

## Step 4: engine dry run

1. Run the harness checks from the harness against a temporary checkout with the draft record:
   `uv run --project <omp root>/agent/extensions/dynaflows/engine guardrails check --repo <temporary checkout>
   --record <draft record dir>`. They read nothing from, and write nothing into, the checkout's tree.
2. Steps the draft record's `guardrails.check.repo` gives to repo checkers run through `checks.commit` instead;
   report that command's result with them.
3. Also run harness `guardrails path-check --repo <temporary checkout> --record <draft record dir>` with candidate changed
   paths. It always uses the required profile `guardrails.structure`, `guardrails.naming` and `guardrails.paths`,
   independently of `guardrails.check.repo`; report its findings separately.
4. Adopting a repository means clearing every finding first; there is no baseline and no new/changed-code view.

## Step 5: approval and proposal

1. In the action matrix, `keep` gives a `repo` step to its checkers (`guardrails.check.repo`), `merge` or `replace` is the
   `grow` PR that changes the repo's checkers before they own the step, and `add` runs a `harness` step.
   No row runs a harness step for a rule a repo checker holds or will hold.
2. Present the draft record for confirmation: every `gate.json` command and its discovered source, `ci.requiredContext`, `ci.runner`, `ownerOnlyPaths`
   (the dependency-audit exception file and any other path only the owner changes), scope exclusions and the
   `profile.md` values, with the consequence of every replacement or exception.
3. A rule still blocked by a missing harness implementation stays blocked; never relabel it as passing.
4. `Minimal` is the least change that gives the record the required `checks.commit` naming the repo's own command and the
   required profile `guardrails.structure`, `guardrails.naming` and `guardrails.paths`, plus repo fixes that clear every
   finding of the rules it enables; state which applicable rules remain unenforced.
5. `Full` is every applicable rule and recommendation, including `checks.ci` wired into the repo's CI by the record's
   `ci.runner` role.
6. In `adopt` mode both tiers give every `repo` and `grow` step to the repo's checkers in `guardrails.check.repo` and
   include every `grow` PR. A tier never keeps a duplicate harness step or widens record allowlists so the harness copy
   passes code the repo checker already accepts.
7. Write the repo-relative candidate paths per tier to `guardrail-proposal.json` in the draft record directory as
   `{"schemaVersion":1,"minimal":[...],"full":[...]}`; it goes into the record with the rest of it.
   Nothing is written into the target, and nothing into the live record, before the owner's approval.
8. Ask the owner to choose a tier, confirm the record, and approve exact candidate paths and merge/replace decisions,
   using `ask` with a question containing "guardrail" and options "Minimal" and "Full". When both tiers hold the same
   record settings and candidate paths, say so and ask only for approval of that one draft, with options "Approve" and
   "Revise".
9. **STOP** until the owner replies. Write neither the record's `gate.json` nor any proposal path before then;
   elapsed time or a default option is not approval.
10. After a narrower rule set, recompute the candidate diff, findings and proposal file, keeping whole-repo scope for
    every rule kept.
