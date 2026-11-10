"""Check that core skill references resolve within this repository."""
from collections import defaultdict
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "agent/skills"
CORE_SKILLS = ("codegraph", "coding-guidance", "test-guidance", "git-tool")
REFERENCE = re.compile(r"skill://([A-Za-z0-9_-]+)(?:/([A-Za-z0-9_./-]+))?")


class SkillReferenceTests(unittest.TestCase):
    def test_core_skill_references_resolve_locally(self):
        sources = {
            ROOT / "agent/agent-rails.yml",
            ROOT / "agent/extensions/agent-rails.ts",
            *(ROOT / "agent/agents").glob("*.md"),
            *(path for path in (SKILLS / "omp-dyna-workflows").rglob("*")
              if path.suffix in {".md", ".py", ".html"} and path.is_file()),
        }
        references = defaultdict(set)
        for source in sorted(sources):
            for match in REFERENCE.finditer(source.read_text(encoding="utf-8")):
                name, relative = match.groups()
                if name in CORE_SKILLS:
                    references[(name, relative or "")].add(str(source.relative_to(ROOT)))

        # Every core skill needs an entry point, even if all citations name subpaths.
        for name in CORE_SKILLS:
            references[(name, "")].update(
                source for (skill, _), sources in list(references.items())
                if skill == name for source in sources
            )

        unresolved = []
        for (name, relative), sources in sorted(references.items()):
            directory = (SKILLS / name).resolve()
            target = (directory / relative).resolve() if relative else directory / "SKILL.md"
            if (not directory.is_relative_to(ROOT)
                    or not (directory / "SKILL.md").resolve().is_relative_to(directory)
                    or not (directory / "SKILL.md").is_file()
                    or not target.resolve().is_relative_to(directory)
                    or not target.exists()):
                reference = name + ("/" + relative if relative else "")
                unresolved.append(
                    f"unresolved skill reference: {reference} (cited by: {', '.join(sorted(sources))})"
                )
        if unresolved:
            self.fail("\n".join(unresolved))

    def test_core_skill_frontmatter_names_match_directories(self):
        for name in CORE_SKILLS:
            with self.subTest(skill=name):
                entry = SKILLS / name / "SKILL.md"
                # Missing entry points are diagnosed with their citations above.
                if not entry.is_file():
                    continue
                text = entry.read_text(encoding="utf-8")
                frontmatter = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", text, re.DOTALL)
                self.assertIsNotNone(frontmatter, f"{entry.relative_to(ROOT)}: missing frontmatter")
                declared = re.search(r"^name:\s*([^\r\n]+)$", frontmatter.group(1), re.MULTILINE)
                self.assertIsNotNone(declared, f"{entry.relative_to(ROOT)}: missing frontmatter name")
                self.assertEqual(declared.group(1).strip().strip("\"'"), name,
                                 f"{entry.relative_to(ROOT)}: frontmatter name must match directory")


if __name__ == "__main__":
    unittest.main()
