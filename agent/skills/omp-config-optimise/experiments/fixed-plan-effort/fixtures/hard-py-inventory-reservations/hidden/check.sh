#!/bin/sh
set -eu
here=$(cd "$(dirname "$0")" && pwd)
cd "$1"
PYTHONDONTWRITEBYTECODE=1 python3 "$here/test.py" "$1"
