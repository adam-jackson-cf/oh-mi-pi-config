"""Check that core skill references resolve within this repository."""

import re
import tomllib
import unittest
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path

import pytest

pytestmark = pytest.mark.fast

ROOT = Path(__file__).resolve().parents[1]

PACKAGE = ROOT / "agent/extensions/agent-rails"

PACKAGE_SKILLS = PACKAGE / "skills"

# Plain docs the refusal texts cite by path: no skill owns them.
PACKAGE_REFERENCES = (
    *sorted((PACKAGE / "references").glob("*.md")),
    PACKAGE / "engine/guardrails/commands/adapters/README.md",
)

CORE_SKILLS = (
    "test-guidance",
    "setup-repo-proofs",
    "jev-actions",
    "git-tool",
)

# Skills copied verbatim from their upstream repository. Only their frontmatter differs; the
# quality gates and the reference width cap leave their text as upstream wrote it.
IMPORTED_SKILLS = frozenset({"setup-repo-proofs", "test-guidance"})

# The engine's own rule catalogue is the only rule authority for the rails. The imported
# setup-repo-proofs catalogue (assets/guardrail-rules.json) is the target repository's.
SKILL_ASSETS_READ = re.compile(
    r"setup-repo-proofs[\"']?\s*[,/]\s*[\"']?assets", re.IGNORECASE
)

REFERENCE = re.compile(
    r"skill://([A-Za-z0-9_-]+)(?:/([A-Za-z0-9_./-]+))?(?:#([A-Za-z0-9_-]+))?"
)


def heading_anchors(target: Path) -> set[str]:
    return {
        re.sub(r"[^\w-]", "", heading.strip().lower().replace(" ", "-"))
        for heading in re.findall(
            r"^#{1,6} (.+)$", target.read_text(encoding="utf-8"), re.MULTILINE
        )
    }


def skill_directory(name: str) -> Path:
    return PACKAGE_SKILLS / name


def citing_sources(
    references: Mapping[tuple[str, str, str], set[str]], name: str
) -> set[str]:
    return {
        source
        for (skill, _, _), sources in references.items()
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
        relative, _, anchor = target.partition("#")
        linked = source.parent / relative
        if not (linked.is_file() or (relative.endswith("/") and linked.is_dir())) or (
            anchor and linked.suffix == ".md" and anchor not in heading_anchors(linked)
        ):
            unresolved.append(
                f"unresolved relative markdown link: {target} "
                f"(cited by: {source.relative_to(ROOT)})"
            )
    return unresolved


class SkillReferenceTests(unittest.TestCase):
    def test_imported_skills_are_independent_of_the_assistant(self) -> None:
        forbidden = re.compile(
            r"\b(?:omp|agent-rails|agentrails|harness)\b", re.IGNORECASE
        )
        for name in sorted(IMPORTED_SKILLS):
            directory = skill_directory(name)
            for source in sorted(directory.rglob("*.md")):
                with self.subTest(source=source.relative_to(ROOT)):
                    assert forbidden.search(source.read_text(encoding="utf-8")) is None
            entry = (directory / "SKILL.md").read_text(encoding="utf-8")
            assert "hide: true" in entry
            assert "disable-model-invocation: true" in entry

    def test_rails_never_read_the_imported_setup_catalogue(self) -> None:
        sources = [
            PACKAGE / "index.ts",
            *(PACKAGE / "engine").rglob("*.py"),
            *(PACKAGE / "lib").rglob("*.ts"),
            *(PACKAGE / "lib").rglob("*.py"),
        ]
        readers = [
            str(source.relative_to(ROOT))
            for source in sorted(sources)
            if SKILL_ASSETS_READ.search(source.read_text(encoding="utf-8"))
        ]
        assert readers == []
        assert SKILL_ASSETS_READ.search('join(skills, "setup-repo-proofs", "assets")')
        assert SKILL_ASSETS_READ.search("skills / 'setup-repo-proofs' / 'assets'")

    def test_quality_gates_leave_imported_skills_verbatim(self) -> None:
        tool = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
            "tool"
        ]
        gates = (ROOT / "scripts/run-ci-quality-gates.sh").read_text(encoding="utf-8")
        for name in sorted(IMPORTED_SKILLS):
            with self.subTest(skill=name):
                path = skill_directory(name).relative_to(ROOT).as_posix()
                assert path in tool["ruff"]["extend-exclude"]
                assert f"^{path}/" in tool["mypy"]["exclude"]
                assert f"{path}/*" in tool["coverage"]["run"]["omit"]
                assert f"':(exclude,glob){path}/**'" in gates

    def test_core_skill_references_resolve_locally(self) -> None:
        sources = {
            PACKAGE / "rails.yml",
            PACKAGE / "index.ts",
            PACKAGE / "AGENTS.md",
            PACKAGE / "lib/rails_kernel.py",
            *PACKAGE_REFERENCES,
            *(ROOT / "agent/agents").glob("*.md"),
            *(ROOT / "agent/rules").glob("*.md"),
            *(PACKAGE / "rules").glob("*.md"),
            *(
                path
                for path in PACKAGE_SKILLS.rglob("*")
                if path.suffix in {".md", ".py", ".html"} and path.is_file()
            ),
        }
        references: defaultdict[tuple[str, str, str], set[str]] = defaultdict(set)
        for source in sorted(sources):
            for match in REFERENCE.finditer(source.read_text(encoding="utf-8")):
                name, relative, anchor = match.groups()
                if name in CORE_SKILLS:
                    references[(name, relative or "", anchor or "")].add(
                        str(source.relative_to(ROOT))
                    )

        # Every core skill needs an entry point, even if all citations name subpaths.
        for name in CORE_SKILLS:
            references[(name, "", "")].update(citing_sources(references, name))

        unresolved = []
        for (name, relative, anchor), citing in sorted(references.items()):
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
                or (anchor and anchor not in heading_anchors(target))
            ):
                reference = (
                    name
                    + ("/" + relative if relative else "")
                    + ("#" + anchor if anchor else "")
                )
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

    def test_package_reference_links_resolve_locally(self) -> None:
        assert {path.name for path in PACKAGE_REFERENCES} == {
            "judgments.md",
            "proofs-and-merge.md",
            "structure-and-naming.md",
            "README.md",
        }
        unresolved = [
            failure
            for source in PACKAGE_REFERENCES
            for failure in unresolved_links(source)
        ]
        if unresolved:
            self.fail("\n".join(unresolved))

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
