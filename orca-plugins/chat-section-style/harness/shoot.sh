#!/bin/sh
# Usage: harness/shoot.sh <mode> <out-dir> [palette]
# Renders a long two-turn mixed-media transcript with Orca's real renderer CSS plus this plugin's chat.css and
# id-highlight.js, in headless Chrome: <out-dir>/<mode>-light.png and <mode>-dark.png (1500px wide) and
# <mode>-narrow.png (760px, dark). Never touches the running Orca.
#
# First run per Orca version extracts the installed app.asar into $TMPDIR/orca-harness-<version> (read-only copy).
# harness-tokens.js holds installed theme tokens; regenerate with node harness/tokens.mjs.
set -eu
mode="$1"
out="$2"
palette="${3:-catppuccin}"
here="$(cd "$(dirname "$0")" && pwd)"
style_dir="$(dirname "$here")"
version="$(plutil -extract CFBundleShortVersionString raw /Applications/Orca.app/Contents/Info.plist)"
work="${TMPDIR:-/tmp}/orca-harness-$version"
renderer="$work/out/renderer"
if [ ! -d "$renderer/assets" ]; then
  npx -y @electron/asar extract /Applications/Orca.app/Contents/Resources/app.asar "$work"
fi
client_css="$(cd "$renderer/assets" && ls client-*.css | sed -n 1p)"
row_css="$(cd "$renderer/assets" && ls NativeChatUserMessageRow-*.css | sed -n 1p)"
palette_links=""
for css in "$style_dir"/palettes/*.css; do
  palette_links="$palette_links<link rel=\"stylesheet\" href=\"file://$css\">"
done
sed -e "s|__CLIENT_CSS__|$client_css|" -e "s|__ROW_CSS__|$row_css|" -e "s|__STYLE_DIR__|$style_dir|g" -e "s|__PALETTE_LINKS__|$palette_links|" \
  "$here/harness.html" > "$renderer/harness.html"
cp "$here/harness-tokens.js" "$renderer/harness-tokens.js"

mkdir -p "$out"
chrome="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
page="file://$renderer/harness.html"
shoot() { # <width> <height> <theme> <file> [variant]
  "$chrome" --headless=new --disable-gpu --hide-scrollbars --allow-file-access-from-files \
    --force-device-scale-factor=1 --window-size="$1,$2" --virtual-time-budget=3000 \
    --screenshot="$4" "$page?mode=$mode&theme=$3&palette=$palette&variant=${5:-}" >/dev/null 2>&1
}
shoot 1500 3000 light "$out/$mode-light.png"
shoot 1500 3000 dark "$out/$mode-dark.png"
shoot 760 3800 dark "$out/$mode-narrow.png"
case "$palette" in
  ayu) shoot 1500 3000 dark "$out/$mode-dark-mirage.png" ayu-mirage ;;
  github) shoot 1500 3000 dark "$out/$mode-dark-dimmed.png" github-dark-dimmed ;;
esac
echo "$out/$mode-light.png $out/$mode-dark.png $out/$mode-narrow.png"
