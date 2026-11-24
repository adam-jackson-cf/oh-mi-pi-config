"""Review cases built from real defects in this repository's history.

`mine` finds lines that a later `fix` commit changed and blames them to the commit that
introduced them. A case is that introducing commit: the workspace holds its parent's tree as
the baseline commit (no history) and the commit's diff as the uncommitted change. Each fix
hunk becomes one defect span in the change's working-tree line numbers. Curate the mined
candidates into a frozen manifest before any trial.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import re
import shutil
import statistics
import subprocess
import sys
import tarfile
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).resolve().parent.parent))
from analyze import latest_by_label, same_file

REPO = Path.home() / ".omp"
EXCLUDED = "agent/skills/omp-config-optimise/experiments"
CODE_SUFFIXES = (".ts", ".py", ".sh", ".yml", ".yaml", ".toml")
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")
MAX_CHANGED_LINES = 800
MAX_FILES = 25
MERGE_GAP = 3
WINDOW = 5
T95 = {
    1: 12.706,
    2: 4.303,
    3: 3.182,
    4: 2.776,
    5: 2.571,
    6: 2.447,
    7: 2.365,
    8: 2.306,
    9: 2.262,
    10: 2.228,
    11: 2.201,
    12: 2.179,
    13: 2.160,
    14: 2.145,
    15: 2.131,
    16: 2.120,
    17: 2.110,
    18: 2.101,
    19: 2.093,
    20: 2.086,
    21: 2.080,
    22: 2.074,
    23: 2.069,
    24: 2.064,
    25: 2.060,
}


@dataclass(frozen=True)
class RealCase:
    id: str
    intro: str
    plan: str
    defects: tuple[dict[str, Any], ...]

    @property
    def fixture(self) -> str:
        return f"repo@{self.intro}"


def git(*args: str, cwd: Path = REPO, data: bytes | None = None) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=cwd, input=data, capture_output=True, check=True
    ).stdout


def is_code(path: str) -> bool:
    return (
        path.endswith(CODE_SUFFIXES)
        and "test" not in path
        and not path.startswith(EXCLUDED)
    )


def fix_hunks(fix: str) -> list[tuple[str, int, int]]:
    """(old path, first old line, last old line) per code hunk of the fix; insertions anchor on the line before."""
    hunks: list[tuple[str, int, int]] = []
    path = ""
    for line in (
        git("diff", "-U0", "--no-renames", f"{fix}^", fix).decode().splitlines()
    ):
        if line.startswith("--- "):
            path = line[6:] if line.startswith("--- a/") else ""
        match = HUNK.match(line)
        if match and path and is_code(path):
            start, count = int(match[1]), int(match[2] or "1")
            if count:
                hunks.append((path, start, start + count - 1))
            elif start:
                hunks.append((path, start, start))
    return hunks


def blame(fix: str, path: str, first: int, last: int) -> list[tuple[str, str, int]]:
    """(introducing commit, path at that commit, line there) for each old line of a fix hunk."""
    out = git(
        "blame", "--line-porcelain", "-L", f"{first},{last}", f"{fix}^", "--", path
    ).decode()
    rows: list[tuple[str, str, int]] = []
    sha, orig = "", 0
    for line in out.splitlines():
        head = re.match(r"^([0-9a-f]{40}) (\d+) \d+", line)
        if head:
            sha, orig = head[1], int(head[2])
        elif line.startswith("filename "):
            rows.append((sha, line[9:], orig))
    return rows


def reviewable(intro: str) -> bool:
    parents = git("rev-list", "--parents", "-n1", intro).decode().split()
    if len(parents) != 2:
        return False
    stats = [
        row.split("\t")
        for row in git("diff", "--numstat", f"{intro}^", intro).decode().splitlines()
    ]
    changed = sum(int(a) + int(d) for a, d, _ in stats if a != "-")
    return (
        0 < len(stats) <= MAX_FILES
        and changed <= MAX_CHANGED_LINES
        and not any(p.startswith(EXCLUDED) for _, _, p in stats)
    )


def spans(lines: list[tuple[str, int]]) -> list[tuple[str, int, int]]:
    merged: list[tuple[str, int, int]] = []
    for path, n in sorted(set(lines)):
        if merged and merged[-1][0] == path and n - merged[-1][2] <= MERGE_GAP:
            merged[-1] = (path, merged[-1][1], n)
        else:
            merged.append((path, n, n))
    return merged


def mine(since: str) -> list[dict[str, Any]]:
    fixes = (
        git("log", "--no-merges", f"--since={since}", "--pretty=%H %s")
        .decode()
        .splitlines()
    )
    found: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in fixes:
        fix, subject = row.split(" ", 1)
        if (
            not re.match(r"^fix(\(|:|!)", subject)
            or len(git("rev-list", "--parents", "-n1", fix).split()) != 2
        ):
            continue
        for path, first, last in fix_hunks(fix):
            by_intro: dict[str, list[tuple[str, int]]] = defaultdict(list)
            for sha, at_path, n in blame(fix, path, first, last):
                if sha != fix:
                    by_intro[sha].append((at_path, n))
            for intro, lines in by_intro.items():
                for file, start, end in spans(lines):
                    found[intro].append({
                        "fix": fix[:7],
                        "fixSubject": subject,
                        "file": file,
                        "start": start,
                        "end": end,
                    })
    candidates = []
    for intro, defects in found.items():
        if reviewable(intro):
            subject = git("log", "-1", "--pretty=%s", intro).decode().strip()
            candidates.append({
                "intro": intro[:7],
                "subject": subject,
                "defects": defects,
            })
    return candidates


def load_manifest(path: Path) -> list[RealCase]:
    data = json.loads(path.read_text())
    return [
        RealCase(
            id=c["id"],
            intro=c["intro"],
            plan=git("log", "-1", "--pretty=%B", c["intro"]).decode().strip(),
            defects=tuple(c["defects"]),
        )
        for c in data["cases"]
    ]


def build_workspace(case: RealCase, ws: Path) -> dict[str, dict[str, Any]]:
    """Parent tree as the baseline commit, the introducing commit's diff left uncommitted."""
    ws.mkdir(parents=True)
    archive = git("archive", f"{case.intro}^", "--", ".", f":(exclude){EXCLUDED}")
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(ws, filter="data")
    git("init", "-q", cwd=ws)
    git("add", "-A", cwd=ws)
    git(
        "-c",
        "user.email=eval@local",
        "-c",
        "user.name=eval",
        "commit",
        "-qm",
        "baseline",
        cwd=ws,
    )
    git(
        "apply",
        "--whitespace=nowarn",
        cwd=ws,
        data=git("diff", "--binary", f"{case.intro}^", case.intro),
    )
    return {
        d["id"]: {
            "file": d["file"],
            "start": d["start"],
            "end": d["end"],
            "steps": [],
            "fix": d["fix"],
        }
        for d in case.defects
    }


def validate(manifest: Path) -> bool:
    """Every case builds, and every defect span lies inside its working-tree file."""
    ok = True
    for case in load_manifest(manifest):
        ws = Path(tempfile.mkdtemp(prefix="real-case-")) / "ws"
        try:
            spans = build_workspace(case, ws)
            for defect_id, span in spans.items():
                lines = (ws / span["file"]).read_text().count("\n") + 1
                if not 1 <= span["start"] <= span["end"] <= lines:
                    print(
                        f"FAIL {case.id} {defect_id}: span outside {span['file']} ({lines} lines)"
                    )
                    ok = False
            print(
                f"{case.id:28} {len(spans):3} spans  {len({s['fix'] for s in spans.values()})} defects  PASS"
            )
        except (subprocess.CalledProcessError, OSError) as exc:
            print(f"FAIL {case.id}: {exc}")
            ok = False
        finally:
            shutil.rmtree(ws.parent, ignore_errors=True)
    return ok


def trial_detection(rec: dict[str, Any]) -> float:
    """Share of the case's defects (fix commits) hit by a blocking finding within WINDOW lines of any span.

    An unparsed trial, such as a provider policy refusal, detects nothing.
    """
    spans = rec["defects"]
    groups = {s["fix"] for s in spans.values()}
    hit: set[str] = set()
    for f in rec.get("findings") or []:
        if (
            not isinstance(f, dict)
            or f.get("severity") != "blocking"
            or not isinstance(f.get("line"), int)
        ):
            continue
        for span in spans.values():
            if (
                same_file(str(f.get("file", "")), span["file"])
                and span["start"] - WINDOW <= f["line"] <= span["end"] + WINDOW
            ):
                hit.add(span["fix"])
    return len(hit) / len(groups)


def analyse(out: Path) -> list[str]:
    """Per-case detection averaged over reps, then paired differences between arms (95% t-interval)."""
    groups = latest_by_label(out)
    if len(groups) != 1:
        return [
            f"Expected one toolset label, found {len(groups)}; analyse each run separately."
        ]
    latest = next(iter(groups.values()))
    by_cell: dict[tuple[str, str], list[float]] = defaultdict(list)
    refused: dict[str, int] = defaultdict(int)
    walls: dict[str, list[float]] = defaultdict(list)
    for (case, arm, _), rec in latest.items():
        by_cell[(case, arm)].append(trial_detection(rec))
        refused[arm] += rec.get("findings") is None
        walls[arm].append(rec.get("wallSeconds", 0.0))
    arms = sorted({arm for _, arm in by_cell})
    cases = sorted({case for case, _ in by_cell})
    mean = {(c, a): statistics.mean(by_cell[(c, a)]) for c in cases for a in arms}
    lines = [
        f"Cases: {len(cases)}; window ±{WINDOW} lines; unparsed trials score 0.",
        "",
    ]
    lines += [
        "| Arm | Mean per-case detection | Unparsed trials | Leaked trials | Median wall |",
        "|---|---|---|---|---|",
    ]
    for a in arms:
        leaks = sum(
            bool(r.get("outsideAccess"))
            for (_, arm, _), r in latest.items()
            if arm == a
        )
        lines.append(
            f"| {a} | {statistics.mean(mean[(c, a)] for c in cases):.3f} | {refused[a]} | {leaks} |"
            f" {statistics.median(walls[a]):.0f}s |"
        )
    lines += ["", "Paired per-case differences (95% t-interval):", ""]
    for i, a in enumerate(arms):
        for b in arms[i + 1 :]:
            diffs = [mean[(c, a)] - mean[(c, b)] for c in cases]
            mu = statistics.mean(diffs)
            half = (
                T95[len(diffs) - 1] * statistics.stdev(diffs) / math.sqrt(len(diffs))
                if len(diffs) > 1
                else math.inf
            )
            lines.append(
                f"- {a} − {b}: {mu:+.3f} [{mu - half:+.3f}, {mu + half:+.3f}] (n={len(diffs)})"
            )
    lines += ["", "| Case | " + " | ".join(arms) + " |", "|---|" + "---|" * len(arms)]
    lines += [
        f"| {c} | " + " | ".join(f"{mean[(c, a)]:.2f}" for a in arms) + " |"
        for c in cases
    ]
    return lines


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    mine_cmd = sub.add_parser("mine", help="list candidate cases from fix commits")
    mine_cmd.add_argument("--since", default="2026-09-01")
    mine_cmd.add_argument(
        "--out", type=Path, required=True, help="candidates JSON (outside the skill)"
    )
    sub.add_parser("validate").add_argument("manifest", type=Path)
    sub.add_parser("analyse").add_argument("out", type=Path)
    args = parser.parse_args()
    if args.command == "mine":
        args.out.write_text(json.dumps(mine(args.since), indent=1))
    elif args.command == "validate":
        raise SystemExit(0 if validate(args.manifest) else 1)
    else:
        print("\n".join(analyse(args.out)))


if __name__ == "__main__":
    main()
