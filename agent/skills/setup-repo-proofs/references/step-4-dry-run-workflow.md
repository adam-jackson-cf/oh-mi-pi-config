# Dry-run impact

## Objective

Show the full cost of clearing findings before changing the target.

## Required actions

1. Render candidates under the temporary root and compare them exactly with the target. Count
   creates, edits, deletes, added/deleted lines and unresolved merge conflicts.
2. Run the proposed check-only controls against the temporary checkout. Count findings by rule ID
   and path over the whole repository; report each owning command's result. Unavailable checkers
   or unsafe execution make the estimate `unknown`, never zero.
3. Report one view: the whole repository minus evidenced scope exclusions. Clear every enabled
   rule's findings without a waived-finding baseline. A narrower rollout changes enabled rules,
   never the paths a retained rule scans.
4. Estimate setup dependencies and added local/CI runtime using measured commands when possible.
   Separate measurements from estimates and state cache/platform assumptions.
5. Include missing implementations, existing-rule conflicts and work to reach zero findings. Give
   the full-plan total even when recommending a narrower rollout; expose every rule omission.
6. Keep candidates and output temporary and outside protected paths. No fix mode, package installs,
   staging or retained target summary during dry run.

## Done when

Whole-repository findings, file/line counts, confidence, unknowns and Minimal/Full differences are
visible without hidden rule omissions.
