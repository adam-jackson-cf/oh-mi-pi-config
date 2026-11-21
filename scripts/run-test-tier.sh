#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'Usage: %s t1|t2 [--framework bun|pytest] [--coverage DIR]\n' "$0"
}

tier="${1:-}"
case "$tier" in
  t1 | t2) shift ;;
  -h | --help)
    usage
    exit 0
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
frameworks=(bun pytest)
coverage=""
while [[ -n "${1+set}" ]]; do
  case "$1" in
    --framework)
      case "${2:-}" in
        bun | pytest) frameworks=("$2") ;;
        *)
          usage >&2
          exit 2
          ;;
      esac
      shift
      ;;
    --coverage)
      coverage="${2:-}"
      if [[ -z "$coverage" ]]; then
        usage >&2
        exit 2
      fi
      shift
      ;;
    *)
      printf 'Unknown argument: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
unset "${!GIT_@}"
if [[ "$tier" == t2 ]] && command -v go >/dev/null; then
  PATH="$(go env GOROOT)/bin:$PATH"
  export PATH
fi
scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT
preload="$repo_root/agent/extensions/dynaflows/engine/guardrails/commands/adapters/bun_test/preload.ts"

run_bun() {
  local args=() file file_tier
  local entry="$scratch/bun-$tier.preload.mjs"
  uv run --locked python - "$preload" "$tier" "$scratch/bun-$tier.journal.jsonl" >"$entry" <<'EOF'
import json
import pathlib
import sys

settings = {"mode": "run", "tier": sys.argv[2], "journal": sys.argv[3]}
print(f"import {{ start }} from {json.dumps(pathlib.Path(sys.argv[1]).as_uri())};")
print(f"start({json.dumps(settings)});")
EOF
  # Seam tests run real git, uv and omp subprocesses, so a slower gate machine stretches them;
  # their per-test timeout matches tests/test_ci_gate.py PROCESS_TIMEOUT, not bun's 5 s default.
  if [[ "$tier" == t2 ]]; then
    args+=(--timeout 60000)
  fi
  args+=(--preload "$entry")
  git ls-files -z -co --exclude-standard -- 'tests/*.test.ts' >"$scratch/bun-files"
  while IFS= read -r -d '' file; do
    case "$file" in
      *-seams.test.ts) file_tier=t2 ;;
      *) file_tier=t1 ;;
    esac
    if [[ "$file_tier" == "$tier" ]]; then
      args+=("./$file")
    fi
  done <"$scratch/bun-files"
  if [[ -n "$coverage" ]]; then
    args=(--coverage --coverage-reporter=lcov "--coverage-dir=$coverage/bun-$tier" "${args[@]}")
    NODE_V8_COVERAGE="$coverage/node" bun test "${args[@]}"
  else
    bun test "${args[@]}"
  fi
}

run_pytest() {
  local args=(-p no:cacheprovider)
  local status=0
  uv run --locked pytest -p no:cacheprovider -q --collect-only \
    -m '(not fast and not seam) or (fast and seam)' >"$scratch/markers.txt" || status=$?
  if ((status != 5)); then
    cat "$scratch/markers.txt" >&2
    printf 'These pytest tests need exactly one tier marker, fast or seam.\n' >&2
    exit 1
  fi
  if [[ "$tier" == t1 ]]; then
    args+=(-p guardrails.commands.adapters.pytest.plugin '--omp-guard=fast-tier,user-data' -m fast)
  else
    args+=(-p guardrails.commands.adapters.pytest.plugin '--omp-guard=user-data' -m seam -n auto)
  fi
  if [[ -n "$coverage" ]]; then
    COVERAGE_FILE="$coverage/python.coverage" uv run --locked pytest "${args[@]}" \
      --cov --cov-append --cov-report=
  else
    uv run --locked pytest "${args[@]}"
  fi
}

for framework in "${frameworks[@]}"; do
  "run_$framework"
done
