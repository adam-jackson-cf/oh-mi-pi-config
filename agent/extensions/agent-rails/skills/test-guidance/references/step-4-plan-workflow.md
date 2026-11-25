# Implementation plan

## Objective

Present every object this run will write, where and why, as one plan the owner signs before any repository change.

## Required actions

1. Fill `assets/templates/plan.md` with one row per object, resolving each location and action by [placement](placement.md)
   and the owner's decisions. Write it to `<notes>/plan.md`. Rows cover, as the option and breadth call for:
   - the index skill rendered from `assets/templates/installed/SKILL.md`, each runbook from
     `assets/templates/installed/references/` without the rules its option does not adopt, the skill symlink;
   - each merged rule section, each `full` route line replacing a duplicate, and each instruction pointer;
   - under `exemplars`, each kept exemplar, each helper it needs and each design change;
   - each earlier-install file to edit or delete.
   A row's `Depends on` names the rows it needs: an exemplar its helper, a helper its design change, a pointer the index.
2. Under `## Exact text`, show the full text of every new or merged file, rule section, pointer and route line, filled
   with the placeholder values. A merged section keeps the target file's heading level and list style.
3. Under `## Design changes`, show each production diff: the new input and its production default, which leaves current
   callers unchanged. Under `## Exemplar intents`, give each exemplar's test, pattern, rewrite route (real module first,
   then a production input, then a substitute only for an external boundary), its helper's home, and for a deletion the
   retained test that covers its behaviour.
4. Under `## Not adopted`, list each declined rule, conflict kept in the repository's favour and declined optional row.
5. Show the whole plan in the reply, table and exact text included (a link to `<notes>/plan.md` alone is not showing it),
   and ask exactly: "Apply this plan?" **STOP** until the owner replies. Silence is not approval.
6. Resume on the reply:
   - approval: continue to Step 5;
   - rows rejected: remove them and every row whose `Depends on` names one, rerender remaining text that names them, say which, and continue to Step 5;
   - edits requested: revise those rows, show the changed rows and ask again;
   - a question or comment: answer it and ask again;
   - the whole plan rejected: return the rule map and plan as the result, delete `<notes>` and end the run.
7. Record the approved rows and each rejection under `## Decisions` in `inventory.md`.

## Done when

- `## Decisions` lists the approved plan rows, or the run has ended.
- The target is unchanged apart from Step 3's dependencies and caches.
