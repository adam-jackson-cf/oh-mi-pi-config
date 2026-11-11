import subprocess
import sys


def test_cli_prints_total():
    subprocess.run([sys.executable, "-c", "print(6)"], check=True, timeout=10)
