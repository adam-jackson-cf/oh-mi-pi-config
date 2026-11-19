#!/bin/sh
# Usage: sh harness/launchagent.sh
# Checks the LaunchAgent plist that ../../install.sh renders for this machine, without loading it or
# touching the running Orca.
set -eu

here="$(cd "$(dirname "$0")" && pwd -P)"
inject="$(dirname "$here")/inject.ts"
plist="$(mktemp -t orca-launchagent).plist"
trap 'rm -f "$plist"' EXIT
failed=0
check() { # <description> <command...>
  description="$1"
  shift
  if "$@"; then printf 'ok: %s\n' "$description"; else
    printf 'FAIL: %s\n' "$description"
    failed=1
  fi
}
# extract KEY: the plist value at KEY, or nothing when the key is missing; a check then fails.
extract() { plutil -extract "$1" raw "$plist" 2>/dev/null || :; }

sh "$(dirname "$(dirname "$here")")/install.sh" --print-launchagent >"$plist"

check 'plist passes plutil -lint' plutil -lint -s "$plist"
label=$(extract Label)
node_argument=$(extract ProgramArguments.0)
node_path=$(command -v node) || node_path=''
inject_argument=$(extract ProgramArguments.1)
mode_argument=$(extract ProgramArguments.2)
run_at_load=$(extract RunAtLoad)
interval=$(extract StartInterval)
stdout_path=$(extract StandardOutPath)
stderr_path=$(extract StandardErrorPath)
# Nothing may follow --if-needed in ProgramArguments.
if plutil -extract ProgramArguments.3 raw "$plist" >/dev/null 2>&1; then trailing=present; else trailing=none; fi
check 'Label is local.orca-chat-section-style' [ "$label" = local.orca-chat-section-style ]
check 'ProgramArguments.0 is the node on PATH' [ "$node_argument" = "$node_path" ]
check 'ProgramArguments.1 is this checkout'"'"'s inject.ts' [ "$inject_argument" = "$inject" ]
check 'ProgramArguments.2 is --if-needed' [ "$mode_argument" = --if-needed ]
check 'nothing follows --if-needed' [ "$trailing" = none ]
check 'RunAtLoad is true and StartInterval is 30' [ "$run_at_load-$interval" = true-30 ]
check 'stdout and stderr use /tmp/orca-chat-section-style.log' \
  [ "$stdout_path|$stderr_path" = '/tmp/orca-chat-section-style.log|/tmp/orca-chat-section-style.log' ]

exit "$failed"
