"""Proposed native-tool configs for TypeScript, Go and shell repos, from the reference's data.

Like ``native_config`` for Python, init writes each proposal to the record's ``proposed/`` folder
and the owner applies it to the repository as an ordinary fix. A pack is present when the repo
root holds its marker: ``package.json`` or ``tsconfig.json`` (TypeScript), ``go.mod`` (Go), or a
tracked ``.sh``/``.bash`` file (shell). Proposals, all from ``data/native-tools.json``:

- ``tsconfig.json`` with the TSC.* compiler options. An existing tsconfig is kept; init reads its
  ``compilerOptions`` through every ``extends`` it can resolve and reports each TSC.* value it
  lacks, rule by rule. A base it cannot read is reported, so nothing passes unread.
- ``.oxlintrc.json`` with the OXLINT.* plugins, categories and rules. An existing oxlint, ESLint
  or Biome configuration is kept and reported: the repo's own linter is the one to grow.
- ``.golangci.json`` with the GOLANGCI.* linters, limits and formatters, and the goimports local
  prefix from the ``module`` line of ``go.mod``.
- ``.shellcheckrc`` with the SHELLCHECK.* directives.
- ``.editorconfig`` with the SHFMT.* shell section. An existing ``.editorconfig`` is kept; init
  reports each SHFMT.* value its sections do not give ``.sh`` and ``.bash`` files.
"""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

from guardrails.init.native_config import HEADER, load_data
from guardrails.structure.inventory import InventoryError, repo_paths

SHELL_SUFFIXES = (".sh", ".bash")
OXLINT_FILES = (".oxlintrc.json", "oxlint.config.ts")
ESLINT_FILES = (
    "eslint.config.js", "eslint.config.mjs", "eslint.config.cjs", "eslint.config.ts",
    "eslint.config.mts", "eslint.config.cts", ".eslintrc", ".eslintrc.js", ".eslintrc.cjs",
    ".eslintrc.json", ".eslintrc.yml", ".eslintrc.yaml", "biome.json", "biome.jsonc",
)
GOLANGCI_FILES = (".golangci.yml", ".golangci.yaml", ".golangci.toml", ".golangci.json")
SHELLCHECK_FILES = (".shellcheckrc", "shellcheckrc")
MODULE = re.compile(r"^module\s+(\S+)", re.MULTILINE)
SECTION = re.compile(r"^\s*\[(.+)\]\s*$")
SETTING = re.compile(r"^\s*([A-Za-z0-9_]+)\s*=\s*(.*?)\s*$")


class ConfigError(ValueError):
    """A repository config file cannot be read as its tool reads it."""


def jsonc(text: str) -> object:
    """JSON with comments and trailing commas, as tsconfig allows."""
    out: list[str] = []
    index, length = 0, len(text)
    while index < length:
        char = text[index]
        if char == '"':
            end = index + 1
            while end < length and text[end] != '"':
                end += 2 if text[end] == "\\" else 1
            out.append(text[index : end + 1])
            index = end + 1
        elif text.startswith("//", index):
            newline = text.find("\n", index)
            index = length if newline < 0 else newline
        elif text.startswith("/*", index):
            close = text.find("*/", index + 2)
            if close < 0:
                raise ConfigError("unterminated block comment")
            index = close + 2
        else:
            out.append(char)
            index += 1
    try:
        return json.loads(re.sub(r",(\s*[}\]])", r"\1", "".join(out)))
    except json.JSONDecodeError as error:
        raise ConfigError(str(error)) from error


def extends_target(repo: Path, folder: Path, base: str) -> Path:
    root = folder / base if base.startswith((".", "/")) else repo / "node_modules" / base
    for candidate in (root, Path(f"{root}.json"), root / "tsconfig.json"):
        if candidate.is_file():
            return candidate
    raise ConfigError(f"extends {base!r} cannot be read; install the repository's dependencies")


def compiler_options(repo: Path, path: Path, seen: frozenset[Path] = frozenset()) -> dict[str, Any]:
    """compilerOptions after every extends, bases first, as tsc merges them."""
    if path in seen:
        raise ConfigError(f"extends cycle at {path.name}")
    try:
        data = jsonc(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ConfigError(str(error)) from error
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name} is not a JSON object")
    bases = data.get("extends") or []
    options: dict[str, Any] = {}
    for base in [bases] if isinstance(bases, str) else bases:
        target = extends_target(repo, path.parent, str(base))
        options.update(compiler_options(repo, target, seen | {path}))
    own = data.get("compilerOptions") or {}
    if not isinstance(own, dict):
        raise ConfigError(f"{path.name} compilerOptions is not an object")
    options.update(own)
    return options


def tsconfig_report(repo: Path, wanted: dict[str, Any]) -> list[str]:
    try:
        options = compiler_options(repo, repo / "tsconfig.json")
    except ConfigError as error:
        return [f"native: tsc settings not verified; tsconfig.json: {error}"]
    return [
        f"native: tsconfig.json lacks TSC.{key} (compilerOptions.{key}: {json.dumps(value)})"
        for key, value in wanted.items()
        if options.get(key) != value
    ]


def glob_pattern(glob: str) -> re.Pattern[str]:
    """An EditorConfig section glob; one without a slash matches the file name anywhere."""
    pattern, index = "", 0
    while index < len(glob):
        char = glob[index]
        if glob.startswith("**", index):
            pattern, index = pattern + ".*", index + 2
            continue
        if char == "{" and "}" in glob[index:]:
            close = glob.index("}", index)
            pattern += "(?:" + "|".join(map(re.escape, glob[index + 1 : close].split(","))) + ")"
            index = close + 1
            continue
        pattern += {"*": "[^/]*", "?": "[^/]"}.get(char, re.escape(char))
        index += 1
    prefix = "" if "/" in glob else "(?:.*/)?"
    return re.compile(prefix + pattern.lstrip("/"))


def editorconfig_values(text: str, name: str) -> dict[str, str]:
    values: dict[str, str] = {}
    applies = False
    for line in text.splitlines():
        header = SECTION.match(line)
        if header:
            applies = glob_pattern(header.group(1)).fullmatch(name) is not None
            continue
        setting = SETTING.match(line)
        if applies and setting:
            values[setting.group(1).lower()] = setting.group(2).lower()
    return values


def setting_text(value: object) -> str:
    return json.dumps(value) if isinstance(value, bool) else str(value)


def editorconfig_report(text: str, shfmt: dict[str, Any]) -> list[str]:
    report = []
    for key, value in shfmt["settings"].items():
        wanted = setting_text(value)
        if any(
            editorconfig_values(text, f"script{suffix}").get(key) != wanted
            for suffix in SHELL_SUFFIXES
        ):
            report.append(f"native: .editorconfig lacks SHFMT.{key} ({key} = {wanted} for shell files)")
    return report


def render_tsconfig(data: dict[str, Any]) -> str:
    return json.dumps({"compilerOptions": data["tsc"]["compilerOptions"]}, indent=2) + "\n"


def render_oxlint(data: dict[str, Any]) -> str:
    return json.dumps(data["oxlint"], indent=2) + "\n"


def render_golangci(data: dict[str, Any], module: str) -> str:
    config = json.loads(json.dumps(data["golangci"]))
    config["formatters"]["settings"] = {"goimports": {"local-prefixes": [module]}}
    return json.dumps(config, indent=2) + "\n"


def render_shellcheck(data: dict[str, Any]) -> str:
    rc = data["shellcheck"]
    lines = [HEADER.rstrip("\n"), *(f"enable={name}" for name in rc["enable"])]
    lines.extend(f"{key}={setting_text(value)}" for key, value in rc.items() if key != "enable")
    return "\n".join(lines) + "\n"


def render_editorconfig(data: dict[str, Any]) -> str:
    shfmt = data["shfmt"]
    lines = [HEADER.rstrip("\n"), "root = true", "", shfmt["section"]]
    lines.extend(f"{key} = {setting_text(value)}" for key, value in shfmt["settings"].items())
    return "\n".join(lines) + "\n"


def first_file(repo: Path, names: tuple[str, ...]) -> str | None:
    return next((name for name in names if (repo / name).is_file()), None)


def shell_scripts(repo: Path) -> bool:
    return any(PurePosixPath(path).suffix in SHELL_SUFFIXES for path in repo_paths(repo))


def typescript_configs(repo: Path, data: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
    planned: dict[str, str] = {}
    report: list[str] = []
    if (repo / "tsconfig.json").is_file():
        report.extend(tsconfig_report(repo, data["tsc"]["compilerOptions"]))
    else:
        planned["tsconfig.json"] = render_tsconfig(data)
    kept = first_file(repo, OXLINT_FILES + ESLINT_FILES)
    if kept:
        report.append(f"native: kept the existing lint configuration in {kept}")
    else:
        planned[".oxlintrc.json"] = render_oxlint(data)
    return planned, report


def go_configs(repo: Path, data: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
    kept = first_file(repo, GOLANGCI_FILES)
    if kept:
        return {}, [f"native: kept the existing golangci-lint configuration in {kept}"]
    module = MODULE.search((repo / "go.mod").read_text(encoding="utf-8"))
    if module is None:
        return {}, ["native: golangci-lint not proposed; go.mod declares no module path"]
    return {".golangci.json": render_golangci(data, module.group(1))}, []


def shell_configs(repo: Path, data: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
    planned: dict[str, str] = {}
    report: list[str] = []
    kept = first_file(repo, SHELLCHECK_FILES)
    if kept:
        report.append(f"native: kept the existing shellcheck configuration in {kept}")
    else:
        planned[".shellcheckrc"] = render_shellcheck(data)
    editorconfig = repo / ".editorconfig"
    if editorconfig.is_file():
        report.extend(editorconfig_report(editorconfig.read_text(encoding="utf-8"), data["shfmt"]))
    else:
        planned[".editorconfig"] = render_editorconfig(data)
    return planned, report


def planned_configs(repo: Path) -> tuple[dict[str, str], list[str]]:
    """Files to write (path -> text) and report lines, for every pack the repo holds."""
    data = load_data()
    planned: dict[str, str] = {}
    report: list[str] = []
    try:
        shell = shell_scripts(repo)
    except InventoryError as error:
        shell = False
        report.append(f"native: shell scripts not listed; {error}")
    selected = (
        (typescript_configs, any((repo / name).is_file() for name in ("package.json", "tsconfig.json"))),
        (go_configs, (repo / "go.mod").is_file()),
        (shell_configs, shell),
    )
    for configs, present in selected:
        if present:
            files, lines = configs(repo, data)
            planned.update(files)
            report.extend(lines)
    return planned, report
