#!/usr/bin/env python3
"""Run allowlisted exact-revision CI gates in rootless Podman on the NUC."""
import argparse
import base64
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
import urllib.request

SELF = Path(__file__).resolve()
PROJECT = '052b4c81-b9cc-4694-9019-d131ec3817cc'


def executable(name):
    return os.environ.get('NUC_GATE_' + name.upper().replace('-', '_'), name)


def root(kind):
    default = '~/.local/state' if kind == 'STATE' else '~/.local/share'
    return Path(os.environ.get('XDG_' + kind + '_HOME', default)).expanduser() / 'nuc-gate'


def repositories():
    return json.loads(Path(os.environ.get('NUC_GATE_REPOS', SELF.with_name('nuc-gate-repos.json'))).read_text())


def validate(repo, sha):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo) or any(p in ('.', '..') for p in repo.split('/')) or repo not in repositories():
        raise RuntimeError('Repository is not allowlisted')
    if not re.fullmatch(r'[0-9a-f]{40}', sha):
        raise RuntimeError('SHA must be 40 lowercase hex characters')


def job_path(repo, sha):
    validate(repo, sha)
    return root('STATE') / 'jobs' / repo / sha


def save(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def load(path):
    return json.loads(path.read_text())


def ci_argv(policy):
    """Return the CI argv from .omp/gate.json: checks.ci (v2) or gate (v1)."""
    version = policy.get('schemaVersion') if isinstance(policy, dict) else None
    if version == 2:
        checks = policy.get('checks')
        argv = checks.get('ci') if isinstance(checks, dict) else None
    elif version == 1:
        # schema v1: remove once every repo has migrated to v2
        argv = policy.get('gate')
    else:
        argv = None
    if not isinstance(argv, list) or not argv or any(not isinstance(arg, str) or not arg.strip() for arg in argv):
        raise RuntimeError('Invalid gate argv')
    return argv


@contextmanager
def lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


@contextmanager
def slot():
    count = int(os.environ.get('NUC_GATE_SLOTS', '1'))
    if count < 1:
        raise RuntimeError('NUC_GATE_SLOTS must be positive')
    streams = []
    try:
        directory = root('STATE') / 'locks'
        directory.mkdir(parents=True, exist_ok=True)
        streams = [(directory / f'slot-{i}').open('a') for i in range(count)]
        while True:
            for stream in streams:
                try:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    yield
                    return
                except BlockingIOError:
                    continue
            time.sleep(0.05)
    finally:
        for stream in streams:
            stream.close()


def repo_lock(repo):
    return root('STATE') / 'locks' / (repo.replace('/', '-') + '.lock')


def infisical():
    return [executable('infisical'), 'run', '--projectId', os.environ.get('NUC_GATE_INFISICAL_PROJECT', PROJECT),
            '--env', os.environ.get('NUC_GATE_INFISICAL_ENV', 'dev'), '--']


def publish(job, state, description):
    metadata = load(job / 'job.json')
    name = repositories()[metadata['repo']]['tokenEnv']
    token = os.environ.get(name)
    if not token:
        raise RuntimeError(f'Missing GitHub token environment variable: {name}')
    body = json.dumps({'context': 'gate', 'state': state, 'description': description[:140]}).encode()
    url = os.environ.get('NUC_GATE_GITHUB_API', 'https://api.github.com').rstrip('/')
    url += f"/repos/{metadata['repo']}/statuses/{metadata['sha']}"
    request = urllib.request.Request(url, data=body, method='POST', headers={
        'Authorization': 'Bearer ' + token, 'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'nuc-gate', 'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(request, timeout=30) as response:
        response.read()


def pending(job, log):
    metadata = load(job / 'job.json')
    if os.environ.get(repositories()[metadata['repo']]['tokenEnv']):
        publish(job, 'pending', 'Queued on NUC (0s)')
    else:
        result = subprocess.run(infisical() + [sys.executable, str(SELF), 'publish', str(job),
                                'pending', 'Queued on NUC (0s)'], stdout=log, stderr=log)
        if result.returncode:
            log.write('Pending status publication failed; run will retain a local result.\n')


def fetch(job):
    metadata = load(job / 'job.json')
    repo, sha = metadata['repo'], metadata['sha']
    if job.resolve() != job_path(repo, sha).resolve():
        raise RuntimeError('Job directory does not match configured state directory')
    mirror = root('DATA') / 'mirrors' / (repo + '.git')
    mirror.parent.mkdir(parents=True, exist_ok=True)
    git = [executable('git')]
    with (job / 'log').open('a') as log:
        if not mirror.exists():
            subprocess.run(git + ['init', '--bare', str(mirror)], check=True, stdout=log, stderr=log)
        environment = os.environ.copy()
        token = environment.get(repositories()[repo]['tokenEnv'])
        if token:
            credential = base64.b64encode(('x-access-token:' + token).encode()).decode()
            # Scope credentials to this GitHub fetch, never argv or saved config.
            environment.update(GIT_CONFIG_COUNT='1',
                               GIT_CONFIG_KEY_0='http.https://github.com/.extraheader',
                               GIT_CONFIG_VALUE_0='Authorization: Basic ' + credential)
        subprocess.run(git + ['--git-dir', str(mirror), 'fetch', '--no-tags',
                       f'https://github.com/{repo}.git', sha], env=environment,
                       check=True, stdout=log, stderr=log)


def submit(repo, sha):
    job = job_path(repo, sha)
    job.mkdir(parents=True, exist_ok=True)
    with lock(job / 'submit.lock'):
        retry = False
        if (job / 'result.json').exists():
            result = load(job / 'result.json')
            if result['state'] != 'error':
                print(json.dumps(result))
                return 0
            # Wait for the previous run to release its lock before replacing its result.
            with lock(job / 'run.lock'):
                shutil.rmtree(job / 'src', ignore_errors=True)
                (job / 'result.json').unlink()
                (job / 'job.json').unlink(missing_ok=True)
            retry = True
        if (job / 'job.json').exists():
            print(json.dumps({'state': 'running', 'job': str(job), 'sha': sha}))
            return 0
        save(job / 'job.json', {'repo': repo, 'sha': sha})
        try:
            with (job / 'log').open('a') as log, lock(repo_lock(repo)):
                mirror = root('DATA') / 'mirrors' / (repo + '.git')
                if os.environ.get(repositories()[repo]['tokenEnv']):
                    fetch(job)
                else:
                    subprocess.run(infisical() + [sys.executable, str(SELF), 'fetch', str(job)],
                                   check=True, stdout=log, stderr=log)
                git = [executable('git')]
                subprocess.run(git + ['clone', '--no-checkout', '--no-hardlinks', str(mirror), str(job / 'src')],
                               check=True, stdout=log, stderr=log)
                subprocess.run(git + ['-C', str(job / 'src'), 'checkout', '--detach', sha],
                               check=True, stdout=log, stderr=log)
            with (job / 'log').open('a') as log:
                try:
                    pending(job, log)
                except (RuntimeError, OSError) as error:
                    log.write(str(error) if isinstance(error, RuntimeError) else 'Pending status publication failed')
                unit = 'nuc-gate-' + repo.replace('/', '-') + '-' + sha[:12]
                if retry:
                    # The previous transient unit may still be collecting.
                    unit += '-retry-' + str(time.time_ns())
                command = [executable('systemd-run'), '--user', '--unit', unit, '--collect',
                           '-p', 'RuntimeMaxSec=' + os.environ.get('NUC_GATE_TIMEOUT', '1200'),
                           '-p', 'TimeoutStopSec=45', '-p', 'KillMode=mixed']
                # Transient units do not inherit SSH's environment. Forward service settings,
                # never credentials; Infisical owns the unit's secret injection.
                for key, value in os.environ.items():
                    if key.startswith('NUC_GATE_') and key not in {entry['tokenEnv'] for entry in repositories().values()} or key in ('XDG_DATA_HOME', 'XDG_STATE_HOME'):
                        command.append('--setenv=' + key + '=' + value)
                subprocess.run(command + infisical() + [sys.executable, str(SELF), 'run', str(job)],
                               check=True, stdout=log, stderr=log)
        except (OSError, subprocess.SubprocessError):
            result = {'repo': repo, 'sha': sha, 'image': None, 'exitCode': 1, 'state': 'error',
                      'startedAt': now(), 'finishedAt': now(), 'durationSeconds': 0, 'error': 'submission failed'}
            save(job / 'result.json', result)
            shutil.rmtree(job / 'src', ignore_errors=True)
            raise RuntimeError('Submission failed; inspect job log: ' + str(job)) from None
    print(json.dumps({'state': 'running', 'job': str(job), 'sha': sha}))
    return 0


def now():
    return datetime.now(timezone.utc).isoformat()


def runtime_flags(build=False):
    # NUC Podman 6.1.4 passes --no-new-keyring during builds; runsc rejects it.
    # Build only the owner's allowlisted repositories' Containerfiles with
    # Podman's default runtime. Actual gate execution stays inside gVisor.
    key = 'NUC_GATE_BUILD_RUNTIME_FLAGS' if build else 'NUC_GATE_RUNTIME_FLAGS'
    default = [] if build else ['--runtime', '/usr/local/bin/runsc', '--runtime-flag=ignore-cgroups']
    return json.loads(os.environ.get(key, json.dumps(default)))


def checkout_file(src, relative):
    path = (src / relative).resolve()
    if not path.is_relative_to(src.resolve()):
        raise RuntimeError('Configuration path escapes checkout')
    return path


def run(job):
    metadata = load(job / 'job.json')
    validate(metadata['repo'], metadata['sha'])
    if job.resolve() != job_path(metadata['repo'], metadata['sha']).resolve():
        raise RuntimeError('Job directory does not match configured state directory')
    with lock(job / 'run.lock'):
        if (job / 'result.json').exists():
            result = load(job / 'result.json')
            return 0 if result['state'] == 'success' and not result.get('publicationError') else 1
        return execute(job, metadata)


def container_command(argv, log):
    process = subprocess.Popen(argv, stdout=log, stderr=log)
    try:
        return process.wait()
    except BaseException:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        raise


@contextmanager
def container_lifetime(podman, container, log):
    try:
        yield
    finally:
        # Keep the global slot until an interrupted container is stopped.
        subprocess.run([podman, 'rm', '--force', container], stdout=log, stderr=log, timeout=30)


def execute(job, metadata):
    start, started = time.monotonic(), now()
    image, state, code, reason = None, 'error', 1, None
    container = 'nuc-gate-' + metadata['repo'].replace('/', '-') + '-' + metadata['sha'][:12]
    podman = executable('podman')
    src = job / 'src'

    def terminated(signum, frame):
        raise RuntimeError('timeout')

    previous = signal.signal(signal.SIGTERM, terminated)
    try:
        with (job / 'log').open('a', buffering=1) as log:
            try:
                with slot(), container_lifetime(podman, container, log):
                    gate = ci_argv(load(checkout_file(src, '.omp/gate.json')))
                    config = load(checkout_file(src, '.devcontainer/devcontainer.json'))
                    post = config.get('postCreateCommand', '')
                    if isinstance(post, list) and all(isinstance(arg, str) for arg in post):
                        post = shlex.join(post)
                    if not isinstance(post, str):
                        raise RuntimeError('Invalid postCreateCommand')
                    with lock(repo_lock(metadata['repo'])):
                        if 'build' in config:
                            dockerfile = checkout_file(src, '.devcontainer/' + config['build']['dockerfile'])
                            digest = hashlib.sha256(dockerfile.read_bytes()).hexdigest()[:12]
                            image = 'localhost/nuc-gate/' + metadata['repo'].replace('/', '-') + ':' + digest
                            exists = container_command([podman, 'image', 'exists', image], log)
                            if exists == 1:
                                built = container_command([podman, 'build', *runtime_flags(True), '-t', image,
                                                           '-f', str(dockerfile), str(dockerfile.parent)], log)
                                if built:
                                    raise RuntimeError('Image build failed')
                            elif exists:
                                raise RuntimeError('Podman image lookup failed')
                        else:
                            image = config['image']
                            if not isinstance(image, str) or not image or image.startswith('-'):
                                raise RuntimeError('Invalid container image')
                    command = (post + ' && ' if post else '') + shlex.join(gate)
                    code = container_command([podman, 'run', '--rm', '--name', container, *runtime_flags(),
                                              '-v', str(src) + ':/work:rw', '-w', '/work', image,
                                              'bash', '-c', command], log)
                    if code in (125, 126, 127) or code < 0:
                        raise RuntimeError('Podman execution failed')
                    state = 'success' if code == 0 else 'failure'
            except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
                reason = str(error) if isinstance(error, RuntimeError) else 'Infrastructure failure (' + type(error).__name__ + ')'
                log.write(reason + '\n')
                state, code = 'error', 1
    except (OSError, subprocess.SubprocessError):
        state, code, reason = 'error', 1, 'Container cleanup failed'
    finally:
        signal.signal(signal.SIGTERM, previous)
        duration = round(time.monotonic() - start, 3)
        result = {**metadata, 'image': image, 'exitCode': code, 'state': state,
                  'startedAt': started, 'finishedAt': now(), 'durationSeconds': duration}
        if reason:
            result['error'] = reason
        shutil.rmtree(src, ignore_errors=True)
    description = f"{state}: {reason or 'gate'} ({duration:.1f}s)"
    try:
        publish(job, state, description)
    except (RuntimeError, OSError) as error:
        message = str(error) if isinstance(error, RuntimeError) else 'GitHub status publication failed'
        result['publicationError'] = message
        print(message, file=sys.stderr)
        with (job / 'log').open('a') as log:
            log.write(message + '\n')
    finally:
        # A completed result means both the gate and final publication have finished.
        save(job / 'result.json', result)
    return 0 if state == 'success' and not result.get('publicationError') else 1


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('submit', 'status', 'wait', 'logs'):
        command = commands.add_parser(name)
        command.add_argument('repo')
        command.add_argument('sha')
        if name == 'wait':
            command.add_argument('--timeout', type=float, default=1200)
        if name == 'logs':
            command.add_argument('--tail', type=int)
    commands.add_parser('run').add_argument('job', type=Path)
    commands.add_parser('fetch').add_argument('job', type=Path)
    command = commands.add_parser('publish')
    command.add_argument('job', type=Path)
    command.add_argument('state')
    command.add_argument('description')
    args = parser.parse_args()
    if args.command == 'submit':
        return submit(args.repo, args.sha)
    if args.command == 'run':
        return run(args.job)
    if args.command == 'fetch':
        fetch(args.job)
        return 0
    if args.command == 'publish':
        publish(args.job, args.state, args.description)
        return 0
    job = job_path(args.repo, args.sha)
    if args.command == 'logs':
        if args.tail is not None and args.tail < 0:
            parser.error('--tail must be nonnegative')
        with (job / 'log').open() as stream:
            if args.tail is None:
                shutil.copyfileobj(stream, sys.stdout)
            elif args.tail:
                sys.stdout.writelines(deque(stream, maxlen=args.tail))
        return 0
    deadline = time.monotonic() + getattr(args, 'timeout', 0)
    while True:
        if (job / 'result.json').exists():
            result = load(job / 'result.json')
            print(json.dumps(result))
            if result.get('publicationError'):
                print(result['publicationError'], file=sys.stderr)
                return 1
            return 0 if result['state'] == 'success' else 1
        if args.command == 'status':
            if not (job / 'job.json').exists():
                raise RuntimeError('Unknown job')
            print(json.dumps({'state': 'running', 'job': str(job)}))
            return 0
        if time.monotonic() >= deadline:
            print('Timed out waiting for ' + str(job), file=sys.stderr)
            return 2
        time.sleep(0.1)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        # Never print subprocess argv/output or HTTP exception details: authentication
        # headers may be present. Repository-controlled output is only in the job log.
        print(str(error) if isinstance(error, RuntimeError) else 'NUC gate operation failed; inspect the job log.', file=sys.stderr)
        sys.exit(1)
