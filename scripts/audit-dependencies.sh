#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

locks="$(mktemp)"
trap 'rm -f "$locks"' EXIT
git ls-files -z -- '*.lock' '*.lockb' '*package-lock.json' '*npm-shrinkwrap.json' \
  '*pnpm-lock.yaml' '*go.sum' \
  ':(exclude,glob)agent/skills/omp-config-optimise/experiments/*/fixtures/**' \
  ':(exclude,glob)tests/fixtures/**' >"$locks"

failed=()
while IFS= read -r -d '' -u 3 lock; do
  directory="$(dirname -- "$lock")"
  status=0
  case "${lock##*/}" in
    bun.lock) (cd -- "$directory" && bun audit) || status=$? ;;
    uv.lock) (cd -- "$directory" && uv audit --locked --preview-features audit-command) || status=$? ;;
    requirements*.lock)
      uv run --locked pip-audit --requirement "$lock" --require-hashes --disable-pip \
        --progress-spinner off || status=$?
      ;;
    *)
      printf 'No dependency auditor for %s.\n' "$lock" >&2
      status=1
      ;;
  esac
  if ((status != 0)); then
    failed+=("$lock")
  fi
done 3<"$locks"

if [[ -n "${failed[*]-}" ]]; then
  printf 'Dependency audit failed for: %s\n' "${failed[*]}" >&2
  exit 1
fi
printf 'Dependency audit passed.\n'
