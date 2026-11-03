"""Behavioral checks for tracked-file deployment boundaries."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('omp_sync', Path(__file__).parents[1] / 'scripts/omp-config-sync.py')
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'source'
        self.runtime = self.root / 'runtime'
        self.source.mkdir()
        self.runtime.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('config', 'user.name', 'Test')
        self.write('agent/skills/workflow-templates/SKILL.md', b'old workflow')
        self.write('agent/config.yml', b'old config')
        self.commit()
        self.previous = self.git('rev-parse', 'HEAD').decode().strip()
        sync.deploy(self.source, self.runtime, self.previous, self.root / 'backups')

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.source), *args], stderr=subprocess.DEVNULL)

    def write(self, name, data):
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def commit(self):
        self.git('add', '.')
        self.git('commit', '-qm', 'fixture')

    def rename(self):
        self.git('mv', 'agent/skills/workflow-templates', 'agent/skills/omp-dyna-workflows')
        self.write('agent/config.yml', b'new config')
        self.commit()

    def deploy(self):
        return sync.deploy(self.source, self.runtime, self.previous, self.root / 'backups')

    def test_rename_removes_unchanged_retired_file_and_backs_up_privately(self):
        self.rename()
        self.deploy()
        self.assertFalse((self.runtime / 'agent/skills/workflow-templates').exists())
        self.assertEqual((self.runtime / 'agent/skills/omp-dyna-workflows/SKILL.md').read_bytes(), b'old workflow')
        backups = list((self.root / 'backups').glob('*/agent/skills/workflow-templates/SKILL.md'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), b'old workflow')
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.root / 'backups').stat().st_mode & 0o777, 0o700)

    def test_locally_modified_retirement_blocks_before_any_runtime_change(self):
        self.rename()
        retired = self.runtime / 'agent/skills/workflow-templates/SKILL.md'
        retired.write_bytes(b'local changes')
        with self.assertRaisesRegex(RuntimeError, 'Locally modified retired file preserved'):
            self.deploy()
        self.assertEqual(retired.read_bytes(), b'local changes')
        self.assertEqual((self.runtime / 'agent/config.yml').read_bytes(), b'old config')
        self.assertFalse((self.runtime / 'agent/skills/omp-dyna-workflows').exists())

    def test_auth_sessions_and_untracked_files_survive(self):
        private = ['agent/auth.json', 'agent/sessions/history.jsonl', 'agent/skills/local/SKILL.md', '.private']
        for name in private:
            path = self.runtime / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'private fixture')
        self.rename()
        self.deploy()
        for name in private:
            self.assertEqual((self.runtime / name).read_bytes(), b'private fixture')
        self.assertFalse((self.runtime / '.git').exists())

    def test_runtime_symlink_cannot_escape_into_another_directory(self):
        outside = self.root / 'outside'
        outside.mkdir()
        shutil_target = self.runtime / 'agent/config.yml'
        shutil_target.unlink()
        shutil_target.symlink_to(outside / 'config.yml')
        with self.assertRaisesRegex(RuntimeError, 'Refusing runtime symlink'):
            self.deploy()
        self.assertFalse((outside / 'config.yml').exists())

    def test_modified_current_file_is_preserved_before_other_changes(self):
        self.rename()
        config = self.runtime / 'agent/config.yml'
        config.write_bytes(b'UI edit')
        with self.assertRaisesRegex(RuntimeError, 'Locally modified tracked file preserved'):
            self.deploy()
        self.assertEqual(config.read_bytes(), b'UI edit')
        self.assertTrue((self.runtime / 'agent/skills/workflow-templates/SKILL.md').exists())

    def test_no_change_creates_no_backups_and_preserves_expected_overlay(self):
        config = self.runtime / 'agent/config.yml'
        config.write_bytes(b'managed overlay')
        expected = sync.runtime_hashes(self.source, self.runtime)
        sync.deploy(self.source, self.runtime, self.previous, self.root / 'backups', expected)
        self.assertEqual(config.read_bytes(), b'managed overlay')
        self.assertFalse((self.root / 'backups').exists())

    def test_corrupt_missing_and_unavailable_revisions_are_explicit(self):
        state = self.root / 'deployment.json'
        for content in ['broken private fixture', '{}', '{"revision":"private fixture"}',
                        json.dumps({'revision': '0' * 40}),
                        json.dumps({'revision': self.previous, 'hashes': {'x': 'invalid'}})]:
            state.write_text(content)
            with self.assertRaises(RuntimeError) as raised:
                sync.load_state(self.source, state)
            self.assertNotIn('private fixture', str(raised.exception))
        state.unlink()
        self.assertEqual(sync.load_state(self.source, state)['revision'], self.previous)

    def test_failed_overlay_records_hashes_and_retry_succeeds(self):
        self.rename()
        self.git('branch', '-M', 'nuc-config')
        state_dir = self.root / 'state'
        state_dir.mkdir()
        state_file = state_dir / 'deployment.json'
        sync.save_state(state_file, self.previous, {})
        original_run = subprocess.run
        fail_overlay = True

        def managed_run(command, *args, **kwargs):
            nonlocal fail_overlay
            if command[0] == 'git' and 'pull' in command:
                return subprocess.CompletedProcess(command, 0)
            if command[0] == '/fixture/bun':
                return subprocess.CompletedProcess(command, 0)
            if command[0] == '/fixture/overlay':
                (self.runtime / 'agent/config.yml').write_bytes(b'managed overlay')
                if fail_overlay:
                    raise subprocess.CalledProcessError(1, command)
                return subprocess.CompletedProcess(command, 0)
            return original_run(command, *args, **kwargs)

        argv = ['sync', '--source', str(self.source), '--runtime', str(self.runtime), '--state', str(state_dir)]
        with patch.object(sync.sys, 'platform', 'linux'), patch.object(sync.sys, 'argv', argv), \
             patch.object(sync, 'dependencies', return_value=(Path('/fixture/bun'), Path('/fixture/overlay'))), \
             patch.object(sync.subprocess, 'run', side_effect=managed_run):
            with self.assertRaises(subprocess.CalledProcessError):
                sync.main()
            recorded = sync.load_state(self.source, state_file)
            self.assertEqual(recorded['revision'], self.git('rev-parse', 'HEAD').decode().strip())
            self.assertEqual(recorded['hashes']['agent/config.yml'], sync.digest(b'managed overlay'))
            fail_overlay = False
            sync.main()
        self.assertEqual(state_file.stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.runtime / 'agent/config.yml').read_bytes(), b'managed overlay')

    def test_lock_prevents_overlapping_sync_before_source_or_runtime_mutation(self):
        argv = ['sync', '--source', str(self.source), '--runtime', str(self.runtime), '--state', str(self.root / 'state')]
        with patch.object(sync.sys, 'platform', 'linux'), patch.object(sync.sys, 'argv', argv), \
             patch.object(sync, 'dependencies', return_value=(Path('/fixture/bun'), Path('/fixture/overlay'))), \
             patch.object(sync.fcntl, 'flock', side_effect=BlockingIOError):
            with self.assertRaisesRegex(RuntimeError, 'Another configuration sync'):
                sync.main()
        self.assertEqual((self.runtime / 'agent/config.yml').read_bytes(), b'old config')

    def test_missing_dependency_has_friendly_error(self):
        with patch.object(sync.Path, 'is_file', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'Required executable is missing'):
                sync.dependencies()


if __name__ == '__main__':
    unittest.main()
