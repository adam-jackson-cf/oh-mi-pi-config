# Terminal reference

The terminal is Orca's terminal pane running omp's TUI. It draws text cells, so `chat.css` cannot
reach it. Every chat mode and palette has a terminal equivalent, driven by the same `mode`/`palette`
choice unless overridden. Choosing surface, mode and palette: `../SKILL.md`.

## Layers

- `Orca terminal colours`: the data plugin `~/.omp/orca-plugins/terminal-sections/` (id
  `terminal-sections`, pluginKey `local.terminal-sections`) contributes terminal themes
  **Sections Dark** (`terminal/dark.json`) and **Sections Light** (`terminal/light.json`):
  background, foreground, cursor, selection and the 16 ANSI colours of the active palette. The
  generator rewrites both files and runs `orca plugin dev reload`; Orca refreshes the live terminal
  within about 2 seconds, with no re-selection (spike, Orca 1.4.248).
- `omp TUI theme`: generated `~/.omp/agent/themes/orca-sections-dark.json` and
  `orca-sections-light.json`, selected by `theme.dark`/`theme.light` in `~/.omp/agent/config.yml`.
  They carry the mode into omp's own colours and symbols (tool-block fills, frames, code borders,
  bullets, rules, headings). omp watches the active custom theme file and reloads running sessions
  on change.

Generated files are runtime output and never tracked. Source: `chat-section-style/terminal/`
(`build.mjs`, `palettes.mjs`, `modes.mjs`); `inject.mjs` calls it on every run.

## One-time setup

1. `orca plugin dev load ~/.omp/orca-plugins/terminal-sections` (check with
   `orca plugin list --json`: `local.terminal-sections` with `isDev: true`).
2. Settings → Appearance → Terminal: Dark theme **Sections Dark**, Light theme **Sections Light**;
   line height 1.25 (the field ignores `set-value`: click it, select all and type).
3. Run `node ~/.omp/orca-plugins/chat-section-style/inject.mjs` once to generate the files (and, on
   a new Mac, the omp themes). `agent/config.yml` already selects `orca-sections-dark` and
   `orca-sections-light`.

## How switching works

- Terminal choice files: optional `terminal-mode` and `terminal-palette` in
  `chat-section-style/`. Absent = the terminal follows `mode` and `palette`.
- `inject.mjs --mode/--palette --surface chat|terminal|both` (default `both`):
  - `both`: writes the chat files and deletes the terminal override files (terminal follows chat).
  - `chat`: pins the terminal first (writes the override files with the current effective terminal
    values if absent), then writes the chat files.
  - `terminal`: writes only the terminal override files.
  - `--surface` without `--mode`/`--palette` is an error.
- Every `inject.mjs` run, including the LaunchAgent's 30-second `--if-needed` run, regenerates the
  four theme files (only when bytes differ) and reloads the plugin when the Orca colours changed.
  The result prints as `terminal: { mode, palette, slots, changed, reload }`.

## How `auto` works

Terminal palette `auto` resolves per slot (dark, light): read Orca's settings read-only with
`node:sqlite` from the profile database (`pluginAppThemeDark`, `pluginAppThemeLight`, format
`plugin:<pluginKey>/<themeId>`); `PLUGIN_PALETTES` in `theme-palettes.mjs` maps the plugin to a
palette, and the theme id picks the variant (`mirage`, `dimmed`). Unknown, missing or unreadable
falls back to `catppuccin`, as chat `auto` does. Never write the database. Within 30 seconds of an
Orca theme change the terminal follows.

A fixed palette uses its default variant (`mocha`/`latte`; ayu, github and vscode `dark`/`light`)
unless the slot's active app theme is the same palette, in which case that variant is used.

## Checks

- `bun ~/.omp/orca-plugins/chat-section-style/harness/terminal-themes.mjs [repo-root]`: every
  palette × variant × mode × slot has all required omp tokens, ≥4.5:1 text contrast on real
  backgrounds (`dim` and decorative borders exempt), valid Orca terminal themes whose
  background/foreground equal the app theme's, observably different modes, correct `auto` mapping,
  and `config.yml` slots. Exit 1 on any failure.
- Read settings without writing:

  ```sh
  sqlite3 -readonly \
    ~/Library/Application\ Support/orca/profiles/local-default/profile-state.db \
    "select json_extract(payload,'$.terminalThemeDark'), "\
  "json_extract(payload,'$.terminalThemeLight'), json_extract(payload,'$.terminalLineHeight'), "\
  "json_extract(payload,'$.pluginAppThemeDark'), json_extract(payload,'$.pluginAppThemeLight') "\
  "from profile_state_documents where domain='settings'"
  omp config get theme.dark && omp config get theme.light
  ```

  Expect the Sections Dark/Light terminal themes, `1.25`, and `orca-sections-dark` /
  `orca-sections-light`.
- Take a terminal screenshot (`orca computer get-app-state`) in light and dark and confirm the look
  against the mode table in `../SKILL.md`.

## Limits

omp exposes no theme token for: ID colouring by kind (K/C/S/F), section gutter glyphs, per-embed-type
hues (one `mdCodeBlockBorder`), hover or fading older turns, or table zebra rows. The palette's
`table` and `diagram` hues are unused in the terminal. Mode overlays use only colour tokens and
single-cell `symbols.overrides`.
