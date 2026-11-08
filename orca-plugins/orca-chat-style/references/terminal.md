# Terminal styling

Orca's terminal is a text-cell surface: chat CSS cannot style it. Two generated layers share the
chat's mode and palette, unless `terminal-mode` or `terminal-palette` overrides that choice:

1. `orca-plugins/terminal-sections/terminal/{dark,light}.json`: background, foreground, cursor,
   selection and 16 ANSI colours. The data-only dev plugin contributes **Sections Dark** and
   **Sections Light**; backgrounds and foregrounds match the paired app themes.
2. `~/.omp/agent/themes/orca-sections-{dark,light}.json`: omp TUI colours and single-cell mode
   symbols. `agent/config.yml` selects `theme.dark: orca-sections-dark` and
   `theme.light: orca-sections-light`. omp chooses the slot from the terminal background and watches
   the active custom theme file for changes in running sessions.

All four JSON files are runtime output, not tracked sources. `terminal/build.mjs` writes only when
bytes differ; `terminal/palettes.mjs` holds palette/variant source data, and `terminal/modes.mjs`
holds mode overlays. Table and diagram hues are retained as source data but have no terminal tokens.

## Area colours and modes

All palettes, including Catppuccin, use one role mapping (no theme-template passthrough):

| Area / tokens | Palette role |
| --- | --- |
| Prose headings and bullets (`mdHeading`, `mdListBullet`) | Chat `accent` |
| TODO/Subagents titles, progress percent, TODO spine (`accent`) | `decision`, mauve family |
| Tool and IRC headers (`toolTitle`) | `code` hue |
| Secondary details/output/meta/labels (`muted`, `toolOutput`) | Neutral `subtle` |
| Italic reasoning prose (`thinkingText`) | `subtle` mixed 25% toward `decision` |
| Status (`success`, `error`, `warning`) | `suggestion` green / `gap` red / `review` orange |
| Custom/skill labels and user skill rail (`customMessageLabel`) | `related` teal |
| Links (`mdLink`) | `key` blue |

Secondary ink covers tool details/output, IRC metadata, subagent descriptions and reasoning labels.

The mapping follows omp 18.16.0: `tools/wait.ts:787–796` (IRC),
`chat/assistant-message.ts:1454–1457,774–794` (reasoning),
`tools/job-widget.ts:410–520` and `tools/agent-tree.ts:127` (progress),
`pi-coding-agent/src/modes/interactive-mode.ts:5096–5243,4789–4805` (TODO) and
`:1261–1327` (Subagents), `chat/user-message.ts`, `custom-message.ts:21`, and
`chat/tool-execution.ts` / `chat/execution-shared.ts` (messages/tools).

- **document:** tool and custom backgrounds equal the canvas; user fill is soft mantle.
- **split-cards:** pending tools use mantle, success/error tools get state tints; custom messages
  mix the canvas 8% toward related teal; user fill uses the palette's user-message colour.
- **gutter:** unfilled tools, mantle custom messages, open frames and `●` bullets.
- **editorial:** unfilled tools; secondary ink, tool output and reasoning fade another 15% toward
  the canvas; chat-accent headings, `◆` bullets and `▏` quotes.

Existing frame behaviour remains: document has neutral code borders, other modes code-hue borders.
Text inks are adjusted toward black/white only as needed for ≥4.5:1 contrast on their backgrounds.
The nine area inks stay pairwise ≥0.05 apart in OKLab, including after editorial fading.

## One-time setup

From the installed checkout, generate the files first:

```sh
node ~/.omp/orca-plugins/chat-section-style/inject.mjs --if-needed
orca plugin dev load <absolute-checkout>/orca-plugins/terminal-sections
```

In Settings → Appearance → Terminal, set **Dark theme: Sections Dark**, **Light theme: Sections
Light**, and **line height: 1.25**. This selection is required once; subsequent palette changes
rewrite the selected themes, rather than requiring reselection. Do not write Orca's profile DB.
The line-height field ignores `set-value`: click it, select all and type through the UI.

## Switching and auto

```sh
# Both surfaces; also remove both terminal overrides.
node ~/.omp/orca-plugins/chat-section-style/inject.mjs --mode gutter --palette auto
# Only terminal choices; leave chat files unchanged.
node ~/.omp/orca-plugins/chat-section-style/inject.mjs \
  --surface terminal --mode editorial --palette github
# Only chat choices; pin both effective terminal choices before changing chat.
node ~/.omp/orca-plugins/chat-section-style/inject.mjs --surface chat --mode document
```

`--surface` requires a mode or palette flag; omitted means `both`. A `both` switch removes both
terminal override files even when only one choice is supplied. A chat-only pin preserves the
choice, not a resolved colour: pinned `auto` still follows app themes. Surface flags govern choice
files only; they do not suppress terminal generation or the chat installation path.

Every injector run synchronizes the terminal before Orca pid/marker early exits. If Orca colour
files changed and `orca plugin list --json` lists `local.terminal-sections`, the injector runs
`orca plugin dev reload <absolute-plugin-dir>`. JSON output includes
`terminal: { mode, palette, slots, changed, reload }`; a generation failure instead includes `error`.
Reload errors are reported without stopping the chat path. `reload.status` can be `unchanged`,
`not-loaded`, `reloaded` or `error`; `unchanged` means no Orca colour-file change required a reload,
not necessarily that omp files stayed unchanged.

The existing LaunchAgent runs `--if-needed` at load and every **30 seconds**, keeping terminal
`auto` in step with app-theme settings and regenerating missing files. It need not re-open the
inspector when the chat hook is already installed. Chat auto detects live renderer tokens;
terminal auto independently reads the dark/light app-theme selections from Orca's local-default
settings DB, using `node:sqlite` in read-only mode. Missing/unreadable settings or an unknown plugin
fall back to Catppuccin per slot.

Supported variants: Catppuccin Mocha/Latte, Ayu Dark or Mirage/Light, GitHub Dark or Dimmed/Light,
and VS Code Dark/Light. Fixed palettes use the default variant unless the slot's active app theme
belongs to that palette; then Mirage/Dimmed is preserved. GitHub high-contrast IDs use default
dark/light colours. Changing the palette never selects an Orca app theme for you.

## Checks

From the checkout root:

```sh
bun orca-plugins/chat-section-style/harness/terminal-themes.mjs
```

The harness checks every palette/variant/mode/slot: required omp tokens and contrast, Orca theme
shape and paired app colours, observable mode differences, slot resolution, config selection,
generator idempotence, non-empty tool titles and ≥0.05 pairwise OKLab area separation. Editorial
secondary ink must have lower contrast than gutter while retaining ≥4.5:1.
Deliberately dimmed `dim` and decorative borders are contrast exemptions.
Validate the data plugin after generation with
`orca plugin validate <absolute-checkout>/orca-plugins/terminal-sections`.

Read settings without writing:

```sh
sqlite3 -readonly \
  ~/Library/Application\ Support/orca/profiles/local-default/profile-state.db \
  "select json_extract(payload,'$.terminalThemeDark'), "\
"json_extract(payload,'$.terminalThemeLight'), json_extract(payload,'$.terminalLineHeight'), "\
"json_extract(payload,'$.pluginAppThemeDark'), json_extract(payload,'$.pluginAppThemeLight') "\
"from profile_state_documents where domain='settings'"
omp config get theme.dark && omp config get theme.light
```

Expected terminal selections identify `local.terminal-sections/sections-dark` and
`local.terminal-sections/sections-light`, line height `1.25`, and omp names `orca-sections-dark` /
`orca-sections-light`. Confirm JSON generation/reload output, then take a real terminal screenshot
in light and dark, including a mixed-media omp reply. Use
`orca computer get-app-state --app com.stablyai.orca --window-id <id> --max-elements 1 --json`.
Restore anything switched for checks. Read-only settings and a passing harness alone do not prove
that the visible terminal is using the selected themes.

## Limits

Terminal mode is an equivalent, not a chat layout clone: area hues, filled/unfilled tools, frame
symbols, bullets, rules, quotes and quieter secondary/reasoning ink convey its intent. Tokens are
shared across areas: TODO and Subagents cannot have independent title hues, nor can each IRC kind.
There is no per-ID-kind colouring: the only extension hook, `registerAssistantTextRenderer`,
replaces markdown text; omp strips escape codes before lexing
(`pi-tui/src/components/markdown.ts:2556–2566`), so per-span colours require an omp host change.
There are no per-embed-type hues (one `mdCodeBlockBorder`), hover/fade or table zebra rows.
The table and diagram hues are unused. Terminal colour generation needs no inspector; chat still
depends on Orca's inspector fuse and renderer selectors.
