"""Behavioral checks for tracked-file deployment boundaries."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

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


if __name__ == '__main__':
    unittest.main()
