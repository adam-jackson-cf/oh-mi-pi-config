"""Toolchain parity: interpreter pins, installer extras, documented installs and locks.

``omp-guardrails toolchain-parity`` takes no arguments. Every section runs only when
``guardrails.toolchain_parity`` configures it; a profile that configures none is itself a
finding, so an unconfigured check never passes silently. The dependency audit
(``SECURITY.dependency-audit``) is not here: the repository's own checker owns it, and the
harness never runs a copy (see the catalogue row's contract).

Settings (``guardrails.toolchain_parity``):

- ``python_pins``: pin files (e.g. ``.python-version``) that must be byte-equal and match the
  running interpreter (``GATE.local-ci-parity``: local and CI run one interpreter).
- ``uv``: ``project`` (pyproject path), ``lock`` (``uv.lock`` path), ``lock_projects`` (pyprojects
  whose requirements and own version must match the lock; default ``[project]``),
  ``installer_sources`` (repo globs scanned for ``uv sync`` lines; Makefile recipes also name make
  targets), ``guidance`` (Markdown files whose shell blocks document installs) and
  ``exact_uv_pin`` (require one ``uv==X.Y.Z`` dependency equal to the locked uv).
  Rules: ``PARITY.dependency-inventory``, ``PARITY.install-guidance``, ``PARITY.lock-version``.
- ``prettier``: ``version`` (exact pin), ``package_json``, ``package_lock`` and ``npx_sources``
  (globs where an ``npx prettier`` call would bypass the lock). Rule: ``PRETTIER.version``.
"""

from __future__ import annotations

import json
import platform
import re
import shlex
from collections.abc import Mapping
from pathlib import Path, PurePosixPath

import tomllib

from omp_guardrails.catalogue.versions import (
    VersionError,
    canonical_name,
    parse_requirement,
)
from omp_guardrails.config.profile import PROFILE_PATH
from omp_guardrails.findings import Finding
from omp_guardrails.structure.inventory import (
    InventoryError,
    as_list,
    matches_any,
    repo_paths,
    settings,
)

INVENTORY = "PARITY.dependency-inventory"
GUIDANCE = "PARITY.install-guidance"
LOCK_VERSION = "PARITY.lock-version"
PRETTIER = "PRETTIER.version"
LOCAL_CI = "GATE.local-ci-parity"
UV_SYNC = re.compile(r"\buv\s+sync\b")
MAKE_TARGET = re.compile(r"^([A-Za-z0-9_.-]+)\s*:(?!=)")


def option_values(tokens: list[str], option: str) -> set[str]:
    values = {token.split("=", 1)[1] for token in tokens if token.startswith(option + "=")}
    values.update(tokens[i + 1] for i, token in enumerate(tokens[:-1]) if token == option)
    return values


def project_dir(path: str) -> str:
    return PurePosixPath(path.strip() or ".").as_posix()


def sync_signature(command: str) -> tuple[str, frozenset[str], frozenset[str], bool]:
    """Project, selected extras (``*`` for all), excluded extras and lock enforcement."""
    tokens = shlex.split(command, comments=True)
    extras = option_values(tokens, "--extra")
    if "--all-extras" in tokens:
        extras.add("*")
    return (
        project_dir(next(iter(option_values(tokens, "--project")), ".")),
        frozenset(extras),
        frozenset(option_values(tokens, "--no-extra")),
        bool({"--locked", "--frozen"}.intersection(tokens)),
    )


def joined_lines(text: str) -> list[tuple[int, str]]:
    """Lines with backslash continuations joined, numbered by their first physical line."""
    lines, pending, start = [], "", 0
    for number, line in enumerate(text.splitlines(), 1):
        if not pending:
            start = number
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        lines.append((start, pending + line))
        pending = ""
    if pending:
        lines.append((start, pending))
    return lines


def make_targets(text: str) -> dict[str, str]:
    """Each Makefile target whose recipe runs ``uv sync``, mapped to that command."""
    targets, current = {}, None
    for _, line in joined_lines(text):
        header = MAKE_TARGET.match(line)
        if header and not line.startswith("\t"):
            current = header.group(1)
        elif current and line.startswith("\t") and UV_SYNC.search(line):
            targets.setdefault(current, line.strip().lstrip("@-+").strip())
    return targets


def installer_commands(sources: Mapping[str, str]) -> dict[str, str]:
    installers = {}
    for path, text in sources.items():
        for number, line in joined_lines(text):
            if UV_SYNC.search(line) and not line.lstrip().startswith("#"):
                installers[f"{path}:{number}"] = line.strip().lstrip("@-+").strip()
    return installers


def dependency_inventory_findings(
    project_path: str, project_toml: str, installers: Mapping[str, str]
) -> list[Finding]:
    declared = set(tomllib.loads(project_toml)["project"].get("optional-dependencies", {}))
    own = project_dir(str(PurePosixPath(project_path).parent))
    findings = []
    for location, command in installers.items():
        project, extras, excluded, _ = sync_signature(command)
        if project != own:
            continue
        selected = declared if "*" in extras else set(extras)
        unknown = set(extras) - declared - {"*"}
        if selected - excluded != declared or unknown or excluded - declared:
            findings.append(
                Finding(
                    INVENTORY,
                    location,
                    "installer extras differ from the declared optional-dependency groups",
                    "select every declared extra (--all-extras) and drop unknown exclusions",
                )
            )
    return findings


def guidance_commands(text: str) -> list[str]:
    commands: list[str] = []
    for match in re.finditer(r"```(?:sh|bash|shell|console)\s*\n(.*?)```", text, re.DOTALL):
        commands.extend(line for _, line in joined_lines(match.group(1)))
    return commands


def documented_install(command: str, targets: Mapping[str, str]) -> str | None:
    """The install a documented line performs, ``""`` for an unknown setup target, else None."""
    tokens = shlex.split(command.lstrip("$ "), comments=True)
    if "make" in tokens:
        target = tokens[tokens.index("make") + 1 :]
        if not target:
            return None
        if target[0] in targets:
            return targets[target[0]]
        return "" if ("setup" in target[0] or "install" in target[0]) else None
    return command if UV_SYNC.search(command) else None


def install_guidance_findings(
    guidance: Mapping[str, str], installers: Mapping[str, str], targets: Mapping[str, str]
) -> list[Finding]:
    expected = {sync_signature(command) for command in installers.values()}
    findings = []
    for path, text in guidance.items():
        for command in guidance_commands(text):
            install = documented_install(command, targets)
            if install is None:
                continue
            if not install or sync_signature(install) not in expected:
                findings.append(
                    Finding(
                        GUIDANCE,
                        path,
                        f"documented install {command.strip()!r} differs from the maintained "
                        "locked installer",
                        "document the maintained make target or the same project, extras and "
                        "--locked/--frozen selection",
                    )
                )
    return findings


def project_requirements(project_toml: str) -> list[str]:
    project = tomllib.loads(project_toml)["project"]
    requirements = list(project.get("dependencies", []))
    for group in project.get("optional-dependencies", {}).values():
        requirements.extend(group)
    if project.get("version"):
        requirements.append(f"{project['name']}=={project['version']}")
    return requirements


def locked_versions(lock_toml: str) -> dict[str, set[str]]:
    packages: dict[str, set[str]] = {}
    for package in tomllib.loads(lock_toml).get("package", []):
        if package.get("version"):
            packages.setdefault(canonical_name(package["name"]), set()).add(package["version"])
    return packages


def lock_version_findings(
    projects: Mapping[str, str], lock_path: str, lock_toml: str
) -> list[Finding]:
    packages = locked_versions(lock_toml)
    findings, seen = [], set()
    for path, text in projects.items():
        for raw in project_requirements(text):
            if raw in seen:
                continue
            seen.add(raw)
            requirement = parse_requirement(raw)
            versions = packages.get(requirement.name, set())
            if not versions or not all(requirement.admits(version) for version in versions):
                findings.append(
                    Finding(
                        LOCK_VERSION,
                        lock_path,
                        f"{path} requirement {raw} differs from locked {sorted(versions)}",
                        "synchronise the project metadata and regenerate the lock",
                    )
                )
    return findings


def exact_uv_pin_findings(
    project_path: str, project_toml: str, lock_path: str, lock_toml: str
) -> list[Finding]:
    dependencies = tomllib.loads(project_toml)["project"].get("dependencies", [])
    declared = [item for item in dependencies if parse_requirement(item).name == "uv"]
    exact = None
    if len(declared) == 1:
        exact = re.fullmatch(r"uv==(\d+\.\d+\.\d+)", declared[0].strip())
    if exact is None:
        return [
            Finding(
                LOCK_VERSION,
                project_path,
                "[project].dependencies must declare exactly one exact uv==X.Y.Z pin",
                "pin uv exactly so local and CI resolve with one uv",
            )
        ]
    locked = sorted(locked_versions(lock_toml).get("uv", set()))
    if locked != [exact.group(1)]:
        return [
            Finding(
                LOCK_VERSION,
                lock_path,
                f"locked uv {locked} differs from the declared uv {exact.group(1)}",
                "regenerate the lock after changing the uv pin",
            )
        ]
    return []


def python_pin_findings(pins: Mapping[str, bytes | None], interpreter: str) -> list[Finding]:
    fix = "keep every pin file byte-equal and install the pinned interpreter"
    findings = [
        Finding(LOCAL_CI, path, "interpreter pin file is missing", fix)
        for path, value in pins.items()
        if value is None
    ]
    present = {path: value for path, value in pins.items() if value is not None}
    if len(set(present.values())) > 1:
        findings.append(Finding(LOCAL_CI, ", ".join(present), "interpreter pin files differ", fix))
    for path, value in present.items():
        pinned = value.decode(errors="replace").strip()
        if interpreter != pinned:
            findings.append(
                Finding(LOCAL_CI, path, f"running Python {interpreter} differs from {pinned}", fix)
            )
            break
    return findings


def prettier_findings(options: dict, sources: Mapping[str, str]) -> list[Finding]:
    version = options.get("version")
    package_json = options.get("package_json", "package.json")
    package_lock = options.get("package_lock", "package-lock.json")
    findings = []
    try:
        manifest = json.loads(sources.get(package_json) or "{}")
        lock = json.loads(sources.get(package_lock) or "{}")
        packages = lock.get("packages", {})
        locked = packages.get("node_modules/prettier", {})
        valid = (
            manifest.get("devDependencies", {}).get("prettier") == version
            and packages.get("", {}).get("devDependencies", {}).get("prettier") == version
            and locked.get("version") == version
            and bool(locked.get("integrity"))
            and bool(locked.get("resolved"))
        )
    except (ValueError, AttributeError):
        valid = False
    if not valid:
        findings.append(
            Finding(
                PRETTIER,
                package_json,
                f"Prettier is not exactly pinned and locked at {version}",
                f"pin prettier {version} in devDependencies and commit its npm lock record",
            )
        )
    for path, text in sources.items():
        if path not in {package_json, package_lock} and re.search(
            r"\bnpx\b[^\n]*\bprettier(?:@\S+)?", text
        ):
            findings.append(
                Finding(
                    PRETTIER,
                    path,
                    "npx prettier can fetch a formatter outside the locked toolchain",
                    "call node_modules/.bin/prettier installed by npm ci",
                )
            )
    return findings


def read_text(repo: Path, path: str) -> str:
    return (repo / path).read_text(encoding="utf-8")


def globbed_sources(repo: Path, inventory: list[str], patterns: object) -> dict[str, str]:
    selected = [path for path in inventory if matches_any(path, as_list(patterns))]
    return {path: read_text(repo, path) for path in selected if (repo / path).is_file()}


def uv_findings(repo: Path, options: dict, inventory: list[str]) -> list[Finding]:
    project_path = options.get("project", "pyproject.toml")
    lock_path = options.get("lock", "uv.lock")
    project_toml, lock_toml = read_text(repo, project_path), read_text(repo, lock_path)
    sources = globbed_sources(repo, inventory, options.get("installer_sources"))
    installers = installer_commands(sources)
    targets = {}
    for path, text in sources.items():
        if PurePosixPath(path).name in {"Makefile", "makefile", "GNUmakefile"}:
            targets.update(make_targets(text))
    guidance = {path: read_text(repo, path) for path in as_list(options.get("guidance"))}
    projects = {
        path: read_text(repo, path) for path in as_list(options.get("lock_projects", project_path))
    }
    findings = [
        *dependency_inventory_findings(project_path, project_toml, installers),
        *install_guidance_findings(guidance, installers, targets),
        *lock_version_findings(projects, lock_path, lock_toml),
    ]
    if options.get("exact_uv_pin"):
        findings.extend(exact_uv_pin_findings(project_path, project_toml, lock_path, lock_toml))
    return findings


def static_findings(repo: Path, options: dict) -> list[Finding]:
    findings = []
    pins = as_list(options.get("python_pins"))
    if pins:
        values = {
            path: (repo / path).read_bytes() if (repo / path).is_file() else None for path in pins
        }
        findings.extend(python_pin_findings(values, platform.python_version()))
    uv = options.get("uv")
    prettier = options.get("prettier")
    inventory = repo_paths(repo) if uv or prettier else []
    if isinstance(uv, dict):
        findings.extend(uv_findings(repo, uv, inventory))
    if isinstance(prettier, dict):
        names = [prettier.get("package_json", "package.json")]
        names.append(prettier.get("package_lock", "package-lock.json"))
        sources = globbed_sources(repo, inventory, prettier.get("npx_sources"))
        sources.update({name: read_text(repo, name) for name in names if (repo / name).is_file()})
        findings.extend(prettier_findings(prettier, sources))
    return findings


def run(repo: Path, profile: dict, argv: list[str]) -> list[Finding]:
    if argv:
        return [
            Finding(
                LOCAL_CI,
                "omp-guardrails toolchain-parity",
                f"toolchain-parity takes no arguments, got {argv}",
                "run it without arguments; its sections are record settings",
            )
        ]
    options = settings(profile, "toolchain_parity")
    configured = ("python_pins", "uv", "prettier")
    if not any(options.get(key) for key in configured):
        return [
            Finding(
                LOCAL_CI,
                PROFILE_PATH,
                "guardrails.toolchain_parity configures no section",
                f"configure one of {', '.join(configured)} or drop this check from the gate",
            )
        ]
    try:
        return static_findings(repo, options)
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        InventoryError,
        VersionError,
    ) as error:
        return [
            Finding(
                LOCK_VERSION,
                PROFILE_PATH,
                f"toolchain inputs cannot be read: {' '.join(str(error).split())}",
                "repair the configured toolchain files or settings",
            )
        ]
