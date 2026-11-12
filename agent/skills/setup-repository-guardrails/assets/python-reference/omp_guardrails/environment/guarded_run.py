"""TEST.user-data-guard and TEST.fast-tier-isolation (container mode) around one test command.

``omp-guardrails guard-run [--tier t1] [--network-none] [-- <command> ...]`` runs the command
given after ``--``, or the profile's ``tiers.<tier>.command``, from the repo root:

1. ``HOME``, ``USERPROFILE`` and ``XDG_{CONFIG,DATA,CACHE,STATE}_HOME``/``XDG_RUNTIME_DIR``
   point at a fresh temporary directory, removed afterwards.
2. The real home's watched paths are snapshotted (path, type, size, mtime) before and after the
   command. Any added, removed or modified entry is a ``TEST.user-data-guard`` finding.
3. With ``--network-none`` the command runs behind the configured network-none prefix (for
   example a container runtime's ``run --network none`` invocation). In the prefix, ``{home}``
   is replaced by the temporary home so the prefix can mount or forward it. Without a
   configured prefix, ``--network-none`` is a ``TEST.fast-tier-isolation`` finding.
4. A non-zero exit is a ``TEST.failure-propagation`` finding, so the guard never hides a failure.

The fast-tier process guard and the local network guard are not here: they live in the
profile's test-framework adapter (``guards.process``, ``guards.network``).

Profile ``guardrails.guarded_run`` settings:

- ``watch`` (required): paths relative to the real home to watch, e.g. ``[".config/<app>"]``.
- ``ignore``: patterns, relative to the real home, of entries other processes change.
- ``network_none_command``: the prefix as a list of arguments or one shell-quoted string.
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import tempfile
from pathlib import Path

from omp_guardrails.child_env import repo_environment
from omp_guardrails.config.profile import PROFILE_PATH
from omp_guardrails.findings import Finding
from omp_guardrails.structure.inventory import as_list, matches_any, settings

USER_DATA = "TEST.user-data-guard"
ISOLATION = "TEST.fast-tier-isolation"
PROPAGATION = "TEST.failure-propagation"
CHECKER = "guarded_run"
XDG_DIRS = {
    "XDG_CONFIG_HOME": "config",
    "XDG_DATA_HOME": "data",
    "XDG_CACHE_HOME": "cache",
    "XDG_STATE_HOME": "state",
    "XDG_RUNTIME_DIR": "runtime",
}
LISTED_CHANGES = 10

Snapshot = dict[str, tuple[str, int, int]]


class GuardError(Exception):
    """The guarded command cannot be resolved from the arguments and profile."""


def parse(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    command: list[str] = []
    if "--" in argv:
        split = argv.index("--")
        argv, command = argv[:split], argv[split + 1 :]
    parser = argparse.ArgumentParser(prog="omp-guardrails guard-run", add_help=False)
    parser.add_argument("--tier")
    parser.add_argument("--network-none", action="store_true")
    options, _ = parser.parse_known_args(argv)
    return options, command


def resolve_command(options: argparse.Namespace, command: list[str], profile: dict) -> list[str]:
    if command:
        return command
    if not options.tier:
        raise GuardError("pass --tier <tier> or a command after '--'")
    tiers = profile.get("tiers")
    tier = tiers.get(options.tier) if isinstance(tiers, dict) else None
    text = tier.get("command") if isinstance(tier, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise GuardError(f"tiers.{options.tier}.command is not set in {PROFILE_PATH}")
    return shlex.split(text)


def network_prefix(config: dict, home: Path) -> list[str] | None:
    value = config.get("network_none_command")
    prefix = shlex.split(value) if isinstance(value, str) else as_list(value)
    return [part.replace("{home}", str(home)) for part in prefix] or None


def guarded_env(root: Path) -> tuple[dict[str, str], Path]:
    home = root / "home"
    home.mkdir()
    env = repo_environment()
    env.update(HOME=str(home), USERPROFILE=str(home))
    for name, folder in XDG_DIRS.items():
        target = root / "xdg" / folder
        target.mkdir(parents=True, mode=0o700)
        env[name] = str(target)
    return env, home


def snapshot(real_home: Path, watched: list[str], ignore: list[str]) -> Snapshot:
    """Every entry under the watched paths, keyed by its home-relative posix path."""
    entries: Snapshot = {}
    for relative in watched:
        root = real_home / relative
        record(real_home, root, ignore, entries)
        if root.is_dir() and not root.is_symlink():
            for directory, folders, files in os.walk(root):
                for name in [*folders, *files]:
                    record(real_home, Path(directory) / name, ignore, entries)
    return entries


def record(real_home: Path, path: Path, ignore: list[str], entries: Snapshot) -> None:
    key = path.relative_to(real_home).as_posix()
    if matches_any(key, ignore):
        return
    try:
        stat = path.lstat()
    except OSError:
        return
    kind = "link" if path.is_symlink() else "dir" if path.is_dir() else "file"
    entries[key] = (kind, 0 if kind == "dir" else stat.st_size, stat.st_mtime_ns)


def changed_entries(before: Snapshot, after: Snapshot) -> list[str]:
    changed = []
    for key in sorted(set(before) | set(after)):
        if key not in after:
            changed.append(f"removed {key}")
        elif key not in before:
            changed.append(f"added {key}")
        elif before[key] != after[key] and after[key][0] != "dir":
            changed.append(f"modified {key}")
    return changed


def user_data_finding(changed: list[str]) -> Finding:
    listed = "; ".join(changed[:LISTED_CHANGES])
    more = len(changed) - LISTED_CHANGES
    suffix = f"; and {more} more" if more > 0 else ""
    return Finding(
        USER_DATA,
        "~",
        f"the test command changed real user data: {listed}{suffix}",
        "make the tests write only under HOME/XDG or a temporary directory they create",
    )


def run(repo: Path, profile: dict, argv: list[str]) -> list[Finding]:
    """``omp-guardrails guard-run [--tier <tier>] [--network-none] [-- <command> ...]``."""
    config = settings(profile, CHECKER)
    options, command = parse(argv)
    try:
        command = resolve_command(options, command, profile)
    except GuardError as error:
        return [Finding(USER_DATA, PROFILE_PATH, str(error), "name the command to guard")]
    watched = as_list(config.get("watch"))
    if not watched:
        return [
            Finding(
                USER_DATA,
                PROFILE_PATH,
                "guardrails.guarded_run.watch names no real-home paths to protect",
                "list the user-data paths the product and its tools write, relative to home",
            )
        ]
    ignore = as_list(config.get("ignore"))
    real_home = Path.home()
    with tempfile.TemporaryDirectory(prefix="omp-guard-") as scratch:
        env, home = guarded_env(Path(scratch))
        if options.network_none:
            prefix = network_prefix(config, home)
            if prefix is None:
                return [
                    Finding(
                        ISOLATION,
                        PROFILE_PATH,
                        "--network-none needs guardrails.guarded_run.network_none_command",
                        "record the container or sandbox prefix that denies network access",
                    )
                ]
            command = [*prefix, *command]
        before = snapshot(real_home, watched, ignore)
        try:
            returncode = subprocess.run(command, cwd=repo, env=env, check=False).returncode
        except OSError as error:
            return [Finding(PROPAGATION, command[0], f"cannot start: {error}", "fix the command")]
        after = snapshot(real_home, watched, ignore)
    found = []
    changed = changed_entries(before, after)
    if changed:
        found.append(user_data_finding(changed))
    if returncode != 0:
        found.append(
            Finding(
                PROPAGATION,
                command[0],
                f"guarded command exited {returncode}: {shlex.join(command)}",
                "fix the failing tests; the guard never turns a failure into a pass",
            )
        )
    return found
