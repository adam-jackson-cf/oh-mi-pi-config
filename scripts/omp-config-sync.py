#!/usr/bin/env python3
"""Deploy tracked NUC configuration without mirroring private runtime state."""
import argparse
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def git(source, *args):
    return subprocess.check_output(['git', '-C', str(source), *args], stderr=subprocess.PIPE, timeout=120)


def tracked(source, revision):
    entries = {}
    for record in git(source, 'ls-tree', '-rz', revision).split(b'\0'):
        if not record:
            continue
        metadata, name = record.split(b'\t', 1)
        mode, kind, oid = metadata.split()
        path = os.fsdecode(name)
        if kind != b'blob' or mode not in (b'100644', b'100755'):
            raise RuntimeError(f'Unsupported tracked file: {path}')
        if Path(path).is_absolute() or '..' in Path(path).parts or '.git' in Path(path).parts:
            raise RuntimeError(f'Unsafe tracked path: {path}')
        entries[path] = (git(source, 'cat-file', 'blob', oid.decode()), int(mode, 8) & 0o777)
    return entries


def destination(runtime, name):
    path = runtime / name
    for component in [path, *path.parents]:
        if component == runtime.parent:
            break
        if component.is_symlink():
            raise RuntimeError(f'Refusing runtime symlink: {name}')
    return path


def digest(data):
    return hashlib.sha256(data).hexdigest()


def validate_revision(source, revision):
    if not isinstance(revision, str) or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', revision):
        raise RuntimeError('Deployment state has an invalid revision; restore the last deployed commit in deployment.json')
    try:
        git(source, 'cat-file', '-e', revision + '^{commit}')
    except subprocess.CalledProcessError:
        raise RuntimeError('Previous deployed revision is unavailable; fetch its history or restore the correct deployment.json') from None


def load_state(source, state_file):
    if not state_file.exists():
        return {'revision': git(source, 'rev-parse', 'HEAD').decode().strip()}
    try:
        state = json.loads(state_file.read_text())
        if not isinstance(state, dict) or 'revision' not in state:
            raise ValueError()
        hashes = state.get('hashes', {})
        if not isinstance(hashes, dict) or any(
            not isinstance(name, str) or not isinstance(value, str)
            or not re.fullmatch(r'[0-9a-f]{64}', value)
            for name, value in hashes.items()
        ):
            raise ValueError()
    except (ValueError, OSError):
        raise RuntimeError('Deployment state is unreadable or corrupt; restore deployment.json from the last known deployment') from None
    validate_revision(source, state['revision'])
    return state


def save_state(state_file, revision, hashes):
    temporary_state = state_file.with_suffix('.json.tmp')
    temporary_state.write_text(json.dumps({'revision': revision, 'hashes': hashes}) + '\n')
    temporary_state.chmod(0o600)
    temporary_state.replace(state_file)


def runtime_hashes(source, runtime):
    return {name: digest(destination(runtime, name).read_bytes()) for name in tracked(source, 'HEAD')
            if destination(runtime, name).is_file()}


def deploy(source, runtime, previous_revision, backup_root, expected_hashes=None):
    """Refuse runtime divergence, back up changed files and deploy source changes."""
    validate_revision(source, previous_revision)
    current = tracked(source, 'HEAD')
    previous = tracked(source, previous_revision)
    expected_hashes = expected_hashes or {}
    retired = previous.keys() - current.keys()
    changed = []
    for name in current.keys() | retired:
        path = destination(runtime, name)
        if path.exists() and not path.is_file():
            raise RuntimeError(f'Runtime destination is not a file: {name}')
        if name in retired:
            if path.exists() and path.read_bytes() != previous[name][0]:
                raise RuntimeError(f'Locally modified retired file preserved: {name}')
            if path.exists():
                changed.append(name)
            continue
        if path.exists():
            expected = expected_hashes.get(name, digest(previous.get(name, current[name])[0]))
            if digest(path.read_bytes()) != expected:
                raise RuntimeError(f'Locally modified tracked file preserved: {name}')
            # Preserve the managed overlay when the corresponding source did not change.
            if name in previous and current[name] == previous[name]:
                continue
            if path.read_bytes() == current[name][0] and path.stat().st_mode & 0o777 == current[name][1]:
                continue
        changed.append(name)
    existing = [name for name in changed if destination(runtime, name).is_file()]
    if existing:
        backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(backup_root, 0o700)
        backup = Path(tempfile.mkdtemp(prefix='deployment-', dir=backup_root))
        for name in existing:
            saved = backup / name
            saved.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            saved.write_bytes(destination(runtime, name).read_bytes())
            saved.chmod(0o600)
    for name in changed:
        path = destination(runtime, name)
        if name in retired:
            path.unlink()
            parent = path.parent
            while parent != runtime:
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent
        else:
            data, mode = current[name]
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
                temporary.write(data)
                temporary_path = Path(temporary.name)
            temporary_path.chmod(mode)
            temporary_path.replace(path)
    return git(source, 'rev-parse', 'HEAD').decode().strip()


def dependencies():
    bun = Path.home() / '.local/share/mise/installs/bun/1.3.0/bin/bun'
    overlay = Path.home() / '.local/bin/agent-introspection'
    for executable in (bun, overlay):
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise RuntimeError(f'Required executable is missing or not executable: {executable.name}')
    if not shutil.which('git'):
        raise RuntimeError('Required executable is missing: git')
    if subprocess.check_output([str(bun), '--version'], stderr=subprocess.PIPE, timeout=15).decode().strip() != '1.3.0':
        raise RuntimeError('Install Bun 1.3.0 in mise before syncing')
    return bun, overlay


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path.home() / 'Projects/tools/omp-nuc-config')
    parser.add_argument('--runtime', type=Path, default=Path.home() / '.omp')
    parser.add_argument('--state', type=Path, default=Path.home() / '.local/state/omp-config-sync')
    args = parser.parse_args()
    if sys.platform != 'linux':
        parser.error('This deployment entry point is Linux NUC only')
    os.umask(0o077)
    source, runtime, state = (p.expanduser().resolve() for p in (args.source, args.runtime, args.state))
    if source == runtime or source in runtime.parents or runtime in source.parents:
        parser.error('Source and runtime must be separate directories')
    bun, overlay = dependencies()
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    state.chmod(0o700)
    with (state / 'sync.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another configuration sync is running') from None
        if git(source, 'branch', '--show-current').decode().strip() != 'nuc-config':
            raise RuntimeError('Source must be on nuc-config')
        if git(source, 'status', '--porcelain').strip():
            raise RuntimeError('Source checkout must be clean')
        state_file = state / 'deployment.json'
        # Persist the pre-pull baseline so a failed pull/deployment can retry safely.
        previous = load_state(source, state_file)
        if not state_file.exists():
            save_state(state_file, previous['revision'], {})
        subprocess.run(['git', '-C', str(source), 'pull', '--ff-only', 'origin', 'nuc-config'],
                       check=True, capture_output=True, timeout=120)
        revision = deploy(source, runtime, previous['revision'], state / 'backups', previous.get('hashes'))
        save_state(state_file, revision, runtime_hashes(source, runtime))
        environment = os.environ.copy()
        environment['PATH'] = os.pathsep.join([str(bun.parent), str(Path.home() / '.local/share/mise/shims'), str(Path.home() / '.local/bin'), environment.get('PATH', '')])
        environment['SHARP_IGNORE_GLOBAL_LIBVIPS'] = '1'
        try:
            subprocess.run([str(bun), 'install', '--frozen-lockfile'], cwd=runtime, env=environment, check=True, timeout=600)
            subprocess.run([str(overlay), 'harness', 'configure', 'oh-my-pi'],
                           env=environment, check=True, capture_output=True, timeout=120)
        finally:
            # A failed overlay may still have changed tracked bytes. Record only hashes,
            # so retries accept changes made by this managed command, never secret values.
            save_state(state_file, revision, runtime_hashes(source, runtime))
        print('NUC configuration synchronized; restart OMP sessions to load changes.')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, ValueError) as error:
        # Do not include command output or exception details that could contain credentials.
        print(str(error) if isinstance(error, RuntimeError) else 'Configuration sync failed or timed out; verify dependencies and local service status, then retry.', file=sys.stderr)
        sys.exit(1)
