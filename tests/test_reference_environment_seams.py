from __future__ import annotations

import shlex
import sys
import unittest
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/agent-rails/engine")
)


import pytest
from test_reference_environment import GuardedRunCase, python

pytestmark = pytest.mark.seam


class GuardedRunTests(GuardedRunCase):
    def test_command_writing_only_to_redirected_home_passes(self) -> None:
        code = (
            "import os, pathlib\n"
            "home = pathlib.Path(os.environ['HOME'])\n"
            f"assert home != pathlib.Path({str(self.real_home)!r})\n"
            "(home / '.config' / 'app').mkdir(parents=True)\n"
            "(home / '.config' / 'app' / 'settings.json').write_text('changed')\n"
            "for kind in ('CONFIG', 'DATA', 'CACHE', 'STATE'):\n"
            "    assert os.path.isdir(os.environ[f'XDG_{kind}_HOME']), kind\n"
        )
        assert self.guard(["--", *python(code)]) == []

    def test_command_changing_a_watched_real_file_is_reported(self) -> None:
        target = self.real_home / ".config/app/settings.json"
        code = f"open({str(target)!r}, 'w').write('changed by a test')"
        findings = self.guard(["--", *python(code)])
        assert [finding.rule_id for finding in findings] == ["TEST.user-data-guard"]
        assert "modified .config/app/settings.json" in findings[0].message

    def test_added_real_file_is_reported_and_ignored_paths_are_not(self) -> None:
        added = self.real_home / ".config/app/new.db"
        noisy = self.real_home / ".config/app/history.log"
        code = f"open({str(added)!r}, 'w').write('x'); open({str(noisy)!r}, 'w').write('x')"
        profile = {
            "checkers": {
                "guarded_run": {
                    "watch": [".config/app"],
                    "ignore": [".config/app/*.log"],
                }
            }
        }
        findings = self.guard(["--", *python(code)], profile)
        assert len(findings) == 1
        assert "added .config/app/new.db" in findings[0].message
        assert "history.log" not in findings[0].message

    def test_unwatched_real_paths_are_not_reported(self) -> None:
        code = f"open({str(self.real_home / '.cache/tool')!r}, 'w').write('x')"
        assert self.guard(["--", *python(code)]) == []

    def test_failing_command_is_never_hidden(self) -> None:
        findings = self.guard(["--", *python("raise SystemExit(5)")])
        assert [finding.rule_id for finding in findings] == ["TEST.failure-propagation"]
        assert "exited 5" in findings[0].message

    def test_tier_command_comes_from_the_profile(self) -> None:
        marker = self.repo / "ran"
        profile = {
            **self.profile,
            "tests": {
                **self.profile.get("tests", {}),
                "tiers": {
                    "t1": {"command": shlex.join(python(f"open({str(marker)!r}, 'w')"))}
                },
            },
        }
        assert self.guard(["--tier", "t1"], profile) == []
        assert marker.exists()

    def test_network_none_runs_behind_the_configured_prefix(self) -> None:
        profile = {
            "checkers": {
                "guarded_run": {
                    "watch": [".config/app"],
                    "network_none_command": "env OMP_GUARD_PREFIX_HOME={home}",
                }
            }
        }
        code = (
            "import os, sys\n"
            "sys.exit(0 if os.environ.get('OMP_GUARD_PREFIX_HOME') == os.environ['HOME'] else 9)"
        )
        assert self.guard(["--network-none", "--", *python(code)], profile) == []


if __name__ == "__main__":
    unittest.main()
