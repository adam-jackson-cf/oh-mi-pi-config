#!/bin/sh
set -eu
bun "$(dirname "$0")/test.ts" "$1"
