#!/usr/bin/env python3
"""Report device revisions and fast-forward remote skills/service deployments.

Apply mode also provisions the judge key into each device's omp credential store
(Infisical -> builder -> `omp auth import -`, streamed over stdin only), runs a
`omp find` canary per device, deletes retired `~/.agents/by-request` husks, and writes
each device's `<omp base>/ci-runners.json` from the registry's `ciRunners` roles, and copies the
gitignored per-repo records (`<omp base>/repos/<owner>/<name>/`) one way from this checkout to
every remote device that runs omp, refusing to overwrite a target record that is newer.
The registry's `infisical` project and environment serve both the judge key and the
`ci-gate` runner commands, which pass them to ci-gate.py as CI_GATE_INFISICAL_* settings.
"""
import argparse
import base64
import hashlib
import json
import re
from pathlib import Path
import shlex
import socket
import subprocess
import sys


# Configuration keys in the registry that are not hosts.
SETTINGS = ("local", "infisical", "judgeKey", "findCanary", "ciRunners")
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


# Runs on the target device: argv[1] check|apply, argv[2] path, argv[3] runner document.
# Expands home-relative argv elements there, then reports current, missing, stale or written.
RUNNERS_WRITER = (
    "import json, os, sys, tempfile\n"
    "mode, path, document = sys.argv[1], os.path.expanduser(sys.argv[2]), json.loads(sys.argv[3])\n"
    "for role in document['runners'].values():\n"
    "    for name, argv in role.items():\n"
    "        if isinstance(argv, list):\n"
    "            role[name] = [os.path.expanduser(a) if a.startswith('~/') else a for a in argv]\n"
    "text = json.dumps(document, indent=2, sort_keys=True) + '\\n'\n"
    "try:\n"
    "    with open(path) as stream:\n"
    "        current = stream.read()\n"
    "except FileNotFoundError:\n"
    "    current = None\n"
    "if current == text:\n"
    "    print('current')\n"
    "elif mode == 'check':\n"
    "    print('missing' if current is None else 'stale')\n"
    "else:\n"
    "    handle, temporary = tempfile.mkstemp(dir=os.path.dirname(path), prefix='.ci-runners.')\n"
    "    with os.fdopen(handle, 'w') as stream:\n"
    "        stream.write(text)\n"
    "    os.chmod(temporary, 0o644)\n"
    "    os.replace(temporary, path)\n"
    "    print('written')\n"
)
# Per-repo records: personal and gitignored, so git never moves them between devices.
RECORDS = "repos"
# A remote host's omp base when its registry `omp` entry names no `base`.
REMOTE_BASE = "~/.omp"
# Runs on the target device: argv[1] omp base. Prints {relative path: [mtime_ns, sha256]} for
# every regular file under <base>/repos.
RECORDS_LISTER = (
    "# omp-records-list\n"
    "import hashlib, json, os, sys\n"
    "root = os.path.join(os.path.expanduser(sys.argv[1]), 'repos')\n"
    "found = {}\n"
    "for directory, _, names in os.walk(root):\n"
    "    for name in names:\n"
    "        path = os.path.join(directory, name)\n"
    "        if os.path.islink(path) or not os.path.isfile(path):\n"
    "            continue\n"
    "        with open(path, 'rb') as stream:\n"
    "            digest = hashlib.sha256(stream.read()).hexdigest()\n"
    "        found[os.path.relpath(path, root).replace(os.sep, '/')] = [os.stat(path).st_mtime_ns, digest]\n"
    "json.dump(found, sys.stdout)\n"
)
# Runs on the target device: argv[1] omp base; argv[2] {relative path: [mtime_ns, sha256] or null},
# the target state the plan saw; stdin {relative path: [mtime_ns, base64 bytes]}. Immediately
# before replacing each file it re-reads the target and refuses that file when the target no longer
# matches the plan (edited, created or deleted since inventory), naming it on stderr and exiting 5
# after the others. Writes atomically (temp file, then rename) and keeps the source mtime, so a
# later edit on the target reads as newer.
RECORDS_WRITER = (
    "# omp-records-write\n"
    "import base64, hashlib, json, os, sys, tempfile\n"
    "root = os.path.join(os.path.expanduser(sys.argv[1]), 'repos')\n"
    "planned = json.loads(sys.argv[2])\n"
    "def current(path):\n"
    "    try:\n"
    "        with open(path, 'rb') as stream:\n"
    "            digest = hashlib.sha256(stream.read()).hexdigest()\n"
    "        return [os.stat(path).st_mtime_ns, digest]\n"
    "    except FileNotFoundError:\n"
    "        return None\n"
    "refused = []\n"
    "for relative, (mtime, data) in json.load(sys.stdin).items():\n"
    "    parts = relative.split('/')\n"
    "    if not relative or relative.startswith('/') or any(part in ('', '.', '..') for part in parts):\n"
    "        sys.exit(4)\n"
    "    path = os.path.join(root, *parts)\n"
    "    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)\n"
    "    handle, temporary = tempfile.mkstemp(dir=os.path.dirname(path), prefix='.record.')\n"
    "    with os.fdopen(handle, 'wb') as stream:\n"
    "        stream.write(base64.b64decode(data))\n"
    "    os.chmod(temporary, 0o600)\n"
    "    os.utime(temporary, ns=(mtime, mtime))\n"
    "    if os.path.islink(path) or current(path) != planned.get(relative):\n"
    "        os.unlink(temporary)\n"
    "        refused.append(relative)\n"
    "        print('changed since inventory: ' + relative, file=sys.stderr)\n"
    "        continue\n"
    "    os.replace(temporary, path)\n"
    "sys.exit(5 if refused else 0)\n"
)
# Runs locally: argv[1] records root, argv[2:] relative paths. Prints the writer's stdin document.
RECORDS_BUNDLER = (
    "import base64, json, os, sys\n"
    "root = sys.argv[1]\n"
    "json.dump({relative: [os.stat(os.path.join(root, relative)).st_mtime_ns,\n"
    "                      base64.b64encode(open(os.path.join(root, relative), 'rb').read()).decode()]\n"
    "           for relative in sys.argv[2:]}, sys.stdout)\n"
)
CI_GATE_ARGS = {
    "submit": ["submit", "{repo}", "{sha}"],
    "wait": ["wait", "{repo}", "{sha}", "--timeout", "{timeout}"],
    "status": ["status", "{repo}", "{sha}"],
    "logs": ["logs", "{repo}", "{sha}", "--tail", "{tail}"],
}
# Failed-job logs of every GitHub Actions run for the SHA; placeholders arrive as positional args.
GITHUB_LOGS = ("gh run list --repo \"$1\" --commit \"$2\" --json databaseId --jq '.[].databaseId' "
               "| while read -r id; do gh run view \"$id\" --repo \"$1\" --log-failed; done "
               "| tail -n \"$3\"")


def infisical_settings(infisical):
    """Return ci-gate.py's CI_GATE_INFISICAL_* assignments from the registry's `infisical` setting."""
    values = (infisical or {}).get("project"), (infisical or {}).get("environment")
    if not all(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]+", value) for value in values):
        raise ValueError("ci-gate runners need the registry's infisical project and environment")
    return ["CI_GATE_INFISICAL_PROJECT=" + values[0], "CI_GATE_INFISICAL_ENV=" + values[1]]


def runner_document(definition, device, base, infisical=None):
    """Return the ci-runners.json document for `device`, whose omp base directory is `base`."""
    remote_base = definition["remoteBase"]
    if not re.fullmatch(r"~/[A-Za-z0-9_./-]+", remote_base):
        raise ValueError("ciRunners.remoteBase must be a ~/ path of safe characters")
    runners = {}
    for role, spec in sorted(definition["roles"].items()):
        if spec["kind"] == "ci-gate":
            host = spec["host"]
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", host):
                raise ValueError("ciRunners ci-gate host must be an ssh alias")
            settings = infisical_settings(infisical)
            if device == host:
                commands = {name: ["env", *settings, "python3", base + "/scripts/ci-gate.py", *args]
                            for name, args in CI_GATE_ARGS.items()}
            else:
                script = remote_base + "/scripts/ci-gate.py"
                commands = {name: ["ssh", "-o", "BatchMode=yes", host,
                                   'bash -lc "' + " ".join(settings) + " python3 " + script + " "
                                   + " ".join(args) + '"']
                            for name, args in CI_GATE_ARGS.items()}
        elif spec["kind"] == "github-actions":
            commands = {"submit": None, "wait": None, "status": None,
                        "logs": ["sh", "-c", GITHUB_LOGS, "sh", "{repo}", "{sha}", "{tail}"]}
        else:
            raise ValueError("unknown ciRunners kind " + repr(spec["kind"]))
        timeout = spec.get("timeout")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ValueError("ciRunners role " + repr(role) + " needs a positive timeout in seconds")
        runners[role] = {**commands, "timeout": timeout}
    return {"schemaVersion": 1, "runners": runners}


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


def record_files(root):
    """{relative path: (mtime_ns, sha256)} for every regular file under the records root."""
    root = Path(root)
    found = {}
    if not root.is_dir():
        return found
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        found[path.relative_to(root).as_posix()] = (path.stat().st_mtime_ns,
                                                    hashlib.sha256(path.read_bytes()).hexdigest())
    return found


def record_plan(source, target):
    """Split source files into (paths to copy, records refused because the target copy is newer).

    A record is `<owner>/<name>`. A target record is newer when any of its files differs from the
    source and was modified after the source file; the whole record is then left alone.
    Target-only files are never touched.
    """
    def record(relative):
        return "/".join(relative.split("/")[:2])
    newer = sorted({record(relative) for relative, (mtime, digest) in source.items()
                    if relative in target and target[relative][1] != digest and target[relative][0] > mtime})
    copy = [relative for relative, (_, digest) in sorted(source.items())
            if record(relative) not in newer and (relative not in target or target[relative][1] != digest)]
    return copy, newer


def shell_path(path):
    return '"$HOME"/' + shlex.quote(path[2:]) if path.startswith("~/") else shlex.quote(path)


class Rollout:
    def __init__(self, hosts, check=False, runner=run_command, home=None, hostname=None,
                 pipeline=run_pipeline, base=None):
        """`base` is the rollout source: the omp checkout whose `repos/` records are copied."""
        hostname = (socket.gethostname() if hostname is None else hostname).split(".")[0].lower()
        self.local = hosts.get("local", {}).get(hostname, hostname)
        self.hosts = {host: config for host, config in hosts.items()
                      if host not in (*SETTINGS, self.local, hostname)}
        self.infisical = hosts.get("infisical")
        self.judge_key = hosts.get("judgeKey")
        self.canary = hosts.get("findCanary")
        self.ci_runners = hosts.get("ciRunners")
        self.base = Path(__file__).resolve().parents[1] if base is None else Path(base)
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
        key, source = self.judge_key, self.infisical
        try:
            producer = ["infisical", "run", "--silent", "--projectId", source["project"],
                        "--env", source["environment"], "--", sys.executable, "-c",
                        CREDENTIAL_BUILDER, key["secret"], key["provider"]]
        except (TypeError, KeyError):
            return False
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

    def finish_device(self, device, start, host=None, agents=None, agents_state=None, omp=True):
        """Apply husk, key and find results to the device's rows from index `start`.

        Key provisioning and the find canary run only on devices that run omp."""
        rows = self.rows[start:]
        husk_state = self.husks(device, agents, host) if agents_state not in (
            None, "unreachable") else None
        if husk_state:
            for row in rows:
                if row[1] == "agents" and row[5] == "current":
                    row[5] = husk_state
        if any(row[5] == "unreachable" for row in rows):
            coverage = "unreachable"
        elif not omp:
            coverage = None
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

    def runners(self, device, start, host=None):
        """Check or write the device's ci-runners.json and record it as a row."""
        if not self.ci_runners:
            return
        if any(row[5] == "unreachable" for row in self.rows[start:]):
            state = "unreachable"
        else:
            if host:
                base = self.ci_runners["remoteBase"]
                python = "python3"
            else:
                base = str(self.base)
                python = sys.executable
            try:
                document = json.dumps(runner_document(self.ci_runners, device, base,
                                                           self.infisical), sort_keys=True)
                result = self.command([python, "-c", RUNNERS_WRITER, "check" if self.check else "apply",
                                       base + "/ci-runners.json", document], host)
                state = result.stdout.strip()
                state = "current" if state in ("current", "written") else (
                    state if state in ("missing", "stale") else "failed")
            except CommandError as error:
                state = error.state
            except (ValueError, KeyError, TypeError):
                state = "failed"
        if state in ("missing", "stale") and self.check:
            self.notes.append(f"{device}: ci-runners.json {state}; run device-rollout.py without --check")
        self.rows.append([device, "ci-runners", "-", "-", "-", state])

    def records(self, host, config, start):
        """Copy this checkout's per-repo records to the host one way; never overwrite a newer target record."""
        source_root = self.base / RECORDS
        source = record_files(source_root)
        if not source:
            return
        base = config.get("base", REMOTE_BASE)
        if any(row[5] == "unreachable" for row in self.rows[start:]):
            state = "unreachable"
        else:
            try:
                listed = json.loads(self.command(["python3", "-c", RECORDS_LISTER, base], host).stdout)
                target = {relative: (int(value[0]), str(value[1])) for relative, value in listed.items()}
                copy, newer = record_plan(source, target)
                for slug in newer:
                    self.notes.append(f"{host}: record {slug} is newer on the target; not overwritten "
                                      "(merge it into the rollout source by hand, then re-run)")
                if copy and self.check:
                    state = "stale"
                    self.notes.append(f"{host}: {len(copy)} record file(s) to copy; "
                                      "run device-rollout.py without --check")
                else:
                    changed = []
                    if copy:
                        producer = [sys.executable, "-c", RECORDS_BUNDLER, str(source_root), *copy]
                        planned = json.dumps({relative: list(target[relative]) if relative in target else None
                                              for relative in copy}, sort_keys=True)
                        consumer = self.remote(["python3", "-c", RECORDS_WRITER, base, planned], host)
                        codes = tuple(self.pipeline(producer, consumer))
                        if codes == (0, 5):
                            # The writer refused files the target changed after inventory; name them.
                            relisted = json.loads(self.command(["python3", "-c", RECORDS_LISTER, base], host).stdout)
                            changed = [relative for relative in copy
                                       if (relisted.get(relative) or [None, None])[1] != source[relative][1]]
                            for relative in changed:
                                self.notes.append(f"{host}: record file {relative} changed on the target after "
                                                  "inventory; not overwritten (merge it into the rollout source "
                                                  "by hand, then re-run)")
                        elif codes != (0, 0):
                            raise CommandError("failed")
                    state = "record-newer" if changed else "current"
                if newer:
                    state = "record-newer"
            except CommandError as error:
                state = error.state
            except (OSError, ValueError, TypeError, AttributeError, IndexError):
                state = "failed"
        self.rows.append([host, "records", "-", "-", "-", state])

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
        self.runners(self.local, 0)
        for host, config in self.hosts.items():
            start = len(self.rows)
            state = self.checkout(host, "agents", config["agents"], host)
            if "omp" in config:
                self.deployment(host, config["omp"], local_omp in ("current", "stale"))
            self.finish_device(host, start, host, config["agents"], state, omp="omp" in config)
            self.runners(host, start, host)
            if "omp" in config:
                self.records(host, config["omp"], start)
        return int(any(row[5] != "current" for row in self.rows))

    def report(self):
        rows = [("device", "component", "deployed", "expected", "find", "state"), *self.rows]
        widths = [max(len(row[column]) for row in rows) for column in range(6)]
        return "\n".join("  ".join(value.ljust(width) for value, width in zip(row, widths)).rstrip()
                         for row in rows) + ("\n" + "\n".join(self.notes) if self.notes else "")


def main(argv=None, runner=run_command, pipeline=run_pipeline, base=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="fetch and report without pulling, starting services, "
                             "provisioning keys, deleting husks or copying records")
    args = parser.parse_args(argv)
    registry = Path(__file__).with_name("device-rollout-hosts.json")
    rollout = Rollout(json.loads(registry.read_text()), check=args.check, runner=runner,
                      pipeline=pipeline, base=base)
    code = rollout.execute()
    print(rollout.report())
    return code


if __name__ == "__main__":
    sys.exit(main())
