# Chat section style

Custom CSS and a small renderer script for Orca's chat. Orca has no custom-CSS setting, so
`inject.mjs` installs `chat.css` and `id-highlight.js` through Orca's main-process inspector. The
skill `../orca-chat-style/SKILL.md` says when and how agents use this; this file is the maintenance
runbook.

## Layers

- `Theme plugin`: Where it lives: `../catppuccin-sections/` (dev plugin
  `local.catppuccin-sections`); What it does: Colours only: user-message fill, teal secondary text,
  stronger borders; Survives an Orca update?: Yes, unless Orca changes its theme token list
- `Chat settings`: Where it lives: Settings → Chat UI; What it does: Contrast 124 (default 100),
  width Wide; Survives an Orca update?: Yes (stored in Orca's profile database)
- `Injected CSS and script`: Where it lives: this folder plus LaunchAgent
  `com.adamjackson.orca-chat-section-style` (tracked plist:
  `launchd/com.adamjackson.orca-chat-section-style.plist`); What it does: Everything under
  "What it does"; Survives an Orca update?: Only while Orca keeps the inspector fuse on and its
  `data-native-chat-*` attributes
- `Terminal themes`: Where it lives: `../terminal-sections/` (data-only dev plugin
  `local.terminal-sections`) and generated omp `orca-sections-dark/light` themes; What it does:
  Shared mode/palette translated into terminal colours and TUI symbols; Survives an Orca update?:
  Yes, while Orca's terminal-theme and omp's theme contracts remain compatible

`~/.omp/orca-plugins/` is tracked in the oh-mi-pi-config repo (`~/.omp`), so a new Mac gets it
from a clone.

## Layout modes

The `mode` file names the active mode; `inject.mjs` sets it as `<html data-omp-chat-mode>`, and
`chat.css` scopes each mode's rules to it.

- `document`: Flat page: no reply card, no section edges, a hairline divider above each
  bold section lead-in. Embeds keep neutral frames; type shows in the tinted code/table header and
  the coloured language label.
- `split-cards`: Each run of text sits in a card; tables, code and visuals leave the card and sit on
  the canvas between cards with a coloured top edge. ID sections show an inset edge in their hue.
- `gutter` (default): No cards or edges. A coloured dot in the left gutter starts each ID section.
  Embeds are soft borderless surfaces whose title (TABLE label or code language label) sits on a
  band tinted with the embed hue.
- `editorial`: No cards. Prose fills the column like the other modes; hairlines span the column
  above K sections only. Each section heading hangs a glyph in the left gutter (◆ K, ▲ C, ✚ S, ● F,
  ↔ R, ◇ A) beside a letter prefix. Embeds are borderless soft panels whose TABLE/YAML/BASH/DIAGRAM
  label sits on a title band tinted with the embed hue, as in document and split-cards; wide tables
  scroll inside their panel and the label reads "TABLE · SCROLL →" while more columns remain. Older
  turns and settled tool rows use quieter inks, tuned separately for light and dark. Scored ≥9.5 in
  every rubric category by two independent assessors.

Switch with `node inject.mjs --mode <document|split-cards|gutter|editorial>`. A hooked Orca applies
it within a second, with no inspector session. Adding a mode also means adding it to `MODES` in
`inject.mjs` and running a full `node inject.mjs`, because the live hook keeps its own copy of the
mode list.

## Palettes

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

## Assessment harness

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
- `bun harness/terminal-themes.mjs` checks every palette/variant/mode/slot: required omp
  tokens and ≥4.5:1 text contrast (decorative borders exempt; `dim` ≥3.0:1 and quieter than `muted`),
  app-background/foreground matches, mode overlays, slot resolution, config and generator idempotence.
  Exit 1 on any failed check.
- `sh harness/launchagent.sh` checks the tracked LaunchAgent plist: valid plist, label matches its
  filename, requires Node (`node` executable) to run this folder's `inject.mjs --if-needed` with
  no other arguments at load and every 30 seconds, and sends stdout and stderr to
  `/tmp/orca-chat-section-style.log`. Exit 1 on any failed check; it never loads the agent or
  touches the running Orca.
- `harness-tokens.js` holds the installed themes' tokens; regenerate it with
  `node harness/tokens.mjs` after a theme update.
- Score screenshots against the rubric in `../orca-chat-style/references/chat.md`.

## What it does (all modes)

- The chat column is wider than Orca's Wide setting: Wide (60rem) plus half the remaining space on
  each side, via `--chat-content-max-width: calc((60rem + 100%) / 2) !important`. This overrides
  Settings → Chat UI → Width.
- Headings and bold lead-ins (`Gaps & Contradictions`, `K1 — …`) use the chat accent: the palette's
  `--omp-accent`, else the theme's `--primary`; editorial section lead-ins use their section hue.
- Your messages get extra space above and an accent left edge; turns before your latest message fade
  to 80% until hovered.
- Tool activity rows get a thin left rail and a smaller faded style that returns to full on hover;
  editorial keeps current-turn rows at full opacity and older-turn rows at 78%.
- The turn status line (`Worked for …`) becomes an uppercase divider; while the agent works its text
  and rule are accent-tinted (a palette's `--omp-status-rule` can override the rule colour).
- Completion-message IDs at the start of a list item or paragraph are coloured by kind: K key point
  blue, C gap/contradiction red, S suggestion green, R related topic teal, A architectural decision
  violet, F focused review orange. The exact inks come from the active palette.
- Embed hue by type: code blue/teal, shell commands green, diagram languages (mermaid, dot,
  plantuml, d2, svg) pink/violet, tables amber, again per palette. How strongly each mode shows it
  is listed above.
- Tables get a row hover tint, roomier cells and aligned numbers; zebra rows are shared, but editorial
  removes their fill and uses row separators instead.
- Reading rhythm: the shared reply line-height baseline is 1.68; editorial uses 1.76, with 2.02 on
  paragraphs and list items containing a direct-child inline `code` element. Paragraph and list
  spacing is steadier; bullets are accent-tinted (neutral secondary ink in editorial), list numbers
  bold and aligned. Inline-code chips are borderless and faintly tinted toward the code hue (or the
  palette's `--omp-code-chip`); bold is heavier, links accented, blockquotes quiet and rules faded.
- The message box shows an accent ring while you type.

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
4. The LaunchAgent runs `inject.mjs --if-needed` at login and every 30 seconds. It skips while that
   pid is still Orca's main process, so a relaunched Orca is restyled within 30 seconds. Its log is
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

## Terminal

The same mode/palette choice now drives two generated terminal layers: the data-only
`../terminal-sections/` plugin supplies Sections Dark/Light colours, and omp's generated
`orca-sections-dark/light` themes supply TUI colours and mode symbols. Optional `terminal-mode`
and `terminal-palette` files override chat choices; `inject.mjs --surface chat|terminal|both`
controls which choices a switch writes (default `both`).

Every palette separates prose headings (chat accent), live-work HUD titles (decision mauve),
tool/IRC headers (code hue), secondary text (neutral subtle), reasoning (decision-tinted subtle),
success/error/warning (green/red/orange) and custom/skill labels (related teal). Document leaves
tools/custom messages unfilled; split-cards uses state-tinted tools and teal custom fill; gutter
uses unfilled tools and mantle custom messages; editorial fades secondary/reasoning ink another 15%.
Text keeps ≥4.5:1 contrast, with ≥0.05 pairwise OKLab separation for the nine area inks.
Per-ID-kind colours need an omp host change: its text-renderer hook supplies markdown, whose
escape codes are stripped before lexing.

Generation runs before chat pid/marker early exits; the existing 30-second LaunchAgent therefore
also keeps terminal `auto` synchronized. Generated JSON files are ignored, not committed.
See [terminal setup, switching, checks and limits](../orca-chat-style/references/terminal.md).

## Orca settings through computer use

- Settings opens from the gear at the bottom of the left sidebar; Appearance and Chat UI are under
  INTERFACE. Coordinate clicks from a fresh screenshot work better than accessibility clicks.
- Theme pickers ignore `set-value` and garble synthetic typing: open, scroll, screenshot, click by
  coordinates. Hovering previews a theme; one stray scroll installed `stablyai.orca-ayu-theme` on
  2026-11-07, so check the plugin list afterwards.
- The contrast slider ignores `set-value`; click the track (range 50–150). Confirm changes with the
  read-only query above.

## Risk

While the inspector is open (about one to two seconds per injection) any local process could control
Orca's main process. The agent session is a child of Orca's main process: never quit, restart or
signal Orca from an agent session.

## Remove

```sh
launchctl bootout gui/$(id -u)/com.adamjackson.orca-chat-section-style
rm ~/Library/LaunchAgents/com.adamjackson.orca-chat-section-style.plist
```

Then restart Orca to drop the injected CSS. To drop the theme, pick the stock Catppuccin themes and
run `orca plugin dev unload ../catppuccin-sections`.
