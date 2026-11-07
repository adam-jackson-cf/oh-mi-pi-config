# Catppuccin Sections

Local Orca app theme: Catppuccin Latte and Mocha with token changes that separate chat sections.
Colors and syntax token colors come from
[stablyai/orca-catppuccin-theme][1]
(MIT, see `LICENSE`).

- `--chat-user-message`: Latte: `#d0d8f9`; Mocha: `#40446a`; Effect in chat: Your messages get a
  lavender fill, so each turn starts with a visible block
- `--muted-foreground`: Latte: `#3a6f87`; Mocha: `#80b5d5`; Effect in chat: Tool activity, status,
  and timestamps turn teal-slate, apart from reply prose
- `--border` / `--input`: Latte: `#bcc0cc` / `#acb0be`; Mocha: `#45475a` / `#585b70`; Effect in
  chat: Table, card, and divider lines are stronger

All values pass Orca's theme contrast guard (4.5:1 text, which drops failing tokens).

Load: `orca plugin dev load ~/.omp/orca-plugins/catppuccin-sections`, then pick the themes in
Settings → Appearance.

Layout changes (reply cards, accent headings, turn dividers) live in `../chat-section-style/`,
because themes can only set colours.

[1]: https://github.com/stablyai/orca-plugins/tree/main/plugins/catppuccin-theme
