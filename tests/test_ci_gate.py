"""Consumer contracts using real checkouts, fake Podman/systemd and local HTTP."""
import base64
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

SOURCE = Path(__file__).resolve().parents[1]
# ci-gate.py finds records beside its own checkout, so tests run a copy inside a temp harness root.
HARNESS_FILES = ('scripts/ci-gate.py', 'scripts/ci-gate-repos.json', 'agent/lib/repo_record.py')
HARNESS = SCRIPT = None
REPO = 'adam-jackson-cf/oh-mi-pi-config'
TOKEN_ENV = 'NUC_GATE_GITHUB_TOKEN_ADAM_JACKSON_CF'
# A context other than 'gate' proves the status context comes from the record's gate.json.
CONTEXT = 'exact-head-gate'


def setUpModule():
    global HARNESS, SCRIPT
    HARNESS = Path(tempfile.mkdtemp(prefix='ci-gate-harness-')).resolve()
    for name in HARNESS_FILES:
        (HARNESS / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(SOURCE / name, HARNESS / name)
    SCRIPT = HARNESS / 'scripts/ci-gate.py'


def tearDownModule():
    shutil.rmtree(HARNESS, ignore_errors=True)

FAKE = r'''#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys, time
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
root = pathlib.Path(os.environ['FAKE_ROOT'])
with (root / (name + '.jsonl')).open('a') as out:
    out.write(json.dumps(args) + '\n')
if name == 'git':
    config = {key: value for key, value in os.environ.items() if key.startswith('GIT_CONFIG_')}
    with (root / 'git-env.jsonl').open('a') as out:
        out.write(json.dumps({'args': args, 'config': config}) + '\n')
    if 'fetch' in args and os.environ.get('FAKE_REQUIRED_AUTH'):
        if config.get('GIT_CONFIG_VALUE_0') != os.environ['FAKE_REQUIRED_AUTH']:
            sys.exit(128)
    args = [os.environ['FAKE_REPO'] if a.startswith('https://github.com/') else a for a in args]
    sys.exit(subprocess.call([os.environ['REAL_GIT'], *args]))
if name == 'systemd-run':
    (root / 'launched').write_text('yes')
    sys.exit(0)
if name == 'infisical':
    if os.environ.get('FAKE_INFISICAL_TOKEN'):
        os.environ['NUC_GATE_GITHUB_TOKEN_ADAM_JACKSON_CF'] = os.environ['FAKE_INFISICAL_TOKEN']
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


class CiGateTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix='ci-gate-test-')
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.source = self.root / 'repo'
        self.source.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.name', 'Gate Test')
        self.git('config', 'user.email', 'gate@example.invalid')
        (self.source / '.devcontainer').mkdir()
        self.record = HARNESS / 'repos' / REPO
        shutil.rmtree(self.record, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.record, True)
        self.write_gate(self.gate(checks={
            'commit': ['bash', 'commit only.sh'], 'ci': ['bash', 'gate script.sh', "a'b"]}))
        (self.source / '.devcontainer/devcontainer.json').write_text(json.dumps({
            'build': {'dockerfile': 'Containerfile'}, 'postCreateCommand': ['echo', 'setup words']}))
        (self.source / '.devcontainer/Containerfile').write_text('FROM test-image\n')
        self.sha = self.commit()
        self.requests = []
        requests = self.requests
        self.final_status_started = threading.Event()
        self.release_final_status = threading.Event()
        self.release_final_status.set()
        self.final_status_code = 201
        gate = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                request = {'path': self.path, 'headers': dict(self.headers),
                           'body': json.loads(self.rfile.read(int(self.headers['Content-Length'])))}
                final = request['body']['state'] != 'pending'
                if final:
                    gate.final_status_started.set()
                    gate.release_final_status.wait(timeout=10)
                requests.append(request)
                self.send_response(gate.final_status_code if final else 201)
                self.end_headers()
                self.wfile.write(b'{}')

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(self.release_final_status.set)
        binary = self.root / 'bin'
        binary.mkdir()
        for name in ('git', 'podman', 'systemd-run', 'infisical'):
            file = binary / name
            file.write_text(FAKE)
            file.chmod(0o755)
        self.env = {**os.environ, 'XDG_STATE_HOME': str(self.root / 'state'),
                    'XDG_DATA_HOME': str(self.root / 'data'), 'FAKE_ROOT': str(self.root),
                    'FAKE_REPO': str(self.source), 'REAL_GIT': shutil.which('git'),
                    'CI_GATE_GITHUB_API': f'http://127.0.0.1:{server.server_port}',
                    'CI_GATE_INFISICAL_PROJECT': 'test-project', 'CI_GATE_INFISICAL_ENV': 'test-env',
                    TOKEN_ENV: 'test-super-secret-token'}
        for name in ('git', 'podman', 'systemd-run', 'infisical'):
            self.env['CI_GATE_' + name.upper().replace('-', '_')] = str(binary / name)
        self.children = []
        self.addCleanup(self.stop_children)

    def stop_children(self):
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.communicate()

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.source), *args], text=True).strip()

    @staticmethod
    def gate(**fields):
        """A complete record gate.json; `fields` replace its defaults, None removes one."""
        gate = {'schemaVersion': 2, 'checks': {'commit': ['true'], 'ci': ['true']}, 'pathCheck': ['true'],
                'ci': {'requiredContext': CONTEXT, 'runner': 'ci-gate'}, 'dynaflows': {'enabled': True}}
        gate.update(fields)
        return {key: value for key, value in gate.items() if value is not None}

    def write_gate(self, gate):
        self.record.mkdir(parents=True, exist_ok=True)
        (self.record / 'gate.json').write_text(json.dumps(gate))

    def commit(self):
        self.git('add', '.')
        self.git('commit', '--allow-empty', '-qm', 'fixture')
        return self.git('rev-parse', 'HEAD')

    def cli(self, *args, expected=0, env=None):
        result = subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], env=env or self.env,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def job(self, sha=None):
        return self.root / 'state/ci-gate/jobs' / REPO / (sha or self.sha)

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
        self.assertEqual(result['context'], CONTEXT)
        self.assertEqual(json.loads((self.job() / 'job.json').read_text())['context'], CONTEXT)
        self.assertFalse((self.job() / 'src').exists())
        self.assertEqual([r['body']['state'] for r in self.requests], ['pending', 'success'])
        for request in self.requests:
            self.assertEqual(request['body']['context'], CONTEXT)
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
        self.assertNotIn('commit only.sh', json.dumps(args))
        self.assertNotIn('-e', args)
        self.assertNotIn('--env-host', args)
        self.assertNotIn(self.env[TOKEN_ENV], json.dumps(self.calls('podman')) + (self.job() / 'log').read_text())
        for argv in self.calls('git'):
            self.assertNotIn(self.env[TOKEN_ENV], json.dumps(argv))
            self.assertFalse(any('Authorization:' in arg for arg in argv))
        self.cli('logs', REPO, self.sha, '--tail', 1)
        self.cli('wait', REPO, self.sha, '--timeout', 0)
        before = len(self.calls('systemd-run'))
        self.assertEqual(json.loads(self.submit().stdout), result)
        self.assertEqual(len(self.calls('systemd-run')), before)

    def test_fetch_credentials_are_ephemeral_for_shell_and_infisical_tokens(self):
        token = self.env[TOKEN_ENV]
        header = 'Authorization: Basic ' + base64.b64encode(('x-access-token:' + token).encode()).decode()
        inherited_config = {key: value for key, value in self.env.items() if key.startswith('GIT_CONFIG_')}
        for mode in ('shell-private', 'infisical-private', 'public'):
            with self.subTest(mode=mode):
                (self.source / 'fetch-mode').write_text(mode)
                sha = self.commit()
                environment = {**self.env}
                if mode != 'shell-private':
                    del environment[TOKEN_ENV]
                if mode == 'infisical-private':
                    environment['FAKE_INFISICAL_TOKEN'] = token
                if mode != 'public':
                    environment['FAKE_REQUIRED_AUTH'] = header
                self.cli('submit', REPO, sha, env=environment)
                self.assertEqual(subprocess.check_output(
                    ['git', '-C', str(self.job(sha) / 'src'), 'rev-parse', 'HEAD'], text=True).strip(), sha)
                fetch = next(call for call in self.calls('git-env')
                             if 'fetch' in call['args'] and call['args'][-1] == sha)
                if mode == 'public':
                    self.assertEqual(fetch['config'], inherited_config)
                else:
                    self.assertEqual(fetch['config'], {**inherited_config,
                        'GIT_CONFIG_COUNT': '1',
                        'GIT_CONFIG_KEY_0': 'http.https://github.com/.extraheader',
                        'GIT_CONFIG_VALUE_0': header})
                if mode != 'shell-private':
                    self.assertTrue(all(call[:5] == ['run', '--projectId', 'test-project', '--env', 'test-env']
                                        for call in self.calls('infisical')))
                    self.assertTrue(any(call[call.index('--') + 1:] ==
                                        [sys.executable, str(SCRIPT), 'fetch', str(self.job(sha))]
                                        for call in self.calls('infisical')))
                if mode == 'infisical-private':
                    self.assertEqual(self.requests[-1]['headers']['Authorization'], 'Bearer ' + token)
                mirror = self.root / 'data/ci-gate/mirrors' / (REPO + '.git')
                config = (mirror / 'config').read_text()
                self.assertNotIn('extraheader', config.lower())
                self.assertNotIn(token, config)
                self.assertNotIn(header, config)
                for call in self.calls('git'):
                    self.assertNotIn(token, json.dumps(call))
                    self.assertNotIn(header, json.dumps(call))
                    self.assertFalse(any('Authorization:' in arg for arg in call))
                for call in self.calls('git-env'):
                    if 'fetch' not in call['args']:
                        self.assertEqual(call['config'], inherited_config)
                self.assertNotIn(token, (self.job(sha) / 'log').read_text())
                self.assertNotIn(header, (self.job(sha) / 'log').read_text())
        self.assertNotIn(token, json.dumps(self.calls('systemd-run')))

    def test_running_resubmit_and_wait_timeout_do_not_relaunch(self):
        self.submit()
        self.cli('wait', REPO, self.sha, '--timeout', 0, expected=2)
        self.submit()
        self.assertEqual(len(self.calls('systemd-run')), 1)
        self.assertEqual(json.loads(self.cli('status', REPO, self.sha).stdout)['state'], 'running')

    def test_missing_record_is_an_error_naming_the_rollout(self):
        shutil.rmtree(self.record)
        self.submit()
        self.cli('run', self.job(), expected=1)
        result = json.loads(self.cli('wait', REPO, self.sha, expected=1).stdout)
        self.assertEqual(result['state'], 'error')
        self.assertIn('No per-repo record for ' + REPO, result['error'])
        self.assertIn('device-rollout.py', (self.job() / 'log').read_text())
        self.assertEqual(self.requests, [], 'no status may go to a guessed context')

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
        for failure in ('missing-ci', 'build', 'podman'):
            with self.subTest(failure=failure):
                gate = self.gate(checks={'commit': ['true']} if failure == 'missing-ci'
                                 else {'commit': ['true'], 'ci': ['true', failure]})
                if failure == 'missing-gate':
                    (self.record / 'gate.json').unlink()
                else:
                    self.write_gate(gate)
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
                self.assertEqual(self.requests[-1]['body']['context'], CONTEXT)

    def test_gate_without_required_context_is_an_unpublished_error(self):
        for failure in ('missing-gate', 'missing-context', 'unknown-schema', 'unknownField',
                        'pathCheck', 'checks'):
            with self.subTest(failure=failure):
                gate = {'missing-context': self.gate(ci=None),
                        'unknown-schema': {'schemaVersion': 3},
                        'unknownField': self.gate(unknownField=3),
                        'pathCheck': self.gate(pathCheck=None),
                        'checks': self.gate(checks={'ci': ['true']})}.get(failure)
                if failure == 'missing-gate':
                    (self.record / 'gate.json').unlink(missing_ok=True)
                else:
                    self.write_gate(gate)
                sha = self.commit()
                before = len(self.requests)
                self.submit(sha)
                self.assertNotIn('context', json.loads((self.job(sha) / 'job.json').read_text()))
                self.cli('run', self.job(sha), expected=1)
                waited = self.cli('wait', REPO, sha, expected=1)
                result = json.loads(waited.stdout)
                self.assertEqual(result['state'], 'error')
                self.assertIn('ci.requiredContext', result['publicationError'])
                if failure in ('unknownField', 'pathCheck', 'checks'):
                    # The run's error names the field the strict record schema rejected.
                    self.assertIn('checks.commit' if failure == 'checks' else failure, result['error'])
                self.assertIn('ci.requiredContext', waited.stderr)
                self.assertEqual(len(self.requests), before, 'no status may go to a guessed context')

    def test_submit_fails_before_job_state_without_infisical_settings(self):
        for name in ('CI_GATE_INFISICAL_PROJECT', 'CI_GATE_INFISICAL_ENV'):
            with self.subTest(missing=name):
                environment = {key: value for key, value in self.env.items() if key != name}
                result = self.cli('submit', REPO, self.sha, expected=1, env=environment)
                self.assertIn(name + ' not set', result.stderr)
                self.assertIn('device-rollout.py', result.stderr)
                self.assertFalse(self.job().exists())
                self.assertEqual(self.calls('git'), [])
                self.assertEqual(self.requests, [])

    def test_publication_failure_preserves_gate_result_but_wait_fails(self):
        for failure in ('missing-token', 'http-error'):
            with self.subTest(failure=failure):
                (self.source / 'publication-failure').write_text(failure)
                sha = self.commit()
                self.submit(sha)
                environment = {**self.env}
                if failure == 'missing-token':
                    del environment[TOKEN_ENV]
                    message = 'Missing GitHub token'
                else:
                    self.final_status_code = 503
                    message = 'GitHub status publication failed'
                result = self.cli('run', self.job(sha), expected=1, env=environment)
                self.assertIn(message, result.stderr)
                waited = self.cli('wait', REPO, sha, '--timeout', 0, expected=1)
                self.assertIn(message, waited.stderr)
                saved = json.loads(waited.stdout)
                self.assertEqual(saved['state'], 'success')
                self.assertIn(message, saved['publicationError'])
                self.assertFalse((self.job(sha) / 'src').exists())
                self.cli('status', REPO, sha, expected=1)
                self.cli('run', self.job(sha), expected=1)

    def test_resubmit_republishes_failed_final_status_without_rerunning_gate(self):
        for mode in ('shell', 'infisical'):
            with self.subTest(mode=mode):
                (self.source / 'publication-retry').write_text(mode)
                sha = self.commit()
                self.submit(sha)
                self.final_status_code = 503
                self.cli('run', self.job(sha), expected=1)
                saved = json.loads((self.job(sha) / 'result.json').read_text())
                self.assertEqual(saved['state'], 'success')
                self.assertIn('GitHub status publication failed', saved['publicationError'])
                self.cli('wait', REPO, sha, '--timeout', 0, expected=1)
                before = len(self.requests)
                launches = len(self.calls('systemd-run'))
                runs = len([call for call in self.calls('podman') if call[0] == 'run'])
                self.final_status_code = 201
                environment = {**self.env}
                if mode == 'infisical':
                    del environment[TOKEN_ENV]
                    environment['FAKE_INFISICAL_TOKEN'] = self.env[TOKEN_ENV]
                retried = json.loads(self.cli('submit', REPO, sha, env=environment).stdout)
                self.assertEqual(len(self.requests), before + 1,
                                 'resubmit must POST the cached final status again')
                self.assertEqual(self.requests[-1]['body']['state'], 'success')
                self.assertEqual(self.requests[-1]['body']['description'],
                                 f"success: gate ({saved['durationSeconds']:.1f}s)")
                self.assertNotIn('publicationError', retried)
                self.assertEqual(retried, {key: value for key, value in saved.items()
                                           if key != 'publicationError'})
                self.assertEqual(json.loads((self.job(sha) / 'result.json').read_text()), retried)
                self.assertEqual(json.loads(self.cli('wait', REPO, sha, '--timeout', 0).stdout), retried)
                self.assertEqual(len(self.calls('systemd-run')), launches)
                self.assertEqual(len([call for call in self.calls('podman') if call[0] == 'run']), runs)
                if mode == 'infisical':
                    self.assertTrue(any(call[call.index('--') + 1:] ==
                                        [sys.executable, str(SCRIPT), 'publish', str(self.job(sha)),
                                         'success', f"success: gate ({saved['durationSeconds']:.1f}s)"]
                                        for call in self.calls('infisical')))

    def test_wait_finishes_after_final_status_publication(self):
        for exit_code, state in ((0, 'success'), (7, 'failure'), (125, 'error')):
            with self.subTest(state=state):
                (self.source / 'publication-state').write_text(state)
                sha = self.commit()
                self.submit(sha)
                self.final_status_started.clear()
                self.release_final_status.clear()
                runner = subprocess.Popen([sys.executable, str(SCRIPT), 'run', str(self.job(sha))],
                                          env={**self.env, 'GATE_EXIT': str(exit_code)},
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                self.children.append(runner)
                try:
                    self.assertTrue(self.final_status_started.wait(timeout=10))
                    # The server has received the final POST but has not accepted it.
                    self.cli('wait', REPO, sha, '--timeout', 0, expected=2)
                    self.assertEqual(self.requests[-1]['body']['state'], 'pending')
                    self.assertFalse((self.job(sha) / 'result.json').exists())
                finally:
                    self.release_final_status.set()
                waited = self.cli('wait', REPO, sha, '--timeout', 10, expected=0 if state == 'success' else 1)
                self.assertEqual(json.loads(waited.stdout)['state'], state)
                self.assertEqual(self.requests[-1]['body']['state'], state)
                self.assertEqual(runner.communicate(timeout=10)[1], '')
                self.assertEqual(runner.returncode, 0 if state == 'success' else 1)

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
        with (self.root / 'state/ci-gate/locks/slot-0').open('a') as held_slot:
            with self.assertRaises(BlockingIOError):
                fcntl.flock(held_slot, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.assertEqual(len(list((self.root / 'state/ci-gate/locks').glob('slot-*'))), 1)
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
