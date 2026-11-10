# Behaviour and statistics

## Objective

Assess behaviour over fixed seed sets and support every measured claim with stated uncertainty.

## Guidance

- Test behaviour, not only terminal outcomes: intermediate defects can leave final results unchanged.
- Express behaviour as invariants over a fixed seed set rather than pinning one run's numbers.
  Check each relevant step from state or logs: an invalid target is released immediately,
  a stopped entity stays stopped, or a plan never selects an unreachable destination.
- When an invariant cannot express the target behaviour, add a per-run audit over logs or telemetry.
  Name its measure, unit and bound, such as response latency in steps below a declared maximum.
  Choose the bound before seeing the result.
- Define one unit of analysis: normally one seed within one sweep cell of factor settings.
  Count that seed once per cell.
  If multiple sides or repeated observations belong to one seed, state their dependence and pairing;
  do not count correlated observations as independent samples.
- Compare variants on matched seeds and identical factor settings, including baseline versus change.
  Balance or counterbalance positional and ordering bias with the same declared scheme in each arm.
- Stratify within factors that influence outcomes rather than splitting only pooled observations.
  Report relevant per-factor results alongside pooled results; do not hide divergent subgroups.
- Fix the seed range, sample count, factors and analysis before running every arm.
  Never add seeds until a desired result appears or discard inconvenient runs.
- Report each rate, threshold comparison and claimed difference with its sample size and interval.
  State the interval's confidence level and method rather than relying on an unstated default.
- For between-arm claims, compute matched differences within each cell and seed.
  Report their mean and interval using a paired analysis; never substitute an unpaired test.
  If the difference interval includes zero, report no measured change, not proof of equivalence.
- A reporting-only task need not add invariants or audits, but must meet the same evidence standard.
- Label claims provisional when they lack fixed matched seeds, sample size or an interval,
  or rest only on a smoke run.
  A provisional result is neither a measured improvement nor a passed measurement gate.
- Follow the repo's defined measure-run protocol before upgrading a provisional claim.
  Run baseline and change on separate trees and output directories with the same full protocol.
  If that run is outside the task's authorization, ask once before running it and keep the claim
  provisional until it completes; if no protocol exists, state the missing prerequisite.
- Capture and inspect relevant visual output and telemetry for defects before asking a person
  to judge subjective quality; keep subjective judgement distinct from measured evidence.
