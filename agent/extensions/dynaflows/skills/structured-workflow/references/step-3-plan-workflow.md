# Plan

## Objective

Map every acceptance criterion to a named proof in a draft manifest tied to the feature map.

## Required actions

1. Keep the plan a draft until spike answers land.
2. Map every criterion to a named proof in a draft manifest under the [freeze contract](freeze.md).
3. Every proof follows one rule: it fails at base and passes at head (TDD).
4. Proofs are outcome tests or lever journeys tied to the feature map. Each criterion names the feature-map entry,
   under the record profile's `verification_surface.feature_map_path`, that will link its proof file.
5. A repo whose record declares no feature map gets one through [Project setup](project-setup.md) before any proof is frozen.
6. A PR with no behaviour change (refactor, docs, project setup without new controls) maps no proofs, changes no
   feature-map doc and freezes an empty manifest; its PR check still checks CI, rename residue and new-path naming/structure.
7. Read `skill://test-guidance/assets/templates/installed/references/writing-tests.md` and `suite-maintenance.md` under that prefix,
   plus the record's `profile.md`. Missing surfaces follow [preflight](step-0-preflight-workflow.md) playbook selection.
8. Plan review is optional unless requested.

## Done when

- Every criterion has a named proof and feature-map entry, or the PR is recorded as changing no behaviour.
