"""Toolset label for trial records: was native `find` available, and which config ran.

Trials launch `omp -p --no-extensions` without `--config` or `--tools`, so they inherit the
global agent config. Every record stores the label below so analysis never mixes toolsets.
Records written before labelling existed group as PRE_FIND.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from collections.abc import Callable

PRE_FIND = ("pre-find", "-")


def _omp(args: list[str], cwd: str) -> str:
    return subprocess.run(["omp", *args], cwd=cwd, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=60, check=True).stdout


def find_available(enabled: str, judge: str | None) -> bool:
    """`find` is offered when it is not switched off and a judge role is configured.

    The raw `findEnabled` and `judgeRole` values are stored alongside, so an `auto` setting
    that omp resolved differently stays visible in the record.
    """
    return bool(judge) and enabled != "off"


def config_hash(settings: dict) -> str:
    """Hash the effective settings omp resolved (defaults plus every layer), not one file.

    `auth.*` keys are dropped so a credential rotation does not split trial groups; values
    are only hashed, never stored.
    """
    effective = {k: v.get("value") if isinstance(v, dict) else v
                 for k, v in settings.items() if not k.startswith("auth.")}
    return hashlib.sha256(json.dumps(effective, sort_keys=True).encode()).hexdigest()


def toolset_label(run: Callable[[list[str], str], str] = _omp) -> dict:
    """Read the effective global config from a neutral cwd (trial workspaces carry no .omp layer)."""
    with tempfile.TemporaryDirectory() as cwd:
        settings = json.loads(run(["config", "list", "--json"], cwd))
    enabled = str(settings["find.enabled"]["value"])
    judge = (settings.get("modelRoles", {}).get("value") or {}).get("judge")
    return {"findAvailable": find_available(enabled, judge), "findEnabled": enabled, "judgeRole": judge,
            "configHash": config_hash(settings)}


def label_of(rec: dict) -> tuple[str, str]:
    if "findAvailable" not in rec:
        return PRE_FIND
    return ("find" if rec["findAvailable"] else "no-find", str(rec.get("configHash", "-"))[:12])


def describe(label: tuple[str, str]) -> str:
    return label[0] if label == PRE_FIND else f"{label[0]} @ config {label[1]}"


def warn_if_mixed(labels: set[tuple[str, str]]) -> str | None:
    if len(labels) < 2:
        return None
    return ("WARNING: results span different toolsets ("
            + ", ".join(describe(l) for l in sorted(labels))
            + "). Compare arms only within one toolset group.")
