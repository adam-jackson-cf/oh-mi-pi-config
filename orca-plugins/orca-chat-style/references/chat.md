# Chat reference

Procedures for Orca's chat styling. Choice of mode, palette and surface lives in `../SKILL.md`; the
maintenance runbook is `~/.omp/orca-plugins/chat-section-style/README.md` (reapplying after an Orca
update, re-mapping selectors, theme limits, removal). Read it before changes or repairs.

## Injection

- Orca has no custom-CSS setting. Its inspector injects styling from
  `~/.omp/orca-plugins/chat-section-style/`: `chat.css`, `palettes/*.css`, `id-highlight.js`,
  `palette-auto.js`, `mode`, `palette`, `inject.mjs`.
- LaunchAgent `com.adamjackson.orca-chat-section-style` re-injects within 30 seconds of each launch.
- App colours: Orca theme (Settings → Appearance; Catppuccin dev plugin `local.catppuccin-sections`
  in `~/.omp/orca-plugins/catppuccin-sections/`). Chat accents: palette. Settings → Chat UI:
  contrast 124, width Wide.
- Inspector access: `inject.mjs`. Throwaway scripts must capture modules before deferring work and
  wrap deferred callbacks in `try`/`catch`: uncaught main-process exceptions show an error dialog.

## Modes

- Exactly one chat mode is active, named in `mode`. Default: `gutter`.
- Shared styling goes in `chat.css`; mode rules in `:root[data-omp-chat-mode='<mode>']`.
- Add new modes to `MODES` in `inject.mjs`, and a matching overlay in the terminal layer
  (`terminal/modes.mjs`); a full `node inject.mjs` re-installs the hook. Never add a mode unless the
  user asks.

## Palettes

- Exactly one chat palette is active, named in `palette`. Default: `auto`, following the Orca theme
  in Settings → Appearance (`palette-auto.js`). Palettes and modes combine freely.
- Switch: `inject.mjs --palette <name|auto> --surface chat`, or without `--surface` for both
  surfaces. Full `node inject.mjs` reports `appliedPalette`. New theme plugins need a
  `PLUGIN_PALETTES` entry in `theme-palettes.mjs`.
- A palette is `palettes/<name>.css`, setting the contract atop `chat.css`. New palettes need full
  `node inject.mjs` and `bun harness/contrast.mjs <name>` (exit 0) before scoring. A new palette
  also needs a terminal record in `terminal/palettes.mjs` (see `terminal.md`).

| Palette | Pair with Orca theme (light / dark) |
| --- | --- |
| `catppuccin` | Catppuccin Sections Latte / Mocha |
| `ayu` | Ayu Light / Ayu Dark or Ayu Mirage |
| `github` | GitHub Light Default / GitHub Dark Default or Dark Dimmed |
| `vscode` | VS Code Light Modern / Dark Modern |

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
- Verify visual changes with a real-chat screenshot:
  `orca computer get-app-state --app com.stablyai.orca --window-id <id> --max-elements 1 --json`.
  For off-screen content, render a temporary fixture as the README describes, then remove it.
