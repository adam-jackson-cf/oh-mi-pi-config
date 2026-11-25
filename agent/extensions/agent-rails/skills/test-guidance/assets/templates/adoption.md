## Adoption options for {{repo_name}}

| Fact | pointer-only | merge | full |
|---|---|---|---|
{{option_rows}}

Recommended: {{recommended_option}}, because {{recommendation_reason}}.

## Conflicts

| # | Approach rule (runbook section) | Repository rule (path:line) | Default under merge | Default under full |
|---|---|---|---|---|
{{conflict_rows}}

## Migration breadth

- `none` (default): no existing test changes; the result lists these candidates as follow-up tasks.
- `exemplars`: migrate the candidates below, after a full baseline run.

| # | Rule group | Tests affected | Exemplar (path, test) | Needs a production change |
|---|---|---|---|---|
{{exemplar_rows}}

## Optional rows

{{optional_rows}}

## Tier budgets and deferrals

| Tier | Budget | Source | Existing deferrals (path, reason) |
|---|---|---|---|
{{budget_rows}}

## Mechanism gaps

Follow-ups for the repository's owner; no option changes a command, collection setting or gate.

| # | Tier or path | What selects it now | Gap |
|---|---|---|---|
{{mechanism_gap_rows}}
