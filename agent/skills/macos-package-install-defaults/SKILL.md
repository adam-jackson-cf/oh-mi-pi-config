---
name: "macos-package-install-defaults"
description: "Apply the default macOS package manager split unless a repo overrides it. USE WHEN installing macOS tools and the repository does not provide more specific package or runtime instructions."
---

# macOS Package Install Defaults Skill

- Treat these defaults as fallback guidance outside repo-specific instructions.
- Use `brew install` or `brew install --cask` for system CLIs and apps, including `codex`.
- Use Volta for Node ecosystem tools: `volta install node@<major>` and `volta install <package>`.
- Use tool-native installers only when Homebrew and Volta do not manage the package.
- If a repo defines its own package or runtime instructions, follow the repo instead.
