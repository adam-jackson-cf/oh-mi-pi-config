# Process profile template

A profile binds one Jev process to the generic [rubric](rubric.md). Fill every field before scoring;
see the [worked example](example-profile.md). Start the file with a `# Profile: <process>` heading
so it can be found by search. Derive fields from the process implementation, its stored questions
and its decision records; ask the user only for fields the evidence cannot settle (usually the
reference-label definition and error costs). Mark each field `observed` (read from code or records)
or `stated` (given by the user).

- **Process:** Name, implementation location, how it is switched on or off
- **Cases:** How to produce [case-format](case-format.md) lines: the command or exporter, and where
  labels live
- **Version:** Where the version string comes from
- **Judgment:** Each question: primitive (choice, noul, score), what it asks, options or levels,
  whether a no-match or abstain outcome exists
- **Stages:** Deterministic rules that decide before Jev, and when Jev is asked
- **Verdict mapping:** How answers become verdicts: thresholds, bands, actions, enforcement
  behaviour
- **Baseline:** What runs without the Jev step (the deterministic stage alone, a person, nothing),
  so value is measured as what Jev adds
- **Label kind:** `reference` (labels the true class) or `verdict-grade` (labels grade the verdict)
- **Labels:** Vocabulary, positive label, how it maps to Jev's options. Labels judge the condition,
  never who asked; where the user's approval is recorded (for example `userDecision`)
- **R1 criteria:** What the state must contain, unclipped, for the judgment to be answerable
- **R2 procedure:** How the evaluator decides the label; abstention rule
- **Error costs:** Which of false positive and false negative is costlier, and why
- **Confirm set:** Cases that must be checked against wider evidence (always include every case at
  or above the action threshold)
- **Wider evidence:** Where to look beyond the recorded state: transcript, file at a hash, fetched
  page
- **Change bar:** The evidence a change needs: labelled sample (replayed from history where
  possible), dev/test split, regression set, and the value decision in
  [development.md](development.md); never tune on the evaluation set
- **Known limits:** Documented blind spots, so they are not re-reported as new findings

## Process with no profile

1. Locate the implementation: search for the code that calls Jev (the TypeSafe API or SDK) and
   for where it logs decisions. Read the questions, thresholds and verdict mapping from code.
2. Decide how cases are produced. If the records already hold the case-format fields, map them
   with a short script or `jq`; if a request and its outcome are separate records, the exporter
   must join them by id. If no decision log exists, say so: the process cannot be evaluated until
   it records its state, questions and answers.
3. Fill the table, then ask the user once for the `stated` fields that remain open.
4. Score with the rubric. Include the filled profile in the report; save it only when the user
   asks, wherever they keep profiles.
