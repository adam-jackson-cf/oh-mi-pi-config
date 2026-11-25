# Change approval

## Objective

Make the owner's scope, tier, path and ownership decisions the boundary before any target write.

## Required actions

1. Present an action matrix: action ID, rule IDs, current owner, proposed owner, audience, operation (`keep`, `merge`, `replace`,
   `add` or `defer`), exact paths, dry-run change count, findings per scope, verification and risk.
   `keep` leaves a family with its existing checker; `merge` or `replace` is the `grow` change to that checker; `add` introduces a
   new repo control. No row introduces a second authority for a rule an existing or planned checker holds.
2. Explain every command and its discovered source (make target, hook entry, CI job, required status), each enforcement point and
   whether it blocks deterministically, owner-only paths (the dependency-audit exception file and any other path only the owner
   changes), scope exclusions, `proofs.toml` values and the consequence of every replacement or exception, including whether a
   current hook, CI workflow, config or developer document would be removed or changed. State that the shipped validator needs
   Python 3.11+ in every place it runs.
3. For existing repositories, request an explicit operation for each conflicting practice, including a tier mechanism named
   for speed alone, each existing deferral ([test evidence](test-evidence.md)), each T3 job's trigger, and each instruction
   that asks for a record in human documentation (a ledger, changelog or decision log) when a source or practice is adopted.
   For a greenfield repository, propose a minimal central runner and only the stack-relevant rules; do not invent a CI provider.
4. Show the complete Step 2 mapping, the Step 4 views and the structure-only coverage result. A rule still blocked by a missing
   implementation or proof stays blocked; never offer a passing path by relabelling it.
5. Offer two tiers:
   - `Minimal`: the least change that enforces every rule it enables: an installed hook and, where the repo has a CI provider, one
     CI job running the same entrypoint plus the validator job, `proofs.toml`, and fixes that clear every finding of the rules it
     enables in the chosen scope. Without host protection a CI status reports but does not block merges, and a hook alone can be
     bypassed; state that, and list the exact rule IDs Minimal leaves unenforced (at least `GIT.required-checks`, and every
     `local_ci` rule plus `GATE.self-check` when the repo has no CI provider). This is a narrower rule set, not a relabel.
   - `Full`: every applicable rule and recommendation, including CI points with their statuses, the validator as its own CI job,
     host protection requiring every claim's status and the validator status where supported, and owner review of `proofs.toml`,
     the validator and CI workflow files.
   In `adopt` mode both tiers include every `grow` change for the families they enable; neither keeps a duplicate checker or widens
   allowlists so a duplicate passes.
6. Write each tier's candidate tree to `state.json` `proposal` ([run state](run-state.md)) as
   `{"scope":{"mode":"<whole|changed|paths>","base_ref":<ref or null>,"paths":[...]},"minimal":{"root":"<candidate dir>","paths":[...]},"full":{...}}`.
   A tier whose files differ (`proofs.toml` included) has its own candidate root, so each tier's settings are exact.
   Nothing else is written into the target before approval.
7. Ask the owner to choose a scope and tier and approve exact candidate paths, settings and merge/replace decisions, with one question
   containing "verification setup" and options "Minimal" and "Full". When both tiers hold the same paths and settings, say so and offer
   "Approve" and "Revise" for that one draft. **STOP** until the owner replies; elapsed time or a default option is not approval.
8. Record approvals only as the owner stated them, in `state.json` `decisions`: budget limits, coverage floors, exclusions,
   exceptions (each with a reason), conditional-rule exclusions (`approved_by` in `proofs.toml` `[[not_applicable]]`), the owner
   identity and entries for owner review, and the exact rule IDs the chosen tier leaves unenforced (Step 7 checks against them).
9. If the owner chooses a narrower scope or rule set, recompute the candidate diff, findings, `proofs.toml` and proposal while keeping
   the complete applicable rule set inside that scope, and show the whole-repository total again.

## Done when

- The approved scope, tier, paths, settings, conflict decisions and rule deviations are explicit.
- Nothing outside the candidate root, `.proofs/` and its `.git/info/exclude` entry has changed, and no blocker is represented as green.
