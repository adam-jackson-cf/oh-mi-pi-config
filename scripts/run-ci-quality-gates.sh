#!/usr/bin/env bash
set -euo pipefail

tier=ci
fix=false
stage=false

usage() {
  printf 'Usage: %s [--tier commit|ci] [--fix] [--stage]\n' "$0"
}

while (($#)); do
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
    -h|--help)
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

# The commit tier runs through .githooks/pre-commit, which must use this runner; the CI tier
# command lives in the owner's personal record, outside this repository. The GitHub Actions
# workflow is an optional manual fallback and is not part of the parity check.
runner_base='scripts/run-ci-quality-gates'
config=.githooks/pre-commit
if [[ ! -f "$config" ]] || ! grep -Fq "$runner_base" "$config"; then
  printf 'Runner parity check failed: %s must reference %s.\n' "$config" "$runner_base" >&2
  exit 1
fi
printf 'Runner parity check passed.\n'

code_files=(oxlint.config.ts)
while IFS= read -r -d '' file; do
  if [[ "$file" != "oxlint.config.ts" && ! -L "$file" ]]; then
    code_files+=("$file")
  fi
done < <(git ls-files -z -- "*.js" "*.jsx" "*.mjs" "*.cjs" "*.ts" "*.tsx" "*.mts" "*.cts")

markdown_files=()
while IFS= read -r -d '' file; do
  markdown_files+=("$file")
done < <(git ls-files -z -- "*.md" "*.markdown")

lint_files=("${code_files[@]}")
for file in "${markdown_files[@]}"; do
  lint_files+=("$file")
done
# ruff --fix may rewrite any owned Python file, so --stage tracks them all.
while IFS= read -r -d '' file; do
  lint_files+=("$file")
done < <(git ls-files -z -- "*.py")

file_hashes=()
if "$fix" && "$stage"; then
  for file in "${lint_files[@]}"; do
    file_hashes+=("$(git hash-object "$file")")
  done
fi

if "$fix"; then
  bunx oxlint --fix "${code_files[@]}"
  if ((${#markdown_files[@]})); then
    bunx markdownlint-cli2 --fix "${markdown_files[@]}"
  fi
else
  bunx oxlint "${code_files[@]}"
  if ((${#markdown_files[@]})); then
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
# Tests run through scripts/run-test-tier.sh, one tier at a time, with every framework:
# - commit: all T1, then T2 for each framework a staged path reaches;
# - ci: all T1 and all T2, with coverage of owned code checked against its floors.
# Line floor: Python statements and TypeScript lines together; branch floor: Python branches.
line_floor=85
branch_floor=80
if [[ "$tier" == commit ]]; then
  bash scripts/run-test-tier.sh t1
  # A staged path reaches a framework's T2 unless it is one that framework's seams never read;
  # an unknown path always selects T2.
  bun_seams=false
  pytest_seams=false
  while IFS= read -r -d '' file; do
    case "$file" in
      orca-plugins/*|tools/*|docs/*|scripts/check_*.py|scripts/device_rollout.py|scripts/*.json|tests/*.py|.devcontainer/*|pyproject.toml|uv.lock) ;;
      *) bun_seams=true ;;
    esac
    case "$file" in
      orca-plugins/*|tools/*|oxlint.config.ts|.markdownlint-cli2.jsonc|package.json|bun.lock|bunfig.toml|tsconfig.json|tests/*.ts) ;;
      *) pytest_seams=true ;;
    esac
  done < <(git diff --cached --name-only -z)
  for framework in bun pytest; do
    selected="${framework}_seams"
    if "${!selected}"; then
      bash scripts/run-test-tier.sh t2 --framework "$framework"
    else
      printf 'T2 %s seams skipped: no staged path reaches them.\n' "$framework"
    fi
  done
else
  coverage="$(mktemp -d)"
  trap 'rm -rf "$coverage"' EXIT
  bash scripts/run-test-tier.sh t1 --coverage "$coverage"
  bash scripts/run-test-tier.sh t2 --coverage "$coverage"
  COVERAGE_FILE="$coverage/python.coverage" uv run --locked coverage json -q -o "$coverage/python.json"
  uv run --locked python scripts/check_coverage.py --python-json "$coverage/python.json" \
    --lcov "$coverage/bun-t1/lcov.info" --lcov "$coverage/bun-t2/lcov.info" \
    --line-floor "$line_floor" --branch-floor "$branch_floor"
fi
(unset "${!GIT_@}"; python3 scripts/check_paths.py)

if "$stage"; then
  for index in "${!lint_files[@]}"; do
    file="${lint_files[$index]}"
    if [[ "$(git hash-object "$file")" != "${file_hashes[$index]}" ]]; then
      git add -- "$file"
    fi
  done
fi
