import json
import sys
from pathlib import Path

workspace = Path(sys.argv[1])
sys.path.insert(0, str(workspace))
passed, total = 0, 7


def check(value):
    global passed
    if value:
        passed += 1


try:
    import settings
    import worker

    check(settings.DEFAULT_SETTINGS == {"maxAttempts": 3, "queueName": "default"})
    check(settings.get_max_attempts({}) == 3)
    check(settings.get_max_attempts({"maxRetries": 4}) == 4)
    check(settings.get_max_attempts({"maxAttempts": 2, "maxRetries": 4}) == 2)
    try:
        settings.get_max_attempts({"maxAttempts": True})
    except ValueError as error:
        check(str(error) == "maxAttempts must be a non-negative integer")
    else:
        check(False)
    check(worker.worker_settings({"maxRetries": 6}) == {"queue": "default", "maxAttempts": 6})
    check("maxAttempts" in (workspace / "example_config.py").read_text() and "maxRetries" not in (workspace / "README.md").read_text() and "Maximum attempts including the initial attempt." in (workspace / "README.md").read_text())
except (ImportError, AttributeError):
    pass
print(json.dumps({"passed": passed, "total": total}))
sys.exit(0 if passed == total else 1);
