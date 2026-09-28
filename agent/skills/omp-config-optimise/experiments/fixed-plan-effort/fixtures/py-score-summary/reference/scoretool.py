"""Small command-line helpers for score files containing one number per line."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def read_scores(path: str) -> list[float]:
    """Read non-empty lines from a score file as floating-point values."""
    return [float(line.strip()) for line in Path(path).read_text().splitlines() if line.strip()]


def summarize_scores(values: list[float]) -> dict[str, int | float]:
    """Produce deterministic descriptive statistics without altering the input."""
    count = len(values)
    if count == 0:
        return {"count": 0, "unique": 0, "mean": 0.0, "median": 0.0}

    ordered = sorted(values)
    middle = count // 2
    median = float(ordered[middle]) if count % 2 else (ordered[middle - 1] + ordered[middle]) / 2
    return {
        "count": count,
        "unique": len(set(values)),
        "mean": round(sum(values) / count, 2),
        "median": median,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--count", action="store_true")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()
    scores = read_scores(args.input)
    if args.summary:
        print(json.dumps(summarize_scores(scores)))
    elif args.count:
        print(len(scores))


if __name__ == "__main__":
    main()
