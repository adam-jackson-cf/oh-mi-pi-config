#!/bin/sh
set -eu
python3 "$(dirname "$0")/test.py" "$1"
