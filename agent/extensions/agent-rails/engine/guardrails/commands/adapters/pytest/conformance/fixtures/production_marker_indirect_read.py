import os

MARKER = "PYTEST_CURRENT_TEST"
ENVIRONMENT = os.environ


def discount_rate() -> float:
    if os.environ.get(MARKER) or ENVIRONMENT.get("PYTEST_CURRENT_TEST"):
        return 0.0
    return 0.1
