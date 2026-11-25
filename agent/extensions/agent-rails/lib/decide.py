"""The rails' entry to the judgment registry in lib/rails_kernel.py.

Usage: python decide.py questions <judgment> | record <judgment> <request.json>
| error <judgment> <request.json>

The process cwd is the rail's (the session's): its repo config sets the effective judgment modes.
"""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

KERNEL = Path(__file__).resolve().with_name("rails_kernel.py")


def main(argv: list[str]) -> int:
    bridge = runpy.run_path(str(KERNEL))["rail_bridge"]
    return int(bridge(argv, os.getcwd()))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
