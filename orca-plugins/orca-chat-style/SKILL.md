---
name: "orca-chat-style"
description: >-
  Use when the user asks about Orca's chat or terminal look, switches the layout mode or palette of
  the chat, the terminal or both, or finds Orca chat or terminal styling missing after an update or
  reinstall.
---

# Guidance

## What exists

Two surfaces share one choice of layout `mode` and colour `palette`:

- **Chat**: CSS injected into Orca's chat from `~/.omp/orca-plugins/chat-section-style/` (files
  `mode`, `palette`). LaunchAgent `com.adamjackson.orca-chat-section-style` re-injects within 30
  seconds of each launch.
- **Terminal** (Orca's terminal pane running omp's TUI): Orca terminal themes Sections Dark/Light
  (data plugin `~/.omp/orca-plugins/terminal-sections/`) plus omp themes `orca-sections-dark`/
  `orca-sections-light`, generated from the same mode and palette. Optional override files
  `terminal-mode` and `terminal-palette` let the terminal differ from the chat.
- App colours: Orca theme (Settings → Appearance). Settings → Chat UI: contrast 124, width Wide.
- Before changes or repairs, read `~/.omp/orca-plugins/chat-section-style/README.md`: the runbook
  for reapplying, re-mapping selectors, theme limits and removal. Procedures:
  `references/chat.md` (injection, palette files, harness, assessment rubric) and
  `references/terminal.md` (setup, switching, `auto`, checks, limits).

## Choose the surface

`inject.mjs --surface chat|terminal|both` (default `both`) goes with `--mode` and/or `--palette`:

- `both`: chat and terminal change together; any terminal override is removed.
- `chat`: only the chat changes; the terminal is first pinned to its current look.
- `terminal`: only the terminal changes.

Use `both` unless the user names one surface. `--surface` without `--mode`/`--palette` is an error.

## Modes

Exactly one mode per surface; chat default `gutter`. Each mode has a terminal equivalent; the
terminal cannot show per-section ID colours, per-embed hues, hover fading or table zebra.

| Mode | Chat look | Terminal look | Choose when |
| --- | --- | --- | --- |
| `document` | Flat page, neutral frames | Unfilled, hairlines, quiet • | Long replies |
| `split-cards` | Text cards, edged embeds | Filled, rounded, accent • | Clear separation |
| `gutter` | Dots, soft embeds | Open sides, code-hued frames, ● | Minimal framing |
| `editorial` | ID glyphs, quiet older turns | ◆, ▏ quotes, quieter output | Polished reading |

See `references/chat.md` and `references/terminal.md` for the full visual mapping.
Switch with `inject.mjs --mode <name> [--surface chat|terminal|both]`.
A hooked Orca applies the chat within a second; running omp sessions reload their theme on change.
Report the active mode per surface. Never add a mode unless the user asks (see `references/chat.md`).

## Palettes

Exactly one palette per surface; default `auto`, following the Orca theme in Settings → Appearance.
Palettes and modes combine freely; prefer `auto`.

| Palette | Orca app theme (light / dark) | Terminal |
| --- | --- | --- |
| `catppuccin` | Catppuccin Sections Latte / Mocha | Mocha / Latte |
| `ayu` | Ayu Light / Dark or Mirage | Dark or Mirage / Light |
| `github` | GitHub Light / Dark Default or Dimmed | Dark or Dimmed / Light |
| `vscode` | VS Code Light Modern / Dark Modern | Dark / Light |
| `auto` | Active app theme | Each slot follows its app palette |

Terminal entries colour Sections Dark/Light and omp `orca-sections-dark`/`orca-sections-light`.
`auto` refreshes within 30 seconds; unknown app themes fall back to `catppuccin`.

Switch: `inject.mjs --palette <name|auto> [--surface chat|terminal|both]` (combines with `--mode`).
Full `node inject.mjs` reports `appliedPalette` and the `terminal` result.

## Behaviour

- Never quit, restart or send Orca a terminating signal from an agent session: it is Orca's child.
- Never patch `app.asar`, write Orca's profile database or change the inspector port.
- Inspector access: `inject.mjs`. Throwaway scripts must capture modules before deferring work;
  wrap deferred callbacks in `try`/`catch`: uncaught main-process exceptions show an error dialog.
- Confirm the inspector port closed afterwards: `lsof -nP -iTCP:9229 -sTCP:LISTEN` prints nothing.
- Verify visual changes with a real-chat or real-terminal screenshot:
  `orca computer get-app-state --app com.stablyai.orca --window-id <id> --max-elements 1 --json`.
  For off-screen chat content, render a temporary fixture as the README describes, then remove it.
- After changes, check `main_uncaught_exception` count has not grown in
  `~/Library/Application Support/orca/logs/main.trace.ndjson`.
- Restore anything switched for checks, e.g. theme mode (System) or active chat tab.
- Score chat modes and palettes by the rubric in `references/chat.md`; check the terminal with
  `bun chat-section-style/harness/terminal-themes.mjs` (`references/terminal.md`).
