from __future__ import annotations

from guardrails.test_evidence.junit import Dialect

GUARDS_PROPERTY = "omp.guards"
XFAIL_TYPE = "go.xfail"
SKIP_TYPE = "go.skip"
XFAIL_PREFIX = "xfail:"
DIALECT = Dialect(name="go", xfail_markers=(XFAIL_TYPE,), requires_exit_code=True)
