#!/bin/sh
# Usage: sh harness/launchagent.sh
# Checks the tracked LaunchAgent plist without loading it or touching the running Orca.
set -eu

plist="$(dirname "$0")/../launchd/com.adamjackson.orca-chat-section-style.plist"
filename="${plist##*/}"
inject_suffix='/orca-plugins/chat-section-style/inject.mjs'
failed=0

if [ -f "$plist" ] && plutil -lint "$plist" >/dev/null 2>&1; then
  printf '%s\n' 'ok: plist exists and passes plutil -lint'
else
  printf '%s\n' 'FAIL: plist exists and passes plutil -lint'
  failed=1
fi

if label=$(plutil -extract Label raw "$plist" 2>/dev/null) &&
  [ "$label" = "${filename%.plist}" ]; then
  printf '%s\n' 'ok: Label matches the plist filename'
else
  printf '%s\n' 'FAIL: Label matches the plist filename'
  failed=1
fi

if node=$(plutil -extract ProgramArguments.0 raw "$plist" 2>/dev/null) &&
  program=$(plutil -extract ProgramArguments.1 raw "$plist" 2>/dev/null) &&
  argument=$(plutil -extract ProgramArguments.2 raw "$plist" 2>/dev/null) &&
  [ "${node##*/}" = 'node' ] &&
  [ "${program%"$inject_suffix"}" != "$program" ] && [ "$argument" = '--if-needed' ] &&
  ! plutil -extract ProgramArguments.3 raw "$plist" >/dev/null 2>&1; then
  printf '%s\n' 'ok: ProgramArguments invoke inject.mjs with --if-needed'
else
  printf '%s\n' 'FAIL: ProgramArguments invoke inject.mjs with --if-needed'
  failed=1
fi

if run_at_load=$(plutil -extract RunAtLoad raw "$plist" 2>/dev/null) &&
  interval=$(plutil -extract StartInterval raw "$plist" 2>/dev/null) &&
  [ "$run_at_load" = 'true' ] && [ "$interval" = '30' ]; then
  printf '%s\n' 'ok: RunAtLoad is true and StartInterval is 30'
else
  printf '%s\n' 'FAIL: RunAtLoad is true and StartInterval is 30'
  failed=1
fi

if stdout=$(plutil -extract StandardOutPath raw "$plist" 2>/dev/null) &&
  stderr=$(plutil -extract StandardErrorPath raw "$plist" 2>/dev/null) &&
  [ "$stdout" = '/tmp/orca-chat-section-style.log' ] &&
  [ "$stderr" = '/tmp/orca-chat-section-style.log' ]; then
  printf '%s\n' 'ok: stdout and stderr use /tmp/orca-chat-section-style.log'
else
  printf '%s\n' 'FAIL: stdout and stderr use /tmp/orca-chat-section-style.log'
  failed=1
fi

exit "$failed"
