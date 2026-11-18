# Change approval

## Objective

Make explicit owner decisions the boundary before target writes.

## Required actions

1. Present an action matrix: action ID, rule IDs, current/proposed owner, audience, operation
   (`keep`, `merge`, `replace`, `add`, `defer`), exact paths, dry-run counts, findings, verification
   and risk. No row introduces a second authority for an existing or planned owned control.
2. Explain every command and its discovered source, required status, runner role, protected paths,
   scope exclusion, replacement and exception. State which hook, workflow, config or developer
   document changes or disappears.
3. Request explicit treatment for each conflicting existing practice. For greenfield work propose
   a minimal central runner and stack-relevant rules; never invent a CI provider.
4. Show the complete Step 2 mapping and Step 4 whole-repository findings. Missing implementations
   remain blockers; approval cannot relabel them as passing.
5. Offer `Minimal`: least change delivering the selected core controls and clearing every finding
   of enabled rules, explicitly listing applicable rules still unenforced. Offer `Full`: every
   applicable rule and recommendation, with CI wiring. Both include all needed existing-control
   extensions and prohibit duplicate controls or weakened allowlists.
6. Ask the owner to choose Minimal/Full and approve exact paths, settings and merge/replace choices.
   If both are identical, say so and offer Approve/Revise for that single draft. **STOP** until an
   explicit answer; elapsed time and default options are not approval. Apply nothing meanwhile.
7. Recompute candidate diffs, findings and approved path lists after a narrower choice, retaining
   whole-repository scope for every selected rule.

## Done when

Approved paths, settings, conflict decisions and deviations are explicit. Nothing outside the
candidate root has changed, and no blocker is represented as green.
