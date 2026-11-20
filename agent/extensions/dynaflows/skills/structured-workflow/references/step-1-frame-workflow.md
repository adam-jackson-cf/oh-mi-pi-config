# Frame

## Objective

Define the requested outcome, every acceptance criterion and only the questions whose answers change the plan.

## Required actions

1. State the requested outcome and every acceptance criterion; apply the active playbook's Frame delta.
2. Ask only questions whose answers change the plan. `kiss` cuts unnecessary work and questions, never requested behaviour.
3. Discover owners and callers in this order:
   1. `find` for behaviour and concepts, with `-k` synonyms, repeated until no new files appear;
   2. `lsp` for definitions, references and implementations;
   3. `ast_grep` for structure;
   4. `grep` or `glob` for exact text.

## Done when

- The outcome, criteria, owners and callers are recorded, and every open question changes the plan.
