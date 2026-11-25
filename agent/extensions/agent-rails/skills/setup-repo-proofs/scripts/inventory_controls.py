#!/usr/bin/env python3
"""Inventory existing repository verification controls without changing the checkout.

Usage:
    python3 scripts/inventory_controls.py --repo <dir> [--offline]
    python3 scripts/inventory_controls.py --self-test

Exit codes: 0 success, 1 findings or self-test failure, 2 usage error/non-Git work tree.
Reads Git metadata, working-tree files, adjacent convention assets, and GitHub APIs
unless --offline is set. Writes one JSON object to stdout, or self-test status.
Only --self-test writes files, in disposable repositories under tempfile.mkdtemp().
Requires Python 3.11 or newer; uses only the standard library.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from urllib.parse import quote, urlparse


ASSETS = Path(__file__).resolve().parent.parent / "assets" / "conventions"
MAKEFILES = ("GNUmakefile", "makefile", "Makefile")
MANIFESTS = {
    "package.json": ("javascript", (
        "package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", "yarn.lock",
        "bun.lock", "bun.lockb",
    )),
    "pyproject.toml": ("python", ("uv.lock", "poetry.lock", "pdm.lock", "pylock.toml")),
    "Pipfile": ("python", ("Pipfile.lock",)),
    "setup.py": ("python", ("uv.lock", "poetry.lock", "pylock.toml")),
    "setup.cfg": ("python", ("uv.lock", "poetry.lock", "pylock.toml")),
    "go.mod": ("go", ("go.sum",)),
    "go.work": ("go", ("go.work.sum",)),
    "Cargo.toml": ("rust", ("Cargo.lock",)),
    "Gemfile": ("ruby", ("Gemfile.lock",)),
    "composer.json": ("php", ("composer.lock",)),
    "mix.exs": ("elixir", ("mix.lock",)),
    "pubspec.yaml": ("dart", ("pubspec.lock",)),
    "Package.swift": ("swift", ("Package.resolved",)),
    "Podfile": ("cocoapods", ("Podfile.lock",)),
    "Cartfile": ("carthage", ("Cartfile.resolved",)),
    "packages.config": ("dotnet", ("packages.lock.json",)),
    "build.gradle": ("gradle", ("gradle.lockfile",)),
    "build.gradle.kts": ("gradle", ("gradle.lockfile",)),
    "pom.xml": ("maven", ()),
    "deno.json": ("deno", ("deno.lock",)),
    "deno.jsonc": ("deno", ("deno.lock",)),
    "flake.nix": ("nix", ("flake.lock",)),
    "environment.yml": ("conda", ("conda-lock.yml",)),
    "environment.yaml": ("conda", ("conda-lock.yml",)),
    "pixi.toml": ("conda", ("pixi.lock",)),
    "vcpkg.json": ("vcpkg", ()),
    "conanfile.txt": ("conan", ("conan.lock",)),
    "conanfile.py": ("conan", ("conan.lock",)),
    "MODULE.bazel": ("bazel", ("MODULE.bazel.lock",)),
    "DESCRIPTION": ("r", ("renv.lock",)),
    "Project.toml": ("julia", ("Manifest.toml",)),
    "shard.yml": ("crystal", ("shard.lock",)),
    "rebar.config": ("erlang", ("rebar.lock",)),
    "paket.dependencies": ("dotnet", ("paket.lock",)),
    "cpanfile": ("perl", ("cpanfile.snapshot",)),
}
LOCKS = {lock: ecosystem for ecosystem, locks in MANIFESTS.values() for lock in locks}
LOCKS.update({
    "requirements.lock": "python", "requirements-dev.lock": "python",
    "renv.lock": "r", "packages.lock.json": "dotnet", "yarn.lock": "javascript",
    "npm-shrinkwrap.json": "javascript", ".terraform.lock.hcl": "terraform",
})
TOOL_CONFIGS = (
    "ruff.toml", ".ruff.toml", "pyproject.toml", "setup.cfg", "tox.ini", "mypy.ini", ".mypy.ini",
    "pytest.ini", ".coveragerc", ".importlinter", "tsconfig.json", "tsconfig.*.json", "jsconfig.json",
    "eslint.config.*", ".eslintrc*", "biome.json", "biome.jsonc", ".oxlintrc.json", "oxlint.config.*",
    "vitest.config.*", "jest.config.*", ".prettierrc*", "prettier.config.*", ".prettierignore",
    ".golangci.yml", ".golangci.yaml", ".golangci.toml", ".golangci.json", ".shellcheckrc",
    "shellcheckrc", ".editorconfig", ".pre-commit-config.yaml", "lefthook.yml", "lefthook.yaml",
    ".gitleaks.toml", ".gitleaksignore", ".markdownlint*", "CODEOWNERS", "renovate.json",
    "dependabot.yml",
)
PYTHON_LOCK = "*requirements*.lock"
PYTHON_MANIFESTS = ("*requirements*.txt", "*requirements*.in")


class InventoryError(Exception):
    """An inventory command could not answer."""


def child_environment() -> dict[str, str]:
    return {
        key: value for key, value in os.environ.items()
        if not key.startswith("GIT_")
    }


def run(repo: Path, args: list[str]) -> str:
    try:
        result = subprocess.run(
            args, cwd=repo, env=child_environment(), capture_output=True,
            text=True, errors="replace", timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise InventoryError(f"{args[0]} failed: {error}") from error
    if result.returncode:
        detail = (result.stderr or result.stdout).strip() or "no output"
        raise InventoryError(f"{args[0]} exited {result.returncode}: {detail}")
    return result.stdout


def git(repo: Path, *args: str) -> str:
    return run(repo, ["git", *args])


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""


def github_origin(url: str) -> str | None:
    match = re.fullmatch(r"git@github\.com:([^/]+/[^/]+?)(?:\.git)?/?", url)
    if match:
        return match.group(1)
    parsed = urlparse(url)
    if parsed.scheme not in {"https", "http", "ssh", "git"}:
        return None
    if parsed.hostname != "github.com":
        return None
    path = parsed.path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return path if len(path.split("/")) == 2 else None


def git_details(repo: Path) -> dict:
    try:
        branch = git(repo, "symbolic-ref", "--quiet", "--short", "HEAD").strip()
    except InventoryError:
        branch = None
    try:
        origin_url = git(repo, "remote", "get-url", "origin").strip()
    except InventoryError:
        origin_url = None
    origin = github_origin(origin_url) if origin_url else None
    entries = iter(git(repo, "status", "--porcelain=v1", "-z").split("\0"))
    modified = untracked = 0
    for entry in entries:
        if not entry:
            continue
        if entry[:2] == "??":
            untracked += 1
        elif entry[:2] != "!!":
            modified += 1
            if "R" in entry[:2] or "C" in entry[:2]:
                next(entries, None)
    return {
        "toplevel": str(repo), "branch": branch,
        "status": {"tracked_modified": modified, "untracked": untracked},
        "github_repo": origin, "origin_url": origin_url,
    }


def dependency_files(files: list[str]) -> list[dict]:
    available = set(files)
    found = []
    for filename in files:
        path = Path(filename)
        name = path.name
        if (name in LOCKS or fnmatch.fnmatchcase(name, "pylock.*.toml")
                or fnmatch.fnmatchcase(name, PYTHON_LOCK)):
            found.append({
                "path": filename, "ecosystem": LOCKS.get(name, "python"), "kind": "lock",
            })
            continue
        spec = MANIFESTS.get(name)
        if any(fnmatch.fnmatchcase(name, pattern) for pattern in PYTHON_MANIFESTS):
            spec = ("python", (path.stem + ".lock", "uv.lock", "pylock.toml"))
        elif path.suffix in {".csproj", ".fsproj", ".vbproj"}:
            spec = ("dotnet", ("packages.lock.json",))
        elif path.suffix == ".gemspec":
            spec = ("ruby", ("Gemfile.lock",))
        if spec is None:
            continue
        ecosystem, locks = spec
        sibling = next(
            (str(path.parent / lock) for lock in locks if str(path.parent / lock) in available),
            None,
        )
        found.append({
            "path": filename, "ecosystem": ecosystem, "kind": "manifest",
            "sibling_lock": sibling,
        })
    return found


def make_targets(repo: Path) -> list[str]:
    for name in MAKEFILES:
        if (repo / name).is_file():
            targets = set()
            for line in read_text(repo / name).splitlines():
                match = re.match(r"^([^\s:#=]+(?:[ \t]+[^\s:#=]+)*)\s*:(?!=)", line)
                if match:
                    targets.update(t for t in match.group(1).split() if not t.startswith("."))
            return sorted(targets)
    return []


def hooks(repo: Path, tracked: list[str]) -> dict:
    configs = []
    managers = set()
    for filename in tracked:
        name = Path(filename).name
        manager = None
        if name in {".pre-commit-config.yaml", ".pre-commit-config.yml"}:
            manager = "pre-commit"
        elif re.fullmatch(r"\.?lefthook(?:-local)?\.(yml|yaml|toml|json)", name):
            manager = "lefthook"
        elif ".husky" in Path(filename).parts:
            manager = "husky"
        elif ".githooks" in Path(filename).parts:
            manager = ".githooks"
        if manager:
            configs.append(filename)
            managers.add(manager)
    try:
        hooks_path = git(repo, "config", "--get", "core.hooksPath").strip()
    except InventoryError:
        hooks_path = None
    active = Path(git(repo, "rev-parse", "--git-path", "hooks").strip())
    if not active.is_absolute():
        active = repo / active
    candidates = [active / "pre-push", repo / ".husky/pre-push", repo / ".githooks/pre-push"]
    candidates.extend(repo / item for item in tracked if Path(item).name == "pre-push")
    ids = []
    for filename in configs:
        if "pre-commit-config" not in filename:
            continue
        for line in read_text(repo / filename).splitlines():
            match = re.match(r"^\s*-\s*id:\s*['\"]?([^'\"\s#]+)", line)
            if match:
                ids.append(match.group(1))
    return {
        "core_hooks_path": hooks_path, "configs": configs, "managers": sorted(managers),
        "pre_commit_hooks": sorted(set(ids)),
        "pre_push_exists": any(path.is_file() for path in candidates),
    }


def workflow_jobs(workflow: str, text: str) -> list[dict]:
    jobs = []
    in_jobs = False
    indent = child = None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line[0].isspace():
            in_jobs = bool(re.match(r"^jobs:\s*(?:#.*)?$", line))
            indent = child = None
            continue
        if not in_jobs:
            continue
        width = len(line) - len(line.lstrip())
        key = re.match(r"^\s+['\"]?([A-Za-z0-9_-]+)['\"]?:\s*(?:#.*)?$", line)
        if key and (indent is None or width == indent):
            indent, child = width, None
            jobs.append({"workflow": workflow, "job_id": key.group(1), "name": key.group(1)})
            continue
        if not jobs or indent is None or width <= indent:
            continue
        child = width if child is None else child
        match = re.match(r"^\s+name:\s*(.+?)\s*$", line)
        if match and width == child:
            jobs[-1]["name"] = match.group(1).strip("'\"")
    return jobs


def pack_inventory(repo: Path, files: list[str], tracked: list[str]) -> tuple[dict, list]:
    languages = {}
    for path in sorted(ASSETS.glob("*.json")):
        extensions = json.loads(path.read_text(encoding="utf-8")).get("extensions")
        if not extensions:
            continue
        if path.stem == "typescript":
            scripts = {".js", ".jsx", ".mjs", ".cjs"}
            languages["typescript"] = sum(
                Path(name).suffix in set(extensions) - scripts for name in files)
            languages["javascript"] = sum(Path(name).suffix in scripts for name in files)
        else:
            languages[path.stem] = sum(Path(name).suffix in extensions for name in files)
    tooling = [
        {"path": name} for name in tracked
        if any(fnmatch.fnmatchcase(Path(name).name, pattern) for pattern in TOOL_CONFIGS)
    ]
    for entry in tooling:
        if Path(entry["path"]).name != "pyproject.toml":
            continue
        try:
            data = tomllib.loads(read_text(repo / entry["path"]))
            tools = data.get("tool", {})
            entry["sections"] = [
                f"tool.{name}" for name in ("ruff", "mypy", "pytest", "coverage") if name in tools
            ]
        except tomllib.TOMLDecodeError as error:
            entry["error"] = str(error)
    return languages, tooling


def agent_skills(files: list[str]) -> list[str]:
    return [name for name in files if Path(name).name == "SKILL.md" and "skills" in Path(name).parts]


def gh_json(repo: Path, args: list[str]) -> object:
    try:
        return json.loads(run(repo, ["gh", *args]))
    except ValueError as error:
        raise InventoryError("gh returned invalid JSON") from error


def required_checks(repo: Path, offline: bool) -> dict:
    if offline:
        return {"status": "unknown", "error": "skipped (--offline)", "checks": None}
    try:
        view = gh_json(repo, ["repo", "view", "--json", "nameWithOwner,defaultBranchRef"])
        if not isinstance(view, dict):
            raise InventoryError("gh returned no repository")
        slug = view.get("nameWithOwner")
        branch = (view.get("defaultBranchRef") or {}).get("name")
        if not isinstance(slug, str) or not isinstance(branch, str):
            raise InventoryError("gh returned no default branch")
        prefix = f"repos/{slug}"
        branch_path = quote(branch, safe="")
        checks = set()
        errors = []
        for endpoint in (
            f"{prefix}/branches/{branch_path}/protection",
            f"{prefix}/rules/branches/{branch_path}",
        ):
            try:
                data = gh_json(repo, ["api", endpoint])
                if endpoint.endswith("/protection"):
                    if not isinstance(data, dict):
                        raise InventoryError("branch protection response is not an object")
                    required = data.get("required_status_checks") or {}
                    checks.update(required.get("contexts") or [])
                    checks.update(item["context"] for item in required.get("checks") or [])
                else:
                    if not isinstance(data, list):
                        raise InventoryError("branch rules response is not a list")
                    for rule in data:
                        if rule.get("type") == "required_status_checks":
                            items = (rule.get("parameters") or {}).get("required_status_checks", [])
                            checks.update(item["context"] for item in items)
            except (InventoryError, KeyError, TypeError, AttributeError) as error:
                errors.append(f"{endpoint}: {error}")
        if errors:
            return {
                "status": "unknown", "error": "; ".join(errors), "checks": None,
                "observed_checks": sorted(checks), "default_branch": branch,
            }
        return {"status": "known", "checks": sorted(checks), "default_branch": branch}
    except (InventoryError, TypeError, AttributeError) as error:
        return {"status": "unknown", "error": str(error), "checks": None}


def inventory(repo: Path, offline: bool) -> dict:
    try:
        if git(repo, "rev-parse", "--is-inside-work-tree").strip() != "true":
            raise InventoryError("not a Git work tree")
        repo = Path(git(repo, "rev-parse", "--show-toplevel").strip()).resolve()
    except InventoryError as error:
        raise ValueError(f"--repo is not a Git work tree: {error}") from error
    files = sorted(set(filter(None, git(
        repo, "ls-files", "--cached", "--others", "--exclude-standard", "-z",
    ).split("\0"))))
    tracked = sorted(set(filter(None, git(repo, "ls-files", "--cached", "-z").split("\0"))))
    languages, tooling = pack_inventory(repo, files, tracked)
    jobs = []
    for filename in files:
        path = Path(filename)
        if path.parent.as_posix() == ".github/workflows" and path.suffix in {".yml", ".yaml"}:
            jobs.extend(workflow_jobs(filename, read_text(repo / filename)))
    return {
        "git": git_details(repo), "make_targets": make_targets(repo),
        "hooks": hooks(repo, tracked), "ci_jobs": jobs,
        "required_checks": required_checks(repo, offline),
        "locks_and_manifests": dependency_files(files),
        "tool_configs": tooling, "languages": languages, "agent_skills": agent_skills(files),
    }


def self_test() -> int:
    folder = Path(tempfile.mkdtemp(prefix="repo-controls-"))
    original = globals()["gh_json"]
    try:
        repo = folder / "repo"
        repo.mkdir()
        git(repo, "init", "--quiet")
        fixtures = {
            "Makefile": "verify lint:\n\t@true\n",
            ".github/workflows/ci.yml": "jobs:\n  quality:\n    name: Quality\n    steps: []\n",
            "package.json": "{}\n", "package-lock.json": "{}\n",
            "unlocked/requirements-dev.txt": "pytest\n",
            ".githooks/pre-push": "#!/bin/sh\nexit 0\n",
            ".pre-commit-config.yaml": "repos:\n  - hooks:\n      - id: local-check\n",
            "pyproject.toml": "[tool.ruff]\nline-length = 100\n[tool.pytest.ini_options]\n",
            "example.py": "pass\n",
            "README.md": "# Project\n", "pytest.ini": "[pytest]\n", ".coveragerc": "[run]\n",
            "tool/stub.js": "export {};\n",
            "toolchain/python-requirements.in": "ruff\n",
            "toolchain/python-requirements.lock": "ruff==0.1\n",
            ".agents/skills/testing/SKILL.md": "---\nname: testing\n---\n",
        }
        for filename, text in fixtures.items():
            path = repo / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        git(repo, "add", ".")
        git(repo, "config", "core.hooksPath", ".githooks")
        git(repo, "remote", "add", "origin", "git@github.com:example/project.git")
        result = inventory(repo, True)
        assert "verify" in result["make_targets"]
        assert result["ci_jobs"][0]["name"] == "Quality"
        dependencies = {item["path"]: item for item in result["locks_and_manifests"]}
        assert dependencies["package.json"]["sibling_lock"] == "package-lock.json"
        assert dependencies["package-lock.json"]["kind"] == "lock"
        assert dependencies["unlocked/requirements-dev.txt"]["sibling_lock"] is None
        assert result["hooks"]["pre_push_exists"]
        assert result["hooks"]["core_hooks_path"] == ".githooks"
        assert result["required_checks"]["status"] == "unknown"
        assert result["required_checks"]["checks"] is None
        assert "skipped" in result["required_checks"]["error"]
        assert result["git"]["github_repo"] == "example/project"
        assert result["git"]["origin_url"] == "git@github.com:example/project.git"
        assert result["languages"]["python"] == 1
        assert result["languages"]["javascript"] == 1 and result["languages"]["typescript"] == 0
        assert any("tool.ruff" in item.get("sections", []) for item in result["tool_configs"])
        configs = {item["path"] for item in result["tool_configs"]}
        assert {"pytest.ini", ".coveragerc"} <= configs and "README.md" not in configs, configs
        assert dependencies["toolchain/python-requirements.lock"]["kind"] == "lock"
        assert (dependencies["toolchain/python-requirements.in"]["sibling_lock"]
                == "toolchain/python-requirements.lock")
        assert result["agent_skills"] == [".agents/skills/testing/SKILL.md"]
        assert github_origin("https://github.com/example/project.git") == "example/project"
        assert github_origin("ssh://git@github.com/example/project.git") == "example/project"
        assert github_origin("https://elsewhere.test/example/project") is None
        (repo / ".githooks/pre-push").unlink()
        assert not inventory(repo, True)["hooks"]["pre_push_exists"]

        def failed_query(target: Path, args: list[str]) -> object:
            if args[0] == "repo":
                return {"nameWithOwner": "example/project", "defaultBranchRef": {"name": "main"}}
            raise InventoryError("permission denied")

        globals()["gh_json"] = failed_query
        failed = required_checks(repo, False)
        assert failed["status"] == "unknown" and failed["checks"] is None
        assert "permission denied" in failed["error"]
        empty = folder / "not-git"
        empty.mkdir()
        process = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--repo", str(empty), "--offline"],
            capture_output=True, text=True, check=False,
        )
        assert process.returncode == 2
        print("self-test ok")
        return 0
    except Exception as error:
        print(f"self-test failed: {error}", file=sys.stderr)
        return 1
    finally:
        globals()["gh_json"] = original
        shutil.rmtree(folder)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if args.repo is None:
        parser.error("--repo is required unless --self-test is used")
    try:
        result = inventory(args.repo.resolve(), args.offline)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    except (InventoryError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
