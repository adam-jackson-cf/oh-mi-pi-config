#!/usr/bin/env bash
# Runs one test tier with every framework that takes part in it: bun test, then pytest.
#
# - t1: tests that start no process, open no network, write no real home and drive no real Git,
#   worktree or container. In-process guards prove it: Bun loads the engine's Bun preload and
#   pytest the engine's pytest plugin, both with the fast-tier and user-data guards armed, so a
#   T1 test that starts git fails.
# - t2: the seams: tests that start real processes or drive real Git repositories.
#
# Membership: Bun files named tests/*-seams.test.ts are T2 and every other tests/*.test.ts is T1;
# pytest modules take the fast (T1) or seam (T2) marker, and a test without exactly one fails.
# --coverage DIR writes Bun lcov reports to DIR/bun-<tier>/ and appends coverage.py data to
# DIR/python.coverage.
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
while (($#)); do
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
# A pre-commit hook exports GIT_DIR and GIT_INDEX_FILE; tests that create fixture repositories
# would otherwise write into this repository's index.
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
  python3 - "$preload" "$tier" "$scratch/bun-$tier.journal.jsonl" >"$entry" <<'EOF'
import json
import pathlib
import sys

settings = {"mode": "run", "tier": sys.argv[2], "journal": sys.argv[3]}
print(f"import {{ start }} from {json.dumps(pathlib.Path(sys.argv[1]).as_uri())};")
print(f"start({json.dumps(settings)});")
EOF
  args+=(--preload "$entry")
  # The tier's Bun test files, tracked or new; Bun treats ./ arguments as paths.
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
  fi
  bun test "${args[@]}"
}

run_pytest() {
  local args=(-p no:cacheprovider)
  # Every test takes exactly one tier marker: a test outside both, or in both, would run in no
  # tier or in two. pytest exits 5 when the expression collects nothing.
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
    args+=(-p guardrails.commands.adapters.pytest.plugin '--omp-guard=user-data' -m seam)
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
