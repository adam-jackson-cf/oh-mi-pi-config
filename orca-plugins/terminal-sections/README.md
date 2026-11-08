# Terminal Sections

Local Orca data plugin that contributes two terminal themes, **Sections Dark** and **Sections Light**.
Their colours (background, foreground, cursor, selection and the 16 ANSI colours) follow the chat
palette chosen for the terminal (`catppuccin`, `ayu`, `github`, `vscode` or `auto`), so the terminal
matches the chat and the Orca app theme. Palette data derives from the Catppuccin, Ayu, GitHub and
VS Code terminal presets (MIT, see `LICENSE`).

## Generated files

- `terminal/dark.json`: Sections Dark; `terminal/light.json`: Sections Light.
- Written by `../chat-section-style/inject.mjs` (module `terminal/build.mjs`), never edited by hand
  and never tracked in git (`.gitignore` excludes `terminal/`). A new Mac gets them from the first
  `inject.mjs` run.
- After a change, `inject.mjs` runs `orca plugin dev reload <this folder>`; Orca refreshes the live
  terminal colours within about two seconds, with no re-selection.

## One-time setup

1. `orca plugin dev load ~/.omp/orca-plugins/terminal-sections`
2. Settings → Appearance → Terminal: Dark theme **Sections Dark**, Light theme **Sections Light**;
   line height 1.25.

Switching and `auto` behaviour, checks and limits:
`../orca-chat-style/references/terminal.md`. Layout lives in `../chat-section-style/`.
