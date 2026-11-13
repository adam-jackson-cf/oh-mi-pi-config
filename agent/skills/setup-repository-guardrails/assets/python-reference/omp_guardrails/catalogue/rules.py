"""The rule catalogue that ships in this package, and which rules the package proves.

The catalogue (``omp_guardrails/data/guardrail-rules.json``) lives only here. A rule is **proven by the package** when it is
``kind: "tool"`` (a native tool enforces its configuration) or names a ``reference`` module (an
omp-guardrails checker enforces it). The package's own test suite proves each such rule once,
for every repository.
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Any

PACKAGED_CATALOGUE = "guardrail-rules.json"


def packaged_catalogue() -> dict[str, Any]:
    data = resources.files("omp_guardrails.data").joinpath(PACKAGED_CATALOGUE).read_bytes()
    return json.loads(data)


def proven_by_package(rule: dict[str, Any]) -> bool:
    return rule.get("kind") == "tool" or bool(rule.get("reference"))
