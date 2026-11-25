#!/usr/bin/env python3
"""Propose native tool settings without modifying the target checkout.

Usage: python3 scripts/propose_tool_configs.py --repo <dir> --out <dir>
       [--tiers a,b,c --import-package pkg] [--offline]
Usage: python3 scripts/propose_tool_configs.py --self-test
Exit codes: 0 success, 1 findings or self-test failure, 2 usage/input error.
Reads Git paths, repository configs and the tool rows of assets/guardrail-rules.json.
Writes proposals only under --out outside
the target checkout; self-test creates and removes temporary fixture repos.
Prints a JSON report to stdout, or 'self-test ok'. No network access is used.
"""

from __future__ import annotations

import argparse
import configparser
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path, PurePosixPath
from typing import Any

ASSETS = Path(__file__).resolve().parent.parent / "assets"
SKIPPED = {"tests", "test", "docs", "build", "dist", "node_modules"}
LINT_FILES = (
    ".oxlintrc.json", "oxlint.config.ts", "eslint.config.js", "eslint.config.mjs",
    "eslint.config.cjs", "eslint.config.ts", "eslint.config.mts", "eslint.config.cts",
    ".eslintrc", ".eslintrc.js", ".eslintrc.cjs", ".eslintrc.json", ".eslintrc.yml",
    ".eslintrc.yaml", "biome.json", "biome.jsonc",
)
GO_FILES = (".golangci.yml", ".golangci.yaml", ".golangci.toml", ".golangci.json")


def git(repo: Path, *args: str) -> str:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, env=environment, check=False,
    )
    if result.returncode:
        raise ValueError(result.stderr.decode(errors="replace").strip() or "Git command failed")
    return result.stdout.decode()


def catalogue_rows(prefix: str) -> dict[str, dict[str, Any]]:
    rules = json.loads((ASSETS / "guardrail-rules.json").read_text(encoding="utf-8"))["rules"]
    return {row["id"].removeprefix(prefix): row["contract"] for row in rules
            if row["kind"] == "tool" and row["id"].startswith(prefix)}


def values(rows: dict[str, dict[str, Any]], group: str) -> dict[str, Any]:
    return {key.removeprefix(group): row["value"] for key, row in rows.items()
            if key.startswith(group) and "value" in row}


def ruff_settings() -> dict[str, Any]:
    rows = catalogue_rows("RUFF.")
    return {
        "root": values(rows, "ROOT."),
        "lint": {"select": [row["selector"] for row in rows.values() if "selector" in row],
                 "ignore": rows["IGNORES"]["ignore"]},
        "lint.per-file-ignores": rows["IGNORES"]["per_file"],
        "lint.isort": values(rows, "ISORT."),
        "lint.mccabe": values(rows, "SETTING.mccabe."),
        "lint.pylint": values(rows, "LIMIT."),
        "format": values(rows, "FORMAT."),
    }


def golangci_settings() -> dict[str, Any]:
    rows = catalogue_rows("GOLANGCI.")
    settings: dict[str, Any] = {}
    for key, row in rows.items():
        linter = row.get("linter")
        if key.startswith("LIMIT.") and linter == "revive":
            rules = settings.setdefault("revive", {"rules": []})["rules"]
            rules.append({"name": row["setting"], "arguments": [row["value"]]})
        elif key.startswith("LIMIT."):
            settings.setdefault(linter, {})[row["setting"]] = row["value"]
        elif key == "RULE.forbidigo.exec-command":
            settings[linter] = {"forbid": row["value"]}
        elif key.startswith("RULE."):
            settings[linter] = row["value"]
    return {
        "version": rows["version"]["value"],
        "run": {"tests": rows["run.tests"]["value"]},
        "linters": {"default": rows["linters.default"]["value"],
                    "enable": [row["linter"] for key, row in rows.items()
                               if key.startswith("ENABLE.")],
                    "settings": settings},
        "formatters": {"enable": [row["formatter"] for key, row in rows.items()
                                  if key.startswith("FORMAT.")]},
    }


def load_data() -> dict[str, Any]:
    oxlint = catalogue_rows("OXLINT.")
    shfmt = catalogue_rows("SHFMT.")
    return {
        "ruff": ruff_settings(),
        "mypy": {key.removeprefix("SETTING."): value
                 for key, value in values(catalogue_rows("MYPY."), "").items()},
        "tsc": {"compilerOptions": values(catalogue_rows("TSC."), "")},
        "oxlint": {"plugins": oxlint["plugins"]["value"],
                   "categories": values(oxlint, "CATEGORY."),
                   "rules": {row["rule"]: row["value"] for row in oxlint.values() if "rule" in row}},
        "golangci": golangci_settings(),
        "shellcheck": values(catalogue_rows("SHELLCHECK."), ""),
        "shfmt": {"section": next(iter(shfmt.values()))["section"],
                  "settings": values(shfmt, "")},
    }


def packages(repo: Path) -> tuple[list[str], list[str]]:
    names, roots = [], []
    for root_name in (".", "src"):
        root = repo / root_name
        if not root.is_dir():
            continue
        found = sorted(
            child.name for child in root.iterdir()
            if child.is_dir() and (child / "__init__.py").is_file()
            and child.name not in SKIPPED and not child.name.startswith(".")
        )
        if found:
            names.extend(found)
            roots.append(root_name)
    return names, roots


def existing(repo: Path, project: dict[str, Any], tool: str) -> str | None:
    files = {
        "ruff": ("ruff.toml", ".ruff.toml"), "mypy": ("mypy.ini", ".mypy.ini"),
        "importlinter": (".importlinter",), "tsc": ("tsconfig.json",),
        "oxlint": LINT_FILES, "golangci-lint": GO_FILES,
        "shellcheck": (".shellcheckrc", "shellcheckrc"), "shfmt": (".editorconfig",),
    }[tool]
    for name in files:
        if (repo / name).is_file():
            return name
    if tool in project.get("tool", {}):
        return f"pyproject.toml [tool.{tool}]"
    setup = repo / "setup.cfg"
    if tool == "mypy" and setup.is_file():
        if re.search(r"^\[mypy\]", setup.read_text(encoding="utf-8"), re.MULTILINE):
            return "setup.cfg [mypy]"
    return None


def toml_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def render_ruff(data: dict[str, Any], version: str, names: list[str]) -> str:
    lines = [f'target-version = "py{version.replace(".", "")}"']
    for table, values in data["ruff"].items():
        if table != "root":
            lines.extend(["", f"[{table}]"])
        if table == "lint.isort":
            lines.append(f"known-first-party = {toml_value(names)}")
        for key, value in values.items():
            key = key if re.fullmatch(r"[A-Za-z0-9_-]+", key) else json.dumps(key)
            lines.append(f"{key} = {toml_value(value)}")
    return "\n".join(lines) + "\n"


def render_mypy(data: dict[str, Any], version: str, names: list[str], roots: list[str]) -> str:
    lines = ["[mypy]", f"python_version = {version}"]
    lines.extend(f"{key} = {value}" for key, value in data["mypy"].items())
    lines.extend([f"packages = {', '.join(names)}", f"mypy_path = {', '.join(roots)}"])
    return "\n".join(lines) + "\n"


def render_layers(package: str, tiers: list[str]) -> str:
    layers = "\n".join(f"    {tier}" for tier in reversed(tiers))
    return (
        f"[importlinter]\nroot_package = {package}\n\n"
        "[importlinter:contract:structure-tiers]\n"
        "name = structure.tiers: import only your own or a lower tier\n"
        f"type = layers\ncontainers =\n    {package}\nlayers =\n{layers}\n"
    )


def jsonc(text: str) -> Any:
    parts, index = [], 0
    while index < len(text):
        if text[index] == '"':
            end = index + 1
            while end < len(text) and text[end] != '"':
                end += 2 if text[end] == "\\" else 1
            parts.append(text[index:end + 1])
            index = end + 1
        elif text.startswith("//", index):
            end = text.find("\n", index)
            index = len(text) if end < 0 else end
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            if end < 0:
                raise ValueError("unterminated block comment")
            parts.append(" ")
            index = end + 2
        else:
            parts.append(text[index])
            index += 1
    return json.loads(re.sub(r",(\s*[}\]])", r"\1", "".join(parts)))


def compiler_options(repo: Path, path: Path, seen: frozenset = frozenset()) -> dict[str, Any]:
    path = path.resolve()
    if path in seen:
        raise ValueError(f"extends cycle at {path.name}")
    data = jsonc(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} is not a JSON object")
    bases = data.get("extends") or []
    bases = [bases] if isinstance(bases, str) else bases
    if not isinstance(bases, list) or not all(isinstance(base, str) for base in bases):
        raise ValueError("extends must be a string or list of strings")
    options = {}
    for base in bases:
        root = path.parent / base if base.startswith((".", "/")) else repo / "node_modules" / base
        target = next(
            (item for item in (root, Path(f"{root}.json"), root / "tsconfig.json")
             if item.is_file()), None,
        )
        if target is None:
            raise ValueError(f"extends {base!r} cannot be read; install repository dependencies")
        options.update(compiler_options(repo, target, seen | {path}))
    own = data.get("compilerOptions") or {}
    if not isinstance(own, dict):
        raise ValueError("compilerOptions is not an object")
    options.update(own)
    return options


def glob_pattern(pattern: str) -> re.Pattern[str]:
    result, index = "", 0
    while index < len(pattern):
        char = pattern[index]
        if pattern.startswith("**", index):
            result, index = result + ".*", index + 2
            continue
        if char == "{" and "}" in pattern[index:]:
            end = pattern.index("}", index)
            result += "(?:" + "|".join(map(re.escape, pattern[index + 1:end].split(","))) + ")"
            index = end + 1
            continue
        result += {"*": "[^/]*", "?": "[^/]"}.get(char, re.escape(char))
        index += 1
    return re.compile(("" if "/" in pattern else "(?:.*/)?") + result.lstrip("/"))


def editor_values(text: str, name: str) -> dict[str, str]:
    values, applies = {}, False
    for line in text.splitlines():
        section = re.match(r"^\s*\[(.+)\]\s*$", line)
        if section:
            applies = glob_pattern(section.group(1)).fullmatch(name) is not None
            continue
        setting = re.match(r"^\s*([A-Za-z0-9_]+)\s*=\s*(.*?)\s*$", line)
        if applies and setting:
            values[setting.group(1).lower()] = setting.group(2).lower()
    return values


def setting_text(value: Any) -> str:
    return json.dumps(value) if isinstance(value, bool) else str(value)


def to_yaml(value: Any, indent: int = 0) -> str:
    """Block YAML for JSON-shaped data; integers stay integers, unlike golangci-lint's JSON loader."""
    pad = " " * indent
    if isinstance(value, dict):
        if not value:
            return pad + "{}\n"
        lines = []
        for key, item in value.items():
            if isinstance(item, (dict, list)) and item:
                lines.append(f"{pad}{json.dumps(str(key))}:\n{to_yaml(item, indent + 2)}")
            else:
                lines.append(f"{pad}{json.dumps(str(key))}: {to_yaml(item).strip()}\n")
        return "".join(lines)
    if isinstance(value, list):
        if not value:
            return pad + "[]\n"
        lines = []
        for item in value:
            if isinstance(item, (dict, list)) and item:
                nested = to_yaml(item, indent + 2)
                lines.append(f"{pad}- {nested[indent + 2:]}")
            else:
                lines.append(f"{pad}- {to_yaml(item).strip()}\n")
        return "".join(lines)
    return json.dumps(value) + "\n"


def merged(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in overlay.items():
        result[key] = (merged(result[key], value)
                       if isinstance(value, dict) and isinstance(result.get(key), dict) else value)
    return result


def load_ruff(repo: Path, path: Path, seen: frozenset = frozenset()) -> dict[str, Any]:
    if path in seen or not path.resolve().is_relative_to(repo.resolve()):
        raise ValueError(f"ruff extend chain leaves the repository or loops at {path}")
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    table = data.get("tool", {}).get("ruff", {}) if path.name == "pyproject.toml" else data
    parent = table.get("extend")
    if not isinstance(parent, str):
        return table
    return merged(load_ruff(repo, path.parent / parent, seen | {path}), table)


def ruff_config(repo: Path, paths: list[str]) -> tuple[str, dict[str, Any], Path] | None:
    """The shallowest tracked Ruff entry config (not another's extend base), with its chain merged."""
    found, bases = [], set()
    for name in paths:
        path = PurePosixPath(name)
        if path.name not in ("ruff.toml", ".ruff.toml", "pyproject.toml") or not (repo / name).is_file():
            continue
        try:
            data = tomllib.loads((repo / name).read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError:
            continue
        table = data.get("tool", {}).get("ruff") if path.name == "pyproject.toml" else data
        if table is None:
            continue
        label = f"{name} [tool.ruff]" if path.name == "pyproject.toml" else name
        if isinstance(table.get("extend"), str):
            base = (repo / name).parent.joinpath(table["extend"]).resolve()
            bases.add(base)
            if base.is_relative_to(repo.resolve()):
                label += f" extending {base.relative_to(repo.resolve()).as_posix()}"
        found.append((len(path.parts), name, label))
    entries = [item for item in found if (repo / item[1]).resolve() not in bases]
    if not entries:
        return None
    _, name, label = min(entries)
    return label, load_ruff(repo, repo / name), (repo / name).parent


def ruff_covers(selector: str, code: str) -> bool:
    """Whether a Ruff selector enables a rule code: prefixes match inside one linter only."""
    letters = re.match(r"[A-Z]*", selector)[0]
    linter = re.match(r"[A-Z]*", code)[0]
    return code.startswith(selector) and (letters == linter or (letters == "PL" and linter.startswith("PL")))


def stricter_or_equal(key: str, current: Any, expected: Any) -> bool:
    if isinstance(expected, list) and isinstance(current, list):
        # Extra exclusions skip code the catalogue checks; extra selections only add checks.
        return set(current) <= set(expected) if "exclude" in key else set(expected) <= set(current)
    if key.startswith("max-") and isinstance(current, int) and isinstance(expected, int):
        return current <= expected
    return current == expected


def kept_mypy(repo: Path, project: dict[str, Any]) -> dict[str, str] | None:
    for name in ("mypy.ini", ".mypy.ini", "setup.cfg"):
        parser = configparser.ConfigParser()
        if (repo / name).is_file() and parser.read(repo / name) and parser.has_section("mypy"):
            return dict(parser["mypy"])
    table = project.get("tool", {}).get("mypy")
    return None if table is None else {key: setting_text(value) for key, value in table.items()}


def python_gaps(repo: Path, project: dict[str, Any], data: dict[str, Any],
                ruff: dict[str, Any] | None) -> list[tuple[str, str]]:
    """Catalogue settings a kept Ruff or mypy config lacks or sets less strictly."""
    gaps: list[tuple[str, str]] = []
    if ruff is not None:
        lint = ruff.get("lint", {})
        selected = {*lint.get("select", ruff.get("select", [])),
                    *lint.get("extend-select", ruff.get("extend-select", []))}
        gaps += [("ruff", f"ruff config lacks lint.select {code}") for code in data["ruff"]["lint"]["select"]
                 if "ALL" not in selected and not any(ruff_covers(item, code) for item in selected)]
        for table, values in data["ruff"].items():
            if table in ("lint", "lint.per-file-ignores"):
                continue
            current = ruff
            for part in ([] if table == "root" else table.split(".")):
                current = current.get(part, {})
            gaps += [("ruff", f"ruff config lacks {table}.{key} = {setting_text(value)}" if key not in current
                      else f"ruff config sets {table}.{key} = {setting_text(current[key])}, "
                      f"catalogue wants {setting_text(value)}")
                     for key, value in values.items()
                     if not stricter_or_equal(key, current.get(key), value)]
    mypy = kept_mypy(repo, project)
    if mypy is not None:
        gaps += [("mypy", f"mypy config lacks {key} = {setting_text(value)}")
                 for key, value in data["mypy"].items()
                 if str(mypy.get(key, "")).lower() != setting_text(value).lower()]
    return gaps


def plan(repo: Path, data: dict[str, Any], tiers: list[str], package: str | None) -> tuple:
    project_path = repo / "pyproject.toml"
    project = (
        tomllib.loads(project_path.read_text(encoding="utf-8"))
        if project_path.is_file() else {}
    )
    paths = [path for path in git(repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard").split("\0")
             if path]
    names, roots = packages(repo)
    ruff = ruff_config(repo, paths)
    ruff_dir = None if ruff is None else ruff[2].resolve().relative_to(repo.resolve()).as_posix()
    ruff_whole = ruff is not None and (ruff_dir == "." or not any(
        path.endswith(".py") and not path.startswith(f"{ruff_dir}/") for path in paths))
    requires = project.get("project", {}).get("requires-python", "")
    if not requires and ruff is not None and (ruff[2] / "pyproject.toml").is_file():
        nested = tomllib.loads((ruff[2] / "pyproject.toml").read_text(encoding="utf-8"))
        requires = nested.get("project", {}).get("requires-python", "")
    match = re.search(r">=\s*(\d+)\.(\d+)", requires) if isinstance(requires, str) else None
    version = f"{match[1]}.{match[2]}" if match else None
    report = {"proposed": [], "kept": [], "unproposed": []}
    files = {}

    def missing(tool: str, reason: str) -> None:
        report["unproposed"].append({"tool": tool, "reason": reason})

    def propose(tool: str, path: str, render: Any) -> None:
        if tool == "ruff" and ruff is not None and not ruff_whole:
            report["kept"].append({"tool": tool, "path": ruff[0]})
            missing(tool, f"kept config {ruff[0]} covers only {ruff_dir}/; the proposed root config covers the rest")
        kept = ruff[0] if tool == "ruff" and ruff_whole else existing(repo, project, tool)
        if kept:
            report["kept"].append({"tool": tool, "path": kept})
            return
        content, reason = render()
        if reason:
            missing(tool, reason)
        else:
            files[path] = content
            report["proposed"].append({"tool": tool, "path": path})

    python = project_path.is_file() or names or any(path.endswith(".py") for path in paths)
    if python:
        propose("ruff", "ruff.toml", lambda: (
            (render_ruff(data, version, names), None) if version else
            (None, "set project.requires-python first")))
        propose("mypy", "mypy.ini", lambda: (
            (None, "set project.requires-python first") if not version else
            (render_mypy(data, version, names, roots), None) if names else
            (None, "the repo has no package with __init__.py")))
        for tool, gap in python_gaps(repo, project, data, None if ruff is None else ruff[1]):
            missing(tool, gap)
    if tiers or package:
        propose("importlinter", ".importlinter", lambda: (
            (render_layers(package, tiers), None) if package and len(tiers) >= 2 else
            (None, "provide --tiers with at least two layers and --import-package")))
    if any((repo / name).is_file() for name in ("package.json", "tsconfig.json")):
        propose("tsc", "tsconfig.json", lambda: (
            json.dumps({"compilerOptions": data["tsc"]["compilerOptions"]}, indent=2) + "\n", None))
        if (repo / "tsconfig.json").is_file():
            try:
                options = compiler_options(repo, repo / "tsconfig.json")
                for key, value in data["tsc"]["compilerOptions"].items():
                    if options.get(key) != value:
                        missing("tsc", f"tsconfig.json lacks TSC.{key}: {json.dumps(value)}")
            except (ValueError, OSError) as error:
                missing("tsc", f"tsc settings not verified: {error}")
        propose("oxlint", ".oxlintrc.json", lambda: (
            json.dumps(data["oxlint"], indent=2) + "\n", None))
    if (repo / "go.mod").is_file():
        def go_config() -> tuple:
            module = re.search(r"^module\s+(\S+)", (repo / "go.mod").read_text(), re.MULTILINE)
            if module is None:
                return None, "go.mod declares no module path"
            config = json.loads(json.dumps(data["golangci"]))
            config["formatters"]["settings"] = {"goimports": {"local-prefixes": [module[1]]}}
            return to_yaml(config), None
        propose("golangci-lint", ".golangci.yml", go_config)
    if any(PurePosixPath(path).suffix in (".sh", ".bash") for path in paths):
        rc = [f"enable={name}" for name in data["shellcheck"]["enable"]]
        rc.extend(f"{key}={setting_text(value)}" for key, value in data["shellcheck"].items()
                  if key != "enable")
        propose("shellcheck", ".shellcheckrc", lambda: ("\n".join(rc) + "\n", None))
        shfmt = data["shfmt"]
        lines = ["root = true", "", shfmt["section"]]
        lines.extend(f"{key} = {setting_text(value)}" for key, value in shfmt["settings"].items())
        propose("shfmt", ".editorconfig", lambda: ("\n".join(lines) + "\n", None))
        if (repo / ".editorconfig").is_file():
            text = (repo / ".editorconfig").read_text(encoding="utf-8")
            for key, value in shfmt["settings"].items():
                if any(editor_values(text, f"script{suffix}").get(key) != setting_text(value)
                       for suffix in (".sh", ".bash")):
                    missing("shfmt", f".editorconfig lacks SHFMT.{key}: {setting_text(value)}")
    return files, report


def generate(repo: Path, out: Path, tiers: list[str], package: str | None) -> dict:
    repo, out = repo.resolve(), out.resolve()
    if not repo.is_dir():
        raise ValueError("--repo must be an existing Git checkout")
    checkout = Path(git(repo, "rev-parse", "--show-toplevel").strip()).resolve()
    if repo != checkout:
        raise ValueError("--repo must name the Git checkout root")
    if out.is_relative_to(checkout):
        raise ValueError("--out must resolve outside the target checkout")
    files, report = plan(repo, load_data(), tiers, package)
    for name in files:
        if (out / name).resolve().is_relative_to(checkout):
            raise ValueError("proposal destination resolves inside the target checkout")
    out.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (out / name).write_text(content, encoding="utf-8")
    return report


def assert_catalogue_consistency(data: dict[str, Any]) -> None:
    rules = json.loads((ASSETS / "guardrail-rules.json").read_text(encoding="utf-8"))["rules"]
    rows = {row["id"]: row["contract"] for row in rules}
    assert all(rule.split("/")[0] in data["oxlint"]["plugins"] for rule in data["oxlint"]["rules"])
    enabled = set(data["golangci"]["linters"]["enable"])
    for key, row in rows.items():
        if key.startswith(("GOLANGCI.LIMIT.", "GOLANGCI.RULE.")):
            assert row["linter"] in enabled, key
    for key in (
        "OXLINT.LIMIT.complexity", "OXLINT.LIMIT.max-params",
        "OXLINT.LIMIT.max-statements", "OXLINT.LIMIT.max-dependencies",
        "GOLANGCI.LIMIT.gocyclo.min-complexity", "GOLANGCI.LIMIT.funlen.statements",
        "GOLANGCI.LIMIT.revive.argument-limit",
    ):
        row = rows[key]
        limit = row["value"][1]["max"] if key.startswith("OXLINT.") else row["value"]
        mirrored = rows[row["mirrors"]]
        assert limit == mirrored.get("value", mirrored.get("max_non_stdlib_dependencies")), key


def self_test() -> None:
    assert_catalogue_consistency(load_data())
    root = Path(tempfile.mkdtemp(prefix="tool-configs-test-"))
    try:
        repo, out = root / "repo", root / "out"
        repo.mkdir()
        git(repo, "init", "-q")
        (repo / "src/target").mkdir(parents=True)
        (repo / "src/target/__init__.py").write_text("")
        project = '[project]\nname = "target"\nrequires-python = ">=3.11"\n'
        (repo / "pyproject.toml").write_text(project)
        report = generate(repo, out, ["core", "cli"], "target")
        assert {item["tool"] for item in report["proposed"]} == {"ruff", "mypy", "importlinter"}
        ruff = tomllib.loads((out / "ruff.toml").read_text())
        assert ruff["target-version"] == "py311"
        assert ruff["lint"]["isort"]["known-first-party"] == ["target"]
        mypy = configparser.ConfigParser()
        mypy.read(out / "mypy.ini")
        assert mypy["mypy"]["python_version"] == "3.11"
        assert mypy["mypy"]["packages"] == "target"
        assert mypy["mypy"]["mypy_path"] == "src"
        assert "layers =\n    cli\n    core\n" in (out / ".importlinter").read_text()
        assert not (repo / "ruff.toml").exists()
        (repo / "pyproject.toml").write_text(project + "\n[tool.ruff]\nline-length = 100\n")
        report = generate(repo, root / "kept", [], None)
        assert {"tool": "ruff", "path": "pyproject.toml [tool.ruff]"} in report["kept"]
        reasons = [item["reason"] for item in report["unproposed"] if item["tool"] == "ruff"]
        assert "ruff config lacks lint.select C901" in reasons, reasons
        assert "ruff config lacks lint.mccabe.max-complexity = 10" in reasons, reasons
        (repo / "pyproject.toml").write_text(project + "\n[tool.ruff]\nfix = true\n")
        reasons = [item["reason"] for item in generate(repo, root / "kept2", [], None)["unproposed"]]
        assert "ruff config sets root.fix = true, catalogue wants false" in reasons, reasons
        assert not (root / "kept/ruff.toml").exists()
        assert ruff_covers("B", "B006") and not ruff_covers("B", "BLE001")
        assert ruff_covers("PL", "PLR0904") and ruff_covers("C", "C901") and not ruff_covers("C4", "C901")
        assert stricter_or_equal("exclude", [], [".build"]) and not stricter_or_equal("exclude", [".build", "src"], [".build"])
        assert stricter_or_equal("select", ["B", "C901"], ["B"])
        assert stricter_or_equal("max-complexity", 8, 10) and not stricter_or_equal("max-complexity", 12, 10)
        nested = root / "nested"
        (nested / "tools/app").mkdir(parents=True)
        (nested / "shared").mkdir()
        git(nested, "init", "-q")
        (nested / "tools/app/pyproject.toml").write_text(
            '[project]\nname = "app"\nrequires-python = ">=3.12"\n'
            '[tool.ruff]\nextend = "../../shared/ruff.toml"\n')
        (nested / "shared/ruff.toml").write_text('[lint]\nselect = ["B"]\n')
        (nested / "tools/app/main.py").write_text("pass\n")
        report = generate(nested, root / "nested-out", [], None)
        assert {"tool": "ruff", "path": "tools/app/pyproject.toml [tool.ruff] extending shared/ruff.toml"} in report["kept"], report
        assert {"tool": "ruff", "path": "ruff.toml"} not in report["proposed"], report
        reasons = [item["reason"] for item in report["unproposed"] if item["tool"] == "ruff"]
        assert "ruff config lacks lint.select BLE001" in reasons, reasons
        assert not any("requires-python" in reason for reason in reasons), reasons
        (nested / "scripts").mkdir()
        (nested / "scripts/tool.py").write_text("pass\n")
        report = generate(nested, root / "nested-root", [], None)
        assert {"tool": "ruff", "path": "ruff.toml"} in report["proposed"], report
        assert any("covers only tools/app/" in item["reason"] for item in report["unproposed"]), report
        (repo / "package.json").write_text("{}")
        (repo / "go.mod").write_text("module example.com/shop\n")
        (repo / "release.sh").write_text("#!/bin/sh\n")
        out = root / "languages"
        report = generate(repo, out, [], None)
        options = json.loads((out / "tsconfig.json").read_text())["compilerOptions"]
        assert options == load_data()["tsc"]["compilerOptions"]
        assert json.loads((out / ".oxlintrc.json").read_text()) == load_data()["oxlint"]
        go_text = (out / ".golangci.yml").read_text()
        assert '"local-prefixes":\n' in go_text and '- "example.com/shop"' in go_text, go_text
        assert re.search(r'"arguments":\n\s+- \d+\n', go_text), go_text
        assert not (out / ".golangci.json").exists()
        assert "enable=check-set-e-suppressed\n" in (out / ".shellcheckrc").read_text()
        assert "[*.{sh,bash}]\nindent_style = space\n" in (out / ".editorconfig").read_text()
        (repo / "tsconfig.json").write_text('{"extends":"./base.json"}')
        (repo / "base.json").write_text('{"compilerOptions":{"strict":true,},}')
        (repo / "eslint.config.js").write_text("export default [];\n")
        (repo / ".editorconfig").write_text("[*.{sh,bash}]\nindent_style = space\n")
        report = generate(repo, root / "existing", [], None)
        assert {"tool": "tsc", "path": "tsconfig.json"} in report["kept"]
        assert {"tool": "oxlint", "path": "eslint.config.js"} in report["kept"]
        assert not any("TSC.strict:" in item["reason"] for item in report["unproposed"])
        assert any("SHFMT.indent_size" in item["reason"] for item in report["unproposed"])
        (repo / "pyproject.toml").write_text('[project]\nname = "target"\n')
        (repo / "go.mod").write_text("go 1.24\n")
        report = generate(repo, root / "underivable", [], None)
        assert any(item["tool"] == "ruff" for item in report["unproposed"])
        assert any(item["tool"] == "golangci-lint" for item in report["unproposed"])
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--repo", str(repo),
             "--out", str(repo / "bad")], capture_output=True, text=True, check=False,
        )
        assert result.returncode == 2, result.stderr
        assert not (repo / "bad").exists()
        (root / "linked").symlink_to(repo, target_is_directory=True)
        try:
            generate(repo, root / "linked", [], None)
        except ValueError:
            pass
        else:
            raise AssertionError("inside-checkout symlink accepted")
    finally:
        shutil.rmtree(root)
    print("self-test ok")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--tiers", help="tier modules relative to --import-package, lowest first (core,cli)")
    parser.add_argument("--import-package")
    parser.add_argument("--offline", action="store_true", help="no-op; no network access is used")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        try:
            self_test()
            return 0
        except Exception as error:
            print(f"self-test failed: {error}", file=sys.stderr)
            return 1
    if args.repo is None or args.out is None:
        parser.error("--repo and --out are required")
    tiers = args.tiers.split(",") if args.tiers else []
    identifier = r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*"
    if args.tiers is not None and (len(tiers) < 2 or len(set(tiers)) != len(tiers)
                                   or not all(re.fullmatch(identifier, tier) for tier in tiers)):
        parser.error("--tiers requires at least two distinct module names relative to --import-package, lowest first")
    if args.import_package and not re.fullmatch(identifier, args.import_package):
        parser.error("--import-package must be a dotted Python package name")
    try:
        print(json.dumps(generate(args.repo, args.out, tiers, args.import_package), indent=2))
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
