"""One finding shape for every checker, rendered as one actionable line each."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Finding:
    """A rule violation at a repository path, with the repair that clears it."""

    rule_id: str
    path: str
    message: str
    fix: str

    def render(self) -> str:
        message = " ".join(self.message.split())
        return f"{self.path}: {self.rule_id}: {message}; {self.fix}"


def format_findings(findings: list[Finding]) -> str:
    """Render findings sorted by path and rule, one per line, without duplicates."""
    unique = sorted(set(findings), key=lambda item: (item.path, item.rule_id, item.message))
    return "\n".join(item.render() for item in unique)


def exit_code(findings: list[Finding]) -> int:
    """A check fails (exit 1) when it reports any finding."""
    return 1 if findings else 0
