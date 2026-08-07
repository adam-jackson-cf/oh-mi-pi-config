---
name: "tmux-tool"
description: "Use tmux for long-running, parallel, or persistent terminal work. USE WHEN the request includes `--tmux`, or the task needs a persistent, long-running, or parallel shell session."
---

# Tmux Tool Skill

- Use `tmux` when `--tmux` is in the request or the task is long-running, parallel, or persistent.
- Start sessions with `tmux new-session -d -s <name> '<command>'`.
- Inspect sessions with `tmux list-sessions`, `tmux attach -t <name>`, and `tmux capture-pane -p -S -200 -t <name>`.
- Stop sessions with `tmux kill-session -t <name>`.
