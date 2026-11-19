#!/usr/bin/env python3
"""Report device revisions and fast-forward remote skills/service deployments.

Apply mode also provisions the judge key into each device's omp credential store
(Infisical -> builder -> `omp auth import -`, streamed over stdin only), runs a
`omp find` canary per device, and writes
each device's `<omp base>/ci-runners.json` from the registry's `ciRunners` roles, and copies the
gitignored per-repo records (`<omp base>/repos/<owner>/<name>/`) one way from this checkout to
every remote device that runs omp, refusing to overwrite a target record that is newer.
The registry's `infisical` project and environment serve both the judge key and the
`ci-gate` runner commands, which pass them to ci_gate.py as CI_GATE_INFISICAL_* settings.
"""

import argparse
import hashlib
import json
import re
import shlex
import socket
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Configuration keys in the registry that are not hosts.
SETTINGS = ("local", "infisical", "judgeKey", "findCanary", "ciRunners")
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
GITHUB_LOGS = (
    'gh run list --repo "$1" --commit "$2" --json databaseId --jq \'.[].databaseId\' '
    '| while read -r id; do gh run view "$id" --repo "$1" --log-failed; done '
    '| tail -n "$3"'
)


def infisical_settings(infisical: dict[str, Any] | None) -> list[str]:
    """Return ci_gate.py's CI_GATE_INFISICAL_* assignments from the registry's `infisical` setting."""
    project, environment = (
        (infisical or {}).get("project"),
        (infisical or {}).get("environment"),
    )
    if not all(
        isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]+", value)
        for value in (project, environment)
    ):
        raise ValueError(
            "ci-gate runners need the registry's infisical project and environment"
        )
    return [
        "CI_GATE_INFISICAL_PROJECT=" + str(project),
        "CI_GATE_INFISICAL_ENV=" + str(environment),
    ]


def runner_document(
    definition: dict[str, Any],
    device: str,
    base: str,
    infisical: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the ci-runners.json document for `device`, whose omp base directory is `base`."""
    remote_base = definition["remoteBase"]
    if not re.fullmatch(r"~/[A-Za-z0-9_./-]+", remote_base):
        raise ValueError("ciRunners.remoteBase must be a ~/ path of safe characters")
    runners: dict[str, dict[str, Any]] = {}
    for role, spec in sorted(definition["roles"].items()):
        if spec["kind"] == "ci-gate":
            host = spec["host"]
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", host):
                raise ValueError("ciRunners ci-gate host must be an ssh alias")
            settings = infisical_settings(infisical)
            commands: dict[str, list[str] | None]
            if device == host:
                commands = {
                    name: [
                        "env",
                        *settings,
                        "python3",
                        base + "/scripts/ci_gate.py",
                        *args,
                    ]
                    for name, args in CI_GATE_ARGS.items()
                }
            else:
                script = remote_base + "/scripts/ci_gate.py"
                commands = {
                    name: [
                        "ssh",
                        "-o",
                        "BatchMode=yes",
                        host,
                        'bash -lc "'
                        + " ".join(settings)
                        + " python3 "
                        + script
                        + " "
                        + " ".join(args)
                        + '"',
                    ]
                    for name, args in CI_GATE_ARGS.items()
                }
        elif spec["kind"] == "github-actions":
            commands = {
                "submit": None,
                "wait": None,
                "status": None,
                "logs": ["sh", "-c", GITHUB_LOGS, "sh", "{repo}", "{sha}", "{tail}"],
            }
        else:
            raise ValueError("unknown ciRunners kind " + repr(spec["kind"]))
        timeout = spec.get("timeout")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or timeout <= 0
        ):
            raise ValueError(
                "ciRunners role " + repr(role) + " needs a positive timeout in seconds"
            )
        runners[role] = {**commands, "timeout": timeout}
    return {"schemaVersion": 1, "runners": runners}


class CommandError(Exception):
    def __init__(self, state: str) -> None:
        super().__init__(state)
        self.state = state


Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]
Pipeline = Callable[[list[str], list[str]], tuple[int, int, str]]


def run_command(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, check=False)


# Long unbroken word runs in a consumer's error line could be credential material; never report them.
SECRET_LIKE = re.compile(r"[A-Za-z0-9_\-+/=.]{20,}")


def failure_line(stderr: bytes | None) -> str:
    """The consumer's last stderr line, with credential-like runs redacted, at most 200 characters."""
    lines = [
        line.strip()
        for line in (stderr or b"").decode(errors="replace").splitlines()
        if line.strip()
    ]
    return SECRET_LIKE.sub("<redacted>", lines[-1])[:200] if lines else ""


def run_pipeline(producer: list[str], consumer: list[str]) -> tuple[int, int, str]:
    """Stream producer stdout into consumer stdin.

    Returns both exit codes and the consumer's redacted last stderr line; all other output is
    discarded, and the producer's output never leaves the pipe."""
    try:
        source = subprocess.Popen(
            producer, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
    except OSError:
        return 127, 127, f"cannot start {producer[0]}"
    try:
        sink = subprocess.run(
            consumer, stdin=source.stdout, capture_output=True, check=False
        )
        code, reason = sink.returncode, failure_line(sink.stderr)
    except OSError:
        code, reason = 127, f"cannot start {consumer[0]}"
    finally:
        if source.stdout is not None:
            source.stdout.close()
    return source.wait(), code, reason


RecordFiles = dict[str, tuple[int, str]]


def record_files(root: Path) -> RecordFiles:
    """{relative path: (mtime_ns, sha256)} for every regular file under the records root."""
    found: RecordFiles = {}
    if not root.is_dir():
        return found
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        found[path.relative_to(root).as_posix()] = (
            path.stat().st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
    return found


def record_plan(
    source: RecordFiles, target: RecordFiles
) -> tuple[list[str], list[str]]:
    """Split source files into (paths to copy, records refused because the target copy is newer).

    A record is `<owner>/<name>`. A target record is newer when any of its files differs from the
    source and was modified after the source file; the whole record is then left alone.
    Target-only files are never touched.
    """

    def record(relative: str) -> str:
        return "/".join(relative.split("/")[:2])

    newer = sorted({
        record(relative)
        for relative, (mtime, digest) in source.items()
        if relative in target
        and target[relative][1] != digest
        and target[relative][0] > mtime
    })
    copy = [
        relative
        for relative, (_, digest) in sorted(source.items())
        if record(relative) not in newer
        and (relative not in target or target[relative][1] != digest)
    ]
    return copy, newer


def shell_path(path: str) -> str:
    return (
        '"$HOME"/' + shlex.quote(path[2:])
        if path.startswith("~/")
        else shlex.quote(path)
    )


@dataclass(frozen=True)
class Machine:
    """Where the rollout runs and how it runs commands; tests replace each part.

    `base` is the rollout source: the omp checkout whose `repos/` records are copied.
    """

    runner: Runner = run_command
    pipeline: Pipeline = run_pipeline
    home: Path = field(default_factory=Path.home)
    hostname: str = field(default_factory=socket.gethostname)
    base: Path = Path(__file__).resolve().parents[1]


class Rollout:
    def __init__(
        self,
        hosts: dict[str, Any],
        machine: Machine | None = None,
        *,
        check: bool = False,
    ) -> None:
        machine = Machine() if machine is None else machine
        hostname = machine.hostname.split(".")[0].lower()
        self.local: str = hosts.get("local", {}).get(hostname, hostname)
        self.hosts = {
            host: config
            for host, config in hosts.items()
            if host not in (*SETTINGS, self.local, hostname)
        }
        self.infisical = hosts.get("infisical")
        self.judge_key = hosts.get("judgeKey")
        self.canary = hosts.get("findCanary")
        self.ci_runners = hosts.get("ciRunners")
        self.base = machine.base
        self.check = check
        self.runner = machine.runner
        self.pipeline = machine.pipeline
        self.home = machine.home
        self.rows: list[list[str]] = []
        self.notes: list[str] = []

    def remote(self, argv: list[str], host: str) -> list[str]:
        command = shlex.join(argv)
        if self.hosts[host].get("loginShell"):
            command = "bash -lc " + shlex.quote(command)
        return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, command]

    def command(
        self, argv: list[str], host: str | None = None, allowed: Sequence[int] = (0,)
    ) -> subprocess.CompletedProcess[str]:
        if host:
            argv = self.remote(argv, host)
        try:
            result = self.runner(argv)
        except OSError:
            raise CommandError("unreachable" if host else "failed") from None
        if result.returncode not in allowed:
            raise CommandError(
                "unreachable" if host and result.returncode == 255 else "failed"
            )
        return result

    def git(
        self,
        path: str | Path,
        *args: str,
        host: str | None = None,
        allowed: Sequence[int] = (0,),
    ) -> subprocess.CompletedProcess[str]:
        if host:
            # Expand only the configured home-relative path, never command output.
            script = "git -C " + shell_path(str(path)) + " " + shlex.join(args)
            return self.command(["sh", "-c", script], host, allowed)
        return self.command(["git", "-C", str(path), *args], allowed=allowed)

    def checkout(
        self, device: str, component: str, path: str | Path, host: str | None = None
    ) -> str:
        deployed = expected = "-"
        state = "failed"
        try:
            self.git(path, "fetch", "origin", host=host)
            expected = self.git(
                path, "rev-parse", "origin/main", host=host
            ).stdout.strip()
            deployed = self.git(path, "rev-parse", "HEAD", host=host).stdout.strip()
            state = "current" if deployed == expected else "stale"
            if host:
                dirty = self.git(
                    path, "status", "--porcelain", "--untracked-files=no", host=host
                )
                if dirty.stdout.strip():
                    state = "dirty-blocked"
            if host and not self.check and state != "dirty-blocked":
                self.git(path, "pull", "--ff-only", "origin", "main", host=host)
                deployed = self.git(path, "rev-parse", "HEAD", host=host).stdout.strip()
                expected = self.git(
                    path, "rev-parse", "origin/main", host=host
                ).stdout.strip()
                state = "current" if deployed == expected else "stale"
        except CommandError as error:
            state = error.state
        self.rows.append([device, component, deployed, expected, "-", state])
        return state

    def provision(self, host: str | None = None) -> str | None:
        """Stream the judge key from Infisical into `omp auth import -`; None on success, else why."""
        key, source = self.judge_key, self.infisical
        if key is None or source is None:
            return "the registry's infisical or judgeKey setting is incomplete"
        try:
            producer = [
                "infisical",
                "run",
                "--silent",
                "--projectId",
                source["project"],
                "--env",
                source["environment"],
                "--",
                sys.executable,
                "-c",
                CREDENTIAL_BUILDER,
                key["secret"],
                key["provider"],
            ]
        except (TypeError, KeyError):
            return "the registry's infisical or judgeKey setting is incomplete"
        consumer = ["omp", "auth", "import", "-"]
        if host:
            consumer = self.remote(consumer, host)
        try:
            produced, imported, reason = self.pipeline(producer, consumer)
        except OSError:
            return "cannot start the key pipeline"
        if produced:
            return f"infisical exited {produced}"
        if imported:
            return f"omp auth import exited {imported}" + (
                f": {reason}" if reason else ""
            )
        return None

    def find(self, host: str | None = None) -> str:
        """Run the find canary and return its coverage, or why it has none."""
        canary = self.canary or {}
        path = canary["path"]
        if host:
            target = shell_path(path)
        else:
            target = shlex.quote(
                str(self.home / path[2:]) if path.startswith("~/") else path
            )
        script = (
            "omp find " + shlex.quote(canary["query"]) + " " + target + " --json -q"
        )
        try:
            result = self.command(["sh", "-c", script], host, allowed=(0, 1))
            coverage: object = json.loads(result.stdout).get("coverage")
        except CommandError as error:
            return error.state
        except (ValueError, AttributeError):
            return "failed"
        return (
            coverage
            if isinstance(coverage, str)
            and coverage in {"complete", "partial", "unavailable"}
            else "failed"
        )

    def finish_device(
        self, device: str, start: int, host: str | None = None, *, omp: bool = True
    ) -> None:
        """Apply key and find results to the device's rows from index `start`.

        Key provisioning and the find canary run only on devices that run omp."""
        rows = self.rows[start:]
        coverage: str | None
        if any(row[5] == "unreachable" for row in rows):
            coverage = "unreachable"
        elif not omp:
            coverage = None
        else:
            coverage = None
            failure = (
                self.provision(host) if self.judge_key and not self.check else None
            )
            if failure:
                coverage = "key-failed"
                self.notes.append(
                    f"{device}: judge key provisioning failed ({failure})"
                )
            if self.canary:
                found = self.find(host)
                coverage = coverage or found
        for row in rows:
            row[4] = coverage or "-"
            if coverage and coverage != "complete" and row[5] == "current":
                row[5] = "find-blocked"

    def runners(self, device: str, start: int, host: str | None = None) -> None:
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
                document = json.dumps(
                    runner_document(self.ci_runners, device, base, self.infisical),
                    sort_keys=True,
                )
                result = self.command(
                    [
                        python,
                        "-c",
                        RUNNERS_WRITER,
                        "check" if self.check else "apply",
                        base + "/ci-runners.json",
                        document,
                    ],
                    host,
                )
                state = result.stdout.strip()
                state = (
                    "current"
                    if state in ("current", "written")
                    else (state if state in ("missing", "stale") else "failed")
                )
            except CommandError as error:
                state = error.state
            except (ValueError, KeyError, TypeError):
                state = "failed"
        if state in ("missing", "stale") and self.check:
            self.notes.append(
                f"{device}: ci-runners.json {state}; run device_rollout.py without --check"
            )
        self.rows.append([device, "ci-runners", "-", "-", "-", state])

    def listed_records(self, host: str, base: str) -> RecordFiles:
        """The target's record files: {relative path: (mtime_ns, sha256)}."""
        listed = json.loads(
            self.command(["python3", "-c", RECORDS_LISTER, base], host).stdout
        )
        return {
            relative: (int(value[0]), str(value[1]))
            for relative, value in listed.items()
        }

    def copy_records(
        self,
        host: str,
        base: str,
        source: RecordFiles,
        target: RecordFiles,
        copy: list[str],
    ) -> list[str]:
        """Stream the `copy` files to the host; return the files the target changed after inventory."""
        producer = [
            sys.executable,
            "-c",
            RECORDS_BUNDLER,
            str(self.base / RECORDS),
            *copy,
        ]
        planned = json.dumps(
            {
                relative: list(target[relative]) if relative in target else None
                for relative in copy
            },
            sort_keys=True,
        )
        consumer = self.remote(["python3", "-c", RECORDS_WRITER, base, planned], host)
        codes = tuple(self.pipeline(producer, consumer))[:2]
        if codes == (0, 5):
            # The writer refused files the target changed after inventory; name them.
            relisted = json.loads(
                self.command(["python3", "-c", RECORDS_LISTER, base], host).stdout
            )
            changed = [
                relative
                for relative in copy
                if (relisted.get(relative) or [None, None])[1] != source[relative][1]
            ]
            for relative in changed:
                self.notes.append(
                    f"{host}: record file {relative} changed on the target after "
                    "inventory; not overwritten (merge it into the rollout source "
                    "by hand, then re-run)"
                )
            return changed
        if codes != (0, 0):
            raise CommandError("failed")
        return []

    def record_state(self, host: str, base: str, source: RecordFiles) -> str:
        """Plan and (in apply mode) copy the host's records; the records row state."""
        target = self.listed_records(host, base)
        copy, newer = record_plan(source, target)
        for slug in newer:
            self.notes.append(
                f"{host}: record {slug} is newer on the target; not overwritten "
                "(merge it into the rollout source by hand, then re-run)"
            )
        if copy and self.check:
            state = "stale"
            self.notes.append(
                f"{host}: {len(copy)} record file(s) to copy; "
                "run device_rollout.py without --check"
            )
        else:
            changed = (
                self.copy_records(host, base, source, target, copy) if copy else []
            )
            state = "record-newer" if changed else "current"
        return "record-newer" if newer else state

    def records(self, host: str, config: dict[str, Any], start: int) -> None:
        """Copy this checkout's per-repo records to the host one way; never overwrite a newer target record."""
        source = record_files(self.base / RECORDS)
        if not source:
            return
        base = config.get("base", REMOTE_BASE)
        if any(row[5] == "unreachable" for row in self.rows[start:]):
            state = "unreachable"
        else:
            try:
                state = self.record_state(host, base, source)
            except CommandError as error:
                state = error.state
            except (OSError, ValueError, TypeError, AttributeError, IndexError):
                state = "failed"
        self.rows.append([host, "records", "-", "-", "-", state])

    def deployment(
        self, host: str, config: dict[str, Any], *, local_ready: bool
    ) -> None:
        deployed = expected = "-"
        state = "failed"
        try:
            if not local_ready:
                raise CommandError("failed")
            source = self.home / ".omp"
            branch = "origin/" + config["branch"]
            expected = self.git(source, "rev-parse", branch).stdout.strip()
            ancestor = self.git(
                source,
                "merge-base",
                "--is-ancestor",
                "origin/main",
                branch,
                allowed=(0, 1),
            )
            behind = ancestor.returncode == 1
            if behind:
                count = self.git(
                    source, "rev-list", "--count", branch + "..origin/main"
                ).stdout.strip()
                self.notes.append(
                    f"{host}: {config['branch']} behind main by {count} commits: "
                    "open an agent/nuc-sync-<date> PR"
                )
            if not self.check:
                try:
                    self.command(["systemctl", "--user", "start", config["unit"]], host)
                except CommandError:
                    # No journal messages or command environments: status metadata only.
                    status = self.command(
                        [
                            "systemctl",
                            "--user",
                            "status",
                            config["unit"],
                            "--no-pager",
                            "--lines=0",
                        ],
                        host,
                        allowed=(0, 1, 3),
                    )
                    lines = [
                        line.strip()
                        for line in status.stdout.splitlines()
                        if line.strip().startswith(("Active:", "Result:"))
                    ]
                    self.notes.append(
                        f"{host}: service failed; status tail: "
                        + " | ".join(lines[-10:])
                    )
                    raise CommandError("failed") from None
            result = self.command(
                ["sh", "-c", "cat " + shell_path(config["stateFile"])], host
            )
            revision = json.loads(result.stdout)["revision"]
            if (
                not isinstance(revision, str)
                or len(revision) != 40
                or any(char not in "0123456789abcdef" for char in revision)
            ):
                raise ValueError("invalid revision")
            deployed = revision
            state = (
                "nuc-config-behind"
                if behind
                else ("current" if deployed == expected else "stale")
            )
        except CommandError as error:
            state = error.state
        except (ValueError, KeyError, TypeError):
            deployed = "-"
            state = "failed"
        self.rows.append([host, "omp", deployed, expected, "-", state])

    def execute(self) -> int:
        local_omp = self.checkout(self.local, "omp", self.home / ".omp")
        agents = self.home / ".agents"
        self.checkout(self.local, "agents", agents)
        self.finish_device(self.local, 0)
        self.runners(self.local, 0)
        for host, config in self.hosts.items():
            start = len(self.rows)
            self.checkout(host, "agents", config["agents"], host)
            if "omp" in config:
                self.deployment(
                    host, config["omp"], local_ready=local_omp in {"current", "stale"}
                )
            self.finish_device(host, start, host, omp="omp" in config)
            self.runners(host, start, host)
            if "omp" in config:
                self.records(host, config["omp"], start)
        return int(any(row[5] != "current" for row in self.rows))

    def report(self) -> str:
        rows = [
            ("device", "component", "deployed", "expected", "find", "state"),
            *self.rows,
        ]
        widths = [max(len(row[column]) for row in rows) for column in range(6)]
        return "\n".join(
            "  ".join(
                value.ljust(width) for value, width in zip(row, widths, strict=True)
            ).rstrip()
            for row in rows
        ) + ("\n" + "\n".join(self.notes) if self.notes else "")


def main(argv: list[str] | None = None, machine: Machine | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fetch and report without pulling, starting services, "
        "provisioning keys or copying records",
    )
    args = parser.parse_args(argv)
    registry = Path(__file__).with_name("device-rollout-hosts.json")
    rollout = Rollout(json.loads(registry.read_text()), machine, check=args.check)
    code = rollout.execute()
    print(rollout.report())
    return code


if __name__ == "__main__":
    sys.exit(main())
