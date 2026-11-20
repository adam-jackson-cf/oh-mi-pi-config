import re
import unittest
from pathlib import Path

import pytest
from test_skill_references import ROOT

from guardrails.host.process import run_text

pytestmark = pytest.mark.seam

REFERENCE_LINES, REFERENCE_CHARS, LINE_WIDTH, DESCRIPTION_CHARS = 120, 10000, 160, 200


LATITUDE = (
    "Where this skill is silent, use judgement: keep existing content, make no irreversible write, "
    "ask the user once when the choice changes the outcome, and state the choice in the result."
)


ROUTES = {
    "# Workflow": ("multi-step-workflow", 120, 5000),
    "# Task": ("simple-task:inline", 120, 12000),
    "# Guidance": ("behaviour-guidance", 100, 8000),
}


def tracked_skill_directories() -> list[Path]:
    listed = run_text(
        [
            "git",
            "ls-files",
            "-z",
            "--",
            "agent/extensions/dynaflows/skills",
            "agent/skills",
        ],
        cwd=ROOT,
        check=True,
        timeout=60,
    ).stdout.split("\0")
    return sorted({
        (ROOT / path).parent
        for path in listed
        if path.endswith("/SKILL.md")
        and len(Path(path).parts) == (6 if path.startswith("agent/extensions/") else 4)
    })


def frontmatter_description(text: str) -> str | None:
    block = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
    if block is None:
        return None
    found = re.search(
        r"^description:(.*?)(?=^[A-Za-z][\w-]*:|\Z)",
        block.group(1),
        re.DOTALL | re.MULTILINE,
    )
    return " ".join(found.group(1).split()).strip("\"'") if found else None


def description_failures(entry: Path, text: str) -> list[str]:
    description = frontmatter_description(text)
    if (
        description is None
        or not description.lower().startswith("use when")
        or len(description) >= DESCRIPTION_CHARS
        or re.search(r"[.!?;:]\s", description)
    ):
        return [
            f"{entry}: description must be one 'Use when' sentence under 200 characters"
        ]
    return []


def width_failures(sources: list[Path]) -> list[str]:
    failures = []
    for source in sources:
        wide = [
            number
            for number, line in enumerate(
                source.read_text(encoding="utf-8").splitlines(), 1
            )
            if len(line) > LINE_WIDTH
        ]
        if wide:
            failures.append(
                f"{source.relative_to(ROOT)}: lines over {LINE_WIDTH} characters: {wide}"
            )
    return failures


def reference_failures(directory: Path, text: str, references: list[Path]) -> list[str]:
    failures = []
    linked = {
        match.split("#", 1)[0]
        for match in re.findall(r"\]\((references/[^)\s]+)\)", text)
    }
    for source in references:
        relative = source.relative_to(directory).as_posix()
        content = source.read_text(encoding="utf-8")
        reference = source.relative_to(ROOT)
        if (ROOT / reference).parent != directory / "references":
            failures.append(f"{reference}: references are one level deep")
        if (
            len(content.splitlines()) > REFERENCE_LINES
            or len(content.encode("utf-8")) > REFERENCE_CHARS
        ):
            failures.append(
                f"{reference}: caps are {REFERENCE_LINES} lines and {REFERENCE_CHARS} characters"
            )
        if relative not in linked:
            failures.append(f"{reference}: not linked directly from SKILL.md")
    return failures


def latitude_failures(entry: Path, body: str) -> list[str]:
    sections = re.split(r"^#{2,3} ", body, flags=re.MULTILINE)
    first = next(
        (section for section in sections[1:] if not section.startswith("Output")), ""
    )
    if LATITUDE not in " ".join(first.split()):
        return [f"{entry}: the first step must carry the latitude clause verbatim"]
    return []


def step_failures(entry: Path, body: str) -> list[str]:
    failures = []
    steps = re.findall(
        r"^#{2,3} Step (\d+):[^\n]*\n(.*?)(?=^#{2,3} |\Z)",
        body,
        re.DOTALL | re.MULTILINE,
    )
    numbers = [int(number) for number, _ in steps]
    if (
        not steps
        or numbers != list(range(numbers[0], numbers[0] + len(numbers)))
        or numbers[0] not in {0, 1}
    ):
        failures.append(
            f"{entry}: workflow steps must be numbered contiguously from 0 or 1"
        )
    for number, section in steps:
        if len(re.findall(r"\]\(references/[^)\s#]*workflow\.md\)", section)) != 1:
            failures.append(
                f"{entry}: Step {number} needs exactly one references/*workflow.md link"
            )
        if len(re.findall(r"\*\*STOP\*\*", section)) > 1:
            failures.append(f"{entry}: Step {number} has more than one STOP")
    return failures


def skill_shape_failures(directory: Path) -> list[str]:
    entry = (directory / "SKILL.md").relative_to(ROOT)
    text = (ROOT / entry).read_text(encoding="utf-8")
    failures = description_failures(entry, text)
    body = text.split("\n---\n", 1)[-1]
    heading = next((line for line in body.splitlines() if line.startswith("# ")), None)
    references = sorted((directory / "references").rglob("*.md"))
    if heading not in ROUTES:
        return [*failures, f"{entry}: body heading {heading!r} names no route"]
    route, line_cap, char_cap = ROUTES[heading]
    if route == "simple-task:inline" and references:
        route, char_cap = "simple-task:runbook-index", 5000
    if len(text.splitlines()) > line_cap or len(text.encode("utf-8")) > char_cap:
        failures.append(
            f"{entry}: {route} caps are {line_cap} lines and {char_cap} characters"
        )
    if route == "behaviour-guidance" and (directory / "references").exists():
        failures.append(f"{entry}: behaviour-guidance ships no references")
    failures += width_failures([ROOT / entry, *references])
    failures += reference_failures(directory, text, references)
    if route in {"multi-step-workflow", "simple-task:runbook-index"}:
        failures += latitude_failures(entry, body)
    if route == "multi-step-workflow":
        failures += step_failures(entry, body)
    return failures


class SkillShapeTests(unittest.TestCase):
    def test_tracked_skills_follow_the_route_size_and_link_rules(self) -> None:
        directories = tracked_skill_directories()
        assert {"structured-workflow", "git-tool"} <= {
            directory.name for directory in directories
        }
        for directory in directories:
            with self.subTest(skill=directory.name):
                assert skill_shape_failures(directory) == []


if __name__ == "__main__":
    unittest.main()
