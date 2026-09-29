"""Run each independent test suite in an isolated Python process."""
import os
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"}
failed = []
suites = sorted(root.glob("test_*.py"))
for suite in suites:
    result = subprocess.run([sys.executable, str(suite)], cwd=root.parent,
                            env=environment, text=True, capture_output=True, encoding="utf-8")
    print(f"{'PASS' if result.returncode == 0 else 'FAIL'} {suite.name}")
    if result.returncode:
        failed.append(suite.name)
        print(result.stdout)
        print(result.stderr)
print(f"{len(suites) - len(failed)}/{len(suites)} suites passed")
raise SystemExit(bool(failed))
