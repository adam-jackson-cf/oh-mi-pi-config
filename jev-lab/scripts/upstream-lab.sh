#!/usr/bin/env bash
# Upstream demo lab from the "10 Levels of Jev" video (runtime-only checkout in jev-lab/upstream/).
# Usage: upstream-lab.sh setup | run
set -euo pipefail

REPO="https://github.com/disler/ten-levels-of-jev"
# Pinned to upstream main HEAD as of 2026-09-29.
COMMIT="777adaf47d37ae0553220d35b2f15b3a3a063305"
LAB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPSTREAM="$LAB_DIR/upstream"

case "${1:-}" in
  setup)
    if [ ! -d "$UPSTREAM/.git" ]; then
      git clone "$REPO" "$UPSTREAM"
    fi
    git -C "$UPSTREAM" fetch --quiet origin "$COMMIT" || true
    git -C "$UPSTREAM" checkout --quiet "$COMMIT"
    (cd "$UPSTREAM/apps/ten-levels/web" && bun install && bun run build)
    echo "upstream lab ready at $COMMIT"
    ;;
  run)
    [ -d "$UPSTREAM/apps/ten-levels" ] || { echo "run 'upstream-lab.sh setup' first" >&2; exit 1; }
    OPENROUTER_API_KEY="$(omp token openrouter)"
    export OPENROUTER_API_KEY
    cd "$UPSTREAM/apps/ten-levels"
    echo "upstream lab on http://localhost:4399"
    exec node server/server.mjs
    ;;
  *)
    echo "usage: $0 setup|run" >&2
    exit 2
    ;;
esac
