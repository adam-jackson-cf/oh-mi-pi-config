from __future__ import annotations

from collections.abc import Set
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

from guardrails.context.config import house_style
from guardrails.foundation import conventions
from guardrails.foundation.findings import Finding
from guardrails.foundation.inventory import GLOB_CHARACTERS, as_list
from guardrails.toolkit.python_rules.scope import (
    PythonSettings,
    finding,
    in_roots,
    matched_root,
    root_depth,
    segments,
)


def is_label(prefix: str, config: PythonSettings) -> bool:
    return any(
        len(segments(pattern)) == len(segments(prefix)) and root_depth(prefix, pattern)
        for pattern in config.paths("data_label_dirs")
    )


def below_root_dirs(relpath: str, depth: int) -> list[tuple[str, str]]:
    parts = segments(relpath)
    return [
        (parts[index], "/".join(parts[: index + 1]))
        for index in range(depth, len(parts) - 1)
    ]


def structure_findings(
    relpath: str, config: PythonSettings, existing_segments: Set[str] = frozenset()
) -> list[Finding]:
    if relpath in existing_segments:
        return []
    depth = matched_root(relpath, config.paths("maintained_roots"))
    if depth is None:
        return [
            finding(
                "PYQ100",
                relpath,
                None,
                "Python file is outside the maintained roots",
                "move it into an owned root or add its root to guardrails.python.maintained_roots",
            )
        ]
    if relpath in config.paths("test_root_files"):
        return []
    return [
        *script_findings(relpath, config),
        *test_group_findings(relpath, config),
        *skill_findings(relpath, config, existing_segments),
        *package_dir_findings(relpath, depth, config, existing_segments),
        *module_name_findings(relpath, config),
        *generic_name_findings(relpath, depth, config, existing_segments),
    ]


def script_findings(relpath: str, config: PythonSettings) -> list[Finding]:
    if not in_roots(relpath, config.paths("script_roots")):
        return []
    if relpath in config.paths("script_name_exceptions"):
        return []
    name = PurePosixPath(relpath).name
    approved = name in as_list(config.raw.get("script_names"))
    if approved and house_style.matches_casing(
        PurePosixPath(name).stem, house_style.load_house_style()["asset_id"]["file"]
    ):
        return []
    return [
        finding(
            "PYQ101",
            relpath,
            None,
            f"command '{name}' is not an approved kebab-case command",
            "rename it to kebab-case and add its basename to guardrails.python.script_names",
        )
    ]


def test_group_findings(relpath: str, config: PythonSettings) -> list[Finding]:
    if not in_roots(relpath, config.paths("test_roots")):
        return []
    for patterns in config.mapping("test_groups").values():
        for pattern in as_list(patterns):
            same_depth = "**" in pattern or relpath.count("/") == pattern.count("/")
            if same_depth and fnmatchcase(relpath, pattern):
                return []
    return [
        finding(
            "PYQ102",
            relpath,
            None,
            "test path belongs to no canonical test group",
            "move it into a group or map its pattern in guardrails.python.test_groups",
        )
    ]


def skill_name_finding(relpath: str, skill: str) -> list[Finding]:
    if house_style.matches_casing(
        skill, house_style.load_house_style()["asset_id"]["directory"]
    ):
        return []
    return [
        finding(
            "PYQ106",
            relpath,
            None,
            f"skill directory '{skill}' is not kebab-case",
            "rename the skill directory to kebab-case",
        )
    ]


def skill_findings(
    relpath: str, config: PythonSettings, existing_segments: Set[str] = frozenset()
) -> list[Finding]:
    for root in config.paths("skill_roots"):
        depth = root_depth(relpath, root)
        if depth is not None and len(segments(relpath)) > depth + 1:
            if "/".join(segments(relpath)[: depth + 1]) in existing_segments:
                return []
            return skill_name_finding(relpath, segments(relpath)[depth])
    return []


def near_selection(relpath: str, selected: list[str]) -> bool:
    return any(
        root_depth(relpath, path) is not None or root_depth(path, relpath) is not None
        for path in selected
    )


def skill_directory_findings(
    repo: Path,
    config: PythonSettings,
    selected: list[str],
    existing_segments: Set[str] = frozenset(),
) -> list[Finding]:
    found: list[Finding] = []
    for root in config.paths("skill_roots"):
        if GLOB_CHARACTERS & set(root):
            continue
        skill_root = repo / root
        if not skill_root.is_dir():
            continue
        for directory in sorted(
            child for child in skill_root.iterdir() if child.is_dir()
        ):
            relpath = f"{root}/{directory.name}"
            if relpath in existing_segments:
                continue
            if not selected or near_selection(relpath, selected):
                found.extend(skill_name_finding(relpath, directory.name))
    return found


def package_dir_findings(
    relpath: str,
    depth: int,
    config: PythonSettings,
    existing_segments: Set[str] = frozenset(),
) -> list[Finding]:
    if in_roots(relpath, config.paths("skill_roots")):
        return []
    ignored = set(as_list(config.raw.get("non_package_dirs")))
    return [
        finding(
            "PYQ105",
            relpath,
            None,
            f"Python package directory '{part}' is not snake_case",
            "rename the directory to snake_case",
        )
        for part, prefix in below_root_dirs(relpath, depth)
        if part not in ignored
        and prefix not in existing_segments
        and not is_label(prefix, config)
        and not house_style.matches_casing(
            part, conventions.load_pack("python")["directory"]
        )
    ]


def module_name_findings(relpath: str, config: PythonSettings) -> list[Finding]:
    if in_roots(relpath, config.paths("script_roots")):
        return []
    stem = PurePosixPath(relpath).stem
    if house_style.matches_casing(stem, conventions.load_pack("python")["file"]):
        return []
    return [
        finding(
            "PYQ107",
            relpath,
            None,
            f"Python module '{PurePosixPath(relpath).name}' is not snake_case",
            "rename the module to snake_case",
        )
    ]


def generic_name_findings(
    relpath: str,
    depth: int,
    config: PythonSettings,
    existing_segments: Set[str] = frozenset(),
) -> list[Finding]:
    allowed = set(config.paths("generic_name_allowlist"))
    found = [
        finding(
            "PYQ103",
            relpath,
            None,
            f"catch-all directory '{part}' is a house-style banned word",
            "rename it for its owner or approve the exact path in "
            "guardrails.python.generic_name_allowlist",
        )
        for part, prefix in below_root_dirs(relpath, depth)
        if prefix not in existing_segments
        and house_style.is_banned_word(part)
        and prefix not in allowed
        and not is_label(prefix, config)
    ]
    stem = PurePosixPath(relpath).stem
    if house_style.is_banned_word(stem) and relpath not in allowed:
        found.append(
            finding(
                "PYQ104",
                relpath,
                None,
                f"catch-all module '{stem}' is a house-style banned word",
                "rename it for its responsibility or approve the exact path in "
                "guardrails.python.generic_name_allowlist",
            )
        )
    return found
