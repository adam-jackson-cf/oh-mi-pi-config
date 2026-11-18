from pathlib import Path
import json
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "agent/skills"
POLICY = SKILLS / "omp-dyna-workflows/references"
CATALOGUE = SKILLS / "setup-repo-proofs/assets/python-reference/omp_guardrails/data/guardrail-rules.json"
THRESHOLDS = SKILLS / "omp-dyna-workflows/judgments/structure-naming/thresholds.json"
ENFORCED = re.compile(r"Enforced by ([A-Z]+\.[A-Za-z0-9.-]+) \(omp-guardrails ([a-z-]+(?:, [a-z-]+)*)\)\.")
JUDGED = re.compile(r"Judged by ([a-z-]+)/([a-z_-]+)\.")


class DeliveryPolicyTests(unittest.TestCase):
    def test_policy_rules_have_exactly_one_valid_status(self):
        catalogue = json.loads(CATALOGUE.read_text(encoding="utf-8"))
        rules = {row["id"]: {ref["subcommand"] for ref in row.get("reference", [])}
                 for row in catalogue["rules"]}
        native_rules = {row["id"] for row in catalogue["rules"] if row["kind"] == "tool"}
        decisions = json.loads(THRESHOLDS.read_text(encoding="utf-8"))["decisions"]
        for name in ("structure-and-naming.md", "delivery-principles.md"):
            lines = (POLICY / name).read_text(encoding="utf-8").splitlines()
            rule_lines = [(number, line) for number, line in enumerate(lines, 1)
                          if line.startswith("- ")]
            self.assertTrue(rule_lines, name)
            for number, line in rule_lines:
                with self.subTest(source=name, line=number):
                    enforced = list(ENFORCED.finditer(line))
                    judged = list(JUDGED.finditer(line))
                    guidance = line.count("Guidance only.")
                    self.assertEqual(len(re.findall(r"Enforced by|Judged by|Guidance only", line)), 1, line)
                    self.assertEqual(len(enforced) + len(judged) + guidance, 1, line)
                    self.assertTrue(line.endswith("."), line)
                    for match in enforced:
                        rule, commands = match.groups()
                        self.assertIn(rule, rules)
                        if rules[rule]:
                            self.assertLessEqual(set(commands.split(", ")), rules[rule])
                        else:
                            self.assertIn(rule, native_rules)
                            self.assertEqual(commands, "toolchain-parity")
                        self.assertEqual(match.end(), len(line))
                    for match in judged:
                        judgment, decision = match.groups()
                        if name == "structure-and-naming.md":
                            self.assertEqual(judgment, "structure-naming")
                            self.assertIn(decision, decisions)
                        else:
                            self.assertIn((judgment, decision), {
                                ("main", "implementation-integrity"),
                                ("agent-rails", "clean-cutovers"),
                                ("agent-rails", "regression-coverage"),
                            })
                        self.assertEqual(match.end(), len(line))

    def test_style_lists_have_one_canonical_data_home(self):
        style_path = CATALOGUE.with_name("house-style.json")
        house_style = json.loads(style_path.read_text(encoding="utf-8"))
        text = (POLICY / "structure-and-naming.md").read_text(encoding="utf-8")
        self.assertIn("house-style.json", text)
        self.assertEqual([word for word in house_style["banned_words"] if f"`{word}`" in text], [])
        for source in house_style["sources"][:2]:
            self.assertTrue((ROOT / source["source"].removeprefix("oh-mi-pi-config ")).is_file())

if __name__ == "__main__":
    unittest.main()
