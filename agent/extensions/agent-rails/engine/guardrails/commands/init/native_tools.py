"""The native-tool proposal data and the header every proposed tool config starts with."""

from __future__ import annotations

import json
from typing import Any

from guardrails.foundation import conventions

DATA_FILE = "native-tools.json"
HEADER = "# Proposed native-tool configuration; edit with owner approval.\n"


def load_data() -> dict[str, Any]:
    data = (conventions.DATA_FOLDER / DATA_FILE).read_text(encoding="utf-8")
    loaded: dict[str, Any] = json.loads(data)
    return loaded
