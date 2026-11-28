# Structure and naming

What the checks and judgments cannot decide; each finding prints its own rule and fix.

- A code move updates test imports in the same step; test files stay where they are.
- Reference another part by explicit path, never by search order or shadowing.
- Every instance of one kind shares one layout; older instances change only in a restructure.
- A new folder follows the pattern its same-role peers under one parent share.
- A name covers everything its folder holds; rename the folder when its contents outgrow it.
- Version only what must be reproduced or compared, as `<name>/v<N>`; name the current version in
  one place.
- A pinned or released file moved under a renamed folder is renamed too, with locks redone and
  experiments rerun, or stays put.
- When a rule-correct home needs existing code moved, use the matching-role folder and state the
  mismatch.
- A restructure moves one concept per step, with no behaviour change in the move.
