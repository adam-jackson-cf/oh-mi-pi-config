#!/usr/bin/env bash
# Installs (or removes) the weekly launchd job that runs integrity-maintain.sh.
# Usage: install-integrity-schedule.sh [--uninstall]
# Schedule: Monday 09:00 local time. Logs: ~/.omp/agent/jev-audit/guard.integrity/launchd.log
set -euo pipefail

LABEL="com.omp.jev-integrity-maintainer"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAINTAIN="$SCRIPT_DIR/integrity-maintain.sh"
LOG_DIR="$HOME/.omp/agent/jev-audit/guard.integrity"
LOG="$LOG_DIR/launchd.log"
DOMAIN="gui/$(id -u)"

case "${1:-}" in
  --uninstall)
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "uninstalled $LABEL"
    exit 0
    ;;
  "") ;;
  *) echo "usage: $0 [--uninstall]" >&2; exit 2 ;;
esac

for tool in omp bun gh git; do
  path="$(command -v "$tool" || true)"
  [[ -n "$path" ]] || { echo "cannot resolve '$tool' on PATH; install it first" >&2; exit 1; }
  echo "$tool -> $path"
done
[[ -x "$MAINTAIN" ]] || { echo "not executable: $MAINTAIN" >&2; exit 1; }

# The job inherits only this PATH, so the tools above must live in one of these directories.
JOB_PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.bun/bin:$HOME/.local/bin:/usr/bin:/bin"
for tool in omp bun gh git; do
  PATH="$JOB_PATH" command -v "$tool" >/dev/null \
    || { echo "'$tool' is not reachable via the job PATH ($JOB_PATH)" >&2; exit 1; }
done

mkdir -p "$LOG_DIR" "$(dirname "$PLIST")"
cat >"$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$MAINTAIN</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>$JOB_PATH</string>
    <key>HOME</key>
    <string>$HOME</string>
  </dict>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Weekday</key>
    <integer>1</integer>
    <key>Hour</key>
    <integer>9</integer>
    <key>Minute</key>
    <integer>0</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>$LOG</string>
  <key>StandardErrorPath</key>
  <string>$LOG</string>
</dict>
</plist>
EOF
plutil -lint "$PLIST"

launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$PLIST"
echo "installed $LABEL (Mondays 09:00); log: $LOG"
