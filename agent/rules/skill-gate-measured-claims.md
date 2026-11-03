---
agents: [main, task, sonic, designer]
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

Read skill://test-guidance/references/behaviour-and-statistics.md before making the measured claim.

Verbatim test counts and command durations are pass/fail evidence, not performance conclusions.
