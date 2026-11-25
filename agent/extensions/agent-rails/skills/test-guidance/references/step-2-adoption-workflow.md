# Adoption choice

## Objective

Let the owner choose how much of the approach the repository adopts, before any test run or plan.

## Required actions

1. Fill `assets/templates/adoption.md` from `inventory.md` using [placement](placement.md):
   - `option_rows`: seven rows in this order, each `| <fact> | <pointer-only> | <merge> | <full> |` with that fact's own
     value per option:
     1. paths new, by name;
     2. paths edited, by name;
     3. rules added, as counts per runbook section;
     4. approach rules the option would copy into a second repository file (`0` under `merge` and `full`; repetition
        already in the repository is not counted and stays, except that `full` replaces it with route lines);
     5. new locations (normally `none`);
     6. production code touched (`none`; exemplar design changes are counted in the breadth table);
     7. the task routes the index would hold;
   - the recommendation and its reason from [placement](placement.md);
   - every conflict with both defaults, every migration candidate kept in Step 1, and each optional row with its default;
   - each tier's budget with its source, marked `default` when the repository sets none and `enforced` when a check
     applies it (an enforced budget is a fact, not offered for change), and each existing deferral;
   - each mechanism gap as a `mechanism_gap_rows` row.
   Write it to `<notes>/adoption.md`. Show no time estimate.
2. Present the filled table, then this notice, filling the install command and cache folders from `inventory.md`:
   "After your choice, this skill runs the repository's test commands and checks in place. It installs dependencies with
   `<command>` and writes caches (`<folders>`). It changes no tracked file."
3. Ask exactly: "Choose pointer-only, merge or full, migration breadth none or exemplars, and any conflict, optional row
   or tier budget you want to differ from its default." **STOP** until the owner replies. Silence or elapsed time is not a choice.
4. Resume on the reply:
   - a reply naming an option takes breadth `none`, each conflict's default under that option, and each optional row's
     and tier budget's default for anything it does not name;
   - a question or comment that names no option: answer it and ask the action 3 question again;
   - a rejection of every option: return the rule map as the result, delete `<notes>` and end the run.
5. Record under `## Decisions` in `inventory.md` the option, the breadth, each conflict decision, each optional row and
   each tier budget.

## Done when

- `## Decisions` holds the owner's option, breadth, conflict decisions and optional rows, or the run has ended.
