# Fallback and handoff

## Objective

Bound raw-tool fallback and report setup outcomes accurately.

## Guidance

- Use raw reads or search only for non-indexed files, documentation, configuration, named stale files, missing graph coverage, or a reported setup failure.
- Never use CodeGraph instead of tests, typecheckers, linters, or runtime validation.
- For setup or repair, report the target repository, commands run, final status, and fallback reason.
- For ordinary queries, mention CodeGraph only when graph context materially supports the answer.
