---
agents: [main, task, sonic, designer]
question: >-
  Does this reply state a measured result from this session's own runs, such as a timing,
  speedup, latency, rate or accuracy figure, as a conclusion, without labelling it
  provisional or giving the sample size and variance?
condition: ['(?i)\b\d+(?:\.\d+)?(?:\s?[–-]\s?\d+(?:\.\d+)?)?\s?(?:ms|µs|s|sec|secs|seconds|min|minutes|x|×|%)(?![\w])|\bn\s?=\s?\d+|\b(?:p50|p95|p99|speedup|latency|throughput|pass rate|success rate)\b']
scope: text
---
# Measured claim

Read skill://test-guidance/references/behaviour-and-statistics.md before making the measured claim.
