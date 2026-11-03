#!/usr/bin/env python3
"""Deploy tracked NUC configuration without mirroring private runtime state."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def git(source, *args):
    return subprocess.check_output(['git', '-C', str(source), *args])


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


def deploy(source, runtime, previous_revision, backup_root):
    """Preflight retirement, back up touched files, then deploy HEAD's tracked blobs."""
    current = tracked(source, 'HEAD')
    previous = tracked(source, previous_revision)
    retired = previous.keys() - current.keys()
    for name in current.keys() | retired:
        path = destination(runtime, name)
        if path.exists() and not path.is_file():
            raise RuntimeError(f'Runtime destination is not a file: {name}')
        if name in retired and path.exists() and path.read_bytes() != previous[name][0]:
            raise RuntimeError(f'Locally modified retired file preserved: {name}')
    backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(backup_root, 0o700)
    backup = Path(tempfile.mkdtemp(prefix='deployment-', dir=backup_root))
    for name in current.keys() | retired:
        path = destination(runtime, name)
        if path.is_file():
            saved = backup / name
            saved.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            saved.write_bytes(path.read_bytes())
            saved.chmod(0o600)
    for name in retired:
        path = destination(runtime, name)
        if path.exists():
            path.unlink()
            parent = path.parent
            while parent != runtime:
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent
    for name, (data, mode) in current.items():
        path = destination(runtime, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
            temporary.write(data)
            temporary_path = Path(temporary.name)
        temporary_path.chmod(mode)
        temporary_path.replace(path)
    return git(source, 'rev-parse', 'HEAD').decode().strip()


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
        # First run starts with the pre-pull tree, so renamed files are retired immediately.
        previous = json.loads(state_file.read_text())['revision'] if state_file.exists() else git(source, 'rev-parse', 'HEAD').decode().strip()
        if not state_file.exists():
            state_file.write_text(json.dumps({'revision': previous}) + '\n')
        subprocess.run(['git', '-C', str(source), 'pull', '--ff-only', 'origin', 'nuc-config'], check=True, capture_output=True)
        revision = deploy(source, runtime, previous, state / 'backups')
        # Save after file deployment, before optional external dependency/overlay commands.
        # A retry then safely compares against the tree actually installed.
        temporary_state = state / 'deployment.json.tmp'
        temporary_state.write_text(json.dumps({'revision': revision}) + '\n')
        temporary_state.replace(state_file)
        bun = Path.home() / '.local/share/mise/installs/bun/1.3.0/bin/bun'
        if subprocess.check_output([str(bun), '--version']).decode().strip() != '1.3.0':
            raise RuntimeError('Install Bun 1.3.0 in mise before syncing')
        environment = os.environ.copy()
        environment['PATH'] = os.pathsep.join([str(bun.parent), str(Path.home() / '.local/share/mise/shims'), str(Path.home() / '.local/bin'), environment.get('PATH', '')])
        environment['SHARP_IGNORE_GLOBAL_LIBVIPS'] = '1'
        subprocess.run([str(bun), 'install', '--frozen-lockfile'], cwd=runtime, env=environment, check=True)
        subprocess.run([str(Path.home() / '.local/bin/agent-introspection'), 'harness', 'configure', 'oh-my-pi'], env=environment, check=True, capture_output=True)
        print('NUC configuration synchronized; restart OMP sessions to load changes.')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as error:
        # Do not include command output or exception details that could contain credentials.
        print(str(error) if isinstance(error, RuntimeError) else 'Configuration sync command failed; inspect its local status.', file=sys.stderr)
        sys.exit(1)
