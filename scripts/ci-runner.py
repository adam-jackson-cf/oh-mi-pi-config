#!/usr/bin/env python3
"""Run a CI runner role's submit, wait, status or logs command for one exact revision.

Roles map to commands in the machine config `<omp base>/ci-runners.json` (override the
path with OMP_CI_RUNNERS), written per device by `scripts/device-rollout.py`. A null
submit means the runner starts on push. A null wait or status uses the built-in GitHub
poller: the required context must be a `success` commit status or a successful check run
on the exact SHA, whoever posts it.

Exit codes: 0 success, 1 failure or error, 2 configuration error, 3 pending (status),
124 built-in wait timed out; configured commands pass their own exit code through.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

BASE = Path(__file__).resolve().parents[1]
OPERATIONS = ("submit", "wait", "status", "logs")


class ConfigError(Exception):
    pass


def config_path():
    return Path(os.environ.get("OMP_CI_RUNNERS") or BASE / "ci-runners.json")


def rollout_hint():
    return f"run python3 {BASE / 'scripts' / 'device-rollout.py'} to write ci-runners.json"


def load_role(role):
    path = config_path()
    try:
        document = json.loads(path.read_text())
    except FileNotFoundError:
        raise ConfigError(f"{path} does not exist; {rollout_hint()}") from None
    except (OSError, ValueError):
        raise ConfigError(f"{path} is not readable JSON; {rollout_hint()}") from None
    runners = document.get("runners") if isinstance(document, dict) else None
    if not isinstance(document, dict) or document.get("schemaVersion") != 1 or not isinstance(runners, dict):
        raise ConfigError(f"{path} is not a schemaVersion 1 runner config; {rollout_hint()}")
    spec = runners.get(role)
    if not isinstance(spec, dict):
        raise ConfigError(f"runner role {role!r} is not configured in {path}; {rollout_hint()}")
    for operation in OPERATIONS:
        argv = spec.get(operation, "missing")
        if argv is not None and (not isinstance(argv, list) or not argv
                                 or any(not isinstance(arg, str) or not arg for arg in argv)):
            raise ConfigError(f"runner role {role!r} has an invalid {operation!r} command in {path}; "
                              f"{rollout_hint()}")
    return spec


def substitute(argv, values):
    """Replace {repo} {sha} {timeout} {tail} inside each argv element; other braces stay."""
    pattern = re.compile(r"\{(" + "|".join(values) + r")\}")
    return [pattern.sub(lambda match: values[match.group(1)], arg) for arg in argv]


def gh_json(path):
    result = subprocess.run(["gh", "api", path], capture_output=True, text=True, check=False)
    if result.returncode:
        # gh output is not echoed: it can carry request details.
        raise RuntimeError(f"gh api {path} failed with exit code {result.returncode}")
    return json.loads(result.stdout)


def context_state(repo, sha, context):
    """Return success, failure or pending for the context on the exact SHA."""
    states = []
    combined = gh_json(f"repos/{repo}/commits/{sha}/status")
    for status in combined.get("statuses") or []:
        if status.get("context") == context:
            states.append({"success": "success", "pending": "pending"}.get(status.get("state"), "failure"))
    runs = gh_json(f"repos/{repo}/commits/{sha}/check-runs?per_page=100").get("check_runs") or []
    named = [run for run in runs if run.get("name") == context]
    if named:
        latest = max(named, key=lambda run: run.get("id") or 0)
        if latest.get("status") != "completed":
            states.append("pending")
        else:
            states.append("success" if latest.get("conclusion") == "success" else "failure")
    if "failure" in states:
        return "failure"
    if "pending" in states or not states:
        return "pending"
    return "success"


def report(repo, sha, context, state):
    print(json.dumps({"repo": repo, "sha": sha, "context": context, "state": state}))


def poll(args):
    deadline = time.monotonic() + args.timeout
    while True:
        state = context_state(args.repo, args.sha, args.context)
        if args.operation == "status" or state != "pending":
            report(args.repo, args.sha, args.context, state)
            return {"success": 0, "failure": 1}.get(state, 3)
        if time.monotonic() >= deadline:
            report(args.repo, args.sha, args.context, "timeout")
            print(f"Timed out after {args.timeout:g}s waiting for {args.context} on {args.sha}",
                  file=sys.stderr)
            return 124
        time.sleep(min(args.interval, max(deadline - time.monotonic(), 0)))


def parse(argv):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("operation", choices=OPERATIONS)
    parser.add_argument("role")
    parser.add_argument("repo")
    parser.add_argument("sha")
    parser.add_argument("--context", default="gate",
                        help="required status context or check-run name (built-in poller)")
    parser.add_argument("--timeout", type=float, default=1200, help="wait limit in seconds")
    parser.add_argument("--tail", type=int, default=200, help="log lines to show")
    parser.add_argument("--interval", type=float, default=10,
                        help="built-in poller interval in seconds")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo):
        parser.error("repo must be OWNER/REPO")
    if not re.fullmatch(r"[0-9a-f]{40}", args.sha):
        parser.error("sha must be 40 lowercase hex characters")
    if not args.context.strip():
        parser.error("--context must be non-empty")
    if not args.timeout >= 0 or args.tail < 0 or not args.interval > 0:
        parser.error("--timeout and --tail must be non-negative and --interval positive")
    return args


def main(argv=None):
    args = parse(argv)
    try:
        spec = load_role(args.role)
    except ConfigError as error:
        print(str(error), file=sys.stderr)
        return 2
    command = spec[args.operation]
    if command is None:
        if args.operation == "submit":
            print(f"runner {args.role!r} starts on push; nothing to submit for {args.sha}")
            return 0
        if args.operation == "logs":
            print(f"runner {args.role!r} has no logs command configured", file=sys.stderr)
            return 2
        try:
            return poll(args)
        except (RuntimeError, ValueError, OSError) as error:
            message = str(error) if isinstance(error, RuntimeError) else "GitHub status lookup failed"
            print(message, file=sys.stderr)
            return 1
    values = {"repo": args.repo, "sha": args.sha, "timeout": f"{args.timeout:g}", "tail": str(args.tail)}
    try:
        return subprocess.run(substitute(command, values), check=False).returncode
    except OSError:
        print(f"runner {args.role!r} {args.operation} command could not start: {command[0]}",
              file=sys.stderr)
        return 127


if __name__ == "__main__":
    sys.exit(main())
