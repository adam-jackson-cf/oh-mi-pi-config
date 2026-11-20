# Docs and programming model

## Objective

Apply the System One programming model and read the current TypeSafe docs before designing questions
or writing code.

## Guidance

- Run this first in every task; then run, in order, each `SKILL.md` section whose When holds,
  skipping those whose outputs already exist.
- Pin a versioned Jev model ID, never an alias such as `jev-latest`, which can change answers
  without a code change: the user's existing one, else the newest on the models page or in the
  installed SDK's types, else, when writing questions, ask the user once, in the framing STOP when it
  runs, else before question design; for a review, or with none given, report the missing pin and
  skip version-specific checks.
  Later steps read that version's pages.
  A task that only tunes or analyzes an existing `scores.csv` pins nothing until it re-scores.
- Treat Jev, TypeSafe's System One model, as programmable common sense: typed answers and
  probabilities, never generated text.
- Never leave TODO comments in code or files: record each gap in `shortfalls` in `thresholds.json`
  and in the result.
- Read the live docs, the source of truth, during the task, starting at the
  [documentation index](https://docs.typesafe.ai/llms.txt) with targeted reads.
  Append `.md` to page paths, including extensionless index links, and resolve relative links
  against `https://docs.typesafe.ai`; fall back to the normal page, then the pages below.
- Without live access, use local docs or the installed SDK's types, say so, and invent no
  version-dependent details.
- Read the pages for the work at hand:
  - Programming model: [System One](https://docs.typesafe.ai/concepts/system-one.md),
    [how to build](https://docs.typesafe.ai/concepts/how-to-build-with-system-one.md).
  - What to build: [use-case map](https://docs.typesafe.ai/concepts/use-case-map.md), then the
    closest cookbook.
  - Questions: [state](https://docs.typesafe.ai/concepts/state.md),
    [primitives](https://docs.typesafe.ai/primitives.md), then the chosen primitive's page.
  - Uncertainty: [confidence](https://docs.typesafe.ai/confidence.md),
    [confidence routing](https://docs.typesafe.ai/patterns/confidence-routing.md).
  - Weak spots: [models](https://docs.typesafe.ai/models.md), then the pinned version's jaggedness
    page, such as [Jev 1.13](https://docs.typesafe.ai/model-jaggedness/jev-1.13.md).
  - Coding agents: [Jev with coding agents](https://docs.typesafe.ai/introduction/coding-agents.md).
  - Before writing integration code: [HTTP API](https://docs.typesafe.ai/api.md),
    [Python SDK](https://docs.typesafe.ai/sdk/python.md)
    or [JavaScript SDK](https://docs.typesafe.ai/sdk/javascript.md);
    for older integrations, the installed SDK's reference and changelog.
