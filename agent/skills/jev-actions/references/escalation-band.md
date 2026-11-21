# Escalation band

## Objective

Send uncertain Jev answers to a fallback judge only where measurement on labeled cases shows the
cascade meets the minimums.

## Guidance

- Add an escalation band, sending uncertain Jev answers to a fallback judge, only on judgments
  [design patterns](design-patterns.md) keeps in Jev, whose ranking passes in
  [threshold tuning](threshold-tuning.md), with about 30 cases per label and an agreed
  accuracy loss against the fallback alone; otherwise set none, noting why in `shortfalls`.
- Set the band from saved results, after a [calibration check](calibration-check.md):
  - Run the fallback once on the same `dev` and `test` cases with each Noul's exact question, save
    its verdicts in a `fallback` column, and compute the cascade's precision, recall, accuracy and
    escalation rate by hand, since the script ignores that column.
  - Choose the narrowest band meeting the minimums on `dev` within the agreed loss, report its
    escalation rate, and confirm it once per question on `test`; if none qualifies, or `test`
    misses, set none and report it.
- With one Noul per failure mode, set a band per Noul; prefer a fallback that errs differently from
  Jev, or a person.
- In production, send in-band cases to the fallback, log each decision's state, score, model and
  version key, and add reviewed cases and a labeled random sample of confident decisions to `dev`.
- Store the band in `thresholds.json` as [eval data schemas](eval-data-schemas.md) describes.
