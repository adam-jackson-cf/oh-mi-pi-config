---
agents: [main, task]
repeatMode: once
question: >-
  Does this reply state a measured performance or quality conclusion from this session's own
  runs, such as a speedup, latency, rate or accuracy figure, without labelling it provisional
  or giving the sample size and variance? Answer no when the figures are verbatim command
  output quoted as pass/fail evidence (including test counts and run durations), rather
  than a performance or quality conclusion.
condition: ['(?i)\b(?:speedups?|faster|slower|improv(?:ed|ement)|reduc(?:ed|tion)|latency|throughput|p50|p95|p99|pass rate|success rate|flake rate)\b|\bcut\b[^\n]*\bfrom\b|\b\d+(?:\.\d+)?\s?(?:x\b|×(?!\w))|\bn\s?=\s?\d+|\b(?:increase[ds]?|decrease[ds]?|drop(?:ped)?|rose|fell|change[ds]?)\b[^\n]*\d+(?:\.\d+)?\s?%|\b\d+(?:\.\d+)?\s?%\s+(?:lower|higher|less|more)\b']
scope: text
---
# Measured claim

Support every measured performance or quality claim with its sample size and stated uncertainty,
or label it provisional.

Verbatim test counts and command durations are pass/fail evidence, not performance conclusions.

Design the measurement before running it:

- Test behaviour, not only end results. Express it as invariants over a fixed seed set instead of
  pinning one run's numbers. When no invariant fits, audit each run's logs or telemetry against a
  named measure, unit and bound chosen before seeing the result.
- Define one unit of analysis, normally one seed in one cell of factor settings, counted once per
  cell. State how repeated or multi-sided observations of one seed depend on each other; never
  count correlated observations as independent samples.
- Compare variants, including baseline and change, on matched seeds and identical factor
  settings. Balance positional and ordering bias with the same declared scheme in each arm.
- Stratify within factors that influence outcomes, and report per-factor results beside pooled
  ones; never hide a divergent subgroup.
- Fix the seed range, sample count, factors and analysis before running any arm. Never add seeds
  until a desired result appears, and never discard inconvenient runs.

Report the result:

- Give each rate, threshold comparison and claimed difference with its sample size and interval,
  and state the interval's confidence level and method.
- For a between-arm claim, compute matched differences within each cell and seed, and report their
  mean and interval from a paired analysis; never substitute an unpaired test.
- If the difference interval includes zero, report no measured change, not proof of equivalence.
- Label a claim provisional when it lacks fixed matched seeds, a sample size or an interval, or
  rests only on a smoke run. A provisional result is neither a measured improvement nor a passed
  measurement gate. A reporting-only task meets the same evidence standard.
- Follow the repository's measure-run protocol before upgrading a provisional claim: baseline and
  change on separate trees and output directories with the same full protocol. If that run is
  outside the task's authorization, ask once and keep the claim provisional until it completes;
  if no protocol exists, state the missing prerequisite.
- Inspect visual output and telemetry for defects before asking a person to judge subjective
  quality, and keep that judgement apart from measured evidence.
