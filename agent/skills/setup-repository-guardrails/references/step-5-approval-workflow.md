# Record and fix approval

## Objective

Make the owner's record and fix decisions the boundary before any record or target write.

## Required actions

1. Present an action matrix: ID, rule IDs, current owner, proposed owner (repo control or
   harness check), audience, operation (`keep`, `merge`, `replace`, `add`, or `defer`), paths,
   dry-run change count, findings, verification, and risk. `keep` gives a `repo` step to its
   checkers (`guardrails.check.repo`), `merge` or `replace` is the `grow` PR that changes the
   repo's checkers before they own the step, and `add` runs a `harness` step. No row runs a
   harness step for a rule a repo checker holds or will hold.
2. Present the draft record for confirmation: every `gate.json` command and its discovered
   source (make target, hook entry, CI job, required check), `ci.requiredContext`, `ci.runner`,
   `ownerOnlyPaths` (the dependency-audit exception file and any other path only the owner
   changes), scope exclusions, and the `profile.md` values. Explain the consequence of every replacement
   or exception, including whether a current hook, CI workflow, config, or developer document
   would be removed or changed.
3. For existing repositories, request an explicit operation for each conflicting practice. For a
   greenfield repository, propose a minimal central runner and only the stack-relevant rules; do
   not invent a CI provider.
4. Show the complete applicable rule mapping from Step 2 and the whole-repo findings from Step 4.
   A rule still blocked by a missing harness implementation stays blocked; do not offer a
   passing approval path by relabeling it.
5. Offer two tiers. `Minimal`: least change that gives the record's `gate.json` the required
   `checks.commit` and `pathCheck` naming the repo's own commands, plus the repo fixes that
   clear every finding of the rules it enables; state which applicable rules it leaves
   unenforced (narrower rule set, not a relabel). `Full`: every applicable rule and
   recommendation, including `checks.ci` wired into the repo's CI by the record's `ci.runner`
   role. In `adopt` mode both tiers give every `repo` and `grow` step to the repo's checkers in
   `guardrails.check.repo` and include every `grow` PR; a tier never keeps a duplicate harness
   step, or widens record allowlists so the harness copy passes code the repo checker already
   accepts.
   Write the repo-relative candidate paths per tier to `guardrail-proposal.json` in the draft
   record directory as `{"schemaVersion":1,"minimal":[...],"full":[...]}`; the owner saves it with
   the record. Nothing is written into the target or the live record.
6. Ask the owner to choose a tier, confirm the record, and approve exact candidate paths and
   merge/replace decisions, using `ask` with a question containing "guardrail" and options "Minimal"
   and "Full". When both tiers hold the same record settings and candidate paths, say so and ask
   only for approval of that one draft, with options "Approve" and "Revise". Write neither the
   record's `gate.json` nor any proposal path until this is answered. **STOP** until the owner
   replies; elapsed time or a default option is not approval.
7. If the owner chooses a narrower rule set, recompute the candidate diff, findings and
   proposal file while preserving whole-repo scope for every rule it keeps.

## Done when

- The confirmed record, approved paths, conflict decisions, and rule deviations are explicit.
- No write outside the draft record and the candidate root occurs before approval, and no
  blocker is represented as green.
