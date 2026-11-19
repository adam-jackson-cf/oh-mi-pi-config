"""Check that core skill references resolve within this repository."""

import re
import unittest
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path

import pytest

pytestmark = pytest.mark.fast

ROOT = Path(__file__).resolve().parents[1]

SKILLS = ROOT / "agent/skills"

PACKAGE = ROOT / "agent/extensions/dynaflows"

PACKAGE_SKILLS = PACKAGE / "skills"

WORKFLOW = PACKAGE_SKILLS / "dynaflows"

CORE_SKILLS = (
    "dynaflows",
    "test-guidance",
    "setup-repo-proofs",
    "jev-actions",
    "codify-omp-workflow-corrections",
    "git-tool",
)

REFERENCE = re.compile(r"skill://([A-Za-z0-9_-]+)(?:/([A-Za-z0-9_./-]+))?")


def skill_directory(name: str) -> Path:
    return SKILLS / name if name == "git-tool" else PACKAGE_SKILLS / name


def citing_sources(
    references: Mapping[tuple[str, str], set[str]], name: str
) -> set[str]:
    return {
        source
        for (skill, _), sources in references.items()
        if skill == name
        for source in sources
    }


def unresolved_links(source: Path) -> list[str]:
    unresolved = []
    text = source.read_text(encoding="utf-8")
    for match in re.finditer(r"\[[^\]\n]*\]\(([^)\s]+)\)", text):
        target = match.group(1)
        if target.startswith("#") or re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", target):
            continue
        relative = target.split("#", 1)[0]
        if not (source.parent / relative).is_file():
            unresolved.append(
                f"unresolved relative markdown link: {target} "
                f"(cited by: {source.relative_to(ROOT)})"
            )
    return unresolved


class SkillReferenceTests(unittest.TestCase):
    def test_repository_setup_is_independent_of_the_assistant(self) -> None:
        directory = PACKAGE_SKILLS / "setup-repo-proofs"
        forbidden = re.compile(r"\b(?:omp|dynaflows|harness|freeze)\b", re.IGNORECASE)
        for source in sorted(directory.rglob("*.md")):
            with self.subTest(source=source.relative_to(ROOT)):
                assert forbidden.search(source.read_text(encoding="utf-8")) is None
        entry = (directory / "SKILL.md").read_text(encoding="utf-8")
        assert "hide: true" in entry
        assert "disable-model-invocation: true" in entry

    def test_core_skill_references_resolve_locally(self) -> None:
        sources = {
            ROOT / "agent/rails.yml",
            ROOT / "agent/extensions/agent-rails.ts",
            PACKAGE / "index.ts",
            PACKAGE / "AGENTS.md",
            PACKAGE / "docs/workflow.html",
            PACKAGE / "engine/workflows.py",
            *(ROOT / "agent/agents").glob("*.md"),
            *(ROOT / "agent/rules").glob("*.md"),
            *(PACKAGE / "rules").glob("*.md"),
            *(
                path
                for path in PACKAGE_SKILLS.rglob("*")
                if path.suffix in {".md", ".py", ".html"} and path.is_file()
            ),
        }
        references: defaultdict[tuple[str, str], set[str]] = defaultdict(set)
        for source in sorted(sources):
            for match in REFERENCE.finditer(source.read_text(encoding="utf-8")):
                name, relative = match.groups()
                if name in CORE_SKILLS:
                    references[(name, relative or "")].add(
                        str(source.relative_to(ROOT))
                    )

        # Every core skill needs an entry point, even if all citations name subpaths.
        for name in CORE_SKILLS:
            references[(name, "")].update(citing_sources(references, name))

        unresolved = []
        for (name, relative), citing in sorted(references.items()):
            directory = skill_directory(name).resolve()
            target = (
                (directory / relative).resolve() if relative else directory / "SKILL.md"
            )
            if (
                not directory.is_relative_to(ROOT)
                or not (directory / "SKILL.md").resolve().is_relative_to(directory)
                or not (directory / "SKILL.md").is_file()
                or not target.resolve().is_relative_to(directory)
                or not target.exists()
            ):
                reference = name + ("/" + relative if relative else "")
                unresolved.append(
                    f"unresolved skill reference: {reference} (cited by: {', '.join(sorted(citing))})"
                )
        if unresolved:
            self.fail("\n".join(unresolved))

    def test_core_skill_relative_markdown_links_resolve_locally(self) -> None:
        unresolved = []
        for name in CORE_SKILLS:
            for source in sorted(skill_directory(name).rglob("*.md")):
                unresolved.extend(unresolved_links(source))
        if unresolved:
            self.fail("\n".join(unresolved))

    def test_workflow_page_relative_links_resolve_after_layout_moves(self) -> None:
        source = PACKAGE / "docs/workflow.html"
        for target in re.findall(r'href="([^"]+)"', source.read_text(encoding="utf-8")):
            if target.startswith("#") or re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", target):
                continue
            with self.subTest(target=target):
                assert (source.parent / target.split("#", 1)[0]).is_file()

    def test_core_skill_frontmatter_names_match_directories(self) -> None:
        for name in CORE_SKILLS:
            with self.subTest(skill=name):
                entry = skill_directory(name) / "SKILL.md"
                # Missing entry points are diagnosed with their citations above.
                if not entry.is_file():
                    continue
                text = entry.read_text(encoding="utf-8")
                frontmatter = re.match(
                    r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", text, re.DOTALL
                )
                assert frontmatter is not None, (
                    f"{entry.relative_to(ROOT)}: missing frontmatter"
                )
                declared = re.search(
                    r"^name:\s*([^\r\n]+)$", frontmatter.group(1), re.MULTILINE
                )
                assert declared is not None, (
                    f"{entry.relative_to(ROOT)}: missing frontmatter name"
                )
                assert declared.group(1).strip().strip("\"'") == name, (
                    f"{entry.relative_to(ROOT)}: frontmatter name must match directory"
                )


if __name__ == "__main__":
    unittest.main()
