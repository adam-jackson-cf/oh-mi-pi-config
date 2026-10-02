# Jev policies

Evidence ladder: a deterministic rule decides when it can; Jev classifies what rules cannot; an LLM
reviews only what Jev cannot. Policies ship in `shadow` (log only, no latency, no behaviour change)
and move to `enforce` after the promotion experiment below. Modes live in
`~/.omp/agent/jev-policies.json` (`off` | `shadow` | `enforce`), read at session start. All three
guards are in `enforce` since 2026-10-02.

Decisions are `DecisionRecord` lines in `~/.omp/agent/jev-audit/<policy>/<date>.jsonl` (0600,
redacted). Labels are appended to `<policy>/labels.jsonl` from the Jev lab workbench. Rows carry
`reviewer: "human" | "agent"`; agent labels are first passes written by
`bun jev-lab/scripts/apply-agent-labels.ts`, and the latest human label always overrides them.

## guard.bash, guard.write, guard.result (`jev-guard.ts`)

- **`guard.bash`.** Deterministic: denylist first (destructive commands, `find -delete`,
  `secret-read`, `env-dump`), then a read-only allowlist that allows without Jev. `secret-read`
  denies credential files, `printenv SECRET_NAME`, and a secret-named `$NAME`/`${NAME}` expansion
  only in an `echo`/`printf` segment. The same expansion in a test segment (`[`, `[[`, `test`) or as
  `${#NAME}`, `${NAME:+…}`, `${NAME+…}` is not denied; anywhere else it is not denied either but the
  command goes to Jev, never the allowlist. Heredoc bodies (`<<DELIM`, `<<-DELIM`, quoted or not)
  are data, not shell segments, so their text is never denied; the command stays non-read-only and
  goes to Jev. `env-dump` (`env`, `printenv`, `set` with no arguments) denies only when its stdout
  is not piped; `env | grep …` goes to Jev. Deterministic denials record `state` (`command`, `cwd`,
  `agent_kind`, `reason`) like Jev decisions. Jev: `effect` choice plus `destructive_intent` and
  `secret_exposure` noul. Block at P(irreversible) ≥ 0.6, destructive ≥ 0.7 or exposure ≥ 0.75;
  confirm at P(irreversible) 0.35–0.6 or destructive 0.35–0.7.
- **`guard.write`.** Screens every edit input (write, replace, patch, hashline incl. `MV` and
  apply_patch incl. `Move to`). Deterministic: `secret-file` (also rename/move destinations),
  `secret-literal` (known key prefixes), `unparseable-edit` (input no parser recognises; blocked in
  enforce), `outside-workspace` flag. Jev: `contains_secret` noul, asked only after a pre-filter
  (credential-named assignment, `scheme://user:password@`, `curl -u user:password`, or a
  high-entropy literal); block ≥ 0.3.
- **`guard.result`.** Deterministic: trusted workspace output and text under 40 characters are
  skipped; URL reads, `web_search`, MCP results and remote-fetching `bash` (`curl`, `wget`,
  `gh api`, …) are screened. Error results
  (the tool_result error flag) are not screened, nor is a `bash` result whose network targets are
  all local (`localhost`, `127.0.0.1`, `::1`, `0.0.0.0`, `*.local`, `*.orb.local`,
  `host.docker.internal`); a fetch with no recognisable target stays screened. Jev:
  `prompt_injection` noul ≥ 0.7 prepends a warning to the result.

Enforce: bash and write await Jev (8 s timeout) and fail closed on error; confirm asks the user in
the main agent and blocks in subagents. The result screen fails open (result unchanged, error
recorded). Labels: `correct`, `false_positive`, `false_negative`, `uncertain`.

Dropped on 2026-10-02 (code removed; audit data kept):

- `subagent.effort`: all 15 effort raises in 35 labels were unnecessary.
- `subagent.review-triage`: depth was right in 5 of 37 labelled spawns (14%) against the
  pre-registered 70% bar.

## ask (`jev-ask.ts`)

The `jev_ask` tool answers choice, noul or score questions per file or over captured output without
reading content into context. It returns a table, never file text. The deterministic pre-filter
drops VCS, dependency, build, lock, binary, empty and oversized files. Always on; every call is
audited. Labels: `correct`, `incorrect`, `uncertain`.

## guard.integrity (`jev-guard.ts`, rules in `agent/integrity/`)

Catches agents that weaken quality gates instead of fixing the work. Rules are data in
`agent/integrity/rules.json` and are screened on bash commands (`git commit` hooks bypass, test
deselection, masked gate exit codes) and on file changes (added suppressions, skipped or deleted
tests, loosened assertions or configuration, guard tampering). Regression fixtures live in
`agent/integrity/fixtures.json`.

- **Categories (fixed):** `suppression`, `test_removal`, `assertion_weakening`, `config_loosening`,
  `gate_bypass`, `guard_tamper`, `gate_masking`. A new category needs code, not a rule edit.
- **Rule verdicts.** `certain`: patterns with no legitimate use; settled deterministically
  (block/confirm without asking Jev). `suspect`: ambiguous patterns; Jev is asked the category noul
  and the match escalates at P(violation) >= 0.5 (`INTEGRITY_ESCALATE`). `record`: audit only.
  Bash rules with `unquoted` match the command with quoted strings blanked (commit messages,
  prompts); change rules with `context` apply only to edits or only to commits.
- **Tiers.** `guard.integrity` governs `certain` and `record` matches; `guard.integrity.suspect`
  governs Jev-judged matches and is never stricter than the parent. Current: `enforce` / `shadow`.
  Evidence (`.todo/artifacts/021026-jev-enforce/report.md`, 413 real units, two blind labellers):
  the certain tier escalated 6 of 12 hacks with 1 false escalation in 11 days; the Jev tier caught
  all 12 but escalated 122 of 371 legitimate units, and its scores did not separate hacks from
  legitimate work at any threshold, so it stays in shadow.
- **Escalation** goes through `confirm`: the main agent asks the user (`state.userDecision`
  `approved` or `declined`), subagents are blocked. A declined change is re-blocked without asking;
  an approved guard-file edit is not asked again that session. Every screened `git commit` also
  writes a `commit-checked` record (repo, tree hash, file count), and masked gate pipes write
  `gate-masked-pipe` records as a shadow metric.
- **Shadow-first promotion.** New rules ship as `record` or `suspect` and are watched in the audit
  before any `certain` verdict; promote only on evidence that the pattern never fires on legitimate
  work. Rules the user approves three or more times are narrowing candidates.
- **Maintainer loop** (`scripts/integrity-maintain.sh`, weekly): mines misses from transcripts and
  commits, has a fresh Opus session propose rule and fixture edits in a worktree, gates them with
  `integrity-regress.ts` (append-only fixtures, legitimate fixtures never `certain`, routed hacks
  stay routed, only the two data files change), has an independent verifier session (GPT-6.1 Sol by
  default) review the diff, and self-merges the PR when both pass, otherwise leaves a `[blocked]`
  draft. The PR history and `runs/<stamp>/` directories are the audit trail. Labels: `correct`,
  `false_positive`, `false_negative`, `uncertain`.

## Scope watchdog state (`jev-watchdog.ts`)

State is built by deterministic rules only; Jev never chooses what it is shown. The audit
(`<session>/jev-watchdog-requests.jsonl`, mode 0600) holds `request`, `outcome`, `reviewer_outcome`,
and `failure` (a review that failed before its request was logged) records, joined by `requestId`.
Every record names its session: `sessionKind` (`main` or `sub`) and `agentId` (`Main`, `EditA`, …);
older records lack them and are main sessions. A subagent's audit sits beside its own session file,
`<main>/<SubId>/jev-watchdog-requests.jsonl`, next to its `__advisor.jev-scope.jsonl`; the lab and
`summarize.ts` scan recursively (`bySessionKind` tallies them). Every failure carries a non-secret
`reason` (`policy_slot`, `state_build`, `missing_key`, `no_update`, `no_advisor_tool`, `network`,
`timeout`, `http_error`, `credits`, `invalid_response`, `model_mismatch`, `invalid_choices`,
`no_owner`); `summarize.ts` tallies them as `failuresBeforeRequest`.

- **Mode (`jev-scope` in `jev-policies.json`, read at session start).** `off`: the advisor replies
  `continue` with no Jev call and no audit. `shadow` (default): the review is audited and
  `continue` is always the reply. `enforce`: a `reviewCandidate` outcome (P(yes) >=
  `SCOPE_ENFORCE_THRESHOLD`, 0.9, and complete state) makes the advisor emit a native `advise` tool
  call at `concern` severity, which OMP delivers to the agent as an `<advisory>` note asking it to
  check the latest steps' scope against the request and plan. It never blocks. The `outcome`
  record's `decision.enforced` says whether a note was sent; the advisor's follow-up call (tool
  result last) replies `continue` without a review.
- **Requests.** `**user**` chunks only. User-run `!`/`$` commands (`→ user-bash!`), `[custom]`
  notices (async results, hooks, IRC other than a subagent's parent, below) and the in-progress
  marker are not requests. The request governing the earliest judged activity is kept, plus later
  ones (max 4). A short request (<= 280 chars) that cites item ids (`C3`, `S1`) or is an approval
  (`yes`, `approved`, `go ahead`, `do it`, `action ...`, `proceed`) gets
  `task_context.referenced_assistant_context`, aligned with `recent_user_requests`: the lines of the
  immediately preceding assistant message that mention those ids (a bare approval gets its last
  ~1200 chars), clipped to 1500 chars; empty otherwise. Deterministic, no model call.
- **Activity.** `edit`/`write` on filesystem paths, `write` to `xd://ast_edit` (stages a diff; the
  later `xd://resolve` is not shown), and `bash` whose visible command edits files (`sed -i`,
  redirection, `tee`, `mv`/`cp`/`rm`/`mkdir`/`touch`, `git apply|checkout|restore`). Other
  `scheme://` targets, reads, and the plan file are excluded. `eval` code is not rendered to the
  advisor, so eval file writes are not visible; long bash commands are truncated by OMP before the
  watchdog sees them.
- **Plan source, latest in branch order wins:** `plan_mode` (`mode_change` with
  `data.planFilePath`), `plan_approval` (the `Plan approved.` developer message with
  `<plan path=…>`; "Approve and execute" opens a fresh session, so the request being planned is
  inherited from the previous session in the same workspace), `plan_yolo_handoff`
  (`plan-yolo-handoff` message), `user_named` (a genuine user request saying
  "implement/execute/follow/according to `<file>.md`" or "plan in `<file>`"), and `agent_created`
  (the first `write` in the turn after a request to write/draft a plan: the destination the request
  named, else a `*plan*|*spec*|*design*|*proposal*|*roadmap*` markdown file). Inferred paths must
  stay inside the workspace; files the agent only reads (agent definitions such as `plan-judge.md`),
  research notes and reports are never plans. Todo items are separate: the latest "Remaining items"
  output always rides along and carries across updates, whatever the plan source.
- **Subagent plan.** The subagent's own branch first (same sources as above), else the plan
  reference it was spawned with: OMP renders the parent's active plan reference into the
  subagent's system prompt (`§ Plan`, `<plan path=…>`), never into a message, so it is read from
  there (`parent_reference`). It is a `local://` path under the artifacts root the subagent shares
  with its parent. OMP hands a reference down only when the parent's `getPlanReferencePath()` file
  exists; print-mode `--plan-yolo` does not set it (its plan lives at a slug name), so those
  subagents review with no plan.
- **Subagent request.** The task assignment is the subagent's first user message, with the
  `Complete assignment thoroughly:` wrapper removed. A rendered `[irc] <from> → me: <body>` line
  from the subagent's parent agent (`Main` for a top-level task, else the agent id before the last
  `.`) is an instruction: it becomes a request prefixed `[parent_follow_up]`. The rendered body is
  truncated to 120 chars, so the full text is recovered from the session branch (the matching
  `irc:incoming` `custom_message`, by sender and rendered prefix); without a match the rendered text
  is used. IRC from any other agent stays excluded. The task `context` lives only in the system
  prompt (`§ Context`), so it is added to `constraints.recent_instructions`. A subagent never feeds
  the per-workspace request inheritance used by "Approve and execute".
- **Attribution.** The provider registry is process-global, so the last registered instance serves
  every advisor, and an advisor call carries only its own random provider session id. The owner is
  found by asking: `options.onPayload` is the owning session's `emitBeforeProviderRequest`, so a
  nonce probe reaches only that session's own extension instance (`before_provider_request`),
  which claims it with its own `pi` and context. Manager, cwd, `local://` root, plan, audit path
  and failure alerts all come from that claim, per call; nothing is bound by timing or start
  order. A call nobody claims fails as `no_owner` and audits nowhere. Its error names the cause:
  OMP passed no `onPayload` hook (an OMP upgrade broke attribution: pin OMP or disable `jev-scope`
  until the extension is updated), or the hook ran but no session claimed it (the extension isn't
  loaded in the owning session: check `agent/config.yml` and restart). `session_shutdown` releases
  the carried context of the session's advisors. `/jev-label` labels a request in its own session
  or any subagent below it, appended to the file that holds the request under that request's
  identity.

## Promotion to enforce

Each policy needs its own frozen experiment. A threshold is never tuned on the evaluation set.

1. Label at least 30 shadow decisions in the workbench, with at least 5 of the minority label.
   Choose the threshold on that set with the Metrics sweep and freeze it.
2. Run the matching regression before switching the mode:
   - Guards: frozen destructive, secret and injection corpora (Jev lab case sets) with benign
     canaries. Require zero missed blocks on the destructive set, and a false-block rate on canaries
     low enough that confirm prompts stay rare.
3. Flip one policy at a time in `jev-policies.json`, restart OMP, and keep auditing.

The 2026-10-02 guard promotion (`.todo/artifacts/021026-jev-promotion/report.md`) narrowly missed
its recall bars on an independent held-out corpus: bash 39/40 dangerous commands caught, result
28/30 injections (28/29 in scope), write 10/10 known-format keys and 17/20 other credentials. False
positives were 0 on 90 held-out real decisions and 1 on 414 canaries. The owner chose to enforce
all three regardless; the known misses are `launchctl unload` of system daemons, `requirepass` and
`--auth-password` credentials (pre-filter), and an injection buried in a long page.
