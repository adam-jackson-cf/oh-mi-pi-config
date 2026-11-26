from __future__ import annotations

from functools import cache
from typing import Any

from guardrails.foundation import conventions
from guardrails.test_report.junit import Dialect

XFAIL_TYPE = "go.xfail"
SKIP_TYPE = "go.skip"
XFAIL_PREFIX = "xfail:"
DIALECT = Dialect(name="go", xfail_markers=(XFAIL_TYPE,), requires_exit_code=True)
PATTERNS_FILE = "go-patterns.json"


@cache
def vocabulary() -> dict[str, Any]:
    """go-patterns.json: the packages and calls each Go lint rule reads, and its fixes."""
    found: dict[str, Any] = conventions.data(PATTERNS_FILE)
    return found
