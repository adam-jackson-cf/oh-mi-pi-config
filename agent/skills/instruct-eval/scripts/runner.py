"""Execute frozen instruction-evaluation trials without shell commands."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import subprocess
from pathlib import Path
from string import Template
from typing import Any


def _json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")

def _reject_symlinks(source: Path) -> None:
    if source.is_symlink() or any(path.is_symlink() for path in source.rglob("*")):
        raise ValueError(f"source tree contains a symlink: {source}")


def _copy(source: Path, destination: Path) -> None:
    if not source.is_dir(): raise ValueError(f"source is not a directory: {source}")
    _reject_symlinks(source)
    if destination.exists(): raise ValueError(f"workspace already exists: {destination}")
    shutil.copytree(source, destination)

def _tree(path: Path) -> dict[str, str]:
    return {str(item.relative_to(path)): hashlib.sha256(item.read_bytes()).hexdigest() for item in sorted(path.rglob("*")) if item.is_file()}

def _changed(before: dict[str, str], after: dict[str, str]) -> list[str]: return sorted(key for key in set(before) | set(after) if before.get(key) != after.get(key))
def _production_file(path: Path, root: Path) -> bool:
    parts = {part.lower() for part in path.relative_to(root).parts}
    name = path.name.lower()
    return not (parts & {"test", "tests", "__tests__", "oracle", "oracles", "harness", "harnesses"} or name.startswith(("test_", "test-")) or name.endswith(("_test.py", ".test.js", ".test.ts")))
def _nloc(path: Path) -> int:
    return sum(1 for item in path.rglob("*") if item.is_file() and _production_file(item, path) for line in item.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip() and not line.lstrip().startswith(("#", "//", "/*", "*")))

def _render(path: Path, values: dict[str, str], allowed: set[str]) -> str:
    try: template = Template(path.read_text(encoding="utf-8"))
    except OSError as error: raise ValueError(f"template unavailable: {path}") from error
    names = {match.group("named") or match.group("braced") for match in template.pattern.finditer(template.template) if match.group("named") or match.group("braced")}
    if names != allowed: raise ValueError(f"template has invalid placeholders: {path}")
    try: return template.substitute(values)
    except (KeyError, ValueError) as error: raise ValueError(f"strict template rendering failed: {path}") from error

def execute_omp(workspace: Path, prompt: str, protocol: dict[str, Any]) -> dict[str, Any]:
    if not workspace.is_dir() or not isinstance(prompt, str) or not prompt: raise ValueError("OMP requires a workspace and nonempty prompt")
    argv = ["omp", "--model", f"{protocol['provider']}/{protocol['model']}", "--thinking", protocol["thinking"], "--approval-mode", protocol["permissions"]["approval_mode"], "--tools", ",".join(protocol["permissions"]["tools"]), "--no-session", "-p", prompt]
    try: result = subprocess.run(argv, cwd=workspace, text=True, capture_output=True, timeout=protocol["execution"]["timeout_seconds"], check=False)
    except subprocess.TimeoutExpired as error: return {"argv": argv, "exit_code": None, "stdout": error.stdout or "", "stderr": error.stderr or "", "timed_out": True}
    except OSError as error: raise ValueError(f"OMP execution failed: {error}") from error
    return {"argv": argv, "exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr, "timed_out": False}

def _tests(workspace: Path, probes: list[dict[str, Any]], timeout: int, probe_workspace: Path | None = None) -> dict[str, Any]:
    outcomes = []
    for probe in probes:
        argv = probe["command"]
        cwd, env = workspace, None
        if probe_workspace is not None:
            _reject_symlinks(probe_workspace)
            probe_workspace, evaluated_workspace = probe_workspace.resolve(), workspace.resolve()
            if probe_workspace == evaluated_workspace or probe_workspace in evaluated_workspace.parents or evaluated_workspace in probe_workspace.parents:
                raise ValueError("hidden probe fixture must be outside the evaluated workspace")
            cwd, env = probe_workspace, {**os.environ, "OMP_EVALUATED_WORKSPACE": str(evaluated_workspace)}
        try: result = subprocess.run(argv, cwd=cwd, env=env, text=True, capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as error:
            exit_code, stdout, stderr, timed_out = None, error.stdout or "", error.stderr or "", True
        except OSError as error: raise ValueError(f"probe {probe['id']} failed to start: {error}") from error
        else: exit_code, stdout, stderr, timed_out = result.returncode, result.stdout, result.stderr, False
        passed = not timed_out and ((exit_code == 0) == (probe["expected_outcome"] == "pass"))
        outcomes.append({"id": probe["id"], "command": argv, "expected_outcome": probe["expected_outcome"], "argv": argv, "exit_code": exit_code, "stdout": stdout, "stderr": stderr, "timed_out": timed_out, "passed": passed})
    return {"passed": all(item["passed"] for item in outcomes), "outcomes": outcomes}

def _hidden_tests(workspace: Path, probes: list[dict[str, Any]], timeout: int) -> dict[str, Any]:
    outcomes = [_tests(workspace, [probe], timeout, Path(probe["fixture"]))["outcomes"][0] for probe in probes]
    return {"passed": all(outcome["passed"] for outcome in outcomes), "outcomes": outcomes}

def run_trials(run_dir: Path, protocol: dict[str, Any]) -> dict[str, Any]:
    conditions = protocol.get("conditions")
    if not isinstance(conditions, dict) or set(conditions) != {"baseline", "candidate"}: raise ValueError("protocol conditions are invalid")
    if conditions["baseline"].get("instruction") != "" or conditions["candidate"].get("instruction") != protocol["instruction"]: raise ValueError("protocol treatment isolation is invalid")
    initial_template, checkpoint_template = Path(protocol["assets"]["initial_template"]), Path(protocol["assets"]["checkpoint_template"])
    timeout = protocol["execution"]["timeout_seconds"]
    planned = [(scenario, condition, number) for scenario in protocol["scenarios"] for condition in ("baseline", "candidate") for number in range(1, protocol["trials_per_condition"] + 1)]
    secrets.SystemRandom().shuffle(planned)
    trials = []
    for ordinal, (scenario, condition, number) in enumerate(planned, 1):
        alias = f"trial-{ordinal:04d}"
        root = run_dir / "workspaces" / alias; initial_workspace = root / "initial"; context = root / "context"
        fixture = Path(scenario["fixture"])
        _reject_symlinks(fixture)
        fixture = fixture.resolve()
        forbidden_fixture_names = {"hidden_tests", "oracle", "oracles", "harness", "harnesses"}
        if fixture.name.lower() in forbidden_fixture_names or any(path.name.lower() in forbidden_fixture_names for path in fixture.rglob("*")):
            raise ValueError("base fixture contains hidden oracle paths")
        hidden_probe_roots = [Path(probe["fixture"]) for probe in scenario["hidden_tests"]]
        for probe_root in hidden_probe_roots:
            _reject_symlinks(probe_root)
            resolved_probe = probe_root.resolve()
            if resolved_probe == fixture or resolved_probe.is_relative_to(fixture) or fixture.is_relative_to(resolved_probe):
                raise ValueError("hidden probe fixture must be outside the base fixture")
        _copy(fixture, initial_workspace)
        before = _tree(initial_workspace)
        initial_prompt = _render(initial_template, {"initial_request": scenario["initial_request"], "instruction_block": conditions[condition]["instruction"]}, {"initial_request", "instruction_block"})
        initial = execute_omp(initial_workspace, initial_prompt, protocol); _reject_symlinks(initial_workspace); after = _tree(initial_workspace); initial_nloc = _nloc(initial_workspace); _copy(initial_workspace, context)
        checkpoints = []
        for checkpoint in scenario["checkpoints"]:
            workspace = root / "checkpoints" / checkpoint["id"]; _copy(context, workspace); prior = _tree(workspace)
            prompt = _render(checkpoint_template, {"checkpoint_request": checkpoint["request"]}, {"checkpoint_request"})
            outcome = execute_omp(workspace, prompt, protocol); _reject_symlinks(workspace); current = _tree(workspace); checkpoint_nloc = _nloc(workspace)
            public, hidden = _tests(workspace, scenario["public_tests"], timeout), _hidden_tests(workspace, scenario["hidden_tests"], timeout)
            _reject_symlinks(workspace)
            checkpoints.append({"id": checkpoint["id"], "workspace": str(workspace), "prompt": prompt, "omp": outcome, "public": public, "hidden": hidden, "workspace_checksum_before": prior, "workspace_checksum_after": current, "changed_files": _changed(prior, current), "production_nloc": checkpoint_nloc, "passed": outcome["exit_code"] == 0 and public["passed"] and hidden["passed"]})
        public, hidden = _tests(initial_workspace, scenario["public_tests"], timeout), _hidden_tests(initial_workspace, scenario["hidden_tests"], timeout)
        _reject_symlinks(initial_workspace)
        trials.append({"scenario": scenario["id"], "condition": condition, "trial": number, "alias": alias, "workspace": str(initial_workspace), "context": str(context), "initial_prompt": initial_prompt, "initial": initial, "public": public, "hidden": hidden, "workspace_checksum_before": before, "workspace_checksum_after": after, "context_checksum": _tree(context), "changed_files": _changed(before, after), "production_nloc": initial_nloc, "checkpoints": checkpoints, "passed": initial["exit_code"] == 0 and public["passed"] and hidden["passed"]})
    evidence = {"run_id": protocol["run_id"], "trials": trials}; _json(run_dir / "trials.json", evidence)
    return evidence
