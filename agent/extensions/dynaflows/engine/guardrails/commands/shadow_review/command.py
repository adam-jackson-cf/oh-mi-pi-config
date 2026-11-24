"""guardrails shadow-review: label logged judgment decisions and measure shadow questions.

  export <judgment> [--question Q] [--sample-passes N] [--mode M] [--seed S]
      A blind labelling batch ({guide, items}) of unreviewed flags plus a random sample of passes.
  import <labels.json> [--labeller council|arbiter] [--batch NAME]
      Council or arbiter output back into the store and the judgment's eval data; prunes payloads.
  report [<judgment>]
      shadow_progress, and precision at the cutoff with flag counts per question.
  inspector <judgment> [--question Q] [--verdict V] [--outcome O] [--mode M] <out.jev.json>
      Jev Inspector session file(s) rebuilt from stored payloads.

--machine nuc reads a read-only copy of that machine's store alongside this one.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from guardrails.commands.shadow_review import batches, inspector
from guardrails.commands.shadow_review.stores import (
    MACHINES,
    ReviewError,
    Stores,
    opened_stores,
)
from guardrails.foundation import decision_store

# The harness's judgments, beside this engine: agent/extensions/dynaflows/judgments.
JUDGMENTS = Path(__file__).resolve().parents[4] / "judgments"
JUDGMENT_MODES = ("enforced", "shadow")
VERDICTS = ("true_flag", "false_flag", "missed", "true_pass")
OUTCOMES = ("flag", "pass", "escalate")
QUESTION_REPORT = """
SELECT judgment, question, question_version, mode, MAX(threshold) AS threshold,
  COUNT(*) AS decisions,
  SUM(status != 'ok') AS errors,
  SUM(outcome = 'flag') AS flags,
  SUM(outcome = 'pass') AS passes,
  SUM(outcome = 'escalate') AS escalations,
  SUM(outcome = 'flag' AND review_label IN ('0', '1')) AS labelled_flags,
  SUM(verdict = 'true_flag') AS true_flags,
  SUM(verdict = 'false_flag') AS false_flags,
  SUM(verdict = 'missed') AS missed,
  CAST(SUM(verdict = 'true_flag') AS REAL)
    / NULLIF(SUM(outcome = 'flag' AND review_label IN ('0', '1')), 0) AS precision
FROM decisions
WHERE source = 'jev' AND (? IS NULL OR judgment = ?)
GROUP BY judgment, question, question_version, mode
ORDER BY judgment, question, question_version, mode
"""


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="guardrails shadow-review",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    root.add_argument(
        "--store",
        type=Path,
        default=None,
        help="this machine's decision store (default: the agent dir's)",
    )
    root.add_argument(
        "--machine",
        action="append",
        default=[],
        choices=sorted(MACHINES),
        help="also read this machine's store",
    )
    root.add_argument(
        "--judgments",
        type=Path,
        default=JUDGMENTS,
        help="the judgments directory (label guides, eval data)",
    )
    commands = root.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="a blind labelling batch")
    export.add_argument("judgment")
    export.add_argument("--question")
    export.add_argument("--sample-passes", type=int, default=10)
    export.add_argument("--mode", choices=JUDGMENT_MODES, default="shadow")
    export.add_argument("--seed", type=int, default=0)
    labels = commands.add_parser(
        "import", help="labels back into the store and eval data"
    )
    labels.add_argument("labels", type=Path)
    labels.add_argument("--labeller", choices=batches.LABELLERS, default="council")
    labels.add_argument("--batch")
    report = commands.add_parser(
        "report", help="shadow progress and precision per question"
    )
    report.add_argument("judgment", nargs="?")
    session = commands.add_parser("inspector", help="Jev Inspector session file(s)")
    session.add_argument("judgment")
    session.add_argument("out", type=Path)
    session.add_argument("--question")
    session.add_argument("--verdict", choices=VERDICTS)
    session.add_argument("--outcome", choices=OUTCOMES)
    session.add_argument("--mode", choices=JUDGMENT_MODES)
    return root


def rows(found: list[Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in found]


def report(stores: Stores, judgment: str | None) -> dict[str, Any]:
    progress = stores.query(
        "SELECT * FROM shadow_progress WHERE (? IS NULL OR judgment = ?) "
        "ORDER BY judgment, question, question_version",
        (judgment, judgment),
    )
    shadow = [
        {
            **row,
            "shadow_exit": json.loads(row["shadow_exit"]),
            "exit_met": bool(row["exit_met"]),
        }
        for row in rows(progress)
    ]
    return {
        "shadow_progress": shadow,
        "questions": rows(stores.query(QUESTION_REPORT, (judgment, judgment))),
    }


def guide(judgments: Path, judgment: str) -> str:
    path = judgments / judgment / "label-guide.md"
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReviewError(f"cannot read the label guide {path}: {exc}") from exc


def run(args: argparse.Namespace, stores: Stores) -> Any:
    if args.command == "export":
        request = batches.ExportRequest(
            args.judgment, args.question, args.sample_passes, args.mode, args.seed
        )
        return batches.export_batch(
            stores, request, guide(args.judgments, args.judgment)
        )
    if args.command == "import":
        try:
            document = json.loads(args.labels.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ReviewError(f"cannot read labels {args.labels}: {exc}") from exc
        labels = batches.parse_labels(document, args.labeller)
        imported = batches.ImportRequest(
            args.labeller, args.batch or args.labels.stem, args.judgments
        )
        return batches.import_labels(stores, labels, imported)
    if args.command == "report":
        return report(stores, args.judgment)
    selection = inspector.InspectorFilter(
        args.judgment, args.question, args.verdict, args.outcome, args.mode
    )
    return {"sessions": inspector.write_sessions(stores, selection, args.out)}


def main(argv: list[str]) -> int:
    args = parser().parse_args(argv)
    if getattr(args, "sample_passes", 0) < 0:
        print("shadow-review: --sample-passes must be 0 or more", file=sys.stderr)
        return 2
    try:
        local = decision_store.store_path() if args.store is None else args.store
        with opened_stores(local, args.machine) as stores:
            result = run(args, stores)
    except (ReviewError, decision_store.StoreError) as exc:
        print(f"shadow-review: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0
