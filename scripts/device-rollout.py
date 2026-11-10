#!/usr/bin/env python3
"""Report device revisions and fast-forward remote skills/service deployments.

Apply mode also provisions the judge key into each device's omp credential store
(Infisical -> builder -> `omp auth import -`, streamed over stdin only), runs a
`omp find` canary per device, and deletes retired `~/.agents/by-request` husks.
"""
import argparse
import json
from pathlib import Path
import shlex
import socket
import subprocess
import sys


# Configuration keys in the registry that are not hosts.
SETTINGS = ("local", "judgeKey", "findCanary")
# Retired skill names whose untracked by-request folders shadow skill:// resolution.
HUSKS = ("coding-guidance", "test-guidance", "setup-repository-guardrails")
# Reads the secret named by argv[1] from the environment and prints an
# `omp auth export` document for provider argv[2]; the key never reaches argv.
CREDENTIAL_BUILDER = (
    "import json, os, sys\n"
    "key = os.environ.get(sys.argv[1], '')\n"
    "if not key:\n"
    "    sys.exit(3)\n"
    "json.dump({'version': 1, 'credentials': [{'provider': sys.argv[2],"
    " 'credential': {'type': 'api_key', 'key': key}}]}, sys.stdout)\n"
)


class CommandError(Exception):
    def __init__(self, state):
        self.state = state


def run_command(argv):
    return subprocess.run(argv, capture_output=True, text=True, check=False)


def run_pipeline(producer, consumer):
    """Stream producer stdout into consumer stdin; return both exit codes, discard all output."""
    try:
        source = subprocess.Popen(producer, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError:
        return 127, 127
    try:
        sink = subprocess.run(consumer, stdin=source.stdout, capture_output=True, check=False)
        code = sink.returncode
    except OSError:
        code = 127
    finally:
        source.stdout.close()
    return source.wait(), code


def shell_path(path):
    return '"$HOME"/' + shlex.quote(path[2:]) if path.startswith("~/") else shlex.quote(path)


class Rollout:
    def __init__(self, hosts, check=False, runner=run_command, home=None, hostname=None,
                 pipeline=run_pipeline):
        hostname = (socket.gethostname() if hostname is None else hostname).split(".")[0].lower()
        self.local = hosts.get("local", {}).get(hostname, hostname)
        self.hosts = {host: config for host, config in hosts.items()
                      if host not in (*SETTINGS, self.local, hostname)}
        self.judge_key = hosts.get("judgeKey")
        self.canary = hosts.get("findCanary")
        self.check = check
        self.runner = runner
        self.pipeline = pipeline
        self.home = Path.home() if home is None else Path(home)
        self.rows = []
        self.notes = []

    def remote(self, argv, host):
        command = shlex.join(argv)
        if self.hosts[host].get("loginShell"):
            command = "bash -lc " + shlex.quote(command)
        return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, command]

    def command(self, argv, host=None, allowed=(0,)):
        if host:
            argv = self.remote(argv, host)
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
        self.rows.append([device, component, deployed, expected, "-", state])
        return state

    def husks(self, device, path, host=None):
        """Report (check) or delete (apply) by-request husks that hold no SKILL.md."""
        base = shell_path(path) if host else shlex.quote(str(path))
        delete = "" if self.check else 'rm -rf -- "$d" && echo "deleted $n" && continue; '
        script = ("for n in " + " ".join(HUSKS) + "; do d=" + base + '/by-request/"$n"; '
                  '[ -d "$d" ] || continue; '
                  'if [ -n "$(find "$d" -name SKILL.md -print | head -n 1)" ]; then '
                  'echo "skill $n"; continue; fi; ' + delete + 'echo "husk $n"; done')
        try:
            output = self.command(["sh", "-c", script], host).stdout.split()
        except CommandError as error:
            return error.state
        found = dict(zip(output[1::2], output[0::2]))
        for name in sorted(found):
            if found[name] == "skill":
                self.notes.append(f"{device}: by-request/{name} holds a SKILL.md; not deleted")
            elif found[name] == "deleted":
                self.notes.append(f"{device}: deleted by-request/{name} husk")
        return "husks-present" if any(kind in ("skill", "husk") for kind in found.values()) else None

    def provision(self, host=None):
        """Stream the judge key from Infisical into `omp auth import -`; True on success."""
        key = self.judge_key
        producer = ["infisical", "run", "--silent", "--projectId", key["infisicalProject"],
                    "--env", key["environment"], "--", sys.executable, "-c",
                    CREDENTIAL_BUILDER, key["secret"], key["provider"]]
        consumer = ["omp", "auth", "import", "-"]
        if host:
            consumer = self.remote(consumer, host)
        try:
            codes = self.pipeline(producer, consumer)
        except OSError:
            return False
        return tuple(codes) == (0, 0)

    def find(self, host=None):
        """Run the find canary and return its coverage, or why it has none."""
        path = self.canary["path"]
        if host:
            target = shell_path(path)
        else:
            target = shlex.quote(str(self.home / path[2:]) if path.startswith("~/") else path)
        script = "omp find " + shlex.quote(self.canary["query"]) + " " + target + " --json -q"
        try:
            result = self.command(["sh", "-c", script], host, allowed=(0, 1))
            coverage = json.loads(result.stdout).get("coverage")
        except CommandError as error:
            return error.state
        except (ValueError, AttributeError):
            return "failed"
        return coverage if coverage in ("complete", "partial", "unavailable") else "failed"

    def finish_device(self, device, start, host=None, agents=None, agents_state=None):
        """Apply husk, key and find results to the device's rows from index `start`."""
        rows = self.rows[start:]
        husk_state = self.husks(device, agents, host) if agents_state not in (
            None, "unreachable") else None
        if husk_state:
            for row in rows:
                if row[1] == "agents" and row[5] == "current":
                    row[5] = husk_state
        if any(row[5] == "unreachable" for row in rows):
            coverage = "unreachable"
        else:
            coverage = None
            if self.judge_key and not self.check and not self.provision(host):
                coverage = "key-failed"
                self.notes.append(f"{device}: judge key provisioning failed")
            if self.canary:
                found = self.find(host)
                coverage = coverage or found
        for row in rows:
            row[4] = coverage or "-"
            if coverage and coverage != "complete" and row[5] == "current":
                row[5] = "find-blocked"

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
        self.rows.append([host, "omp", deployed, expected, "-", state])

    def execute(self):
        local_omp = self.checkout(self.local, "omp", self.home / ".omp")
        agents = self.home / ".agents"
        self.finish_device(self.local, 0, agents=agents,
                           agents_state=self.checkout(self.local, "agents", agents))
        for host, config in self.hosts.items():
            start = len(self.rows)
            state = self.checkout(host, "agents", config["agents"], host)
            if "omp" in config:
                self.deployment(host, config["omp"], local_omp in ("current", "stale"))
            self.finish_device(host, start, host, config["agents"], state)
        return int(any(row[5] != "current" for row in self.rows))

    def report(self):
        rows = [("device", "component", "deployed", "expected", "find", "state"), *self.rows]
        widths = [max(len(row[column]) for row in rows) for column in range(6)]
        return "\n".join("  ".join(value.ljust(width) for value, width in zip(row, widths)).rstrip()
                         for row in rows) + ("\n" + "\n".join(self.notes) if self.notes else "")


def main(argv=None, runner=run_command, pipeline=run_pipeline):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="fetch and report without pulling, starting services, "
                             "provisioning keys or deleting husks")
    args = parser.parse_args(argv)
    registry = Path(__file__).with_name("device-rollout-hosts.json")
    rollout = Rollout(json.loads(registry.read_text()), check=args.check, runner=runner,
                      pipeline=pipeline)
    code = rollout.execute()
    print(rollout.report())
    return code


if __name__ == "__main__":
    sys.exit(main())
