"""CLI for controlled instruction evaluations."""
from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import analysis
import runner
import scoring
import validation


def _json(path: Path, value: dict[str, Any] | None = None) -> dict[str, Any]:
    if value is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return value
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid JSON: {path}") from error
    if not isinstance(result, dict):
        raise ValueError(f"JSON object required: {path}")
    return result

def _payload(path: str) -> dict[str, Any]:
    value = _json(Path(path).expanduser())
    required = {"instruction", "provider", "model", "repository"}
    optional = {"thinking", "run_id", "trials_per_condition", "practical_effect_threshold", "significance_level"}
    if not required <= set(value) or not set(value) <= required | optional:
        raise ValueError("payload has missing or extra fields")
    for key in required | (set(value) & {"thinking", "run_id"}):
        if not isinstance(value[key], str) or not value[key]:
            raise ValueError(f"payload {key} must be a nonempty string")
    merged = {"thinking": "medium", "run_id": None, "trials_per_condition": 3, "practical_effect_threshold": .2, "significance_level": .05, **value}
    return merged


def _run_dir(root: Path, run_id: str) -> Path:
    repository = _repository_root(root.expanduser().resolve())
    if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
        raise ValueError("run_id must be one path component")
    experiments = repository / ".experiments"
    base = experiments / "instruct-eval"
    path = base / run_id
    if any(component.is_symlink() for component in (experiments, base, path)):
        raise ValueError("experiment storage path cannot contain symlinks")
    return path


def _omp_json(argv: list[str]) -> Any:
    try:
        result = subprocess.run(argv, text=True, capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError(f"OMP preflight failed: {error}") from error
    if result.returncode:
        raise ValueError(f"OMP preflight failed: {result.stderr.strip() or result.stdout.strip()}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ValueError("OMP did not return JSON model metadata") from error


def _model_entries(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("models", "data"):
            if key in value:
                return _model_entries(value[key])
    raise ValueError("OMP models JSON has no model list")


def _repository_root(root: Path) -> Path:
    if root.name == ".todo":
        raise ValueError("repository root cannot be .todo")
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError(f"Git repository root check failed: {error}") from error
    if result.returncode or not result.stdout.strip():
        raise ValueError("repository root must be a Git repository root")
    if root != Path(result.stdout.strip()).resolve():
        raise ValueError("repository root must be the Git repository root")
    return root


def _preflight(root: Path, provider: str, model: str, thinking: str) -> str:
    try:
        version = subprocess.run(["omp", "--version"], text=True, capture_output=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError(f"OMP is unavailable: {error}") from error
    if version.returncode or not (version.stdout or version.stderr).strip():
        raise ValueError("OMP version check failed")
    matches = [entry for entry in _model_entries(_omp_json(["omp", "models", "--json"])) if entry.get("provider") == provider and entry.get("model", entry.get("id")) == model]
    if len(matches) != 1:
        raise ValueError("requested provider/model is not available")
    supported = matches[0].get("thinking", matches[0].get("thinking_levels", matches[0].get("supported_thinking")))
    if not isinstance(supported, list) or thinking not in supported:
        raise ValueError("requested thinking level is unsupported")
    base = root / ".experiments" / "instruct-eval"
    try:
        base.mkdir(parents=True, exist_ok=True)
        probe = base / ".write-probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as error:
        raise ValueError("repository experiment storage is not writable") from error
    return (version.stdout or version.stderr).strip()


def init(args: argparse.Namespace) -> dict[str, Any]:
    payload = _payload(args.payload)
    root = Path(payload["repository"]).expanduser().resolve()
    if not root.is_dir(): raise ValueError("repository root is not a directory")
    root = _repository_root(root)
    instruction = payload["instruction"]
    trials_per_condition = payload["trials_per_condition"]
    practical_effect_threshold = payload["practical_effect_threshold"]
    significance_level = payload["significance_level"]
    if isinstance(trials_per_condition, bool) or not isinstance(trials_per_condition, int) or trials_per_condition < 1: raise ValueError("trials_per_condition must be a positive integer")
    if isinstance(practical_effect_threshold, bool) or not isinstance(practical_effect_threshold, (int, float)) or not 0 <= practical_effect_threshold <= 1: raise ValueError("practical_effect_threshold must be in [0, 1]")
    if isinstance(significance_level, bool) or not isinstance(significance_level, (int, float)) or not 0 < significance_level <= 1: raise ValueError("significance_level must be in (0, 1]")
    run_id = payload["run_id"] or f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    run_dir = _run_dir(root, run_id)
    if run_dir.exists(): raise ValueError("run already exists")
    omp_version = _preflight(root, payload["provider"], payload["model"], payload["thinking"])
    request = {"schema_version": 1, "run_id": run_id, "created_at": datetime.now(timezone.utc).isoformat(), "instruction": instruction, "provider": payload["provider"], "model": payload["model"], "thinking": payload["thinking"], "trials_per_condition": trials_per_condition, "practical_effect_threshold": practical_effect_threshold, "significance_level": significance_level, "protocol_path": "protocol.json", "repository": str(root), "python_version": platform.python_version(), "omp_version": omp_version}
    return _json(run_dir / "request.json", request)


def freeze(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = _run_dir(Path(args.root or Path.cwd()).resolve(), args.run_id)
    if any((run_dir / name).exists() for name in ("protocol.json", "manifest.json", "checksums.json")):
        raise ValueError("run is already frozen; amend to create a successor run")
    protocol = validation.freeze_protocol(_json(run_dir / "request.json"), _json(Path(args.spec)))
    review = validation.oracle_review(protocol)
    _json(run_dir / "oracle-review.json", review)
    _json(run_dir / "protocol.json", protocol)
    _json(run_dir / "manifest.json", validation.manifest(protocol))
    _json(run_dir / "checksums.json", validation.checksums(run_dir, protocol))
    return protocol


def amend(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = _run_dir(Path(args.root or Path.cwd()).resolve(), args.run_id)
    return validation.amend(_json(run_dir / "protocol.json"), _json(Path(args.amendment)), run_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--root")
    subs = parser.add_subparsers(dest="command", required=True)
    init_p = subs.add_parser("init"); init_p.add_argument("--payload", required=True)
    for name in ("freeze", "amend", "run", "score", "analyze", "validate"):
        command = subs.add_parser(name); command.add_argument("run_id")
        if name == "freeze": command.add_argument("--spec", required=True)
        if name == "amend": command.add_argument("--amendment", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "init": result = init(args)
        elif args.command == "freeze": result = freeze(args)
        elif args.command == "amend": result = amend(args)
        else:
            run_dir = _run_dir(Path(args.root or Path.cwd()).resolve(), args.run_id); protocol = _json(run_dir / "protocol.json"); validation.validate_frozen(run_dir, protocol)
            if args.command == "run": result = runner.run_trials(run_dir, protocol)
            elif args.command == "score": result = scoring.blind_score(run_dir, protocol)
            elif args.command == "analyze":
                analysis_result = analysis.analyze(run_dir, protocol)
                result = {"analysis": analysis_result, "verification": validation.validate_run(run_dir, protocol)}
            else: result = validation.validate_run(run_dir, protocol)
        print(json.dumps(result, indent=2, sort_keys=True)); return 0
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr); return 2

if __name__ == "__main__": raise SystemExit(main())
