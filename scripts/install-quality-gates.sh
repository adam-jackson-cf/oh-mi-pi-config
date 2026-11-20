#!/usr/bin/env bash
set -euo pipefail

go_version=1.26.10

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
bun_version="$(sed -n 's/^  "packageManager": "bun@\(.*\)",$/\1/p' package.json)"
found_bun="$(bun --version)"
if [[ -z "$bun_version" || "$found_bun" != "$bun_version" ]]; then
  printf 'Bun %s is required (package.json packageManager); found %s.\n' "$bun_version" "$found_bun" >&2
  exit 1
fi
found_go="$(go env GOVERSION)"
if [[ "$found_go" != "go$go_version" ]]; then
  printf 'Go %s is required; found %s.\n' "$go_version" "$found_go" >&2
  exit 1
fi
bun install --frozen-lockfile
uv sync --locked
git config core.hooksPath .githooks
