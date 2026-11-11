"""CI wiring: how the repo's CI runner runs ``.omp/gate.json`` ``checks.ci``.

``checks.ci`` is the CI contract for every runner. The gate's ``ci`` object names the required
context (``ci.requiredContext``) that must be green on the exact head and the runner role
(``ci.runner``) that posts it; each machine's runner config resolves the role to commands.
What init writes depends on the role:

- ``github-actions``: ``.github/workflows/omp-gate.yml`` with one job ``gate`` whose name is
  the required context. It checks out the head, runs the repo's ``--ci-setup`` command (the
  install step only the repo knows, e.g. ``uv sync --frozen``), then executes ``checks.ci`` from
  the gate file, so the workflow never repeats the check list. Without ``--ci-setup`` init writes
  no workflow and reports the gap.
- any other role: no file. That role's runner reads ``checks.ci`` from the gate file and posts
  the required context.
- no ``ci`` object: no file, and the report asks for ``--runner``.

An existing workflow file is kept, never replaced.
"""

from __future__ import annotations

import json

from omp_guardrails.findings import Finding

WORKFLOW_PATH = ".github/workflows/omp-gate.yml"
RULE = "GATE.local-ci-parity"
GITHUB_ACTIONS = "github-actions"
CHECKOUT = "actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4"
RUN_CHECKS = """python3 - <<'PY'
import json, os, sys
argv = json.load(open(".omp/gate.json")).get("checks", {}).get("ci")
if not isinstance(argv, list) or not argv or not all(isinstance(a, str) and a for a in argv):
    sys.exit("gate: .omp/gate.json checks.ci must be a nonempty argv array")
os.execvp(argv[0], argv)
PY"""


def indented(text: str, spaces: int) -> str:
    pad = " " * spaces
    return "\n".join(pad + line if line else line for line in text.splitlines())


def workflow(setup: str, context: str) -> str:
    return (
        f"# Runs .omp/gate.json checks.ci as the required context {context!r}. "
        "Written by omp-guardrails init.\n"
        "name: gate\n\n"
        "on:\n  pull_request:\n  push:\n\n"
        f"jobs:\n  gate:\n    name: {json.dumps(context)}\n"
        "    runs-on: ubuntu-latest\n    steps:\n"
        f"      - uses: {CHECKOUT}\n"
        f"      - name: setup\n        run: {json.dumps(setup)}\n"
        "      - name: checks.ci\n        run: |\n"
        f"{indented(RUN_CHECKS, 10)}\n"
    )


def planned_wiring(
    ci: object, setup: str | None, existing: bool
) -> tuple[str | None, list[str], list[Finding]]:
    """Workflow text to write (or None), report lines and setup gaps."""
    if not isinstance(ci, dict):
        return (
            None,
            [
                "ci: .omp/gate.json declares no ci; run init again with --runner <role> "
                "(and --required-context when it is not 'gate') so merges require it"
            ],
            [],
        )
    runner, context = ci.get("runner"), ci.get("requiredContext")
    if runner != GITHUB_ACTIONS:
        return (
            None,
            [
                f"ci: runner role {runner!r} runs .omp/gate.json checks.ci and posts "
                f"{context!r}; no workflow file needed"
            ],
            [],
        )
    if existing:
        return None, [f"ci: kept the existing {WORKFLOW_PATH}"], []
    if not setup:
        return (
            None,
            [],
            [
                Finding(
                    RULE,
                    WORKFLOW_PATH,
                    f"runner {GITHUB_ACTIONS} needs a workflow that runs checks.ci, but no "
                    "install step is known",
                    "run init again with --ci-setup '<command that installs the dev "
                    "dependencies>'",
                )
            ],
        )
    return workflow(setup, str(context)), [], []
