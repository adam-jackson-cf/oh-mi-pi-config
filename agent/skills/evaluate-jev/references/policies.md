# Jev policies

Evidence ladder: a deterministic rule decides when it can; Jev classifies what rules cannot; an LLM
reviews only what Jev cannot. Every policy ships in `shadow` (log only, no latency, no behaviour
change). Modes live in `~/.omp/agent/jev-policies.json` (`off` | `shadow` | `enforce`), read at
session start.

Decisions are `DecisionRecord` lines in `~/.omp/agent/jev-audit/<policy>/<date>.jsonl` (0600,
redacted). Labels are appended to `<policy>/labels.jsonl` from the Jev lab workbench.

## guard.bash, guard.write, guard.result (`jev-guard.ts`)

- **`guard.bash`.** Deterministic: denylist first (destructive commands, `find -delete`,
  `secret-read` of credential files or secret-named env vars, bare `env-dump`), then a read-only
  allowlist that allows without Jev. Jev: `effect` choice plus `destructive_intent` and
  `secret_exposure` noul. Block at P(irreversible) ≥ 0.6, destructive ≥ 0.7 or exposure ≥ 0.7;
  confirm at P(irreversible) 0.35–0.6 or destructive 0.4–0.7.
- **`guard.write`.** Deterministic: `secret-file`, `secret-literal` (known key prefixes),
  `outside-workspace` flag. Jev: `contains_secret` noul, asked only after a credential-assignment or
  high-entropy pre-filter; block ≥ 0.7.
- **`guard.result`.** Deterministic: trusted workspace output is skipped; only URL reads,
  `web_search` and MCP results are screened. Jev: `prompt_injection` noul ≥ 0.7 prepends a warning
  to the result.

Enforce: bash and write await Jev (8 s timeout) and fail closed on error; confirm asks the user in
the main agent and blocks in subagents. The result screen fails open (result unchanged, error
recorded). Labels: `correct`, `false_positive`, `false_negative`, `uncertain`.

## subagent.review-triage, subagent.effort (`jev-subagent-policy.ts`)

- **Review triage** on `reviewer` spawns. Rules: no working-tree diff → standard; a sensitive path
  (auth, security, crypto, permissions, secrets, `.env`, payment, billing, migrations, infra,
  Terraform, CI workflows, Dockerfiles, lockfiles) → deep; docs-only → light. Otherwise Jev scores
  `security_risk`, `complexity` and `behaviour_change` (weights 0.45/0.3/0.25) plus noul
  `missing_tests`. Deep ≥ 0.6. Light < 0.2 only when every confidence ≥ 0.6 and `missing_tests` <
  0.5. Enforce routes light to `openai-codex/gpt-6-luna:medium` and deep to
  `openai-codex/gpt-6-sol:high`. A family guard refuses a reviewer from the author's family. Labels:
  `right_depth`, `should_be_lighter`, `should_be_deeper`, `uncertain`.
- **Effort** on `task` spawns. Rule: an explicit short plan keeps the configured effort. Jev scores
  `openness` and noul `has_plan`; raise to `:medium` when openness ≥ 1.5, or ≥ 1.0 with `has_plan` <
  0.3. Never lowers effort. Labels: `right_effort`, `needed_more`, `needed_less`, `uncertain`.

The assignment text comes from the preceding `task` tool call (`before_subagent_spawn` carries
none), correlated by task name.

## ask (`jev-ask.ts`)

The `jev_ask` tool answers choice, noul or score questions per file or over captured output without
reading content into context. It returns a table, never file text. The deterministic pre-filter
drops VCS, dependency, build, lock, binary, empty and oversized files. Always on; every call is
audited. Labels: `correct`, `incorrect`, `uncertain`.

## Scope watchdog state (`jev-watchdog.ts`)

State is built by deterministic rules only; Jev never chooses what it is shown. The audit
(`<session>/jev-watchdog-requests.jsonl`, mode 0600) holds `request`, `outcome`, `reviewer_outcome`,
and `failure` (a review that failed before its request was logged) records, joined by `requestId`.
Every failure carries a non-secret `reason` (`policy_slot`, `state_build`, `missing_key`,
`no_update`, `no_advisor_tool`, `network`, `timeout`, `http_error`, `credits`, `invalid_response`,
`model_mismatch`, `invalid_choices`); `summarize.ts` tallies them as `failuresBeforeRequest`.

- **Requests.** `**user**` chunks only. User-run `!`/`$` commands (`→ user-bash!`), `[custom]`
  notices (async results, IRC, hooks) and the in-progress marker are not requests. The request
  governing the earliest judged activity is kept, plus later ones (max 4).
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
- **Binding.** The provider is process-global, so the main session's manager and alert channel live
  in a `globalThis` binding set only from a `session_start` with `agent.kind === "main"`; subagent
  instances cannot rebind or steal it.

## Promotion to enforce

Each policy needs its own frozen experiment. A threshold is never tuned on the evaluation set.

1. Label at least 30 shadow decisions in the workbench, with at least 5 of the minority label.
   Choose the threshold on that set with the Metrics sweep and freeze it.
2. Run the matching regression before switching the mode:
   - Review triage: the `review-detection` harness with a Luna light-review arm on the cases triage
     would call light. Enforce only if light arms keep detection parity.
   - Effort: `fixed-plan-effort`. Sonnet is at ceiling on the current hard set, so add harder,
     open-ended fixtures first; otherwise the experiment cannot show a difference.
   - Guards: frozen destructive, secret and injection corpora (Jev lab case sets) with benign
     canaries. Require zero missed blocks on the destructive set, and a false-block rate on canaries
     low enough that confirm prompts stay rare.
3. Flip one policy at a time in `jev-policies.json`, restart OMP, and keep auditing.
