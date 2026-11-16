#!/bin/sh
# Usage: sh install.sh                      install or repair everything for this checkout
#        sh install.sh --print-launchagent  print the LaunchAgent plist this machine would get
#
# Idempotent. Steps: check prerequisites; generate terminal themes and hook the running Orca chat;
# load the two dev theme plugins; point omp at the generated TUI themes; link the orca-chat-style
# skill into omp's skill directory; install the LaunchAgent that keeps the hook after Orca relaunches.
# Theme selection in Orca Settings stays manual: never write Orca's profile database.
set -eu

here=$(cd "$(dirname "$0")" && pwd -P)
label=local.orca-chat-section-style
orca_app=/Applications/Orca.app
orca_cli="$orca_app/Contents/Resources/bin/orca"
inject="$here/chat-section-style/inject.mjs"
skills_dir="$HOME/.omp/agent/skills"
launch_agent="$HOME/Library/LaunchAgents/$label.plist"
log=/tmp/orca-chat-section-style.log

fail() { printf 'install: %s\n' "$1" >&2; exit 1; }
step() { printf '%s\n' "- $1"; }
xml() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }

[ "$(uname -s)" = Darwin ] || fail 'macOS only: inject.mjs drives the macOS Orca app'
node=$(command -v node) || fail 'node not found on PATH; install Node 22.5 or later'
"$node" -e "require('node:sqlite')" 2>/dev/null || fail "$node lacks node:sqlite; install Node 22.5 or later"

render_launch_agent() {
  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$label</string>
  <key>ProgramArguments</key>
  <array>
    <string>$(xml "$node")</string>
    <string>$(xml "$inject")</string>
    <string>--if-needed</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>StartInterval</key>
  <integer>30</integer>
  <key>StandardOutPath</key>
  <string>$log</string>
  <key>StandardErrorPath</key>
  <string>$log</string>
</dict>
</plist>
EOF
}

if [ "${1:-}" = --print-launchagent ]; then render_launch_agent; exit 0; fi
[ $# -eq 0 ] || fail "unknown argument: $1"

[ -x "$orca_app/Contents/MacOS/Orca" ] || fail "Orca not found at $orca_app"
pgrep -qx Orca || fail 'start Orca first: plugin loading and chat injection need the running app'

step "Generating terminal themes and hooking Orca chat ($node)"
"$node" "$inject" --if-needed

plugins=$("$orca_cli" plugin list --json)
for plugin in catppuccin-sections terminal-sections; do
  if printf '%s' "$plugins" | grep -q "\"pluginKey\": *\"local.$plugin\""; then
    step "Plugin local.$plugin already loaded"
  else
    step "Loading plugin local.$plugin"
    "$orca_cli" plugin dev load "$here/$plugin"
  fi
done

if command -v omp >/dev/null 2>&1; then
  for slot in dark light; do
    if [ "$(omp config get "theme.$slot" 2>/dev/null)" = "orca-sections-$slot" ]; then
      step "omp theme.$slot already orca-sections-$slot"
    else
      step "Setting omp theme.$slot to orca-sections-$slot"
      omp config set "theme.$slot" "orca-sections-$slot" >/dev/null
    fi
  done
  link="$skills_dir/orca-chat-style"
  if [ -L "$link" ] && [ "$(readlink "$link")" = "$here/orca-chat-style" ]; then
    step "Skill already linked at $link"
  elif [ -e "$link" ] || [ -L "$link" ]; then
    fail "$link exists and is not a link to $here/orca-chat-style; remove it and rerun"
  else
    step "Linking skill at $link"
    mkdir -p "$skills_dir"
    ln -s "$here/orca-chat-style" "$link"
  fi
else
  step 'omp not on PATH: skipped TUI theme selection and the skill link'
fi

domain="gui/$(id -u)"
rendered=$(render_launch_agent)
if [ -f "$launch_agent" ] && [ "$(cat "$launch_agent")" = "$rendered" ] && launchctl print "$domain/$label" >/dev/null 2>&1; then
  step "LaunchAgent $label already current"
else
  step "Installing LaunchAgent $launch_agent"
  launchctl bootout "$domain/$label" 2>/dev/null || true
  mkdir -p "$(dirname "$launch_agent")"
  printf '%s\n' "$rendered" > "$launch_agent"
  launchctl bootstrap "$domain" "$launch_agent"
fi

cat <<EOF

Done. In Orca Settings → Appearance, choose app themes (Catppuccin Sections Latte/Mocha pairs with
the catppuccin palette) and set Terminal to Sections Dark / Sections Light, line height 1.25.
Confirm the inspector closed: lsof -nP -iTCP:9229 -sTCP:LISTEN must print nothing.
EOF
