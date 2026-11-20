RUNNERS = ("pytest", "unittest")
ARTIFACTS = {"TESTS_INVENTORY": "tests.json"}


def known_runner(name: str) -> bool:
    return name in RUNNERS


def artifact(key: str) -> str:
    return ARTIFACTS[key]
