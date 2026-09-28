"""Small command-line helpers for score files containing one number per line."""

from __future__ import annotations

import argparse
from pathlib import Path


def read_scores(path: str) -> list[float]:
    """Read non-empty lines from a score file as floating-point values."""
    return [float(line.strip()) for line in Path(path).read_text().splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--count", action="store_true")
    args = parser.parse_args()
    scores = read_scores(args.input)
    if args.count:
        print(len(scores))


if __name__ == "__main__":
    main()
