from __future__ import annotations

from dataclasses import dataclass


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
    return "\n".join(item.render() for item in unique)


def exit_code(findings: list[Finding]) -> int:
    return 1 if findings else 0
