# Rules for every session and subagent

## Security

- Never commit, echo, print or log API keys, tokens, passwords or other secrets.
- Verify environment variables with presence checks, never by printing values.
- If a value must be shown, mask it to the first and last 4 characters.

## Act without asking

- Resolve councils: after 2 rounds, decide and record an ADR.

## Ask first

- New privileges, credentials or permissions.
- Longer CI runs, new evals or new tooling.
- Any work outside the agreed scope.

## Code discovery order

1. Behaviour or concept: `find`, with synonyms as `-k` keywords. Re-run per folder until no
   new files appear.
2. Symbols, definitions, references, blast radius: `lsp`; structural patterns: `ast_grep`.
3. Exact text or file names, and confirming a negative `find` result: `grep`, `glob`.
