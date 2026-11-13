# Example per-repo record

A per-repo record is the harness's personal config for one repository. It lives at
`<oh-mi-pi-config root>/repos/<owner>/<name>/`, lowercase, where `<owner>/<name>` comes from the
clone's `origin` remote (`https://github.com/<owner>/<name>(.git)`,
`git@github.com:<owner>/<name>(.git)` or `ssh://git@github.com/<owner>/<name>(.git)`). Worktrees
resolve to the same record as their main checkout. `agent/lib/repo_record.py` and
`agent/lib/repo-record.ts` implement that resolution.

`/repos/` is gitignored in oh-mi-pi-config. Records are never committed, because they mean
nothing to anyone else who takes the config. The device rollout copies them to every device.
The target repository holds nothing about the harness.

The record holds two files:

- `gate.json`: the repo's own commands (`checks.commit`, `checks.ci`, `pathCheck`), test globs,
  the required CI context and runner role, and `dynaflows.enabled`. `pathCheck` and
  `checks.commit` are required; a field outside the schema fails the load, naming the field.
- `profile.md`: the repo test profile, JSON front matter between `---` lines, then prose. Start
  it from `skill://test-guidance/assets/templates/test-profile.md`.

`gate.json` here is a generic example; setup drafts the real one from the repo's make targets,
hook config and CI jobs for the owner to confirm. Rules for the record:

- `dynaflows.enabled` is the on/off switch. No origin remote or no record means dynaflows is off.
- Paths in a record setting are repo-relative when they name repo files and record-relative when
  they name record files. No setting names a harness directory inside the repo.
- The record is the owner's: the owner edits it by hand, and rails refuse every agent write to it.

`omp-guardrails init --repo <clone> --record <draft dir>` drafts both files into a draft outside
the live `repos/` root for owner confirmation and writes nothing into the repo; the owner saves
the draft as the live record by hand. At the agent's commit the rails run
`checks.commit`, then `omp-guardrails check --repo <worktree> --record <record dir>` over the
whole repo. The profile's `guardrails.check.run` picks the check steps,
`guardrails.check.repo` names the repo checkers that already enforce a step (`checks.commit` runs
them, so `check` leaves those steps out) and `guardrails.check.exclude` lists the generated,
vendored and fixture paths left out.
