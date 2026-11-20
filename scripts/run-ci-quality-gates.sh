#!/usr/bin/env bash
set -euo pipefail

tier=ci
fix=false
stage=false

usage() {
  printf 'Usage: %s [--tier commit|ci] [--fix] [--stage]\n' "$0"
}

while [[ -n "${1+set}" ]]; do
  case "$1" in
    --tier)
      tier="${2:-}"
      shift
      ;;
    --fix)
      fix=true
      ;;
    --stage)
      stage=true
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown argument: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if [[ "$tier" != commit && "$tier" != ci ]]; then
  printf 'Unknown tier: %s\n' "$tier" >&2
  usage >&2
  exit 2
fi

if "$stage" && ! "$fix"; then
  printf '%s\n' '--stage requires --fix.' >&2
  exit 2
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

hook=.githooks/pre-commit
hook_command='exec bash scripts/run-ci-quality-gates.sh --tier commit'
if [[ ! -x "$hook" ]] || ! grep -Fxq "$hook_command" "$hook"; then
  printf 'Commit hook check failed: %s must be executable and hold the line: %s\n' "$hook" "$hook_command" >&2
  exit 1
fi
hooks_path="$(git config --get core.hooksPath || true)"
if [[ "$hooks_path" != .githooks ]]; then
  printf 'Commit hook check failed: core.hooksPath is %s; install the hook with: git config core.hooksPath .githooks\n' "${hooks_path:-unset}" >&2
  exit 1
fi
printf 'Commit hook check passed.\n'

if [[ "$tier" == commit ]] && ! "$fix"; then
  unstaged="$(git diff --name-only && git ls-files --others --exclude-standard)"
  if [[ -n "$unstaged" ]]; then
    printf 'The commit tier checks the staged content only; stage or stash these paths first:\n%s\n' "$unstaged" >&2
    exit 1
  fi
fi

scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT

tracked() {
  local out="$1"
  shift
  git ls-files -z -- "$@" >"$out"
}

code_files=()
tracked "$scratch/code" "*.js" "*.jsx" "*.mjs" "*.cjs" "*.ts" "*.tsx" "*.mts" "*.cts" \
  ':(exclude,glob)agent/skills/omp-config-optimise/experiments/*/fixtures/**' \
  ':(exclude,glob)tests/fixtures/**'
while IFS= read -r -d '' file; do
  if [[ ! -L "$file" ]]; then
    code_files+=("$file")
  fi
done <"$scratch/code"

markdown_files=()
tracked "$scratch/markdown" "*.md" "*.markdown"
while IFS= read -r -d '' file; do
  markdown_files+=("$file")
done <"$scratch/markdown"

shell_files=()
tracked "$scratch/all" . ':(exclude,glob)agent/skills/omp-config-optimise/experiments/*/fixtures/**'
while IFS= read -r -d '' file; do
  case "${file##*/}" in
    *.sh | *.bash) shell_files+=("$file") ;;
    *.*) ;;
    *)
      if [[ ! -L "$file" ]] && head -n 1 -- "$file" | grep -Eq '^#!.*[/ ](ba)?sh([[:space:]]|$)'; then
        shell_files+=("$file")
      fi
      ;;
  esac
done <"$scratch/all"

data_files=()
tracked "$scratch/data" "*.yml" "*.yaml" "*.json" "*.jsonc" "*.css" "*.html" \
  ':(exclude,glob)agent/skills/omp-config-optimise/experiments/*/fixtures/**' \
  ':(exclude,glob)tests/fixtures/**'
while IFS= read -r -d '' file; do
  data_files+=("$file")
done <"$scratch/data"

lint_files=()
if [[ -n "${code_files[*]-}" ]]; then
  lint_files+=("${code_files[@]}")
fi
for file in "${markdown_files[@]}"; do
  lint_files+=("$file")
done
if [[ -n "${data_files[*]-}" ]]; then
  lint_files+=("${data_files[@]}")
fi
if [[ -n "${shell_files[*]-}" ]]; then
  lint_files+=("${shell_files[@]}")
fi
# ruff --fix may rewrite any owned Python file, so --stage tracks them all.
tracked "$scratch/python" "*.py"
while IFS= read -r -d '' file; do
  lint_files+=("$file")
done <"$scratch/python"

file_hashes=()
if "$fix" && "$stage"; then
  for file in "${lint_files[@]}"; do
    file_hashes+=("$(git hash-object "$file")")
  done
fi

if [[ -n "${shell_files[*]-}" ]]; then
  uv run --locked shellcheck -- "${shell_files[@]}"
  if "$fix"; then
    uv run --locked shfmt -w -- "${shell_files[@]}"
  else
    uv run --locked shfmt -d -- "${shell_files[@]}"
  fi
fi
if "$fix"; then
  if [[ -n "${data_files[*]-}" ]]; then
    bunx prettier --write --log-level warn -- "${data_files[@]}"
  fi
  if [[ -n "${code_files[*]-}" ]]; then
    bunx oxlint --fix --deny-warnings "${code_files[@]}"
  fi
  if [[ -n "${markdown_files[*]-}" ]]; then
    bunx markdownlint-cli2 --fix "${markdown_files[@]}"
  fi
else
  if [[ -n "${data_files[*]-}" ]]; then
    bunx prettier --check --log-level warn -- "${data_files[@]}"
  fi
  if [[ -n "${code_files[*]-}" ]]; then
    bunx oxlint --deny-warnings "${code_files[@]}"
  fi
  if [[ -n "${markdown_files[*]-}" ]]; then
    bunx markdownlint-cli2 "${markdown_files[@]}"
  fi
fi
bunx tsc --noEmit
# Owned Python: ruff, ruff format and mypy check the whole repository in both tiers, with the
# tooling pyproject.toml pins and uv.lock locks (--locked fails when the lock is stale). Their
# settings mirror the harness catalogue; only third-party code and fixture trees are excluded.
if "$fix"; then
  uv run --locked ruff check .
  uv run --locked ruff format .
else
  uv run --locked ruff check --no-fix .
  uv run --locked ruff format --check .
fi
uv run --locked mypy
bash scripts/audit-dependencies.sh
line_floor=82
branch_floor=83
if [[ "$tier" == commit ]]; then
  bash scripts/run-test-tier.sh t1
  bun_seams=false
  pytest_seams=false
  git diff --cached --name-only -z >"$scratch/staged"
  while IFS= read -r -d '' file; do
    case "$file" in
      orca-plugins/* | tools/* | docs/* | scripts/check_*.py | scripts/device_rollout.py | scripts/*.json | tests/*.py | .devcontainer/* | pyproject.toml | uv.lock) ;;
      *) bun_seams=true ;;
    esac
    case "$file" in
      orca-plugins/* | tools/* | .oxlintrc.json | .markdownlint-cli2.jsonc | package.json | bun.lock | bunfig.toml | tsconfig.json | tests/*.ts) ;;
      *) pytest_seams=true ;;
    esac
  done <"$scratch/staged"
  for framework in bun pytest; do
    case "$framework" in
      bun) selected="$bun_seams" ;;
      pytest) selected="$pytest_seams" ;;
    esac
    if "$selected"; then
      bash scripts/run-test-tier.sh t2 --framework "$framework"
    else
      printf 'T2 %s seams skipped: no staged path reaches them.\n' "$framework"
    fi
  done
else
  coverage="$scratch/coverage"
  mkdir "$coverage"
  bash scripts/run-test-tier.sh t1 --coverage "$coverage"
  bash scripts/run-test-tier.sh t2 --coverage "$coverage"
  COVERAGE_FILE="$coverage/python.coverage" uv run --locked coverage json -q -o "$coverage/python.json"
  uv run --locked python scripts/check_coverage.py --python-json "$coverage/python.json" \
    --lcov "$coverage/bun-t1/lcov.info" --lcov "$coverage/bun-t2/lcov.info" \
    --line-floor "$line_floor" --branch-floor "$branch_floor"
fi
(
  unset "${!GIT_@}"
  python3 scripts/check_paths.py
)

if "$stage"; then
  for index in "${!lint_files[@]}"; do
    file="${lint_files[$index]}"
    hash="$(git hash-object "$file")"
    if [[ "$hash" != "${file_hashes[$index]}" ]]; then
      git add -- "$file"
    fi
  done
fi
