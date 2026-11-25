from __future__ import annotations

from guardrails.test_report.junit import Dialect

XFAIL_TYPE = "go.xfail"
SKIP_TYPE = "go.skip"
XFAIL_PREFIX = "xfail:"
DIALECT = Dialect(name="go", xfail_markers=(XFAIL_TYPE,), requires_exit_code=True)
