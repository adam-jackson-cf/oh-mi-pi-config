"""Consumer contracts using real checkouts, fake Podman/systemd and local HTTP."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/nuc-gate.py'
REPO = 'adam-jackson-cf/oh-mi-pi-config'
TOKEN_ENV = 'NUC_GATE_GITHUB_TOKEN_ADAM_JACKSON_CF'

FAKE = r'''#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys, time
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
root = pathlib.Path(os.environ['FAKE_ROOT'])
with (root / (name + '.jsonl')).open('a') as out:
    # git may legitimately receive an ephemeral authentication header.
    out.write(json.dumps(['<redacted>' if 'Authorization:' in a else a for a in args]) + '\n')
if name == 'git':
    args = [os.environ['FAKE_REPO'] if a.startswith('https://github.com/') else a for a in args]
    sys.exit(subprocess.call([os.environ['REAL_GIT'], *args]))
if name == 'systemd-run':
    (root / 'launched').write_text('yes')
    sys.exit(0)
if name == 'infisical':
    sys.exit(subprocess.call(args[args.index('--') + 1:]))
if args[:2] == ['image', 'exists']:
    sys.exit(0 if (root / ('image-' + args[-1].split(':')[-1])).exists() else 1)
if args[0] == 'build':
    if os.environ.get('FAIL_BUILD'):
        sys.exit(1)
    image = args[args.index('-t') + 1]
    (root / ('image-' + image.split(':')[-1])).touch()
    sys.exit(0)
if args[0] == 'run':
    src = pathlib.Path(args[args.index('-v') + 1].removesuffix(':/work:rw'))
    marker = root / ('entered-' + src.parent.name)
    marker.touch()
    if os.environ.get('BLOCK_RUN'):
        while not (root / ('release-' + src.parent.name)).exists():
            time.sleep(.01)
    print('container gate output', flush=True)
    sys.exit(int(os.environ.get('GATE_EXIT', '0')))
sys.exit(0)
'''


class NucGateTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix='nuc-gate-test-')
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.source = self.root / 'repo'
        self.source.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.name', 'Gate Test')
        self.git('config', 'user.email', 'gate@example.invalid')
        (self.source / '.omp').mkdir()
        (self.source / '.devcontainer').mkdir()
        (self.source / '.omp/gate.json').write_text(json.dumps({'schemaVersion': 1, 'gate': ['bash', 'gate script.sh', "a'b"]}))
        (self.source / '.devcontainer/devcontainer.json').write_text(json.dumps({
            'build': {'dockerfile': 'Containerfile'}, 'postCreateCommand': ['echo', 'setup words']}))
        (self.source / '.devcontainer/Containerfile').write_text('FROM test-image\n')
        self.sha = self.commit()
        self.requests = []
        requests = self.requests

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                requests.append({'path': self.path, 'headers': dict(self.headers),
                                 'body': json.loads(self.rfile.read(int(self.headers['Content-Length'])))})
                self.send_response(201)
                self.end_headers()
                self.wfile.write(b'{}')

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        binary = self.root / 'bin'
        binary.mkdir()
        for name in ('git', 'podman', 'systemd-run', 'infisical'):
            file = binary / name
            file.write_text(FAKE)
            file.chmod(0o755)
        self.env = {**os.environ, 'XDG_STATE_HOME': str(self.root / 'state'),
                    'XDG_DATA_HOME': str(self.root / 'data'), 'FAKE_ROOT': str(self.root),
                    'FAKE_REPO': str(self.source), 'REAL_GIT': shutil.which('git'),
                    'NUC_GATE_GITHUB_API': f'http://127.0.0.1:{server.server_port}',
                    TOKEN_ENV: 'test-super-secret-token'}
        for name in ('git', 'podman', 'systemd-run', 'infisical'):
            self.env['NUC_GATE_' + name.upper().replace('-', '_')] = str(binary / name)
        self.children = []
        self.addCleanup(self.stop_children)

    def stop_children(self):
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.communicate()

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.source), *args], text=True).strip()

    def commit(self):
        self.git('add', '.')
        self.git('commit', '-qm', 'fixture')
        return self.git('rev-parse', 'HEAD')

    def cli(self, *args, expected=0, env=None):
        result = subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], env=env or self.env,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def job(self, sha=None):
        return self.root / 'state/nuc-gate/jobs' / REPO / (sha or self.sha)

    def submit(self, sha=None):
        return self.cli('submit', REPO, sha or self.sha)

    def calls(self, name):
        file = self.root / (name + '.jsonl')
        return [json.loads(line) for line in file.read_text().splitlines()] if file.exists() else []

    def test_rejects_unlisted_repositories_and_malformed_shas_before_execution(self):
        for repo, sha in [('other/repo', self.sha), ('../repo', self.sha), (REPO, 'A' * 40), (REPO, 'a' * 39)]:
            self.cli('submit', repo, sha, expected=1)
        self.assertEqual(self.calls('git'), [])
        self.assertEqual(self.requests, [])

    def test_submit_run_status_and_credentials_boundary(self):
        submitted = json.loads(self.submit().stdout)
        self.assertEqual(submitted['job'], str(self.job()))
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.sha)
        self.assertTrue((self.job() / 'src/.git').is_dir())
        self.cli('run', self.job())
        result = json.loads(self.cli('status', REPO, self.sha).stdout)
        self.assertEqual(result['state'], 'success')
        self.assertEqual(result['sha'], self.sha)
        self.assertEqual(result['exitCode'], 0)
        self.assertFalse((self.job() / 'src').exists())
        self.assertEqual([r['body']['state'] for r in self.requests], ['pending', 'success'])
        for request in self.requests:
            self.assertEqual(request['body']['context'], 'gate')
            self.assertLessEqual(len(request['body']['description']), 140)
            self.assertEqual(request['headers']['Authorization'], 'Bearer ' + self.env[TOKEN_ENV])
            self.assertEqual({k.lower(): v for k, v in request['headers'].items()}['x-github-api-version'], '2022-11-28')
            self.assertIn(self.sha, request['path'])
        args = next(args for args in self.calls('podman') if args[0] == 'run')
        self.assertIn('--runtime-flag=ignore-cgroups', args)
        self.assertEqual(args[args.index('--runtime') + 1], '/usr/local/bin/runsc')
        build = next(argv for argv in self.calls('podman') if argv[0] == 'build')
        self.assertNotIn('--runtime', build)
        self.assertFalse(any(arg.startswith('--runtime') for arg in build))
        self.assertNotIn('/usr/local/bin/runsc', build)
        self.assertIn("echo 'setup words' && bash 'gate script.sh' 'a'\"'\"'b'", args)
        self.assertNotIn('-e', args)
        self.assertNotIn('--env-host', args)
        self.assertNotIn(self.env[TOKEN_ENV], json.dumps(self.calls('podman')) + (self.job() / 'log').read_text())
        self.cli('logs', REPO, self.sha, '--tail', 1)
        self.cli('wait', REPO, self.sha, '--timeout', 0)
        before = len(self.calls('systemd-run'))
        self.assertEqual(json.loads(self.submit().stdout), result)
        self.assertEqual(len(self.calls('systemd-run')), before)

    def test_running_resubmit_and_wait_timeout_do_not_relaunch(self):
        self.submit()
        self.cli('wait', REPO, self.sha, '--timeout', 0, expected=2)
        self.submit()
        self.assertEqual(len(self.calls('systemd-run')), 1)
        self.assertEqual(json.loads(self.cli('status', REPO, self.sha).stdout)['state'], 'running')

    def test_nonzero_gate_is_failure(self):
        self.submit()
        self.env['GATE_EXIT'] = '7'
        self.cli('run', self.job(), expected=1)
        result = json.loads(self.cli('wait', REPO, self.sha, expected=1).stdout)
        self.assertEqual((result['state'], result['exitCode']), ('failure', 7))
        self.assertEqual(self.requests[-1]['body']['state'], 'failure')
        self.assertEqual(json.loads(self.submit().stdout), result)
        self.assertEqual(len(self.calls('systemd-run')), 1)

    def test_resubmitting_infrastructure_error_recreates_checkout_and_runs_again(self):
        self.submit()
        self.cli('run', self.job(), expected=1, env={**self.env, 'FAIL_BUILD': '1'})
        self.assertEqual(json.loads((self.job() / 'result.json').read_text())['state'], 'error')
        self.assertFalse((self.job() / 'src').exists())
        retried = json.loads(self.submit().stdout)
        self.assertEqual(retried['state'], 'running')
        self.assertFalse((self.job() / 'result.json').exists())
        self.assertTrue((self.job() / 'src/.git').is_dir())
        self.assertEqual(len(self.calls('systemd-run')), 2)
        units = [argv[argv.index('--unit') + 1] for argv in self.calls('systemd-run')]
        self.assertNotEqual(units[0], units[1])
        self.cli('run', self.job())
        result = json.loads(self.cli('wait', REPO, self.sha).stdout)
        self.assertEqual((result['state'], result['sha']), ('success', self.sha))
        self.assertEqual([request['body']['state'] for request in self.requests],
                         ['pending', 'error', 'pending', 'success'])

    def test_infrastructure_failures_are_errors(self):
        for failure in ('missing-gate', 'build', 'podman'):
            with self.subTest(failure=failure):
                if failure == 'missing-gate':
                    (self.source / '.omp/gate.json').unlink()
                else:
                    (self.source / '.omp/gate.json').write_text(json.dumps({'schemaVersion': 1, 'gate': ['true', failure]}))
                sha = self.commit()
                self.submit(sha)
                environment = {**self.env}
                if failure == 'build':
                    environment['FAIL_BUILD'] = '1'
                if failure == 'podman':
                    environment['GATE_EXIT'] = '125'
                self.cli('run', self.job(sha), expected=1, env=environment)
                result = json.loads(self.cli('wait', REPO, sha, expected=1).stdout)
                self.assertEqual(result['state'], 'error')
                self.assertEqual(self.requests[-1]['body']['state'], 'error')

    def test_missing_token_preserves_result_and_reports_publication_failure(self):
        self.submit()
        del self.env[TOKEN_ENV]
        result = self.cli('run', self.job(), expected=1)
        self.assertIn('Missing GitHub token', result.stderr)
        self.assertEqual(json.loads((self.job() / 'result.json').read_text())['state'], 'success')
        self.assertFalse((self.job() / 'src').exists())

    def test_unchanged_dockerfile_reuses_image(self):
        self.submit()
        self.cli('run', self.job())
        (self.source / 'new-file').write_text('changed source, same Dockerfile')
        sha = self.commit()
        self.submit(sha)
        self.cli('run', self.job(sha))
        self.assertEqual(len([a for a in self.calls('podman') if a[0] == 'build']), 1)
        images = [json.loads((self.job(s) / 'result.json').read_text())['image'] for s in (self.sha, sha)]
        self.assertEqual(images[0], images[1])

    def start_run(self, sha):
        child = subprocess.Popen([sys.executable, str(SCRIPT), 'run', str(self.job(sha))],
                                 env={**self.env, 'BLOCK_RUN': '1'}, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.children.append(child)
        return child

    def until(self, predicate):
        deadline = time.monotonic() + 10
        while not predicate():
            self.assertLess(time.monotonic(), deadline, 'Timed out waiting for fake process handshake')
            time.sleep(.01)

    def test_global_slot_serializes_runs(self):
        self.submit()
        (self.source / 'second').write_text('second')
        second = self.commit()
        self.submit(second)
        first = self.start_run(self.sha)
        self.until(lambda: (self.root / ('entered-' + self.sha)).exists())
        other = self.start_run(second)
        # The second process is alive but cannot enter podman before the first releases.
        self.until(lambda: (self.job(second) / 'run.lock').exists())
        self.assertFalse((self.root / ('entered-' + second)).exists())
        with (self.root / 'state/nuc-gate/locks/slot-0').open('a') as held_slot:
            with self.assertRaises(BlockingIOError):
                fcntl.flock(held_slot, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.assertEqual(len(list((self.root / 'state/nuc-gate/locks').glob('slot-*'))), 1)
        (self.root / ('release-' + self.sha)).touch()
        self.until(lambda: (self.root / ('entered-' + second)).exists())
        (self.root / ('release-' + second)).touch()
        self.assertEqual(first.communicate(timeout=10)[1], '')
        self.assertEqual(other.communicate(timeout=10)[1], '')
        self.assertEqual((first.returncode, other.returncode), (0, 0))

    def test_image_configuration_without_build(self):
        (self.source / '.devcontainer/devcontainer.json').write_text(json.dumps({'image': 'example/pinned:tag'}))
        sha = self.commit()
        self.submit(sha)
        self.cli('run', self.job(sha))
        self.assertFalse(any(a[0] == 'build' for a in self.calls('podman')))
        self.assertEqual(json.loads((self.job(sha) / 'result.json').read_text())['image'], 'example/pinned:tag')

    def test_sigterm_records_timeout_and_removes_container(self):
        self.submit()
        child = self.start_run(self.sha)
        self.until(lambda: (self.root / ('entered-' + self.sha)).exists())
        child.send_signal(signal.SIGTERM)
        child.communicate(timeout=10)
        self.assertEqual(child.returncode, 1)
        result = json.loads((self.job() / 'result.json').read_text())
        self.assertEqual((result['state'], result['error']), ('error', 'timeout'))
        self.assertEqual(self.requests[-1]['body']['state'], 'error')
        self.assertTrue(any(a[:2] == ['rm', '--force'] for a in self.calls('podman')))


if __name__ == '__main__':
    unittest.main()
