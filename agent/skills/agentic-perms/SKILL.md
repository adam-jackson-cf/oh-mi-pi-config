---
name: "agentic-perms"
description: "Review, change, repair, and verify the user-level Codex permission profile and Orca IDE Codex runtime policy. USE WHEN working with approval prompts, filesystem or network access, destructive command rules, package-manager-installed programs, ~/.codex/config.toml, ~/.codex/aj-secure-default.toml, ~/.codex/rules/default.rules, or Orca's codex-runtime-home"
---

# Agentic Permissions Skill

- Treat the files under `~/.codex` as canonical.
- Keep Orca in local-account `host` mode so it consumes the user-level Codex configuration.
- Never hand-edit Orca's generated runtime `config.toml`.
- Keep Orca's runtime `rules/default.rules` as a symlink to the canonical Codex rules file.
- Never read or print secret-bearing files while auditing permissions.
- Use presence checks for sensitive environment variables and mask values if display is unavoidable.
- Preserve existing policy wording and justification strings exactly when the same rule appears in multiple Codex policy surfaces.
- Request approval before destructive policy repair or before weakening a deny, prompt, or reviewer constraint.

## Canonical Codex Files

- `~/.codex/config.toml`: active user configuration, granular approval policy, default permission profile, filesystem access, network access, and project trust.
- `~/.codex/aj-secure-default.toml`: managed constraints for allowed approval policies, reviewers, permission profiles, filesystem access, network access, and command rules.
- `~/.codex/rules/default.rules`: executable command prefix rules used by Codex and Orca.

Keep these invariants:

- `default_permissions = "aj-secure-default"`.
- Filesystem `":root" = "write"`.
- Network access enabled.
- Sensitive credential paths denied.
- Workspace `.env` variants denied.
- Granular approval prompts enabled for `sandbox_approval` and `rules`.
- `sudo`, `doas`, `pkexec`, destructive filesystem or disk commands, and destructive Git commands prompt.
- Ordinary commands, package managers, network access, and package-manager-installed programs do not prompt unless an explicit command rule matches.

## Orca Runtime Files

- `~/Library/Application Support/orca/orca-data.json`: runtime selection. Inspect only `settings.localAccountRuntime` and `settings.activeCodexManagedAccountIdsByRuntime`; never dump unrelated account data.
- `~/Library/Application Support/orca/codex-runtime-home/home/config.toml`: generated runtime configuration containing canonical permission values plus Orca hooks. Audit it; do not edit it.
- `~/Library/Application Support/orca/codex-runtime-home/home/rules/default.rules`: required symlink to `~/.codex/rules/default.rules`.
- `~/Library/Application Support/orca/codex-accounts/<account-id>/home/config.toml`: managed-account storage. Do not edit it while the active runtime is local-account `host` mode.

## Review Workflow

1. Parse TOML with `tomllib`; do not rely only on text comparison.
2. Confirm `approval_policy`, `default_permissions`, and `permissions` in Orca's generated runtime config equal the canonical `~/.codex/config.toml` values.
3. Confirm Orca retains its `hooks` table.
4. Confirm `settings.localAccountRuntime` is `host`.
5. Confirm the runtime rules path is a symlink resolving to `~/.codex/rules/default.rules`.
6. Evaluate representative safe and destructive commands with `codex execpolicy check` against the runtime rules path.
7. Run a harmless installed executable to prove filesystem and execution access.
8. Report exact paths, resolved targets, and pass or fail evidence. Never claim enforcement from intended configuration alone.

Use commands shaped like:

```bash
readlink "$HOME/Library/Application Support/orca/codex-runtime-home/home/rules/default.rules"

CODEX_HOME="$HOME/Library/Application Support/orca/codex-runtime-home/home" \
  codex execpolicy check --pretty \
  --rules "$HOME/Library/Application Support/orca/codex-runtime-home/home/rules/default.rules" \
  git reset --hard HEAD

CODEX_HOME="$HOME/Library/Application Support/orca/codex-runtime-home/home" \
  codex execpolicy check --pretty \
  --rules "$HOME/Library/Application Support/orca/codex-runtime-home/home/rules/default.rules" \
  git status --short
```

Expect the destructive command to return `prompt` and the safe command to have no matching rule.

## Change Workflows

### Change Filesystem Or Network Permissions

1. Inspect both `~/.codex/config.toml` and `~/.codex/aj-secure-default.toml`.
2. Apply the requested canonical permission change to both files using the exact same profile name, path spelling, and access value.
3. Preserve all unrelated project trust, telemetry, model, hook, and notification settings.
4. Parse both files with `tomllib`.
5. Start a new Codex session when runtime configuration must be regenerated, then repeat the review workflow.

### Change Command Prompt Rules

1. Add or change the managed rule in `~/.codex/aj-secure-default.toml`.
2. Add or change the executable rule in `~/.codex/rules/default.rules` with the same command coverage, decision, and exact justification wording.
3. Test every supported argument order and absolute executable path.
4. Test a nearby non-destructive command and require no match.
5. Confirm Orca resolves the canonical rules symlink; do not copy the edited rules into Orca.

### Repair Orca Policy Linkage

1. Confirm `settings.localAccountRuntime` is `host`.
2. Inspect the existing runtime rules entry before changing it.
3. When the user authorizes repair, replace only the runtime `rules/default.rules` entry with a symlink to `~/.codex/rules/default.rules`.
4. Do not replace the runtime `config.toml` with a symlink because Orca adds required hooks there.
5. Re-run the full review workflow after repair.
6. Do not restart Orca while connected terminals are active; new Codex sessions consume the corrected policy.

### Review Installed Programs And Libraries

1. Resolve the actual executable with `command -v`.
2. Confirm the resolved file is readable and executable.
3. Run `codex execpolicy check` with the exact resolved path.
4. Run a harmless version or help command.
5. Treat libraries as accessible when their install path is permitted; verify import or dynamic linking with the interpreter or runtime that owns the environment.
6. Account for keg-only packages that are not on `PATH`.
7. Distinguish Codex approval policy from macOS Gatekeeper, Accessibility, Screen Recording, and application permission prompts.

## Command Coverage Boundaries

- Prefix rules evaluate the actual command tokens. A newly installed destructive executable at a different absolute path does not inherit coverage from a named path automatically.
- Shell wrappers can hide a nested destructive command from a rule matching only the inner executable.
- Never use an alternate path, alias, wrapper, interpreter, or shell command to bypass a prompt rule.
- When a new executable can perform an already-controlled destructive action, add its exact invocation forms to both command-policy surfaces and verify them before treating coverage as complete.
