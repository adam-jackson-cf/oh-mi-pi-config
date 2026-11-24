---
agents: [main, task]
repeatMode: once
question: >-
  Does this reply announce that the assistant is replacing, renaming or migrating existing code
  now or next, as its own work? Answer no for instructions to another agent, reviews, questions,
  or comparisons of options.
condition: ['(?i)\b(?:replac\w+|renam\w+|migrat\w+|cutover|shims?|compat(?:ibility)?|backwards?[- ]compat\w*|deprecat\w+|legacy|aliases|alias|re-?exports?)\b']
scope: text
---
# Clean cutover

When you replace, rename or migrate existing code, the new design becomes the only design, in the
same change.

- Inventory every caller, test, doc and preserved behaviour first (`lsp references`, search).
- Migrate every caller, test and doc to the new design.
- Remove the obsolete implementation, its aliases, re-exports, compatibility shims, comments and
  dual paths in the same change.
- Never implement backward compatibility, and never refactor code to serve both the new and the
  legacy objective.
- Never add fallback modes, flags or temporary strategies that keep the old path alive.
- Remove legacy references and legacy-only items. Never name a file or module with a version
  suffix (`parser-v2.py`); a `<name>/v<N>` folder is allowed only where the repository's
  structure rules define one.
- Reuse the exact wording and styling of existing concepts, constraints, labels and user phrases.
  Never introduce a semantically equivalent rewrite of canonical wording, labels, limits or
  instructions in one project.
- A behaviour-preserving refactor keeps the repository's existing tests and CI as its proof. A
  part of the change that adds behaviour is a feature and needs its own tests.
- If the old path cannot be removed in this change, stop and ask the owner; never ship both.
