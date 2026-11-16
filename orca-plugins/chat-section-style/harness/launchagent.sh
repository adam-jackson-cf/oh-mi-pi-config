#!/bin/sh
# Usage: sh harness/launchagent.sh
# Checks the LaunchAgent plist that ../../install.sh renders for this machine, without loading it or
# touching the running Orca.
set -eu

here="$(cd "$(dirname "$0")" && pwd -P)"
inject="$(dirname "$here")/inject.mjs"
plist="$(mktemp -t orca-launchagent).plist"
trap 'rm -f "$plist"' EXIT
failed=0
check() { # <description> <command...>
  description="$1"; shift
  if "$@"; then printf 'ok: %s\n' "$description"; else printf 'FAIL: %s\n' "$description"; failed=1; fi
}
extract() { plutil -extract "$1" raw "$plist" 2>/dev/null; }

sh "$(dirname "$(dirname "$here")")/install.sh" --print-launchagent > "$plist"

check 'plist passes plutil -lint' plutil -lint -s "$plist"
check 'Label is local.orca-chat-section-style' [ "$(extract Label)" = local.orca-chat-section-style ]
check 'ProgramArguments.0 is the node on PATH' [ "$(extract ProgramArguments.0)" = "$(command -v node)" ]
check 'ProgramArguments.1 is this checkout'"'"'s inject.mjs' [ "$(extract ProgramArguments.1)" = "$inject" ]
check 'ProgramArguments.2 is --if-needed and nothing follows' \
  sh -c '[ "$1" = --if-needed ] && ! plutil -extract ProgramArguments.3 raw "$2" >/dev/null 2>&1' _ \
  "$(extract ProgramArguments.2)" "$plist"
check 'RunAtLoad is true and StartInterval is 30' [ "$(extract RunAtLoad)-$(extract StartInterval)" = true-30 ]
check 'stdout and stderr use /tmp/orca-chat-section-style.log' \
  [ "$(extract StandardOutPath)|$(extract StandardErrorPath)" = '/tmp/orca-chat-section-style.log|/tmp/orca-chat-section-style.log' ]

exit "$failed"
