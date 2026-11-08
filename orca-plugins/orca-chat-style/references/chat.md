# Chat styling procedures

Paths below are relative to `~/.omp/orca-plugins/chat-section-style/` unless stated otherwise.
Use `--surface chat` for a chat-only choice switch; it pins both effective terminal choices.

## Injection and recovery

## How it is installed

1. `inject.mjs` finds Orca's main process (`/Applications/Orca.app/Contents/MacOS/Orca` with parent
   pid 1). It refuses to run if 9229 is already listening, then sends `SIGUSR1`. Orca's
   `EnableNodeCliInspectArguments` fuse is on, so this opens the inspector on `127.0.0.1:9229`.
2. It installs a main-process hook (`globalThis.__orcaChatSectionStyle`). The hook inserts
   `chat.css` plus every `palettes/*.css` with `webContents.insertCSS`, runs `id-highlight.js`, and
   sets the mode and palette. It re-applies everything after every renderer load and within a second
   of an edit to any of those files, `mode` or `palette`.
3. It closes the inspector about one second later and writes the Orca pid to
   `$TMPDIR/orca-chat-section-style.pid`.
4. The LaunchAgent runs `inject.mjs --if-needed` at login and every 30 seconds. Chat injection skips
   while that pid is still Orca's main process; terminal generation still runs. A relaunched Orca is
   restyled within 30 seconds. Its log is
   `/tmp/orca-chat-section-style.log`.

`id-highlight.js` colours ID text with the CSS Custom Highlight API (`::highlight(omp-id-*)`), so it
never splits React-owned text nodes. It marks sections with `data-omp-id-section` (kind) and
`data-omp-id-part` (`whole`/`start`/`middle`/`end`); an ID'd paragraph's section runs on until a
heading, rule, another ID or a bold lead-in. React neither sets nor removes these attributes.

A stylesheet inserted by an earlier hook version cannot be removed by key once the hook loses track
of it; it disappears when Orca restarts. Rules in `chat.css` that must hold in every mode therefore
state their value explicitly (for example the reply container's transparent background).

## Reapply after an Orca update

1. Check the fuse: `npx -y @electron/fuses read --app /Applications/Orca.app` must show
   `EnableNodeCliInspectArguments is Enabled`. If disabled, injection is impossible; never patch
   `app.asar` (breaks the signature, lost on the next update). Keep only the theme and settings
   layers.
2. Run `node inject.mjs`. With the committed settings and a Catppuccin theme, success prints:

   ```text
   {"pid":…,"styled":[1],"mode":"gutter","palette":"auto",
    "appliedPalette":"catppuccin","lastError":null}
   ```

   `appliedPalette` is the palette `auto` resolved to for the active theme. Then
   `lsof -nP -iTCP:9229 -sTCP:LISTEN` must print nothing.
3. `launchctl print gui/$(id -u)/com.adamjackson.orca-chat-section-style` should show
   `last exit code = 0`. If missing, install the tracked plist. It hard-codes
   `/opt/homebrew/bin/node` and `/Users/adamjackson/.omp/orca-plugins/chat-section-style/inject.mjs`;
   edit those paths first on a Mac with a different user or Node location:

   ```sh
   cp ~/.omp/orca-plugins/chat-section-style/launchd/com.adamjackson.orca-chat-section-style.plist \
     ~/Library/LaunchAgents/
   launchctl bootstrap gui/$(id -u) \
     ~/Library/LaunchAgents/com.adamjackson.orca-chat-section-style.plist
   ```

4. Take a screenshot of a chat. If injection succeeded but nothing changed, Orca renamed its chat
   attributes; see "Re-map selectors".
5. Theme: `orca plugin list --json` must list `local.catppuccin-sections` with `isDev: true`;
   otherwise `orca plugin dev load ../catppuccin-sections` and re-select both themes.
6. Read settings without writing:

   ```sh
   sqlite3 -readonly \
     ~/Library/Application\ Support/orca/profiles/local-default/profile-state.db \
     "select json_extract(payload,'$.pluginAppThemeLight'), "\
   "json_extract(payload,'$.pluginAppThemeDark'), "\
   "json_extract(payload,'$.nativeChatAppearance') "\
   "from profile_state_documents where domain='settings'"
   ```

   Expected: the two `catppuccin-sections` themes and `{"width":"wide","contrast":124}`. Change
   settings through the UI only.

## Re-map selectors

Observed in Orca on 2026-11-07:

- `[data-native-chat-window] > [data-index]` virtualised rows; `[data-native-chat-quote-source]`
  assistant markdown
- `.bg-chat-user-surface` user bubble; `button[data-native-chat-tool-run-state]` tool rows
- `[data-native-chat-turn-status=settled|active]` the "Worked for" line
- `[data-native-chat-code-block]` code blocks, header `div` holding `[data-code-language]`; a `div`
  wrapping `table` for tables
- `.group/visual` (CSS `.group\/visual`) wrapping a chat visual's `iframe`; `.group/composer` the
  message box

To re-map, evaluate a throwaway script over the same inspector connection: pick the `webContents`
whose URL contains `/out/renderer/index.html` and `executeJavaScript` a walk over
`[data-native-chat-window]` children printing tag, `data-*`, `role`/`aria-label` and classes. In
main-process code `require` exists only during the evaluation: capture modules first and wrap every
deferred callback in `try`/`catch`. An uncaught main-process exception shows an Orca error dialog
and logs `main_uncaught_exception` in `~/Library/Application Support/orca/logs/main.trace.ndjson`.

To preview layouts or embeds absent from the screen, append a fixed-position `div` with
`data-native-chat-root` and class `native-chat-appearance` to `document.body`, holding
`[data-native-chat-window]` > `[data-native-chat-quote-source]` sample content. Screenshot it, then
remove it; it sits outside Orca's React tree.

## Theme constraints

- App themes may set only Orca's allowlisted tokens. `--chat-user-message` is the only chat-specific
  one; other chat colours derive from `--background`, `--foreground` and `--muted-foreground`.
- Orca drops any token below 4.5:1 text contrast (for example `--muted-foreground` against
  `--background`/`--muted`, `--chat-user-message` against chat strong text). Latte `#c9d3fa` failed
  at 4.42:1; `#d0d8f9` passes at 4.64:1.
- `--muted-foreground` is app-wide, so sidebar captions and inactive tabs turn teal too.

## Palettes and adding a palette

The `palette` file names the active colour family; `inject.mjs` sets it as
`<html data-omp-chat-palette>`. Palette and mode are independent: any palette works with any mode,
in light and dark.

`auto` (the default) follows the Orca theme chosen in Settings → Appearance. `palette-auto.js`
compares the page's live theme tokens with every installed theme's tokens, applies the best-matching
theme's palette (below a 60% token match it keeps `catppuccin`), and re-checks when Orca swaps
theme. `theme-palettes.mjs` maps theme plugins to palettes and reads their theme files; the hook
re-reads them on every apply, so theme plugin updates need no re-install, but a new theme plugin
needs a line in `PLUGIN_PALETTES`.

- `catppuccin`: Catppuccin Sections Latte / Mocha (`local.catppuccin-sections`), stock Catppuccin
- `ayu`: Ayu Light / Ayu Dark and Ayu Mirage (`stablyai.orca-ayu-theme`)
- `github`: GitHub Light Default / GitHub Dark Default and Dark Dimmed
  (`stablyai.orca-github-vscode-theme`)
- `vscode`: VS Code Light Modern / Dark Modern (`stablyai.orca-vscode-default-themes`)

Switch with `node inject.mjs --palette <name|auto>`; it combines with `--mode`. A full
`node inject.mjs` prints `appliedPalette`, the palette actually in use. Each palette is one file,
`palettes/<name>.css`, scoped to `:root[data-omp-chat-palette='<name>']` (light) and `…'].dark`
(dark). It sets the variables listed in the contract comment at the top of `chat.css`: per ID kind a
section hue, inline ink, inline tint, editorial heading ink and editorial inline ink; and the four
embed hues. Optional variables: `--omp-accent` (replaces the theme `--primary` for headings, links
and the active status), `--omp-code-chip` (hue behind inline-code chips, for themes with neutral
inline code), `--omp-ed-panel-mix` and `--omp-ed-label-mix` (editorial panel fill strength and light
label ink). Where the stock theme falls below 4.5:1, a palette may also retune Orca's chat inks
under `.native-chat-appearance`. The hook injects every palette file and reads the palette list when
installed, so a new palette file needs a full `node inject.mjs`.

To add a palette, create its CSS file with the contract above, add a `PLUGIN_PALETTES` entry in
`theme-palettes.mjs` for any new app-theme plugin, run full `node inject.mjs`, then
`bun harness/contrast.mjs <name>` (exit 0) before scoring. New theme plugin updates are read on
every apply; adding a new palette file requires reinstalling the hook.

## Harness

- `harness/shoot.sh <mode> <out-dir> [palette]` renders a long two-turn mixed-media transcript
  (`harness/harness.html`) with Orca's real renderer CSS plus this folder's `chat.css`,
  `palettes/*.css` and `id-highlight.js`, in headless Chrome: `<mode>-light.png`, `<mode>-dark.png`
  (1500px), `<mode>-narrow.png` (760px, dark), plus `<mode>-dark-mirage.png` for `ayu` and
  `<mode>-dark-dimmed.png` for `github`. The first run per Orca version extracts `app.asar` into
  `$TMPDIR/orca-harness-<version>`; the running Orca is never touched.
- `bun harness/contrast.mjs <palette> [mode …]` measures the rendered WCAG contrast of every text
  run, text label and inline ID in every mode and theme variant, and exits 1 if any text below 4.5:1
  is not deliberately dimmed (older turns and settled tool rows, restored on hover). Run `shoot.sh`
  once first.
- `bun harness/palette-auto.mjs` checks that palette `auto` maps every installed theme to its
  palette, re-detects after a theme swap, leaves a fixed palette alone and falls back to
  `catppuccin` for unknown themes. Exit 1 on any mismatch.
- `bun harness/terminal-themes.mjs` checks every palette/variant/mode/slot: required omp tokens,
  paired app colours, ≥4.5:1 text contrast (except deliberately dimmed `dim` and decorative borders),
  mode overlays, slot resolution, config and generator idempotence. Exit 1 on any failed check.
- `sh harness/launchagent.sh` checks the tracked LaunchAgent plist: valid plist, label matches its
  filename, requires Node (`node` executable) to run this folder's `inject.mjs --if-needed` with
  no other arguments at load and every 30 seconds, and sends stdout and stderr to
  `/tmp/orca-chat-section-style.log`. Exit 1 on any failed check; it never loads the agent or
  touches the running Orca.
- `harness-tokens.js` holds the installed themes' tokens; regenerate it with
  `node harness/tokens.mjs` after a theme update.
- Score screenshots against the rubric in `chat.md`.

## Assessing a chat theme or mode

Score each category 0–10 from screenshots of a long mixed-media reply (summary, K/C/S/F sections,
table, code, diagram, blockquote) in light, dark and narrow (~760px) panes. 10 = nothing to improve;
7 = works with visible friction; 4 = gets in the way. Cite screenshot evidence for each score.

- `1 Scannability`: 10: Every section start (summary, each K, C, S, F) is findable at a glance
  by shape or label, not colour alone.
- `2 Focus`: 10: Current reply dominates; older turns, tool rows and status recede but stay legible.
- `3 Mixed media`: 10: Tables, code and diagrams flow in prose; one frame each, even spacing,
  identifiable type.
- `4 Readability`: 10: Body contrast ≥4.5:1, comfortable line length/height, steady paragraph/list
  rhythm, legible inline code.
- `5 Visual calm`: 10: No nested frames, competing edges/tints or shouting colour; whitespace
  does most separating.
- `6 Semantic colour`: 10: ID/embed hues: consistent, distinguishable, meaningful, never sole cues.
- `7 Light/dark parity`: 10: Both themes score equally; nothing vanishes or glares in either.
- `8 Robustness`: 10: No clipping, overlap or misalignment in narrow panes or nested lists;
  Orca's controls stay usable.
- A mode passes at ≥9.5 in every category, scored by an assessor who did not write it.
- Score first in the headless harness with Orca's real CSS, never live chat:
  `chat-section-style/harness/shoot.sh <mode> <out-dir> [palette]`; then confirm once in live Orca.
