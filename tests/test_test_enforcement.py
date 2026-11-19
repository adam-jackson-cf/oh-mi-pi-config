from pathlib import Path
import json
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "agent/skills"
INDEX = ROOT / "agent/extensions/dynaflows/skills/dynaflows/references/test-enforcement.md"
POINTER = re.compile(
    r"^- ([A-Z]+\.[A-Za-z0-9.-]+) \(guardrails ([a-z-]+(?:, [a-z-]+)*)\)"
    r" → skill://test-guidance/references/([a-z-]+\.md)#([a-z0-9-]+)$",
    re.MULTILINE,
)
PROHIBITED = re.compile(
    r"freeze\(\)|frozen proof|re-freeze|proof task|prove\(|dynaflows|guardrails|\bomp\b|"
    r"Enforced by|setup extension|guardrail level|the checker|agent's commit",
    re.IGNORECASE,
)
RULE_FACTS = {
    "PROFILE.complete": ("every profile value", "placeholder", "approval reference"),
    "TEST.fast-tier-isolation": ("setup and teardown", "never starts a subprocess", "network connection"),
    "TEST.tier-selectors": ("disjoint and complete", "exactly one tier", "collect-only"),
    "TEST.placement": ("declared owner and tier", "T2 ownership areas", "fixtures and support"),
    "NAME.test-files": ("Test file patterns", "no ticket, version or date tokens"),
    "NAME.conventions": ("sibling naming patterns",),
    "TEST.property-seed": ("Set or report the generator seed",),
    "TEST.mutation-sensitivity": ("nightly T3", "changed code", "surviving, untested or unfinished"),
    "TEST.no-self-mocking": ("Do not mock, stub or fake the repo's own code",),
    "TEST.no-test-hooks": ("Never add test-only flags", "environment variables", "only tests reference"),
    "TEST.subprocess-timeouts": ("synchronous subprocess", "communicate", "positive timeout", "async", "shared fixtures"),
    "TEST.user-data-guard": ("HOME and XDG", "temporary directory", "real user home", "failure"),
    "TEST.coverage-ratchet": ("no coverage exclusions", "no-cover pragmas", "lowering a floor", "owner approval"),
    "TEST.growth.justified": ("at least two meaningful words", "parameter rows", "up to 20", "more than 2"),
    "TEST.duplicate-path": ("normalized call path", "normalized assertion", "merge or remove"),
    "TEST.budget.per-test-time": ("at least five", "same machine", "owner approval", "CI does not measure"),
    "TEST.budget.fast-tier-time": ("at least five", "same machine", "owner approval", "measurements"),
    "TEST.identity-artefacts": ("no production file changed", "identity-bearing artefact", "byte-identical"),
    "TEST.failure-propagation": ("missing, skipped, timed out", "infrastructure", "collected zero tests", "failure"),
    "TEST.no-evasion": ("unapproved skip, xfail", "focus/only", "flaky/retry", "rerun until green", "raise a timeout"),
    "TEST.golden-change-record": ("every changed golden file", "change record", "first divergent step"),
    "SURFACE.lever-isolation": ("doctor-failure marker", "only its own process group", "per-instance ports and data",
                                "leaves no processes or ports", "keeps the evidence directory after cleanup"),
    "SURFACE.feature-map": ("one feature-map document per feature", "exactly four H2", "Sub-features",
                            "How to get to it (user POV)", "Driving it with <lever>", "Gotchas", "stale link"),
    "SURFACE.journeys": ("Journey file pattern", "through the lever command"),
}


class TestEnforcementTests(unittest.TestCase):
    def test_every_enforcement_pointer_resolves_to_a_catalogue_rule_command_and_guidance_anchor(self):
        catalogue = json.loads((SKILLS / "../extensions/dynaflows/engine/guardrails/data/guardrail-rules.json").read_text(encoding="utf-8"))
        commands = {
            row["id"]: {item["subcommand"] for item in row.get("reference", [])}
            for row in catalogue["rules"]
        }
        text = INDEX.read_text(encoding="utf-8")
        pointers = POINTER.findall(text)
        self.assertTrue(pointers)
        self.assertEqual(len(pointers), len(re.findall(r"^- ", text, re.MULTILINE)))
        self.assertEqual(len(pointers), len({row[0] for row in pointers}))
        self.assertEqual(set(RULE_FACTS), {row[0] for row in pointers})
        for rule, subcommands, filename, anchor in pointers:
            with self.subTest(rule=rule, target=f"{filename}#{anchor}"):
                self.assertIn(rule, commands)
                self.assertLessEqual(set(subcommands.split(", ")), commands[rule])
                target = SKILLS / "test-guidance/references" / filename
                self.assertTrue(target.is_file())
                content = target.read_text(encoding="utf-8")
                headings = list(re.finditer(r"^(#{1,6}) (.+)$", content, re.MULTILINE))
                selected = next(
                    (heading for heading in headings
                     if re.sub(r"[^\w-]", "", heading[2].lower().replace(" ", "-")) == anchor),
                    None,
                )
                if selected is None:
                    self.fail(f"Missing guidance anchor: {filename}#{anchor}")
                end = next(
                    (heading.start() for heading in headings
                     if heading.start() > selected.start() and len(heading[1]) <= len(selected[1])),
                    len(content),
                )
                section = " ".join(content[selected.end():end].split()).casefold()
                for fact in RULE_FACTS[rule]:
                    self.assertIn(fact.casefold(), section)

    def test_generic_guidance_has_no_workflow_mechanisms_or_enforcement_tags(self):
        self.assertIsNone(PROHIBITED.search("freeze the clock with deterministic production inputs"))
        for source in (SKILLS / "test-guidance").rglob("*.md"):
            with self.subTest(source=str(source.relative_to(ROOT))):
                self.assertIsNone(PROHIBITED.search(source.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
