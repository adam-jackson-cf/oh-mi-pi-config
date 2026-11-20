import os
import shutil
import tempfile
import unittest
from pathlib import Path

import pytest

from guardrails.host.process import run_text

pytestmark = pytest.mark.seam

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "orca-plugins/install.sh"
INJECTOR = ROOT / "orca-plugins/chat-section-style/inject.ts"


def real_node() -> str:
    node = shutil.which("node")
    assert node is not None, "put the repository's Node on PATH before running seams"
    return node


def write_tool(directory: Path, name: str, body: str) -> None:
    tool = directory / name
    tool.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    tool.chmod(0o755)


def print_launch_agent(tools: Path, node_directory: str) -> tuple[int, str, str]:
    environment = {**os.environ, "PATH": f"{tools}:{node_directory}:/usr/bin:/bin"}
    result = run_text(
        ["sh", str(INSTALL), "--print-launchagent"], env=environment, timeout=60
    )
    return result.returncode, result.stdout, result.stderr


class OrcaInstallTests(unittest.TestCase):
    def test_launch_agent_runs_the_typescript_injector_under_node(self) -> None:
        with tempfile.TemporaryDirectory(prefix="orca-install-") as scratch:
            tools = Path(scratch)
            write_tool(tools, "uname", "printf 'Darwin\\n'")
            status, plist, errors = print_launch_agent(
                tools, str(Path(real_node()).parent)
            )
        assert status == 0, errors
        assert f"<string>{INJECTOR}</string>" in plist
        assert INJECTOR.is_file()

    def test_install_refuses_a_node_that_cannot_strip_types(self) -> None:
        node = real_node()
        with tempfile.TemporaryDirectory(prefix="orca-install-") as scratch:
            tools = Path(scratch)
            write_tool(tools, "uname", "printf 'Darwin\\n'")
            write_tool(
                tools, "node", f'exec "{node}" --no-experimental-strip-types "$@"'
            )
            status, plist, errors = print_launch_agent(tools, "/usr/bin")
        assert status == 1
        assert plist == ""
        assert "lacks TypeScript type stripping" in errors
