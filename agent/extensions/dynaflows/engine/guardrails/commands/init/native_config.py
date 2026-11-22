from __future__ import annotations

import json
import re
import tomllib
from importlib import resources
from pathlib import Path
from typing import Any, TypeGuard

from guardrails.context.config.profile import PLACEHOLDER

DATA_FILE = "native-tools.json"
HEADER = "# Proposed native-tool configuration; edit with owner approval.\n"
REQUIRES = re.compile(r">=\s*(\d+)\.(\d+)")
SKIPPED = frozenset({"tests", "test", "docs", "build", "dist", "node_modules"})


def load_data() -> dict[str, Any]:
    data = (
        resources
        .files("guardrails.data")
        .joinpath(DATA_FILE)
        .read_text(encoding="utf-8")
    )
    loaded: dict[str, Any] = json.loads(data)
    return loaded


def pyproject(repo: Path) -> dict[str, Any]:
    path = repo / "pyproject.toml"
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def python_version(project: dict[str, Any]) -> tuple[int, int] | None:
    requires = project.get("project", {}).get("requires-python")
    match = REQUIRES.search(requires) if isinstance(requires, str) else None
    return (int(match.group(1)), int(match.group(2))) if match else None


def packages(repo: Path) -> tuple[list[str], list[str]]:
    names: list[str] = []
    roots: list[str] = []
    for root_name in (".", "src"):
        root = repo / root_name
        if not root.is_dir():
            continue
        found = sorted(
            child.name
            for child in root.iterdir()
            if child.is_dir()
            and (child / "__init__.py").is_file()
            and child.name not in SKIPPED
            and not child.name.startswith(".")
        )
        if found:
            names.extend(found)
            roots.append(root_name)
    return names, roots


def toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    return json.dumps(value)


def toml_key(key: str) -> str:
    return key if re.fullmatch(r"[A-Za-z0-9_-]+", key) else json.dumps(key)


def render_ruff(
    data: dict[str, Any], version: tuple[int, int], first_party: list[str]
) -> str:
    ruff = data["ruff"]
    lines = [HEADER.rstrip("\n"), f'target-version = "py{version[0]}{version[1]}"']
    lines.extend(
        f"{toml_key(key)} = {toml_value(value)}" for key, value in ruff["root"].items()
    )
    for table, values in ruff.items():
        if table == "root":
            continue
        lines.extend(["", f"[{table}]"])
        if table == "lint.isort":
            lines.append(f"known-first-party = {toml_value(first_party)}")
        lines.extend(
            f"{toml_key(key)} = {toml_value(value)}" for key, value in values.items()
        )
    return "\n".join(lines) + "\n"


def ini_value(value: object) -> str:
    if isinstance(value, bool):
        return "True" if value else "False"
    return str(value)


def render_mypy(
    data: dict[str, Any], version: tuple[int, int], names: list[str], roots: list[str]
) -> str:
    lines = [
        HEADER.rstrip("\n"),
        "[mypy]",
        f"python_version = {version[0]}.{version[1]}",
    ]
    lines.extend(f"{key} = {ini_value(value)}" for key, value in data["mypy"].items())
    lines.append(f"packages = {', '.join(names)}")
    lines.append(f"mypy_path = {', '.join(roots)}")
    return "\n".join(lines) + "\n"


def render_importlinter(package: str, tiers: list[str]) -> str:
    layers = "\n".join(f"    {tier}" for tier in reversed(tiers))
    return (
        f"{HEADER}[importlinter]\nroot_package = {package}\n\n"
        "[importlinter:contract:structure-tiers]\n"
        "name = structure.tiers: import only your own or a lower tier\n"
        f"type = layers\ncontainers =\n    {package}\nlayers =\n{layers}\n"
    )


def ini_has_section(path: Path, section: str) -> bool:
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8", errors="replace")
    return re.search(rf"^\[{re.escape(section)}\]", text, re.MULTILINE) is not None


def existing(repo: Path, project: dict[str, Any], tool: str) -> str | None:
    files = {
        "ruff": ("ruff.toml", ".ruff.toml"),
        "mypy": ("mypy.ini", ".mypy.ini"),
        "importlinter": (".importlinter",),
    }[tool]
    for name in files:
        if (repo / name).is_file():
            return name
    if tool in project.get("tool", {}):
        return f"pyproject.toml [tool.{tool}]"
    if tool == "mypy" and ini_has_section(repo / "setup.cfg", "mypy"):
        return "setup.cfg [mypy]"
    return None


def real_text(value: object) -> TypeGuard[str]:
    return (
        isinstance(value, str) and bool(value.strip()) and not PLACEHOLDER.search(value)
    )


def planned_configs(
    repo: Path, profile: dict[str, Any]
) -> tuple[dict[str, str], list[str]]:
    project = pyproject(repo)
    data = load_data()
    version = python_version(project)
    names, roots = packages(repo)
    planned: dict[str, str] = {}
    report: list[str] = []
    for tool, path in (("ruff", "ruff.toml"), ("mypy", "mypy.ini")):
        kept = existing(repo, project, tool)
        if kept:
            report.append(f"native: kept the existing {tool} configuration in {kept}")
        elif version is None:
            report.append(
                f"native: {tool} not proposed; set project.requires-python first"
            )
        elif tool == "ruff":
            planned[path] = render_ruff(data, version, names)
        elif not names:
            report.append(
                "native: mypy not proposed; the repo has no package with __init__.py"
            )
        else:
            planned[path] = render_mypy(data, version, names, roots)
    structure: dict[str, Any] = (
        profile["structure"] if isinstance(profile.get("structure"), dict) else {}
    )
    direction = structure.get("import_direction")
    package = direction.get("package") if isinstance(direction, dict) else None
    tiers: list[Any] = (
        structure["tiers"] if isinstance(structure.get("tiers"), list) else []
    )
    kept = existing(repo, project, "importlinter")
    if kept:
        report.append(
            f"native: kept the existing import-linter configuration in {kept}"
        )
    elif (
        real_text(package)
        and len(tiers) >= 2
        and all(real_text(tier) for tier in tiers)
    ):
        planned[".importlinter"] = render_importlinter(package, tiers)
    else:
        report.append(
            "native: import-linter not proposed; record structure.tiers and "
            "structure.import_direction.package, then run init again"
        )
    return planned, report
