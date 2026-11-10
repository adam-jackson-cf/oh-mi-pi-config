"""CI wiring: how the repo's CI host runs ``.omp/gate.json`` ``checks.ci``.

``checks.ci`` is the CI contract on every host. What init writes depends on the gate's
``ciHost``:

- ``github``: ``.github/workflows/omp-gate.yml`` with one job named ``gate`` (the required
  status context). It checks out the head, runs the repo's ``--ci-setup`` command (the install
  step only the repo knows, e.g. ``uv sync --frozen``), then executes ``checks.ci`` from the
  gate file, so the workflow never repeats the check list. Without ``--ci-setup`` init writes no
  workflow and reports the gap.
- any other host, or none: no file. That host's runner reads ``checks.ci`` from the gate file
  (for example a self-hosted runner role from the machine's runner config).

An existing workflow file is kept, never replaced.
"""

from __future__ import annotations

import json

from omp_guardrails.findings import Finding

WORKFLOW_PATH = ".github/workflows/omp-gate.yml"
RULE = "GATE.local-ci-parity"
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


def workflow(setup: str) -> str:
    return (
        "# Runs .omp/gate.json checks.ci as the required context 'gate'. "
        "Written by omp-guardrails init.\n"
        "name: gate\n\n"
        "on:\n  pull_request:\n  push:\n\n"
        "jobs:\n  gate:\n    runs-on: ubuntu-latest\n    steps:\n"
        f"      - uses: {CHECKOUT}\n"
        f"      - name: setup\n        run: {json.dumps(setup)}\n"
        "      - name: checks.ci\n        run: |\n"
        f"{indented(RUN_CHECKS, 10)}\n"
    )


def planned_wiring(
    ci_host: object, setup: str | None, existing: bool
) -> tuple[str | None, list[str], list[Finding]]:
    """Workflow text to write (or None), report lines and setup gaps."""
    if ci_host != "github":
        host = f"the {ci_host} runner" if isinstance(ci_host, str) else "the CI host's runner"
        return None, [f"ci: {host} runs .omp/gate.json checks.ci; no workflow file needed"], []
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
                    "ciHost github needs a workflow that runs checks.ci, but no install step "
                    "is known",
                    "run init again with --ci-setup '<command that installs the dev "
                    "dependencies>'",
                )
            ],
        )
    return workflow(setup), [], []
