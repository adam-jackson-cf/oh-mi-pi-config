# Developing a Jev process

Use this when building a Jev-backed process, making one good enough to use, or improving one that
underperforms. The goal is a process that adds value now and keeps improving. It is not a one-shot
experiment that either meets every target or gets abandoned. Question and state design is in
[jev-design.md](jev-design.md); case scoring is in the [rubric](rubric.md).

## Principles

1. **Value against the baseline, not perfection.** Ask "does this add value over what runs without
   it, at a burden the user accepts?" A missed target becomes the next iteration's goal, not a
   reason to stop.
2. **Evidence now, not later.** Replay history now and get blind labels straight away from
   separate sessions with no author or replay context (step 2 below). Do not park a process in
   shadow or log-only mode to collect data that logs, transcripts or the repository already hold.
3. **Iterate before concluding.** Diagnose every imperfect result and consider at least three
   alternative changes before any verdict that the approach is not worthwhile.
4. **Find the layer that is wrong.** Jev's perception, the policy, the labels, the state, the
   deterministic stage and the composition code fail differently and have different fixes.
5. **Verify in a fresh instance.** See the skill's fresh-instance rule; the session that made a
   change is still running the old process.

## 1. Frame

Settle these before looking at results:

- **Action.** What happens on a positive (log, advisory note, confirm prompt, block) and to whom.
- **Baseline.** What runs without the Jev step: the deterministic stage alone, a person, or
  nothing. Value is what Jev adds to it.
- **Costs.** What one false action costs at that action type (a confirm prompt costs seconds; a
  block stalls work) and what one miss costs.
- **Policy.** The written decision policy ([jev-design.md](jev-design.md), "Write the policy before
  the questions"). It drives both the composition code and the labelling.
- **Targets and burden budget.** For example a recall target and a maximum number of false actions
  per active day. Pre-register them with the data split. They steer iterations; they are not a
  pass/fail gate for the whole effort.

## 2. Build the evidence set now

- **Replay.** Run the process's routing over history (decision logs, agent transcripts, commit
  history, tickets) and emit units in the [case format](case-format.md). A few hundred real units
  in one session beats a week of waiting for live traffic.
- **Split** by an independent unit (session, user or repository hash) into dev and test. Tune on
  dev only.
- **Rare positives.** Add an independently authored synthetic held-out set; the author of the
  questions never writes it. Real positives stay the stronger evidence: synthetic items lack real
  context such as the surrounding changed files and the session's earlier work.
- **Label blind** against the written policy:
  - Labellers see the unit, not Jev's answers or the process verdict, and cite the policy rule that
    decided each label.
  - Use two independent labellers: separate sessions with no shared context (different model
    families are optional). Agreement sets the label; `legit` against `uncertain` resolves to the
    decided side; adjudicate real disagreements with the wider evidence and record the reason.
  - Read the rule citations. A labeller that reads one rule too broadly shows up as many labels
    citing that rule; clarify the rule and re-derive those labels instead of adjudicating each.
  - Check consistency: near-duplicate units with opposite labels mean the policy does not decide
    that case. Fix the policy and relabel before blaming the model.
  - Agent labels stay `agent` until a person confirms them; a human label always wins.

## 3. Iterate

Run each variant on dev and keep a run log: variant, what changed, dev counts, errors by question
family, notes. After every run, diagnose before concluding anything:

1. **Error table.** Errors per question family with case ids, each attributed to one cause from the
   rubric's R3 table.
2. **Perception versus policy.** On a fixed-seed sample (about 20 per question id), have two blind
   labellers answer Jev's atomic questions from the same state Jev saw. If Jev agrees with their
   consensus about as often as they agree with each other, Jev's perception is fine. Then recompute
   the verdicts with the consensus answers: errors that remain belong to the policy, composition or
   labels, not the model.
3. **Label consistency.** As in step 2 of the evidence set.
4. **State sufficiency.** Rubric R1: is the needed evidence in the state, unclipped, and pointed to
   by the question?
5. **Deterministic stage.** Is the router sending cases a rule could decide, or never routing some
   positives to Jev at all?
6. **Known model limits.** The jaggedness page for the model version in use.

Then list at least three alternatives, drawing on what has failed, before choosing the next
variant:

- Narrow the state; add facts computed by code.
- Split a question; restate it with the exact condition and boundary examples; add a no-match.
- Move a decidable rule into code; refine the deterministic router.
- Change the composition: per-family rules, an uncertainty band routed to a person or a second
  question, per-family thresholds, weighted scores, or a learned model once enough real positives
  exist.
- Change the policy with the user when labels show it does not decide real cases.
- Use another primitive or pattern (a Choice over options, verify-and-escalate, a cascade).

Stop iterating when the process adds value and the user accepts the remaining gaps (ship and keep
a backlog), or when two or three rounds of design-level changes give no dev improvement (report
what was tried and why it did not help). Conclude that Jev cannot do the task only when the
perception test shows its atomic answers disagreeing with consensus well beyond the labellers' own
disagreement, and state and question rewrites did not close the gap.

**Test hygiene.** Evaluate the test split once per decision point. Once its results have been
seen, build a fresh evaluation set from a newer replay window or a new held-out set; do not wait for
new traffic to arrive.

## 4. Decide: does it add value?

Report a value table against the baseline, with denominators:

- **Added catches:** positives the process catches that the baseline misses.
- **Burden:** false actions per active day (false-positive rate × routed units per day).
- **Cost per false action** for the action type.
- **Remaining misses** by cause: the next iteration's backlog.

Ship (enable it for real, for example `enforce`) when it adds catches the baseline misses and the
burden fits the budget for its action. Unmet targets do not block shipping; they become the
backlog. When the trade-off is unclear (a blocking action, burden near the budget), give the user
the value table with a recommendation and let them decide.

Shadow or log-only mode is a tool, not a waiting room. Use it only when no history can be replayed
(a new event type with no logs), time-boxed and labelled as data arrives, or when a false action is
severe and replay cannot estimate the burden. Never recommend "keep it in shadow for a week"
without the value table and the specific evidence the wait would produce that replay cannot.

## 5. Ship and verify

- Bump the process version. Add the labelled cases that drove each change to its regression
  fixtures. Figures measured on an earlier version no longer describe the process: tag every
  quoted figure with its version and re-measure before relying on it.
- If the process maintains itself (a loop that proposes rule changes), it works on a copy behind
  a deterministic gate and an independent reviewer, and deploys from outside any agent; see the
  integration checklist in [jev-design.md](jev-design.md). Never give it an exemption flag.
- Smoke-test in a fresh instance, where the process actually applies (it may exempt scratch or
  temporary directories), against the real model. Confirm the records carry the new version and the
  expected verdicts.
- Then evaluate new-version records with the skill's Evaluate procedure and feed the findings back
  into step 3.

## Worked example: an integrity guard for coding agents

Rules route agent commands and diffs to `certain` (escalate without Jev) or `suspect` (Jev judges).
A positive asks the user to confirm, or blocks a subagent until its orchestrator asks. The rounds,
and what each should have led to:

- **Replay 1** (591 units, two blind labellers). One broad noul per category flagged 122 of 371
  legitimate units. It was moved to shadow. *Should have:* diagnosed; the cause was question and
  state design.
- **V0–V3** (focused state, per-family atomic questions, facts computed by code). Dev false flags
  fell from 33/136 to 12/136; test from 39/173 to 32/173. Reported as failing the bar. *Should
  have:* read the trend as progress and continued.
- **Perception test.** Jev agreed with two labellers' consensus on 232 of 251 atomic answers (92%);
  the labellers agreed with each other on 251 of 270. Consensus answers still flagged 16% of
  legitimate units, so the policy and labels were at fault. This was the most useful diagnostic.
- **Policy written and relabelled.** Rule-coded line directives were allowed and manual
  `pre-commit run` skips were declared diagnostic. The labellers agreed on 240 of 315 units; 41
  labels from one misread rule were found through the rule citations and re-derived.
- **V4.** Test false flags 7/175 (4%); recall 14/21 against a target of 80%. It was proposed for
  "a fresh shadow week". *Should have:* shipped. It added catches behind a deterministic router
  with a confirm action at an estimated one to two prompts per active day.
- **Learned composition.** Trained on dev plus synthetic fixtures, it flagged 40/175 legitimate
  test units: the synthetic positives lacked a user request, so the model learned that absence. The
  rule composition was kept, and the user's request was later removed from the state altogether:
  who asked is the confirm prompt's job, not Jev's.
- **Shipped in enforce after refinements** (`guard-integrity-2026-10-02.4`, before user intent was
  removed; re-measure on later versions). All 13 real hacks escalated (7 through Jev), with about
  5% of routed legitimate work prompting. A fresh-process smoke found a bug the unit tests missed:
  the state showed Jev only the first matching line.
- **Re-measured after removing who-asked** (`.6`, `.7`). A blind relabel with the request hidden
  surfaced a policy ambiguity first: one labeller family read `gate | tail` as masking a failure.
  The policy was clarified to match the code (masking turns failure into success) and every label
  citing that clause was re-derived. `.6` escalated 39 of 322 legitimate units; the error table
  showed two state gaps, not model errors: test deletions whose module was deleted in the same
  commit, and config rules firing on commands that wrote no config. Code-computed facts for both
  brought `.7` to 12 of 322 with hack recall unchanged (19 of 24).
