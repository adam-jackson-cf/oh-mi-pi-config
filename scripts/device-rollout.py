#!/usr/bin/env python3
"""Report device revisions and fast-forward remote skills/service deployments."""
import argparse
import json
from pathlib import Path
import shlex
import subprocess
import sys


class CommandError(Exception):
    def __init__(self, state):
        self.state = state


def run_command(argv):
    return subprocess.run(argv, capture_output=True, text=True, check=False)


def shell_path(path):
    return '"$HOME"/' + shlex.quote(path[2:]) if path.startswith("~/") else shlex.quote(path)


class Rollout:
    def __init__(self, hosts, check=False, runner=run_command, home=None):
        self.hosts = hosts
        self.check = check
        self.runner = runner
        self.home = Path.home() if home is None else Path(home)
        self.rows = []
        self.notes = []

    def command(self, argv, host=None, allowed=(0,)):
        if host:
            command = shlex.join(argv)
            if self.hosts[host].get("loginShell"):
                command = "bash -lc " + shlex.quote(command)
            argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, command]
        try:
            result = self.runner(argv)
        except OSError:
            raise CommandError("unreachable" if host else "failed") from None
        if result.returncode not in allowed:
            raise CommandError("unreachable" if host and result.returncode == 255 else "failed")
        return result

    def git(self, path, *args, host=None, allowed=(0,)):
        if host:
            # Expand only the configured home-relative path, never command output.
            script = "git -C " + shell_path(path) + " " + shlex.join(args)
            return self.command(["sh", "-c", script], host, allowed)
        return self.command(["git", "-C", str(path), *args], allowed=allowed)

    def checkout(self, device, component, path, host=None):
        deployed = expected = "-"
        state = "failed"
        try:
            self.git(path, "fetch", "origin", host=host)
            expected = self.git(path, "rev-parse", "origin/main", host=host).stdout.strip()
            deployed = self.git(path, "rev-parse", "HEAD", host=host).stdout.strip()
            state = "current" if deployed == expected else "stale"
            if host:
                dirty = self.git(path, "status", "--porcelain", "--untracked-files=no", host=host)
                if dirty.stdout.strip():
                    state = "dirty-blocked"
            if host and not self.check and state != "dirty-blocked":
                self.git(path, "pull", "--ff-only", "origin", "main", host=host)
                deployed = self.git(path, "rev-parse", "HEAD", host=host).stdout.strip()
                expected = self.git(path, "rev-parse", "origin/main", host=host).stdout.strip()
                state = "current" if deployed == expected else "stale"
        except CommandError as error:
            state = error.state
        self.rows.append((device, component, deployed, expected, state))
        return state

    def deployment(self, host, config, local_ready):
        deployed = expected = "-"
        state = "failed"
        try:
            if not local_ready:
                raise CommandError("failed")
            source = self.home / ".omp"
            branch = "origin/" + config["branch"]
            expected = self.git(source, "rev-parse", branch).stdout.strip()
            ancestor = self.git(source, "merge-base", "--is-ancestor", "origin/main", branch,
                                allowed=(0, 1))
            behind = ancestor.returncode == 1
            if behind:
                count = self.git(source, "rev-list", "--count", branch + "..origin/main").stdout.strip()
                self.notes.append(f"{host}: {config['branch']} behind main by {count} commits: "
                                  "open an agent/nuc-sync-<date> PR")
            if not self.check:
                try:
                    self.command(["systemctl", "--user", "start", config["unit"]], host)
                except CommandError:
                    # No journal messages or command environments: status metadata only.
                    status = self.command(["systemctl", "--user", "status", config["unit"],
                                           "--no-pager", "--lines=0"], host, allowed=(0, 1, 3))
                    lines = [line.strip() for line in status.stdout.splitlines()
                             if line.strip().startswith(("Active:", "Result:"))]
                    self.notes.append(f"{host}: service failed; status tail: " + " | ".join(lines[-10:]))
                    raise CommandError("failed") from None
            result = self.command(["sh", "-c", "cat " + shell_path(config["stateFile"])], host)
            deployed = json.loads(result.stdout)["revision"]
            if not isinstance(deployed, str) or len(deployed) != 40 or any(
                    char not in "0123456789abcdef" for char in deployed):
                raise ValueError("invalid revision")
            state = "nuc-config-behind" if behind else ("current" if deployed == expected else "stale")
        except CommandError as error:
            state = error.state
        except (ValueError, KeyError, TypeError):
            deployed = "-"
            state = "failed"
        self.rows.append((host, "omp", deployed, expected, state))

    def execute(self):
        local_omp = self.checkout("mac-cf3", "omp", self.home / ".omp")
        self.checkout("mac-cf3", "agents", self.home / ".agents")
        for host, config in self.hosts.items():
            self.checkout(host, "agents", config["agents"], host)
            if "omp" in config:
                self.deployment(host, config["omp"], local_omp in ("current", "stale"))
        return int(any(row[4] != "current" for row in self.rows))

    def report(self):
        rows = [("device", "component", "deployed", "expected", "state"), *self.rows]
        widths = [max(len(row[column]) for row in rows) for column in range(5)]
        return "\n".join("  ".join(value.ljust(width) for value, width in zip(row, widths)).rstrip()
                         for row in rows) + ("\n" + "\n".join(self.notes) if self.notes else "")


def main(argv=None, runner=run_command):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fetch and report without pulling or starting services")
    args = parser.parse_args(argv)
    registry = Path(__file__).with_name("device-rollout-hosts.json")
    rollout = Rollout(json.loads(registry.read_text()), check=args.check, runner=runner)
    code = rollout.execute()
    print(rollout.report())
    return code


if __name__ == "__main__":
    sys.exit(main())
