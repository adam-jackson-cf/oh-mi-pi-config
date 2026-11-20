#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
bun install --frozen-lockfile
uv sync --locked
git config core.hooksPath .githooks
