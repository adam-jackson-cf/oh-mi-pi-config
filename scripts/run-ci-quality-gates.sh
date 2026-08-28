#!/usr/bin/env bash
set -euo pipefail

fix=false
stage=false

usage() {
  printf 'Usage: %s [--fix] [--stage]\n' "$0"
}

while (($#)); do
  case "$1" in
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

if "$stage" && ! "$fix"; then
  printf '%s\n' '--stage requires --fix.' >&2
  exit 2
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

runner_base='scripts/run-ci-quality-gates'
for config in .pre-commit-config.yaml .github/workflows/ci-quality-gates.yml; do
  if [[ ! -f "$config" ]] || ! grep -Fq "$runner_base" "$config"; then
    printf 'Runner parity check failed: %s must reference %s.\n' "$config" "$runner_base" >&2
    exit 1
  fi
done
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
bun test

if "$stage"; then
  for index in "${!lint_files[@]}"; do
    file="${lint_files[$index]}"
    if [[ "$(git hash-object "$file")" != "${file_hashes[$index]}" ]]; then
      git add -- "$file"
    fi
  done
fi
