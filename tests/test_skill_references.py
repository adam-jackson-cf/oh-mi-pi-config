"""Check that core skill references resolve within this repository."""
from collections import defaultdict
from pathlib import Path
import json
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "agent/skills"
CORE_SKILLS = ("coding-guidance", "test-guidance", "git-tool")
REFERENCE = re.compile(r"skill://([A-Za-z0-9_-]+)(?:/([A-Za-z0-9_./-]+))?")
GUARDRAILS = SKILLS / "setup-repository-guardrails/assets"
HOUSE_STYLE = GUARDRAILS / "python-reference/omp_guardrails/data/house-style.json"
ENFORCED_BY = re.compile(
    r"Enforced by ((?:[A-Z]+\.[A-Za-z0-9.-]*[A-Za-z0-9])(?: and [A-Z]+\.[A-Za-z0-9.-]*[A-Za-z0-9])*)"
    r" \(omp-guardrails ([a-z-]+(?:, [a-z-]+)*)\)"
)


class SkillReferenceTests(unittest.TestCase):
    def test_core_skill_references_resolve_locally(self):
        sources = {
            ROOT / "agent/agent-rails.yml",
            ROOT / "agent/extensions/agent-rails.ts",
            *(ROOT / "agent/agents").glob("*.md"),
            *(ROOT / "agent/rules").glob("*.md"),
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

    def test_core_skill_relative_markdown_links_resolve_locally(self):
        unresolved = []
        for name in CORE_SKILLS:
            for source in sorted((SKILLS / name).rglob("*.md")):
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


    def test_test_guidance_pointers_name_catalogue_rules_and_their_subcommands(self):
        catalogue = json.loads((GUARDRAILS / "guardrail-rules.json").read_text(encoding="utf-8"))
        subcommands = {
            row["id"]: {item["subcommand"] for item in row.get("reference", [])}
            for row in catalogue["rules"]
        }
        guidance = SKILLS / "test-guidance"
        pointers = []
        for source in sorted(guidance.rglob("*.md")):
            for match in ENFORCED_BY.finditer(source.read_text(encoding="utf-8")):
                for rule in match.group(1).split(" and "):
                    pointers.append((source.relative_to(ROOT), rule, set(match.group(2).split(", "))))
        self.assertTrue(pointers, "test-guidance has no 'Enforced by' pointers")
        for source, rule, named in pointers:
            with self.subTest(source=str(source), rule=rule):
                self.assertIn(rule, subcommands, f"{source}: unknown rule {rule}")
                self.assertTrue(named & subcommands[rule],
                                f"{source}: {rule} is not implemented by omp-guardrails {named}")
                self.assertLessEqual(named, set().union(*subcommands.values()),
                                     f"{source}: {rule} names an unknown subcommand")

    def test_coding_guidance_style_lists_live_in_house_style(self):
        house_style = json.loads(HOUSE_STYLE.read_text(encoding="utf-8"))
        for name in ("naming.md", "structure-and-grouping.md"):
            source = SKILLS / "coding-guidance/references" / name
            text = source.read_text(encoding="utf-8")
            with self.subTest(source=name):
                self.assertIn("house-style.json", text)
                listed = [word for word in house_style["banned_words"] if f"`{word}`" in text]
                self.assertEqual(listed, [], f"{name} repeats house-style banned words")


if __name__ == "__main__":
    unittest.main()
