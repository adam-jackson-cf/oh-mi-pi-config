---
name: "orca-chat-style"
description: >-
  Use when the user asks about Orca's chat or terminal look, switches layout modes or palettes,
  or finds styling missing after an update or reinstall.
---

# Guidance

## What exists

Two surfaces share `~/.omp/orca-plugins/chat-section-style/mode` and `palette`:

- **Chat:** `chat.css`, `palettes/*.css`, `id-highlight.js` and `palette-auto.js` are installed by
  `inject.mjs` through Orca's inspector; Orca has no custom-CSS setting. App colours come from
  Settings → Appearance (including `local.catppuccin-sections`); Chat UI uses contrast 124, width Wide.
- **Terminal:** the data-only `local.terminal-sections` plugin supplies Sections Dark/Light colours;
  generated omp `orca-sections-dark/light` themes supply TUI colours and mode symbols. Optional
  `terminal-mode` and `terminal-palette` files override the shared choices independently. Missing
  overrides follow chat. Default shared choices are `gutter` and `auto`.
- LaunchAgent `com.adamjackson.orca-chat-section-style` runs `inject.mjs --if-needed` every 30 seconds
  and at load. Terminal generation happens before the chat pid/marker early exits.
- Before changes or repairs, read `~/.omp/orca-plugins/chat-section-style/README.md`, the maintenance
  runbook for reapplying, selectors, theme limits and removal.

## Choose the surface

Use `--surface chat|terminal|both` with `--mode`, `--palette`, or both; the default is `both`.
Flags and effective terminal inputs are validated before choice files are written.

- **both:** write supplied choices to chat files and delete both terminal override files, restoring
  terminal following even for the choice not supplied.
- **chat:** first pin both effective terminal choices if their override files are absent, then write
  supplied chat choices. This leaves the terminal choice unchanged, including an `auto` choice.
- **terminal:** write only supplied terminal override files; chat choices stay unchanged.
- `--surface` without `--mode` or `--palette` is an error. Surface selects choice-file writes, not
  an isolated execution path: every run generates terminal files and can install the chat hook.

## Modes

| Mode | Chat look | Terminal look | Choose when |
| --- | --- | --- | --- |
| `document` | Flat, hairlines | Unfilled role hues, soft user fill, `•` | Calm long replies |
| `split-cards` | Text cards | State tool cards, teal custom fill, `•` | Separate text/embeds |
| `gutter` | Coloured dots | Open role hues, mantle custom fill, `●` | Minimal framing |
| `editorial` | Section glyphs | Open, quiet secondary/reasoning, `◆` | Polished reading |

Terminal prose headings/bullets use the chat accent; HUD/live-work titles use decision mauve,
tool/IRC headers the code hue, and secondary text neutral subtle ink. Reasoning mixes subtle ink
25% toward decision; success/error/warning use green/red/orange, custom/skill labels related teal,
and links key blue. Editorial fades secondary text, tool output and reasoning another 15% toward
the background, retaining ≥4.5:1 contrast; it adds `▏` quotes. Document keeps neutral code frames;
other modes keep code-hue frames. Editorial chat uses ◆ ▲ ✚ ● glyphs, K hairlines and quieter
older turns.
Terminal has no per-ID-kind colour hook: `registerAssistantTextRenderer` replaces markdown text,
and omp strips escapes before lexing; per-span colour needs an omp host change.

Modes and palettes combine freely. Shared chat styling goes in `chat.css`, mode rules in
`:root[data-omp-chat-mode='<mode>']`. Add new modes to `MODES` in `inject.mjs`; full
`node inject.mjs` re-installs the hook. Never add a mode unless the user asks.

## Palettes

| Palette | Orca app theme (light / dark) | Terminal |
| --- | --- | --- |
| `catppuccin` | Catppuccin Sections Latte / Mocha | Catppuccin colours |
| `ayu` | Ayu Light / Dark or Mirage | Ayu colours |
| `github` | GitHub Light Default / Dark Default or Dark Dimmed | GitHub colours |
| `vscode` | VS Code Light Modern / Dark Modern | VS Code colours |
| `auto` | Follow each selected app theme | Each slot's detected palette |

Every terminal entry means **Sections Dark/Light** coloured from that palette, plus omp
`orca-sections-dark/light`. Those theme names stay fixed across all palette choices.

Prefer `auto`. Chat detects the live renderer theme; terminal resolves dark/light app-theme settings
read-only, falling back to Catppuccin for unknown/missing settings. Fixed terminal palettes use their
default variants unless that slot's app theme belongs to the same palette (Mirage/Dimmed then follow).
Switching a palette does not change the selected Orca app theme.

## Switch commands

```sh
node ~/.omp/orca-plugins/chat-section-style/inject.mjs --mode document --palette auto
node ~/.omp/orca-plugins/chat-section-style/inject.mjs --surface chat --mode editorial
node ~/.omp/orca-plugins/chat-section-style/inject.mjs --surface terminal --palette ayu
node ~/.omp/orca-plugins/chat-section-style/inject.mjs --surface both --mode gutter --palette auto
```

A hooked chat applies choice changes within a second. Terminal files regenerate during the command;
loaded terminal plugins reload only when Orca colour files change. Report the effective choices and
any `terminal.reload` error, not just chat `appliedPalette`.

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

## References

- [Chat procedures, palette contract, harness and assessment rubric](references/chat.md)
- [Terminal setup, synchronization, checks and limits](references/terminal.md)
