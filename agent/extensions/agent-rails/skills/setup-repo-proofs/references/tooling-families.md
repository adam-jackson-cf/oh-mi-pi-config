# Tooling families

Each language pack in `assets/conventions/` selects one native tool family. The catalogue's tool rows carry the contract and the
settings `scripts/propose_tool_configs.py` renders as ready-to-use config. Target values (Python version, first-party packages,
source roots, the Go module path) always come from the target repository.

## How to apply a family

1. Detect the pack from tracked file extensions (`scripts/inventory_controls.py` reports `languages`).
2. Run `python3 <skill>/scripts/propose_tool_configs.py --repo <clone> --out <candidate root>/proposed`.
   It proposes a config only where the tool has none, reports kept configs, and reports values it cannot derive
   (no `requires-python`, no package, no Go module) instead of guessing.
3. Where the repo already has a config, keep it as the authority. Add only missing catalogue settings as a `grow` change;
   a stricter existing value stays; a looser one is a Step 5 decision with an owner-approved reason.
4. Pin the tool through the repo's package manager and run it in check-only mode from the hook and CI entrypoints.
   Fix mode stays a separate, explicit command.
5. Record one `proofs.toml` claim per tool, naming its rule family, with one canary run through the enforcement entrypoint
   that the configured setting rejects (for example a function over `max-complexity = 10`). The config stays the only home of each value.
6. A baseline, blanket ignore or unscoped suppression is a finding to fix, never a setting to keep.

## Python: Ruff and mypy

| Catalogue prefix | Setting |
| --- | --- |
| `RUFF.ROOT.*` | `fix = false` (gates stay check-only; fix via explicit `--fix`), `output-format = "full"`, `respect-gitignore`, `preview`, `exclude` |
| `RUFF.SELECT.*` | `E W F I B C4 C901 UP SIM PT PGH A N S110 BLE001 SIM105` and `PLR0904 PLR0911–PLR0915 PLR0917` |
| `RUFF.IGNORES` | `E501 B006 B008 A005`; per-file `__init__.py: F401 F403`, tests: `S101 PLR2004` |
| `RUFF.LIMIT.*` | max-args 5, max-positional-args 5, max-branches 12, max-locals 15, max-returns 6, max-statements 50, max-public-methods 20 |
| `RUFF.SETTING.*` | mccabe `max-complexity = 10`; `target-version` from `project.requires-python` |
| `RUFF.ISORT.*` | `combine-as-imports`, `split-on-trailing-comma`, no `force-single-line`, `known-first-party` = repo packages |
| `RUFF.FORMAT.*` | double quotes, space indent, `line-ending = "auto"` |
| `MYPY.*` | `strict`, `warn_unused_ignores`, `show_error_codes`, `follow_imports = normal`; `python_version`, `packages`, `mypy_path` from the repo |

mypy rules: every maintained package is checked and package-list drift fails (`MYPY.package-coverage`); exclusions name
non-maintained paths with a rationale; module overrides are named and evidenced, a published stub package comes before
`ignore_missing_imports`, and in-repo third-party stubs are forbidden (`MYPY.follow_imports`, `MYPY.module-overrides`).
Python bespoke rules (`PYQ*`, `PYS*`, `FANOUT.*`) come from the [python reference](../assets/python-reference/README.md).
An import-direction contract (for example import-linter layers from `proofs.toml` `[structure]` tiers) can carry
`STRUCT.dependency-direction`; the script proposes one when given `--tiers` and `--import-package`.

## TypeScript and JavaScript: tsc and oxlint

- `TSC.*`: `strict`, `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`, `noImplicitOverride`, `noImplicitReturns`,
  `noFallthroughCasesInSwitch`, `noPropertyAccessFromIndexSignature`, `noUnusedLocals`, `noUnusedParameters`,
  `forceConsistentCasingInFileNames` all true; `allowUnreachableCode` and `allowUnusedLabels` false.
  `TSC.project-coverage`: the repo's own tsc checks every maintained TypeScript file; third-party types come from the package
  or a published `@types` package, never an in-repo declaration shim.
- `OXLINT.*`: plugins `eslint typescript unicorn oxc import`; `correctness` and `suspicious` categories as errors;
  `eslint/complexity` max 10, `eslint/max-params` max 5, `eslint/max-statements` max 50, `import/max-dependencies` max 10,
  `eslint/no-empty` without empty catch, `typescript/ban-ts-comment` and `typescript/no-explicit-any` as errors.
  Each mirrors a Python rule (complexity, arguments, statements, fan-out, swallowed exceptions, blanket ignores, `Any`).
- An existing ESLint or Biome config is kept; add the mirrored limits there instead of a second linter.
- `PRETTIER.version`: applies when the repo has a JavaScript or TypeScript toolchain (a tracked `package.json`); then one exact
  Prettier pin in the repo, matched by the lock, invoked for tracked Markdown and YAML. Markdown and YAML alone do not bring it in.
- `PARITY.package-manager`: one exact `packageManager` pin, the matching lock file only, no foreign lock files.

## Go: golangci-lint

- `GOLANGCI.version` 2 config format; `run.tests: true`; `linters.default: standard` (govet, errcheck, staticcheck, unused, ineffassign).
- Enable `gocyclo` (min-complexity 10), `funlen` (statements 50, lines off), `revive` (argument-limit 5), `forbidigo`
  (forbid `exec.Command`; use `exec.CommandContext` with a deadline) and `nolintlint` (every `//nolint` names its linter and explains why).
- Formatters `gofmt` and `goimports`, with the module path as the local prefix.

## Shell: ShellCheck and shfmt

- `SHELLCHECK.*`: `.shellcheckrc` enabling `avoid-nullary-conditions`, `check-extra-masked-returns`, `check-set-e-suppressed`,
  `check-unassigned-uppercase`, `deprecate-which`, `quote-safe-variables`; `external-sources=true`; `source-path=SCRIPTDIR`.
  `SHELLCHECK.coverage`: every maintained script from `git ls-files`, including extensionless scripts with a shell shebang.
- `SHFMT.*`: an `.editorconfig` `[*.{sh,bash}]` section with space indent, size 2, `binary_next_line`, `switch_case_indent`,
  and `function_next_line = false`.

## Cross-family parity

- `PARITY.lock-version`: a tracked lock file whose versions match package metadata.
- `PARITY.dependency-inventory`: declared optional analyzer dependencies match what the installer installs.
- `PARITY.install-guidance`: documented install commands match the actual installer.
- `SKILL.metadata`: when the repo holds agent skills, names match directories, descriptions start with "use when",
  and metadata follows the repo's schema.

## Tooling pack

`assets/conventions/tooling.json` lists ecosystem fixed files (Makefile, lock files, tool configs, CI and container files)
with their upstream references. Naming checks never flag a fixed file; inventory uses the list to find tool configs.
