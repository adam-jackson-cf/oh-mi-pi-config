from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any

RULE_CATALOGUE = "guardrail-rules.json"
# Findings about the repo's settings name the config file and the section at fault.
CONFIG_LABEL = ".agentrails/config.json"


def config_label(section: str) -> str:
    """A finding path for one section of the repo's .agentrails/config.json."""
    return f"{CONFIG_LABEL}#{section}"


@dataclass(frozen=True)
class Remediation:
    root_cause: str
    fix: str

    def render(self) -> str:
        return f"root cause: {self.root_cause}; fix: {self.fix}"


@cache
def catalogue_rules() -> tuple[dict[str, Any], ...]:
    catalogue = json.loads(
        resources.files("guardrails.data").joinpath(RULE_CATALOGUE).read_bytes()
    )
    return tuple(catalogue["rules"])


def remediation(data: dict[str, str]) -> Remediation:
    return Remediation(data["root_cause"], data["fix"])


@cache
def remediations() -> dict[str, Remediation]:
    return {rule["id"]: remediation(rule["remediation"]) for rule in catalogue_rules()}


@dataclass(frozen=True)
class Finding:
    rule_id: str
    path: str
    message: str
    fix: str
    check: str | None = None

    def render(self) -> str:
        message = " ".join(self.message.split())
        return f"{self.path}: {self.rule_id}: {message}; {self.fix}"


def format_findings(findings: list[Finding]) -> str:
    unique = sorted(
        set(findings), key=lambda item: (item.path, item.rule_id, item.message)
    )
    known = remediations()
    guidance = [
        f"remediation {rule_id}: {known[rule_id].render()}"
        for rule_id in sorted({item.rule_id for item in unique})
    ]
    return "\n".join([*(item.render() for item in unique), *guidance])


def exit_code(findings: list[Finding]) -> int:
    return 1 if findings else 0
