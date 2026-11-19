from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from guardrails.foundation.findings import Finding

TOOL = "github.com/go-gremlins/gremlins/cmd/gremlins"
VERSION = "v0.6.0"
RULE = "TEST.mutation-sensitivity"
STATUSES = {
    "KILLED": "killed",
    "LIVED": "survived",
    "NOT COVERED": "no_tests",
    "TIMED OUT": "timeout",
    "NOT VIABLE": "incomplete",
    "SKIPPED": "incomplete",
    "RUNNABLE": "incomplete",
}


def relative(repo: Path, name: str) -> str:
    path = Path(name)
    resolved = (path if path.is_absolute() else repo / path).resolve()
    return resolved.relative_to(repo.resolve()).as_posix()


def normalise(repo: Path, source: Path) -> dict[str, Any]:
    data = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("files"), list):
        raise ValueError("gremlins report requires a files list")
    mutants = []
    for entry in data["files"]:
        name = entry["file_name"]
        path = relative(repo, name)
        if not isinstance(entry.get("mutations"), list):
            raise ValueError(f"{name}: missing mutations list")
        for item in entry["mutations"]:
            line, column, kind = item["line"], item["column"], item["type"]
            if type(line) is not int or type(column) is not int or line < 1:
                raise ValueError(f"{name}: invalid mutation position")
            mutants.append({
                "id": f"{path}::{line}:{column}:{kind}",
                "path": path,
                "start_line": line,
                "end_line": line,
                "status": STATUSES[item["status"]],
            })
    return {"version": 1, "tool": f"{TOOL}@{VERSION}", "mutants": mutants}


def write_report(repo: Path, source: Path, out: Path) -> list[Finding]:
    try:
        report = normalise(repo, source)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, KeyError, TypeError) as error:
        return [
            Finding(
                RULE,
                str(source),
                f"invalid gremlins report: {error}",
                f"run {TOOL}@{VERSION} unleash --output <file> and supply --input",
            )
        ]
    return []
