import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

workspace = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("scoretool", workspace / "scoretool.py")
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
passed, total = 0, 7


def check(value):
    global passed
    if value:
        passed += 1


summary_function = getattr(module, "summarize_scores", None)
empty = summary_function([]) if summary_function else None
check(empty == {"count": 0, "unique": 0, "mean": 0.0, "median": 0.0})
values = [4.0, 1.0, 2.0, 2.0]
summary = summary_function(values) if summary_function else None
check(values == [4.0, 1.0, 2.0, 2.0])
check(summary == {"count": 4, "unique": 3, "mean": 2.25, "median": 2.0})
check(summary_function([1.0, 2.0, 6.0]) == {"count": 3, "unique": 3, "mean": 3.0, "median": 2.0} if summary_function else False)
check(summary_function([1.0, 2.0, 2.0])["mean"] == 1.67 if summary_function else False)
with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / "scores.txt"
    path.write_text("4\n1\n2\n2\n")
    result = subprocess.run([sys.executable, workspace / "scoretool.py", "--input", path, "--count", "--summary"], capture_output=True, text=True)
    check(result.returncode == 0 and json.loads(result.stdout) == {"count": 4, "unique": 3, "mean": 2.25, "median": 2.0})
    result = subprocess.run([sys.executable, workspace / "scoretool.py", "--input", path, "--count"], capture_output=True, text=True)
    check(result.returncode == 0 and result.stdout == "4\n")
print(json.dumps({"passed": passed, "total": total}))
sys.exit(0 if passed == total else 1)
