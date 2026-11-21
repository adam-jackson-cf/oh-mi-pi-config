# Integrate

## Objective

Merge the verified exact head under git-tool's authority, and recover a red default branch by revert.

## Required actions

1. The valid schema-v2 record from [Implement](step-4-implement-workflow.md) remains required;
   the Git no-CI exception does not waive setup.
2. Main never commits. Follow `skill://git-tool/references/merge-and-recover.md` for merge method, confirmation and
   recovery. Git-tool owns Git authority: `agentMerge: disabled` requires owner approval.
   Do not copy merge mechanics into playbooks.
3. Before merge, main must have run both `prove()` and `structure_review()` on the current exact PR head, with CI green,
   prove `status: pass` and structure `halt: false`, unless the record's matching `mergePolicies` entry sets
   `proof: false`. Rails' `gh pr merge` guard enforces this; missing, corrupt or
   wrong-head results block, even with an empty manifest.
4. A rebase or other branch update requires both helpers on the new head before merge. Fix structure findings and rerun
   both helpers and CI on every new head.
5. Recovery uses git-tool's revert mechanics, with an empty manifest when the revert changes no behaviour.
   A revert still needs both exact-head helpers. If it changes a feature-map doc without a linked frozen proof,
   `prove()` blocks; escalate with those doc paths rather than waive the gate.
6. The durable fix gets its own PR with proofs reproducing the failure.

## Done when

- The PR merged on a head with green CI, prove `status: pass` and structure `halt: false`, or the blocker is escalated.
