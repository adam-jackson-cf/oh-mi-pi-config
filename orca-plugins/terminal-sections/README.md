# Terminal Sections

Data-only Orca plugin contributing **Sections Dark** and **Sections Light**. Their background,
foreground and ANSI colours follow the terminal palette chosen by `../chat-section-style/inject.mjs`.
The companion omp themes also translate the chat layout mode into terminal colours and symbols.

`terminal/dark.json` and `terminal/light.json` are generated runtime output and ignored by Git.
Run `node ~/.omp/orca-plugins/chat-section-style/inject.mjs --if-needed` to generate them on the
installed checkout before loading or validating this plugin. The same run generates
`~/.omp/agent/themes/orca-sections-{dark,light}.json`; never commit those files.

## One-time setup

1. Generate the files as above.
2. Run `orca plugin dev load <absolute-checkout>/orca-plugins/terminal-sections`.
3. In Settings → Appearance → Terminal, choose **Sections Dark** for Dark theme,
   **Sections Light** for Light theme, and line height **1.25**.

The injector reloads this plugin when its generated colours change, if it is loaded. The existing
30-second LaunchAgent also synchronizes terminal `auto` choices. No terminal CSS injection is used.
See [terminal procedures and limits](../orca-chat-style/references/terminal.md).
