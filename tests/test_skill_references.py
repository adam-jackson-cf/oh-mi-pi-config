"""Check that core skill references resolve within this repository."""
from collections import defaultdict
from pathlib import Path
import re
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "agent/skills"
PACKAGE = ROOT / "agent/extensions/dynaflows"
PACKAGE_SKILLS = PACKAGE / "skills"
WORKFLOW = PACKAGE_SKILLS / "dynaflows"
CORE_SKILLS = (
    "dynaflows", "test-guidance", "setup-repo-proofs", "jev-actions",
    "codify-omp-workflow-corrections", "git-tool",
)
REFERENCE = re.compile(r"skill://([A-Za-z0-9_-]+)(?:/([A-Za-z0-9_./-]+))?")

def skill_directory(name):
    return SKILLS / name if name == "git-tool" else PACKAGE_SKILLS / name


class SkillReferenceTests(unittest.TestCase):
    def test_repository_setup_is_independent_of_the_assistant(self):
        directory = PACKAGE_SKILLS / "setup-repo-proofs"
        forbidden = re.compile(r"\b(?:omp|dynaflows|harness|freeze)\b", re.IGNORECASE)
        for source in sorted(directory.rglob("*.md")):
            with self.subTest(source=source.relative_to(ROOT)):
                self.assertIsNone(forbidden.search(source.read_text(encoding="utf-8")))
        entry = (directory / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("hide: true", entry)
        self.assertIn("disable-model-invocation: true", entry)

    def test_core_skill_references_resolve_locally(self):
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
            *(path for path in PACKAGE_SKILLS.rglob("*")
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
            directory = skill_directory(name).resolve()
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
            for source in sorted(skill_directory(name).rglob("*.md")):
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

    def test_workflow_page_relative_links_resolve_after_layout_moves(self):
        source = PACKAGE / "docs/workflow.html"
        for target in re.findall(r'href="([^"]+)"', source.read_text(encoding="utf-8")):
            if target.startswith("#") or re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", target):
                continue
            with self.subTest(target=target):
                self.assertTrue((source.parent / target.split("#", 1)[0]).is_file())


    def test_core_skill_frontmatter_names_match_directories(self):
        for name in CORE_SKILLS:
            with self.subTest(skill=name):
                entry = skill_directory(name) / "SKILL.md"
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


LATITUDE = ("Where this skill is silent, use judgement: keep existing content, make no irreversible write, "
            "ask the user once when the choice changes the outcome, and state the choice in the result.")
# Body heading -> (route, SKILL.md line cap, SKILL.md character cap); "# Task" splits on references/.
ROUTES = {
    "# Workflow": ("multi-step-workflow", 120, 5000),
    "# Task": ("simple-task:inline", 120, 12000),
    "# Guidance": ("behaviour-guidance", 100, 8000),
}
REFERENCE_LINES, REFERENCE_CHARS, LINE_WIDTH, DESCRIPTION_CHARS = 120, 10000, 160, 200


def tracked_skill_directories():
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--", "agent/extensions/dynaflows/skills", "agent/skills"],
        cwd=ROOT, check=True, capture_output=True, text=True).stdout.split("\0")
    return sorted({(ROOT / path).parent for path in listed if path.endswith("/SKILL.md")
                   and len(Path(path).parts) == (6 if path.startswith("agent/extensions/") else 4)})


def frontmatter_description(text):
    block = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
    if block is None:
        return None
    found = re.search(r"^description:(.*?)(?=^[A-Za-z][\w-]*:|\Z)", block.group(1), re.DOTALL | re.MULTILINE)
    return " ".join(found.group(1).split()).strip("\"'") if found else None


def skill_shape_failures(directory):
    """Every breach of the create-skill route, size and linking rules for one skill directory."""
    failures = []
    entry = (directory / "SKILL.md").relative_to(ROOT)
    text = (ROOT / entry).read_text(encoding="utf-8")
    description = frontmatter_description(text)
    if (description is None or not description.lower().startswith("use when")
            or len(description) >= DESCRIPTION_CHARS or re.search(r"[.!?;:]\s", description)):
        failures.append(f"{entry}: description must be one 'Use when' sentence under 200 characters")
    body = text.split("\n---\n", 1)[-1]
    heading = next((line for line in body.splitlines() if line.startswith("# ")), None)
    references = sorted((directory / "references").rglob("*.md"))
    if heading not in ROUTES:
        return failures + [f"{entry}: body heading {heading!r} names no route"]
    route, line_cap, char_cap = ROUTES[heading]
    if route == "simple-task:inline" and references:
        route, char_cap = "simple-task:runbook-index", 5000
    lines = text.splitlines()
    if len(lines) > line_cap or len(text.encode("utf-8")) > char_cap:
        failures.append(f"{entry}: {route} caps are {line_cap} lines and {char_cap} characters")
    if route == "behaviour-guidance" and (directory / "references").exists():
        failures.append(f"{entry}: behaviour-guidance ships no references")
    for source in [ROOT / entry, *references]:
        wide = [number for number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1)
                if len(line) > LINE_WIDTH]
        if wide:
            failures.append(f"{source.relative_to(ROOT)}: lines over {LINE_WIDTH} characters: {wide}")
    linked = {match.split("#", 1)[0] for match in re.findall(r"\]\((references/[^)\s]+)\)", text)}
    for reference in references:
        relative = reference.relative_to(directory).as_posix()
        content = reference.read_text(encoding="utf-8")
        reference = reference.relative_to(ROOT)
        if (ROOT / reference).parent != directory / "references":
            failures.append(f"{reference}: references are one level deep")
        if len(content.splitlines()) > REFERENCE_LINES or len(content.encode("utf-8")) > REFERENCE_CHARS:
            failures.append(f"{reference}: caps are {REFERENCE_LINES} lines and {REFERENCE_CHARS} characters")
        if relative not in linked:
            failures.append(f"{reference}: not linked directly from SKILL.md")
    if route in {"multi-step-workflow", "simple-task:runbook-index"}:
        sections = re.split(r"^#{2,3} ", body, flags=re.MULTILINE)
        first = next((section for section in sections[1:] if not section.startswith("Output")), "")
        if LATITUDE not in " ".join(first.split()):
            failures.append(f"{entry}: the first step must carry the latitude clause verbatim")
    if route == "multi-step-workflow":
        steps = re.findall(r"^#{2,3} Step (\d+):[^\n]*\n(.*?)(?=^#{2,3} |\Z)", body, re.DOTALL | re.MULTILINE)
        numbers = [int(number) for number, _ in steps]
        if not steps or numbers != list(range(numbers[0], numbers[0] + len(numbers))) or numbers[0] not in {0, 1}:
            failures.append(f"{entry}: workflow steps must be numbered contiguously from 0 or 1")
        for number, section in steps:
            if len(re.findall(r"\]\(references/[^)\s#]*workflow\.md\)", section)) != 1:
                failures.append(f"{entry}: Step {number} needs exactly one references/*workflow.md link")
            if len(re.findall(r"\*\*STOP\*\*", section)) > 1:
                failures.append(f"{entry}: Step {number} has more than one STOP")
    return failures


class SkillShapeTests(unittest.TestCase):
    def test_tracked_skills_follow_the_route_size_and_link_rules(self):
        directories = tracked_skill_directories()
        self.assertTrue({"dynaflows", "git-tool"} <= {directory.name for directory in directories})
        for directory in directories:
            with self.subTest(skill=directory.name):
                self.assertEqual(skill_shape_failures(directory), [])


if __name__ == "__main__":
    unittest.main()
