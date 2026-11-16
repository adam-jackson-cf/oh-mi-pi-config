"""``python -m omp_guardrails.adapters.pytest.conformance [CASE ...]``: pass or fail per case.

Runs without a repository profile, so the reference's own gate can prove the adapter. Exit 0
when every selected case passes, 1 otherwise.
"""

from __future__ import annotations

import sys

from omp_guardrails.adapters.pytest.conformance import run_conformance


def main(argv: list[str]) -> int:
    results = run_conformance(argv)
    for result in results:
        status = "pass" if result.passed else "FAIL"
        line = f"{status} {result.case_id} ({result.rule})"
        print(line if result.passed else f"{line}: {result.detail}")
    failed = sum(not result.passed for result in results)
    print(f"{len(results) - failed}/{len(results)} conformance cases pass")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
