# PR description

## Audience

- Write for a reviewer arriving cold: no session history, no knowledge of the plan, limited time.
- The description exists to direct review attention, not to prove every claim. Evidence is a summary; the diff and linked docs hold the detail.
- Target 400–650 words. If you are over, cut evidence before cutting the risk table.
- The implementation writer owns the body and later corrections. Main's complete verification record is a separate
  PR comment under [verify-loop](verify-loop.md#verification-comment-and-agent-verified-status), outside this word budget.

## Template

Use these sections in this order. Omit `Gate changes`, `Decisions needed from the owner`, `After merge` and `Notes` only when empty.

```markdown
## In short

<1–2 sentences: what changes, in plain words.>

- **Why:** <the problem it fixes, one sentence; link the issue>
- **Unchanged:** <what a reviewer might fear changed but did not: infra, protected paths, versions, interfaces>

Closes #<n>. <Depends on <repo>#<n>, if any.>

## Where to focus review (highest risk first)

| # | Area | Files | Why it's risky |
|---|---|---|---|
| 1 | **<short name>** | `<path>` | <the concrete failure if this is wrong; say what is untested> |
| 2 | ... | ... | ... |

**Lower risk, skim only:** <generated, copied, test, fixture, config and doc paths, with one clause each>

## Evidence

- **<Check name>:** <existing CI-reported counts/timings; comparable head/default same-tier values if available, otherwise baseline unavailable>
- **<Smoke / live run, if already exercised>:** <what ran, the observable outcome>

**Not tested:** <what could not be exercised before merge, and where it will be>

## Gate changes

- `<path>` — <effect on checks; omit this section when empty>

## Decisions needed from the owner

1. <yes/no question> (#<risk row>)

## After merge

<The exact action to take, then the observable signals that mean it worked.>

## Notes

- **<Topic>:** <commit-split or bisect caveats, downstream pins, known differences>
```

## Risk table rules

- Rank rows by the damage if the change is wrong: production behaviour first, then silently skewed data or results, then reviewer or maintainer cost.
- Keep 3–6 rows. Merge minor items into `Lower risk, skim only`.
- Each row names real file paths and one concrete failure mode, not a category like "complexity".
- Treat anything verified only against fakes, mocks or unit tests as risk, and say so in the row.
- A behaviour change that is intended still goes in the table when it alters cost, latency, logs or outputs.

## Evidence rules

- Use existing CI-reported measurements only. Include test count/time comparisons only for comparable head/default runs of the same tier;
  otherwise say `baseline unavailable`. Never invent a budget, fabricate measurements or run extra suites/benchmarks solely to fill the body.
- One line per check. Link long analysis rather than pasting tables of per-item results.
- The writer names gate/config paths and their effects under `Gate changes`, updating omissions discovered by `prove().gateChanges`.
  Listing a path is not itself a merge gate. Full verification evidence belongs in main's separate comment, not this summary.
- Always include `Not tested:`; an empty answer must be earned.
- When a comparison is confounded, say what the numbers do not show in bold, before the reader draws the wrong conclusion.

## Decisions and notes

- Phrase each owner decision as a question answerable with yes or no, and point to the risk row it relates to.
- Put merge-method caveats (including any project-documented merge-method exception), re-pinning and follow-ups in `Notes`, not `In short`.

## Avoid

- Pasting gate or CI command strings, per-hook or per-stage tables, and transcripts.
- File-by-file changelogs; the diff already shows them.
- Repeating the issue body or restating `Done when` lines one by one.
- Unexplained internal jargon; name the thing plainly on first use.
- Leading with process (how the work was done) instead of outcome (what changed and where to look).
