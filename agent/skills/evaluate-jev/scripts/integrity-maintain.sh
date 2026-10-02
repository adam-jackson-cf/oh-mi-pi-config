#!/usr/bin/env bash
# Autonomous guard.integrity maintainer: mine misses -> propose rule changes in a worktree ->
# authoritative regression gate -> independent verifier -> PR -> self-merge -> deploy.
# Usage: integrity-maintain.sh [--since <ISO>] [--dry-run]
#   --since    window start (default: last successful run, else 7 days ago)
#   --dry-run  mine, propose, regress and verify; never push, open a PR, merge or deploy
# Env overrides: OMP_HOME, INTEGRITY_PROPOSER_MODEL, INTEGRITY_VERIFIER_MODEL, INTEGRITY_AGENT_TIMEOUT.
# Exits non-zero on any unexpected error and never deploys on failure.
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(dirname "$SCRIPT_DIR")"
OMP_HOME="${OMP_HOME:-$HOME/.omp}"
PROPOSER_MODEL="${INTEGRITY_PROPOSER_MODEL:-anthropic/claude-opus-5-5:medium}"
VERIFIER_MODEL="${INTEGRITY_VERIFIER_MODEL:-openai-codex/gpt-6.1-sol:low}"
AGENT_TIMEOUT="${INTEGRITY_AGENT_TIMEOUT:-2700}"
AUDIT="$OMP_HOME/agent/jev-audit/guard.integrity"
STATE_FILE="$AUDIT/maintainer-state.json"
LOG_FILE="$AUDIT/maintainer.log"
LOCK_DIR="$AUDIT/maintainer.lock"
RULES_PATH="agent/integrity/rules.json"
FIXTURES_PATH="agent/integrity/fixtures.json"
DATA_PATHS=("$RULES_PATH" "$FIXTURES_PATH")

DRY_RUN=0
SINCE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --since) SINCE="${2:?--since needs a value}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) sed -n '2,8p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

for tool in git gh bun omp jq perl; do
  command -v "$tool" >/dev/null || { echo "missing required tool: $tool" >&2; exit 2; }
done

mkdir -p "$AUDIT/runs"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
STARTED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
RUN="$AUDIT/runs/$STAMP"
BRANCH="integrity/rules-$STAMP"
WT_PARENT=""
WT=""
PR_URL=""
SUMMARY=""
OUTCOME=""
FINISHED=0

# --- lock (mkdir is atomic; a dead pid means a stale lock) ---
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  holder="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
  if [[ -n "$holder" ]] && kill -0 "$holder" 2>/dev/null; then
    echo "another integrity maintainer run is active (pid $holder)" >&2
    exit 3
  fi
  rm -rf "$LOCK_DIR"
  mkdir "$LOCK_DIR"
fi
echo "$$" >"$LOCK_DIR/pid"
mkdir -p "$RUN"

log() { printf '[%s] %s\n' "$(date -u +%H:%M:%SZ)" "$*" | tee -a "$RUN/maintain.log"; }

notify() {
  local text="${1//[\"\\]/}"
  osascript -e "display notification \"${text:0:200}\" with title \"Jev integrity maintainer\"" >/dev/null 2>&1 || true
}

# Masks well-known secret shapes (same patterns as lib/jev.ts redact) on stdin.
mask() {
  perl -0pe 's/\b(?:sk-[\w-]{8,}|gh[pousr]_[\w-]{8,}|github_pat_[\w-]{8,}|AKIA[0-9A-Z]{16})\b/[REDACTED]/g; s/-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----/[REDACTED PRIVATE KEY]/gs'
}

run_limited() {
  if command -v gtimeout >/dev/null; then gtimeout "$AGENT_TIMEOUT" "$@"
  elif command -v timeout >/dev/null; then timeout "$AGENT_TIMEOUT" "$@"
  else "$@"; fi
}

cleanup() {
  if [[ -n "$WT" && -d "$WT" ]]; then
    git -C "$OMP_HOME" worktree remove --force "$WT" >/dev/null 2>&1 || true
  fi
  if [[ -n "$WT_PARENT" ]]; then rm -rf "$WT_PARENT"; fi
  git -C "$OMP_HOME" worktree prune >/dev/null 2>&1 || true
  # The branch lives on the remote after a push; the local ref is only a scratch handle.
  git -C "$OMP_HOME" branch -D "$BRANCH" >/dev/null 2>&1 || true
  rm -rf "$LOCK_DIR"
}

write_outcome() {
  local mode=""
  if [[ $DRY_RUN -eq 1 ]]; then mode=" (dry run)"; fi
  {
    echo
    echo "## Outcome"
    echo
    echo "- Run: \`$STAMP\`$mode"
    echo "- Result: $OUTCOME"
    if [[ -n "$PR_URL" ]]; then echo "- PR: $PR_URL"; fi
    if [[ -n "$SUMMARY" ]]; then echo "- Summary: $SUMMARY"; fi
  } >>"$RUN/digest.md"
}

finish() { # finish <outcome text>
  OUTCOME="$1"
  FINISHED=1
  write_outcome
  printf '%s\t%s\t%s\t%s\n' "$STAMP" "$OUTCOME" "${PR_URL:--}" "${SUMMARY:--}" >>"$LOG_FILE"
  if [[ $DRY_RUN -eq 0 ]]; then
    jq -n --arg t "$STARTED" --arg r "$STAMP" '{lastSuccess: $t, lastRun: $r}' >"$STATE_FILE"
  fi
  notify "$OUTCOME${PR_URL:+ $PR_URL}"
  log "$OUTCOME"
}

on_exit() {
  local code=$?
  if [[ $FINISHED -eq 0 ]]; then
    OUTCOME="FAILED (exit $code), see $RUN/maintain.log"
    printf '%s\t%s\t%s\t%s\n' "$STAMP" "$OUTCOME" "${PR_URL:--}" "${SUMMARY:--}" >>"$LOG_FILE" || true
    write_outcome || true
    notify "Integrity maintainer failed (exit $code)"
  fi
  cleanup
  exit "$code"
}
trap on_exit EXIT

die() { echo "error: $*" | tee -a "$RUN/maintain.log" >&2; exit 1; }

# --- window ---
if [[ -z "$SINCE" ]]; then
  if [[ -f "$STATE_FILE" ]] && SINCE="$(jq -er '.lastSuccess' "$STATE_FILE" 2>/dev/null)"; then :
  else SINCE="$(date -u -v-7d +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d '7 days ago' +%Y-%m-%dT%H:%M:%SZ)"; fi
fi
log "run $STAMP since $SINCE dry_run=$DRY_RUN"

# --- 1. mine ---
bun "$SCRIPT_DIR/integrity-mine.ts" --since "$SINCE" --out "$RUN" 2>&1 | tee -a "$RUN/maintain.log"
[[ -f "$RUN/candidates.json" && -f "$RUN/digest.md" ]] || die "miner produced no output"
CANDIDATES="$(jq '(.bash | length) + (.commits | length)' "$RUN/candidates.json")"
NARROWING="$(jq '.narrowing_candidates | length' "$RUN/candidates.json")"
log "candidates=$CANDIDATES narrowing=$NARROWING"
if [[ "$CANDIDATES" -eq 0 && "$NARROWING" -eq 0 ]]; then
  finish "no candidates; rules unchanged"
  exit 0
fi

# --- 2. worktree from origin/main ---
git -C "$OMP_HOME" fetch origin main 2>&1 | tee -a "$RUN/maintain.log"
BASE_SHA="$(git -C "$OMP_HOME" rev-parse origin/main)"
for path in "${DATA_PATHS[@]}" agent/skills/evaluate-jev/scripts/integrity-regress.ts; do
  git -C "$OMP_HOME" cat-file -e "origin/main:$path" 2>/dev/null \
    || die "origin/main lacks $path; merge the integrity feature to main before the first run"
done
WT_PARENT="$(mktemp -d "${TMPDIR:-/tmp}/integrity-wt.XXXXXX")"
WT="$WT_PARENT/wt"
git -C "$OMP_HOME" worktree add "$WT" -b "$BRANCH" origin/main 2>&1 | tee -a "$RUN/maintain.log"

# --- 3. proposer ---
{
  cat "$SKILL_DIR/references/integrity-maintainer.md"
  cat <<EOF

## This run

- Stamp: \`$STAMP\` (fixture \`source\` is \`maintainer-$STAMP\`)
- Window: $SINCE to now
- Worktree (your cwd; edit only the two data files here): $WT
- Candidates: $RUN/candidates.json
- Digest: $RUN/digest.md
- Regression gate: \`bun $SCRIPT_DIR/integrity-regress.ts --scope-check --base origin/main\`
- Write the PR body to: $RUN/pr-body.md
- Write the proposal summary to: $RUN/proposal.json as {"changed": <bool>, "summary": "<one line>"}
EOF
} >"$RUN/proposer-prompt.md"
log "proposer: $PROPOSER_MODEL"
(cd "$WT" && JEV_INTEGRITY_MAINTAINER=1 run_limited omp -p --model "$PROPOSER_MODEL" --cwd "$WT" "$(cat "$RUN/proposer-prompt.md")" </dev/null) \
  >"$RUN/proposer.log" 2>&1 || die "proposer failed (see $RUN/proposer.log)"
jq -e '(.changed | type == "boolean") and (.summary | type == "string")' "$RUN/proposal.json" >/dev/null 2>&1 \
  || die "proposer wrote no valid proposal.json"
SUMMARY="$(jq -r '.summary' "$RUN/proposal.json" | tr '\n' ' ' | cut -c1-72)"
CHANGED="$(jq -r '.changed' "$RUN/proposal.json")"
DIRTY="$(git -C "$WT" status --porcelain)"
if [[ "$CHANGED" == "false" ]]; then
  [[ -z "$DIRTY" ]] || die "proposal says unchanged but the worktree is dirty"
  finish "proposer made no change: $SUMMARY"
  exit 0
fi
[[ -n "$DIRTY" ]] || die "proposal says changed but the worktree has no changes"
[[ -f "$RUN/pr-body.md" ]] || die "proposer wrote no pr-body.md"

# --- 4. authoritative regression gate (trusted script, worktree cwd) ---
git -C "$WT" add -A
REGRESS_OK=1
(cd "$WT" && bun "$SCRIPT_DIR/integrity-regress.ts" --scope-check --base origin/main --json "$RUN/regress.json") \
  >"$RUN/regress.md" 2>&1 || REGRESS_OK=0
log "regress passed=$REGRESS_OK"
git -C "$WT" diff --cached origin/main >"$RUN/proposal.diff"
SNAPSHOT="$(git -C "$WT" diff --cached origin/main | shasum | cut -d' ' -f1)$(git -C "$WT" status --porcelain | shasum | cut -d' ' -f1)"

# --- 5. verifier (independent session: fresh omp -p, no shared context; prefers GPT-6.1 Sol) ---
{
  cat "$SKILL_DIR/references/integrity-verifier.md"
  cat <<EOF

## This run

- Worktree (read-only for you): $WT
- Diff to review: $RUN/proposal.diff
- PR body with evidence: $RUN/pr-body.md
- Raw candidates: $RUN/candidates.json
- Regression gate result: $RUN/regress.md
- Write the verdict to: $RUN/verdict.json
EOF
} >"$RUN/verifier-prompt.md"
log "verifier: $VERIFIER_MODEL"
rm -f "$RUN/verdict.json"
(cd "$WT" && JEV_INTEGRITY_MAINTAINER=1 run_limited omp -p --model "$VERIFIER_MODEL" --cwd "$WT" "$(cat "$RUN/verifier-prompt.md")" </dev/null) \
  >"$RUN/verifier.log" 2>&1 || log "verifier process failed; treating as reject"
AFTER="$(git -C "$WT" diff --cached origin/main | shasum | cut -d' ' -f1)$(git -C "$WT" status --porcelain | shasum | cut -d' ' -f1)"
if [[ "$AFTER" != "$SNAPSHOT" ]]; then
  jq -n '{verdict: "reject", reasons: ["verifier modified the worktree"]}' >"$RUN/verdict.json"
elif ! jq -e '(.verdict == "approve" or .verdict == "reject") and (.reasons | type == "array")' "$RUN/verdict.json" >/dev/null 2>&1; then
  jq -n '{verdict: "reject", reasons: ["verifier wrote no valid verdict.json"]}' >"$RUN/verdict.json"
fi
VERDICT="$(jq -r '.verdict' "$RUN/verdict.json")"
log "verdict=$VERDICT"
MERGEABLE=0
[[ "$REGRESS_OK" -eq 1 && "$VERDICT" == "approve" ]] && MERGEABLE=1

if [[ $DRY_RUN -eq 1 ]]; then
  echo "DRY RUN: would commit 'chore(integrity): $SUMMARY', push $BRANCH, open a PR, and $([[ $MERGEABLE -eq 1 ]] && echo 'squash-merge and deploy' || echo 'leave it as a blocked draft')."
  echo "diff: $RUN/proposal.diff  verdict: $VERDICT  regress passed: $REGRESS_OK"
  finish "dry run: mergeable=$MERGEABLE verdict=$VERDICT regress=$REGRESS_OK"
  exit 0
fi

# --- 6. commit, push, PR ---
git -C "$WT" commit -q -m "chore(integrity): $SUMMARY" -m "Run $STAMP, window since $SINCE." 2>&1 | tee -a "$RUN/maintain.log"
git -C "$WT" push -u origin "$BRANCH" 2>&1 | tee -a "$RUN/maintain.log"
{
  cat "$RUN/pr-body.md"
  printf '\n\n## Verifier verdict\n\n**%s**\n\n' "$VERDICT"
  jq -r '.reasons[] | "- \(.)"' "$RUN/verdict.json"
  printf '\n## Regression gate (authoritative)\n\n'
  cat "$RUN/regress.md"
  # shellcheck disable=SC2016
  printf '\n\n---\nAutonomous run `%s`; evidence in `%s`.\n' "$STAMP" "$RUN"
} | mask >"$RUN/pr-body.final.md"
REPO_SLUG="$(git -C "$OMP_HOME" remote get-url origin | sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##')"
TITLE="chore(integrity): $SUMMARY"
PR_ARGS=(--repo "$REPO_SLUG" --base main --head "$BRANCH" --body-file "$RUN/pr-body.final.md")
if [[ $MERGEABLE -eq 1 ]]; then
  PR_URL="$(gh pr create "${PR_ARGS[@]}" --title "$TITLE")"
else
  PR_URL="$(gh pr create "${PR_ARGS[@]}" --title "[blocked] $TITLE" --draft)"
  log "blocked: regress=$REGRESS_OK verdict=$VERDICT; PR left as draft"
  finish "BLOCKED: PR left as draft (regress=$REGRESS_OK verdict=$VERDICT)"
  exit 0
fi
log "PR $PR_URL"

# --- 7. merge and deploy (never reached on failure) ---
# Run outside the checkout with --repo so gh does not try to delete the worktree's branch.
(cd / && gh pr merge "$PR_URL" --repo "$REPO_SLUG" --squash --delete-branch) 2>&1 | tee -a "$RUN/maintain.log"
git -C "$OMP_HOME" fetch origin main 2>&1 | tee -a "$RUN/maintain.log"
mkdir -p "$RUN/deploy-backup"
for path in "${DATA_PATHS[@]}"; do
  live="$OMP_HOME/$path"
  if git -C "$OMP_HOME" cat-file -e "$BASE_SHA:$path" 2>/dev/null && [[ -f "$live" ]]; then
    git -C "$OMP_HOME" show "$BASE_SHA:$path" | cmp -s - "$live" \
      || die "$path has local edits relative to the run base; merged PR $PR_URL not deployed"
  fi
done
for path in "${DATA_PATHS[@]}"; do
  if [[ -f "$OMP_HOME/$path" ]]; then cp "$OMP_HOME/$path" "$RUN/deploy-backup/$(basename "$path")"; fi
  git -C "$OMP_HOME" show "origin/main:$path" >"$OMP_HOME/$path.tmp-deploy"
  mv "$OMP_HOME/$path.tmp-deploy" "$OMP_HOME/$path"
done
if ! (cd "$OMP_HOME" && bun "$SCRIPT_DIR/integrity-regress.ts" --base origin/main >"$RUN/deploy-regress.md" 2>&1); then
  for path in "${DATA_PATHS[@]}"; do
    if [[ -f "$RUN/deploy-backup/$(basename "$path")" ]]; then cp "$RUN/deploy-backup/$(basename "$path")" "$OMP_HOME/$path"; fi
  done
  die "post-deploy regression failed; live files restored (see $RUN/deploy-regress.md)"
fi
finish "merged and deployed: $SUMMARY"
