# Scope watchdog state (`jev-watchdog.ts`)

State is built by deterministic rules only; Jev never chooses what it is shown. The audit
(`<session>/jev-watchdog-requests.jsonl`, mode 0600) holds `request`, `outcome`, `reviewer_outcome`,
and `failure` (a review that failed before its request was logged) records, joined by `requestId`.
Every record names its session: `sessionKind` (`main` or `sub`) and `agentId` (`Main`, `EditA`, …);
older records lack them and are main sessions. A subagent's audit sits beside its own session file,
`<main>/<SubId>/jev-watchdog-requests.jsonl`, next to its `__advisor.jev-scope.jsonl`; the lab and
`export-cases.ts` scan recursively (the summary's `byGroup` tallies them). Every failure carries a non-secret
`reason` (`policy_slot`, `state_build`, `missing_key`, `no_update`, `no_advisor_tool`, `network`,
`timeout`, `http_error`, `credits`, `invalid_response`, `model_mismatch`, `invalid_choices`,
`aborted`, `no_owner`, `unexpected`); the exporter writes them as failure lines, tallied as
`failuresBeforeCase`. A successful `outcome` also records `usage` (`inputTokens`, `outputTokens`,
`costUsd`) and `latencyMs` (the Jev request), which the loader maps to `costUsd`/`latencyMs`.

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
  advisor, so eval file writes are not visible; long bash commands are truncated by OMP (`…`)
  before the watchdog sees them. Code sets `state.activity_hidden` when the update contains an
  `eval` call or a truncated bash command (fenced result text is ignored); the composed verdict
  then abstains (`unknown`) instead of `no`.
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
