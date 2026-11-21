"""The rails' entry to the judgment registry in engine/workflows.py.

Usage: python decide.py questions <judgment> | record <judgment> <request.json>
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parent.parent / "engine" / "workflows.py"


def main(argv: list[str]) -> int:
    bridge = runpy.run_path(str(WORKFLOWS))["rail_bridge"]
    return int(bridge(argv))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
