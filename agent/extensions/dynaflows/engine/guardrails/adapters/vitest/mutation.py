"""Convert Stryker's mutation-testing-report-schema JSON to shared evidence."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from guardrails.findings import Finding

TOOL = "@stryker-mutator/core"
VERSION = "9.4.0"
STATUSES = {"Killed": "killed", "Survived": "survived", "NoCoverage": "no_tests", "Timeout": "timeout", "CompileError": "incomplete", "RuntimeError": "incomplete", "Ignored": "incomplete", "Pending": "incomplete"}


def normalise(repo: Path, source: Path) -> dict[str, Any]:
    data = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
        raise ValueError("Stryker report requires a files object")
    mutants = []
    for filename, entry in data["files"].items():
        path = Path(filename)
        resolved = (repo / path).resolve()
        relative = resolved.relative_to(repo.resolve()).as_posix()
        if not isinstance(entry, dict) or not isinstance(entry.get("mutants"), list):
            raise ValueError(f"{filename}: missing mutants list")
        for item in entry["mutants"]:
            identifier = item["id"]
            location = item["location"]
            start, end = location["start"]["line"], location["end"]["line"]
            if not isinstance(identifier, str) or type(start) is not int or type(end) is not int or start < 1 or end < start:
                raise ValueError(f"{filename}: invalid mutant id or line span")
            status = STATUSES[item["status"]]
            mutants.append({"id": f"{relative}::{identifier}", "path": relative, "start_line": start, "end_line": end, "status": status})
    return {"version": 1, "tool": TOOL, "mutants": mutants}


def write_report(repo: Path, source: Path, out: Path) -> list[Finding]:
    try:
        report = normalise(repo, source)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, KeyError, TypeError) as error:
        return [Finding("TEST.mutation-sensitivity", str(source), f"invalid Stryker report: {error}", "run the pinned Stryker tool with its JSON reporter and supply --input")]
    return []
