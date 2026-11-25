# Compare rules statically

## Objective

Record, from files alone, which approach rules the repository states, contradicts or lacks, and how its tests practise them.

## Required actions

1. For each area in [suite-signals](suite-signals.md), run the searches it names and record under `## <area>` in
   `inventory.md` each value found, its `path:line` evidence and the counts the area asks for, or `none found` with
   the searches run. Read files and search only; run no test, install or build command in this step.
2. Read every rule in the runbook templates under `assets/templates/installed/references/` and the index template's
   `Every task` section. Mark each rule `stated`, `contradicted` or `missing` as [placement](placement.md) defines,
   checking every file `## Homes` lists as stating testing rules. Cite the repository `path:line` for each stated or
   contradicted rule.
3. Write the rule map under `## Rule map`: one row per rule group with its stated, contradicted and missing rule counts,
   the practice verdict of each suite-signals area judged against it, and one line of evidence.
4. Record under `## Conflicts` each contradicted rule: the approach rule, the repository rule with `path:line`, and the
   defaults [placement](placement.md) gives under each option. A helper a migration would need that a repository rule
   forbids (for example a shared fake) is a conflict too, as is each instruction that asks for a record in human
   documentation (a ledger, changelog or decision log) when a source, rule or practice is adopted.
5. Record under `## Runbooks` each runbook template with its install condition from [placement](placement.md), the
   evidence that meets it or the searches that do not, and each optional row.
6. Record under `## Migration candidates` the ranked patterns, counts and exemplars from suite-signals.
   Keep at most one exemplar per pattern and at most three in total, taking the highest-ranked patterns.
7. Record under `## Tier facts` each repository tier mechanism, the selection it gives, the tier and tag it maps to,
   whether that tier has a complete command, each tier's budget and its source, each existing deferral and any record of
   deferral approvals as [tiers](tiers.md) defines them, every scheduled workflow, every test file no tier selects, and
   each mechanism gap.

## Done when

- Every suite-signals area holds values with evidence, or `none found` with the searches run.
- `inventory.md` holds the rule map, conflicts, runbooks, migration candidates and tier facts, each with entries or `none`.
- The target is unchanged.
