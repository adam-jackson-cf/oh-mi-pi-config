---
name: "orca-chat-style"
description: >-
  Use when the user asks about Orca's chat look, switches its layout mode, or finds Orca chat
  styling missing after an update or reinstall.
---

# Guidance

## What exists

- Orca has no custom-CSS setting. Its inspector injects styling from
  `~/.omp/orca-plugins/chat-section-style/`: `chat.css`, `palettes/*.css`, `id-highlight.js`,
  `palette-auto.js`, `mode`, `palette`, `inject.mjs`.
- LaunchAgent `com.adamjackson.orca-chat-section-style` re-injects within 30 seconds of each launch.
- App colours: Orca theme (Settings → Appearance; Catppuccin dev plugin `local.catppuccin-sections`
  in `~/.omp/orca-plugins/catppuccin-sections/`). Chat accents: palette. Settings → Chat UI:
  contrast 124, width Wide.
- Before changes or repairs, read `~/.omp/orca-plugins/chat-section-style/README.md`: the runbook
  for reapplying, re-mapping selectors, theme limits and removal.

## Layout modes

Exactly one mode is active, named in `mode`. Default: `document`.

- `document`: Flat page; no cards or section edges; hairlines above bold section lead-ins;
  neutral embed frames. Calmest for long replies.
- `split-cards`: Text runs in cards; tables, code and visuals between cards on the canvas.
  Clearest text/embed separation.
- `gutter`: No cards. Coloured left-margin dots start each ID section; embeds are soft borderless
  panels with type labels.
- `editorial`: No cards. Section gutter glyphs (◆ K, ▲ C, ✚ S, ● F), full-width prose, hairlines
  above K only, tinted embed title bands.
- Switch with:

      node ~/.omp/orca-plugins/chat-section-style/inject.mjs \
        --mode <document|split-cards|gutter|editorial>

  A hooked Orca applies it within a second; report the active mode.
- Calmer, flatter or document-like chat: `document`; separate text/embeds: `split-cards`;
  minimal framing with visible section markers: `gutter`; polished, rubric-scored reading:
  `editorial`.
- Shared styling goes in `chat.css`; mode rules in `:root[data-omp-chat-mode='<mode>']`.
  Add new modes to `MODES` in `inject.mjs`; full `node inject.mjs` re-installs the hook.
  Never add a mode unless the user asks.

## Palettes

Exactly one palette is active, named in `palette`. Default: `auto`, following the Orca theme in
Settings → Appearance (`palette-auto.js`). Palettes and modes combine freely.

| Palette | Pair with Orca theme (light / dark) |
| --- | --- |
| `catppuccin` | Catppuccin Sections Latte / Mocha |
| `ayu` | Ayu Light / Ayu Dark or Ayu Mirage |
| `github` | GitHub Light Default / GitHub Dark Default or Dark Dimmed |
| `vscode` | VS Code Light Modern / Dark Modern |

- Switch: `inject.mjs --palette <name|auto>` (combines with `--mode`); prefer `auto`.
  Full `node inject.mjs` reports `appliedPalette`. New theme plugins need a `PLUGIN_PALETTES`
  entry in `theme-palettes.mjs`.
- A palette is `palettes/<name>.css`, setting the contract atop `chat.css`. New palettes need
  full `node inject.mjs` and `bun harness/contrast.mjs <name>` (exit 0) before scoring.

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

## Behaviour

- Never quit, restart or send Orca a terminating signal from an agent session: it is Orca's child.
- Never patch `app.asar`, write Orca's profile database or change the inspector port.
- Inspector access: `inject.mjs`. Throwaway scripts must capture modules before deferring work;
  wrap deferred callbacks in `try`/`catch`: uncaught main-process exceptions show an error dialog.
- Confirm the inspector port closed afterwards: `lsof -nP -iTCP:9229 -sTCP:LISTEN` prints nothing.
- Verify visual changes with a real-chat screenshot:
  `orca computer get-app-state --app com.stablyai.orca --window-id <id> --max-elements 1 --json`.
  For off-screen content, render a temporary fixture as the README describes, then remove it.
- After changes, check `main_uncaught_exception` count has not grown in
  `~/Library/Application Support/orca/logs/main.trace.ndjson`.
- Restore anything switched for checks, e.g. theme mode (System) or active chat tab.
