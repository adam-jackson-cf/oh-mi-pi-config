"""Rule coverage: the rule-evidence ledger check, then each repo checker's canaries run live.

``omp-guardrails rule-coverage`` first runs every ``rule-evidence`` check
(``catalogue.rule_evidence``). For each valid ledger row it then:

1. runs the canary ``command`` from the repo root once with ``{fixture}`` replaced by the
   ``passing`` fixture, which must exit 0, and once with the ``violating`` fixture, which must
   exit non-zero and print the ``diagnostic`` marker, each within ``timeout_seconds``;
2. for a ``git_hook`` row, runs ``hook_install_check.command``, which must exit 0 and print its
   ``diagnostic``.

Only repo checkers have rows, so a repo with none runs nothing. Rules the package proves (tool
rules and rules with a ``reference`` module) are proven by the skill's own test suite.

This check spawns processes, so it belongs in ``checks.ci`` or a setup run, never in
``checks.commit`` (``init`` keeps it out of the commit gate).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from omp_guardrails.catalogue import rule_evidence
from omp_guardrails.catalogue.scratch import output_tail, run_bounded
from omp_guardrails.findings import Finding

HOOK_TIMEOUT_S = 60


def run_proof(repo: Path, proof: rule_evidence.Proof) -> str | None:
    """The first way the repo checker failed its canaries or installation check, if any."""
    canary = proof.canary
    timeout = canary.get("timeout_seconds", rule_evidence.DEFAULT_CANARY_TIMEOUT_S)
    marker = canary["diagnostic"]
    for key, should_pass in (("passing", True), ("violating", False)):
        fixture = str((repo / canary[key]).resolve())
        argv = [part.replace("{fixture}", fixture) for part in canary["command"]]
        result = run_bounded(argv, repo, timeout=timeout)
        if should_pass and result.returncode != 0:
            return f"compliant canary failed: {output_tail(result)}"
        if not should_pass and result.returncode == 0:
            return "violating canary passed"
        if not should_pass and marker not in result.stdout + result.stderr:
            return f"violating canary did not print its diagnostic {marker!r}"
    hook = proof.hook_install_check
    if hook is None:
        return None
    result = run_bounded(hook["command"], repo, timeout=HOOK_TIMEOUT_S)
    if result.returncode != 0:
        return f"hook installation check failed: {output_tail(result)}"
    if hook["diagnostic"] not in result.stdout + result.stderr:
        return "hook installation check did not print its diagnostic"
    return None


def run(repo: Path, profile: dict, argv: list[str]) -> list[Finding]:
    findings, proofs = rule_evidence.check(repo, profile)
    path = rule_evidence.ledger_path(profile)
    for proof in proofs:
        try:
            failure = run_proof(repo, proof)
        except (OSError, subprocess.TimeoutExpired) as error:
            failure = f"canary could not complete: {error}"
        if failure:
            findings.append(
                Finding(proof.rule_id, path, failure, "repair the repo checker or its canary")
            )
    return findings
